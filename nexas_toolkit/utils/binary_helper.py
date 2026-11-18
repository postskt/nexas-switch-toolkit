# -*- coding: utf-8 -*-
"""
Binary Stream Helper Utilities for NeXAS File Formats
Provides structured reading, writing, endianness handling, and encoding safe decoders.
"""

import io
import struct
from typing import Optional, Union


class BinaryReader:
    """
    Stream reader with convenience methods for NeXAS binary parsing (Little-Endian).
    """

    def __init__(self, data_or_stream: Union[bytes, bytearray, io.IOBase]):
        if isinstance(data_or_stream, (bytes, bytearray)):
            self.stream = io.BytesIO(data_or_stream)
        else:
            self.stream = data_or_stream

    def read_u8(self) -> int:
        b = self.stream.read(1)
        if len(b) < 1:
            raise EOFError("Unexpected EOF reading uint8")
        return b[0]

    def read_u16(self) -> int:
        b = self.stream.read(2)
        if len(b) < 2:
            raise EOFError("Unexpected EOF reading uint16")
        return struct.unpack("<H", b)[0]

    def read_u32(self) -> int:
        b = self.stream.read(4)
        if len(b) < 4:
            raise EOFError("Unexpected EOF reading uint32")
        return struct.unpack("<I", b)[0]

    def read_i32(self) -> int:
        b = self.stream.read(4)
        if len(b) < 4:
            raise EOFError("Unexpected EOF reading int32")
        return struct.unpack("<i", b)[0]

    def read_bytes(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) < size:
            raise EOFError(f"Requested {size} bytes, got {len(data)}")
        return data

    def read_fixed_string(self, length: int, encoding: str = "cp932") -> str:
        raw = self.read_bytes(length)
        # Find null terminator
        null_pos = raw.find(b"\x00")
        if null_pos != -1:
            raw = raw[:null_pos]
        return decode_string(raw, encoding)

    def read_cstring(self, encoding: str = "cp932") -> str:
        chars = bytearray()
        while True:
            b = self.stream.read(1)
            if not b or b == b"\x00":
                break
            chars.extend(b)
        return decode_string(bytes(chars), encoding)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        return self.stream.tell()

    def size(self) -> int:
        pos = self.stream.tell()
        self.stream.seek(0, io.SEEK_END)
        total = self.stream.tell()
        self.stream.seek(pos, io.SEEK_SET)
        return total

    def remaining(self) -> int:
        return self.size() - self.tell()


class BinaryWriter:
    """
    Stream writer with convenience methods for building NeXAS binary structures.
    """

    def __init__(self):
        self.stream = io.BytesIO()

    def write_u8(self, val: int) -> None:
        self.stream.write(struct.pack("B", val & 0xFF))

    def write_u16(self, val: int) -> None:
        self.stream.write(struct.pack("<H", val & 0xFFFF))

    def write_u32(self, val: int) -> None:
        self.stream.write(struct.pack("<I", val & 0xFFFFFFFF))

    def write_i32(self, val: int) -> None:
        self.stream.write(struct.pack("<i", val))

    def write_bytes(self, data: bytes) -> None:
        self.stream.write(data)

    def write_fixed_string(self, s: str, length: int, encoding: str = "utf-8", pad_byte: int = 0) -> None:
        raw = encode_string(s, encoding)
        if len(raw) > length:
            raise ValueError(f"String '{s}' ({len(raw)} bytes) exceeds fixed length {length}")
        padded = raw + bytes([pad_byte] * (length - len(raw)))
        self.stream.write(padded)

    def write_cstring(self, s: str, encoding: str = "utf-8") -> None:
        raw = encode_string(s, encoding)
        self.stream.write(raw + b"\x00")

    def pad_to(self, alignment: int, pad_byte: int = 0) -> int:
        curr = self.stream.tell()
        remainder = curr % alignment
        if remainder != 0:
            pad_len = alignment - remainder
            self.stream.write(bytes([pad_byte] * pad_len))
            return pad_len
        return 0

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def tell(self) -> int:
        return self.stream.tell()

    def get_bytes(self) -> bytes:
        return self.stream.getvalue()


def decode_string(raw: bytes, encoding: str = "cp932") -> str:
    """
    Safely decode byte strings, with fallback attempts for game assets.
    """
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        # Fallback to UTF-8 or CP932 with replace
        alternative = "utf-8" if encoding.lower() in ("cp932", "shift_jis", "sjis") else "cp932"
        try:
            return raw.decode(alternative)
        except UnicodeDecodeError:
            return raw.decode(encoding, errors="replace")


def encode_string(text: str, encoding: str = "utf-8") -> bytes:
    """
    Encode strings for game binaries.
    """
    try:
        return text.encode(encoding)
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace")
