# -*- coding: utf-8 -*-
"""
NeXAS Engine Compression Utilities
Supports:
- Zlib (Deflate RFC 1950)
- Zstandard (RFC 8878)
- LZSS (NeXAS / GIGA ring buffer variant: N=4096, F=18, THRESHOLD=2)
- Raw / No compression
"""

import zlib
from typing import Optional, Tuple

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False


# ==========================================
# LZSS Pure Python Implementation
# ==========================================
class LZSS:
    """
    Standard Okumura LZSS codec as used by NeXAS / GIGA engines.
    Parameters:
      N = 4096 (ring buffer size, 12-bit offset)
      F = 18   (max match length, 4-bit length + THRESHOLD)
      THRESHOLD = 2
    """
    N = 4096
    F = 18
    THRESHOLD = 2

    @classmethod
    def decompress(cls, src_data: bytes, uncompressed_size: Optional[int] = None) -> bytes:
        src_len = len(src_data)
        if src_len == 0:
            return b""

        ring_buffer = bytearray(cls.N + cls.F - 1)
        # NeXAS typically initializes ring buffer with 0x00 or space (0x20)
        # 0x00 is default for NeXAS LZSS
        for i in range(cls.N - cls.F):
            ring_buffer[i] = 0

        r = cls.N - cls.F
        flags = 0
        src_idx = 0
        out = bytearray()

        while src_idx < src_len:
            flags >>= 1
            if (flags & 0x100) == 0:
                if src_idx >= src_len:
                    break
                flags = src_data[src_idx] | 0xFF00
                src_idx += 1

            if flags & 1:
                if src_idx >= src_len:
                    break
                c = src_data[src_idx]
                src_idx += 1
                out.append(c)
                ring_buffer[r] = c
                r = (r + 1) & (cls.N - 1)
                if uncompressed_size is not None and len(out) >= uncompressed_size:
                    break
            else:
                if src_idx + 1 >= src_len:
                    break
                b1 = src_data[src_idx]
                b2 = src_data[src_idx + 1]
                src_idx += 2

                match_offset = b1 | ((b2 & 0xF0) << 4)
                match_length = (b2 & 0x0F) + cls.THRESHOLD

                for k in range(match_length + 1):
                    c = ring_buffer[(match_offset + k) & (cls.N - 1)]
                    out.append(c)
                    ring_buffer[r] = c
                    r = (r + 1) & (cls.N - 1)
                    if uncompressed_size is not None and len(out) >= uncompressed_size:
                        break

            if uncompressed_size is not None and len(out) >= uncompressed_size:
                break

        return bytes(out)

    @classmethod
    def compress(cls, src_data: bytes) -> bytes:
        """
        Simple greedy LZSS compressor matching the NeXAS dictionary format.
        """
        src_len = len(src_data)
        if src_len == 0:
            return b""

        ring_buffer = bytearray(cls.N + cls.F - 1)
        r = cls.N - cls.F
        out = bytearray()
        src_idx = 0

        code_buf = bytearray(17)
        mask = 1
        code_buf[0] = 0
        code_buf_ptr = 1

        while src_idx < src_len:
            # Find longest match in ring buffer
            max_len = 0
            best_offset = 0

            limit = min(cls.F, src_len - src_idx)
            if limit > cls.THRESHOLD:
                # Search window
                search_len = cls.N - cls.F if src_idx < (cls.N - cls.F) else cls.N
                for off in range(search_len):
                    match_len = 0
                    while match_len < limit:
                        if ring_buffer[(off + match_len) & (cls.N - 1)] != src_data[src_idx + match_len]:
                            break
                        match_len += 1
                    if match_len > max_len:
                        max_len = match_len
                        best_offset = off
                        if max_len == cls.F:
                            break

            if max_len <= cls.THRESHOLD:
                # Literal byte
                c = src_data[src_idx]
                src_idx += 1
                code_buf[0] |= mask
                code_buf[code_buf_ptr] = c
                code_buf_ptr += 1
                ring_buffer[r] = c
                r = (r + 1) & (cls.N - 1)
            else:
                # Match
                src_idx += max_len
                b1 = best_offset & 0xFF
                b2 = ((best_offset >> 4) & 0xF0) | ((max_len - 1 - cls.THRESHOLD) & 0x0F)
                code_buf[code_buf_ptr] = b1
                code_buf[code_buf_ptr + 1] = b2
                code_buf_ptr += 2
                for k in range(max_len):
                    ring_buffer[r] = src_data[src_idx - max_len + k]
                    r = (r + 1) & (cls.N - 1)

            mask <<= 1
            if mask == 0x100:
                out.extend(code_buf[:code_buf_ptr])
                code_buf[0] = 0
                code_buf_ptr = 1
                mask = 1

        if code_buf_ptr > 1:
            out.extend(code_buf[:code_buf_ptr])

        return bytes(out)


# ==========================================
# Compression Dispatcher
# ==========================================
def detect_compression(data: bytes, orig_size: Optional[int] = None) -> str:
    """
    Detect compression algorithm by analyzing binary signature.
    """
    if len(data) == 0:
        return "none"

    # If uncompressed size is provided and matches data length, likely raw
    if orig_size is not None and len(data) == orig_size:
        return "none"

    # Zstandard magic bytes: 0x28 0xB5 0x2F 0xFD
    if len(data) >= 4 and data[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"

    # Zlib magic header detection:
    # Common headers: 0x78 0x9C (default), 0x78 0x01 (no comp/low), 0x78 0xDA (best)
    if len(data) >= 2:
        cmf = data[0]
        flg = data[1]
        if (cmf & 0x0F) == 8 and ((cmf >> 4) <= 7):
            if (cmf * 256 + flg) % 31 == 0:
                return "zlib"

    # LZSS or Unknown
    return "auto"


def decompress_data(data: bytes, orig_size: int, comp_type: str = "auto") -> bytes:
    """
    Decompress byte payload based on algorithm type.
    """
    if len(data) == 0 or orig_size == 0:
        return b""

    # Already uncompressed
    if len(data) == orig_size and comp_type == "none":
        return data

    if comp_type == "auto":
        detected = detect_compression(data, orig_size)
        if detected != "auto":
            comp_type = detected

    # 1. Try Zstandard
    if comp_type == "zstd" or comp_type == "auto":
        if HAS_ZSTD and len(data) >= 4 and data[:4] == b"\x28\xb5\x2f\xfd":
            try:
                dctx = zstd.ZstdDecompressor()
                return dctx.decompress(data, max_output_size=orig_size * 2 + 1024)
            except Exception:
                pass

    # 2. Try Zlib
    if comp_type == "zlib" or comp_type == "auto":
        try:
            return zlib.decompress(data)
        except Exception:
            pass

    # 3. Try LZSS
    if comp_type == "lzss" or comp_type == "auto":
        try:
            decomp = LZSS.decompress(data, uncompressed_size=orig_size)
            if len(decomp) == orig_size:
                return decomp
        except Exception:
            pass

    # 4. Fallback to raw if length matches or no other option works
    if len(data) == orig_size:
        return data

    raise ValueError(
        f"Failed to decompress {len(data)} bytes into {orig_size} bytes (comp_type={comp_type})"
    )


def compress_data(data: bytes, method: str = "zstd", level: int = 3) -> Tuple[bytes, str]:
    """
    Compress byte payload using specified method.
    Returns (compressed_data, actual_method_used).
    """
    if len(data) == 0:
        return b"", "none"

    method = method.lower()
    if method == "none" or method == "raw":
        return data, "none"

    if method == "zstd":
        if not HAS_ZSTD:
            # Fallback to zlib if zstd is not installed
            compressed = zlib.compress(data, level=6)
            return compressed, "zlib"
        cctx = zstd.ZstdCompressor(level=level)
        return cctx.compress(data), "zstd"

    if method == "zlib":
        zlib_level = min(max(level, 1), 9)
        return zlib.compress(data, level=zlib_level), "zlib"

    if method == "lzss":
        return LZSS.compress(data), "lzss"

    raise ValueError(f"Unsupported compression method: {method}")
