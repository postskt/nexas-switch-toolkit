# -*- coding: utf-8 -*-
"""
NeXAS Script Converter & Pointer Relocation Engine
Handles conversion between PC (.bin) and Switch (.binu8) formats,
rebuilding string pools, recalculating byte offsets, and relocating bytecode operands.
"""

from dataclasses import replace
import struct
from typing import Dict, List, Optional, Tuple

from ..utils.binary_helper import BinaryWriter, encode_string
from .script_parser import NexasScript, ScriptInstruction, ScriptParser, ScriptString


class ScriptConverter:
    """
    Core engine for converting NeXAS scripts and maintaining bytecode pointer integrity.
    """

    def __init__(self):
        pass

    def convert_pc_to_switch(
        self,
        script: NexasScript,
        target_encoding: str = "utf-8",
        new_strings: Optional[List[str]] = None,
    ) -> Tuple[NexasScript, bytes]:
        """
        Convert a PC NeXAS script (.bin) to Nintendo Switch format (.binu8).
        - Re-encodes text from Shift-JIS to UTF-8
        - Adds Switch 4-byte string length prefix
        - Recomputes String Offset Table
        - Relocates all bytecode operands referencing old string offsets
        """
        # 1. Determine base offsets
        header_len = len(script.version_bytes)
        if script.header_type in ("VER9", "VER13"):
            header_len += 4 + len(script.unk_table) * 4

        init_code_bytes_len = 4 + len(script.init_codes) * 8
        code_bytes_len = 4 + len(script.codes) * 8
        str_count_len = 4

        new_str_pool_start = header_len + init_code_bytes_len + code_bytes_len + str_count_len

        # Determine old string pool start
        old_str_pool_start = 0
        if script.strings:
            old_str_pool_start = script.strings[0].offset

        # 2. Build new string objects and compute new offsets
        converted_strings: List[ScriptString] = []
        cur_offset = new_str_pool_start

        # Relocation mapping tables
        # Map: old_offset -> new_offset
        offset_map: Dict[int, int] = {}
        # Map: old_relative_offset -> new_relative_offset
        rel_offset_map: Dict[int, int] = {}

        source_texts = new_strings if new_strings is not None else [s.text for s in script.strings]
        if len(source_texts) != len(script.strings):
            raise ValueError(
                f"String count mismatch: script has {len(script.strings)} strings, "
                f"provided {len(source_texts)} replacement strings"
            )

        for i, old_s in enumerate(script.strings):
            text = source_texts[i]
            raw_utf8 = encode_string(text, target_encoding)
            str_byte_len = len(raw_utf8)

            record_offset = cur_offset
            text_offset = cur_offset + 4  # 4 bytes for length prefix

            # Map old record offset to new record offset
            offset_map[old_s.offset] = record_offset
            if old_s.has_length_prefix:
                offset_map[old_s.text_offset] = text_offset

            if old_str_pool_start > 0:
                old_rel = old_s.offset - old_str_pool_start
                rel_offset_map[old_rel] = record_offset - new_str_pool_start
                if old_s.has_length_prefix:
                    old_text_rel = old_s.text_offset - old_str_pool_start
                    rel_offset_map[old_text_rel] = text_offset - new_str_pool_start

            new_str = ScriptString(
                index=i,
                offset=record_offset,
                text_offset=text_offset,
                length=str_byte_len,
                text=text,
                raw_bytes=raw_utf8,
                has_length_prefix=True,
            )
            converted_strings.append(new_str)

            # Advance offset: 4 bytes length + string bytes + 1 byte null terminator
            cur_offset += 4 + str_byte_len + 1

        # 3. Relocate Bytecode Operands in init_codes and codes
        new_init_codes = self._relocate_instructions(script.init_codes, offset_map, rel_offset_map)
        new_codes = self._relocate_instructions(script.codes, offset_map, rel_offset_map)

        # 4. Construct new NexasScript representation
        new_script = NexasScript(
            header_type=script.header_type,
            version_bytes=script.version_bytes,
            unk_table=list(script.unk_table),
            init_codes=new_init_codes,
            codes=new_codes,
            strings=converted_strings,
            encoding=target_encoding,
            is_switch=True,
            raw_tail=script.raw_tail,
        )

        # 5. Serialize into .binu8 binary bytes
        binu8_bytes = self.serialize(new_script)
        return new_script, binu8_bytes

    def convert_switch_to_pc(
        self,
        script: NexasScript,
        target_encoding: str = "cp932",
        new_strings: Optional[List[str]] = None,
    ) -> Tuple[NexasScript, bytes]:
        """
        Reciprocal conversion: Convert Switch .binu8 back to PC .bin.
        Useful for verification, diffing, and bidirectional tooling.
        """
        header_len = len(script.version_bytes)
        if script.header_type in ("VER9", "VER13"):
            header_len += 4 + len(script.unk_table) * 4

        init_code_bytes_len = 4 + len(script.init_codes) * 8
        code_bytes_len = 4 + len(script.codes) * 8
        str_count_len = 4

        new_str_pool_start = header_len + init_code_bytes_len + code_bytes_len + str_count_len

        old_str_pool_start = script.strings[0].offset if script.strings else 0

        converted_strings: List[ScriptString] = []
        cur_offset = new_str_pool_start
        offset_map: Dict[int, int] = {}
        rel_offset_map: Dict[int, int] = {}

        source_texts = new_strings if new_strings is not None else [s.text for s in script.strings]

        for i, old_s in enumerate(script.strings):
            text = source_texts[i]
            raw_sjis = encode_string(text, target_encoding)
            str_byte_len = len(raw_sjis)

            record_offset = cur_offset
            text_offset = cur_offset

            offset_map[old_s.offset] = record_offset
            offset_map[old_s.text_offset] = text_offset

            if old_str_pool_start > 0:
                old_rel = old_s.offset - old_str_pool_start
                old_text_rel = old_s.text_offset - old_str_pool_start
                rel_offset_map[old_rel] = record_offset - new_str_pool_start
                rel_offset_map[old_text_rel] = text_offset - new_str_pool_start

            new_str = ScriptString(
                index=i,
                offset=record_offset,
                text_offset=text_offset,
                length=str_byte_len,
                text=text,
                raw_bytes=raw_sjis,
                has_length_prefix=False,
            )
            converted_strings.append(new_str)

            # Advance offset: string bytes + 1 byte null terminator (no 4-byte length prefix)
            cur_offset += str_byte_len + 1

        new_init_codes = self._relocate_instructions(script.init_codes, offset_map, rel_offset_map)
        new_codes = self._relocate_instructions(script.codes, offset_map, rel_offset_map)

        new_script = NexasScript(
            header_type=script.header_type,
            version_bytes=script.version_bytes,
            unk_table=list(script.unk_table),
            init_codes=new_init_codes,
            codes=new_codes,
            strings=converted_strings,
            encoding=target_encoding,
            is_switch=False,
            raw_tail=script.raw_tail,
        )

        bin_bytes = self.serialize(new_script)
        return new_script, bin_bytes

    def _relocate_instructions(
        self,
        instructions: List[ScriptInstruction],
        offset_map: Dict[int, int],
        rel_offset_map: Dict[int, int],
    ) -> List[ScriptInstruction]:
        """
        Relocate operand pointers in instructions that target relocated string offsets.
        """
        relocated = []
        for instr in instructions:
            new_operand = instr.operand
            if instr.operand in offset_map:
                new_operand = offset_map[instr.operand]
            elif instr.operand in rel_offset_map:
                new_operand = rel_offset_map[instr.operand]

            relocated.append(
                ScriptInstruction(
                    offset=instr.offset,
                    opcode=instr.opcode,
                    operand=new_operand,
                )
            )
        return relocated

    def serialize(self, script: NexasScript) -> bytes:
        """
        Serialize NexasScript object to binary format (.bin or .binu8).
        """
        writer = BinaryWriter()

        # 1. Header
        if script.header_type in ("VER9", "VER13"):
            writer.write_bytes(script.version_bytes)
            writer.write_u32(len(script.unk_table))
            for val in script.unk_table:
                writer.write_u32(val)

        # 2. Init codes
        writer.write_u32(len(script.init_codes))
        for instr in script.init_codes:
            writer.write_u32(instr.opcode)
            writer.write_u32(instr.operand)

        # 3. Codes
        writer.write_u32(len(script.codes))
        for instr in script.codes:
            writer.write_u32(instr.opcode)
            writer.write_u32(instr.operand)

        # 4. Strings
        writer.write_u32(len(script.strings))
        for s in script.strings:
            if s.has_length_prefix:
                # Switch .binu8 format: 4-byte length prefix + bytes + null
                writer.write_u32(len(s.raw_bytes))
                writer.write_bytes(s.raw_bytes)
                writer.write_u8(0)
            else:
                # PC .bin format: bytes + null
                writer.write_bytes(s.raw_bytes)
                writer.write_u8(0)

        # 5. Raw tail if any
        if script.raw_tail:
            writer.write_bytes(script.raw_tail)

        return writer.get_bytes()
