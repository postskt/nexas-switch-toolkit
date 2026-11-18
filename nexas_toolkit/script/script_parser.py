# -*- coding: utf-8 -*-
"""
NeXAS Script Binary Parser (.bin / .binu8)
Parses bytecode instructions, initialization routines, header metadata, and string pools.
"""

from dataclasses import dataclass, field
import io
import struct
from typing import List, Optional, Tuple

from ..utils.binary_helper import BinaryReader, decode_string


@dataclass
class ScriptString:
    """Represents a string entry within the script."""
    index: int
    offset: int                # Absolute offset where the string record started in the original file
    text_offset: int           # Absolute offset where the actual text bytes started
    length: int                # String byte length
    text: str                  # Decoded string
    raw_bytes: bytes           # Raw byte payload
    has_length_prefix: bool    # True for Switch .binu8 style (4-byte length before string)


@dataclass
class ScriptInstruction:
    """Represents an 8-byte bytecode instruction."""
    offset: int                # Offset in binary file
    opcode: int                # 32-bit opcode
    operand: int               # 32-bit operand or pointer offset

    def __repr__(self) -> str:
        return f"Instr(off=0x{self.offset:X}, op=0x{self.opcode:08X}, arg=0x{self.operand:08X})"


@dataclass
class NexasScript:
    """Represents an in-memory parsed NeXAS binary script."""
    header_type: str                         # 'VER9', 'VER13', or 'RAW'
    version_bytes: bytes                     # Raw bytes of the version string if present
    unk_table: List[int]                     # Header skip table (32-bit values)
    init_codes: List[ScriptInstruction]      # Initialization routine opcodes (8 bytes each)
    codes: List[ScriptInstruction]           # Main instruction stream (8 bytes each)
    strings: List[ScriptString]              # Extracted dialogue and system strings
    encoding: str = "cp932"                  # Detected or configured encoding
    is_switch: bool = False                  # True if .binu8 format
    raw_tail: bytes = b""                    # Any remaining bytes at the end of the file


class ScriptParser:
    """
    Parser for NeXAS compiled scripts (.bin and .binu8).
    """

    def __init__(self, encoding: Optional[str] = None):
        self.forced_encoding = encoding

    def parse(self, data: bytes) -> NexasScript:
        reader = BinaryReader(data)
        total_len = len(data)

        if total_len < 16:
            raise ValueError(f"Script file is too small ({total_len} bytes)")

        # 1. Parse Header
        header_type = "RAW"
        version_bytes = b""
        unk_table = []

        first_4 = data[:4]
        # Check Case 1: Starts directly with 'VER' (0x56, 0x45, 0x52)
        if data[:3] == b"VER":
            header_type = "VER9"
            version_bytes = reader.read_bytes(9)
            unk_count = reader.read_u32()
            for _ in range(unk_count):
                unk_table.append(reader.read_u32())
        # Check Case 2: Starts with length prefix 9 (0x09 0x00 0x00 0x00) and 'VER' at offset 4
        elif len(data) > 13 and first_4 == b"\x09\x00\x00\x00" and data[4:7] == b"VER":
            header_type = "VER13"
            version_bytes = reader.read_bytes(13)
            unk_count = reader.read_u32()
            for _ in range(unk_count):
                unk_table.append(reader.read_u32())
        else:
            # Case 3: Raw / Legacy header (starts directly with init_code_count)
            header_type = "RAW"
            reader.seek(0)

        # 2. Parse Init Codes
        init_codes = []
        init_code_count = reader.read_u32()
        for _ in range(init_code_count):
            off = reader.tell()
            opcode = reader.read_u32()
            operand = reader.read_u32()
            init_codes.append(ScriptInstruction(offset=off, opcode=opcode, operand=operand))

        # 3. Parse Main Code Stream
        codes = []
        code_count = reader.read_u32()
        for _ in range(code_count):
            off = reader.tell()
            opcode = reader.read_u32()
            operand = reader.read_u32()
            codes.append(ScriptInstruction(offset=off, opcode=opcode, operand=operand))

        # 4. Parse String Pool
        str_count = reader.read_u32()
        strings = []

        # Determine if this file uses Switch .binu8 style (length prefix) or PC style
        # We test the format at the first string position
        cur_pos = reader.tell()
        is_switch_detected = False
        default_enc = self.forced_encoding

        if default_enc is None:
            # Probe: if reading 4 bytes length matches null-terminated utf-8, likely Switch
            if reader.remaining() >= 5:
                test_len = struct.unpack("<I", reader.read_bytes(4))[0]
                reader.seek(cur_pos)
                if 0 < test_len < reader.remaining():
                    candidate_bytes = data[cur_pos + 4 : cur_pos + 4 + test_len]
                    try:
                        candidate_bytes.decode("utf-8")
                        is_switch_detected = True
                        default_enc = "utf-8"
                    except UnicodeDecodeError:
                        pass

        if default_enc is None:
            default_enc = "utf-8" if is_switch_detected else "cp932"

        # Read strings
        for i in range(str_count):
            if reader.remaining() <= 0:
                break

            record_offset = reader.tell()
            has_len_prefix = False

            # Check if Switch style (has 4-byte string length prefix)
            if is_switch_detected or default_enc == "utf-8":
                if reader.remaining() >= 4:
                    str_len_prefix = reader.read_u32()
                    has_len_prefix = True
                    text_start_offset = reader.tell()
                    # Read until null terminator
                    raw_chars = bytearray()
                    while reader.remaining() > 0:
                        b = reader.read_bytes(1)
                        if b == b"\x00":
                            break
                        raw_chars.extend(b)
                    text_str = decode_string(bytes(raw_chars), default_enc)
                    strings.append(
                        ScriptString(
                            index=i,
                            offset=record_offset,
                            text_offset=text_start_offset,
                            length=len(raw_chars),
                            text=text_str,
                            raw_bytes=bytes(raw_chars),
                            has_length_prefix=True,
                        )
                    )
                    continue

            # PC style: null-terminated C-string
            text_start_offset = record_offset
            raw_chars = bytearray()
            while reader.remaining() > 0:
                b = reader.read_bytes(1)
                if b == b"\x00":
                    break
                raw_chars.extend(b)
            text_str = decode_string(bytes(raw_chars), default_enc)
            strings.append(
                ScriptString(
                    index=i,
                    offset=record_offset,
                    text_offset=text_start_offset,
                    length=len(raw_chars),
                    text=text_str,
                    raw_bytes=bytes(raw_chars),
                    has_length_prefix=False,
                )
            )

        # Capture any tail bytes (alignment padding, checksums, etc.)
        raw_tail = reader.read_bytes(reader.remaining())

        return NexasScript(
            header_type=header_type,
            version_bytes=version_bytes,
            unk_table=unk_table,
            init_codes=init_codes,
            codes=codes,
            strings=strings,
            encoding=default_enc,
            is_switch=is_switch_detected,
            raw_tail=raw_tail,
        )
