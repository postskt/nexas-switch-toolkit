# -*- coding: utf-8 -*-
"""
NeXAS PAC Archive Builder
Reconstructs valid PAC archives for PC or Nintendo Switch targets.
"""

import json
import os
from pathlib import Path
from typing import Callable, List, Optional, Union

from ..utils.binary_helper import BinaryWriter, encode_string
from ..utils.compression import compress_data
from .pac_common import (
    PAC_ENTRY_SIZE,
    PAC_MAGIC,
    PAC_NAME_MAX_LEN,
    PacArchive,
    PacEntry,
    PacVariant,
)


class PacBuilder:
    """
    Builder and repacker for NeXAS PAC archives.
    """

    def __init__(
        self,
        target_platform: str = "switch",
        compression: str = "zstd",
        compression_level: int = 3,
        encoding: Optional[str] = None,
        variant: Optional[PacVariant] = None,
        alignment: int = 4,
    ):
        """
        :param target_platform: 'switch' or 'pc'
        :param compression: 'zstd', 'zlib', 'lzss', 'none'
        :param compression_level: compression level (1-9 for zlib, 1-22 for zstd)
        :param encoding: 'utf-8' (Switch default) or 'cp932' (PC default)
        :param variant: PacVariant.V4_MAGIC or PacVariant.V1_NO_MAGIC
        :param alignment: byte alignment for each file payload (default 4 bytes)
        """
        self.target_platform = target_platform.lower()
        self.compression = compression.lower()
        self.compression_level = compression_level
        self.alignment = max(alignment, 1)

        # Platform default configurations
        if self.target_platform == "switch":
            self.encoding = encoding or "utf-8"
            self.variant = variant or PacVariant.V4_MAGIC
            if self.compression == "auto":
                self.compression = "zstd"
        else:
            self.encoding = encoding or "cp932"
            self.variant = variant or PacVariant.V4_MAGIC
            if self.compression == "auto":
                self.compression = "zlib"

    def build_from_directory(
        self,
        input_dir: str,
        output_pac_path: str,
        manifest_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> PacArchive:
        """
        Pack all files in `input_dir` into a single .pac archive.
        """
        in_path = Path(input_dir)
        if not in_path.is_dir():
            raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

        out_pac = Path(output_pac_path)
        out_pac.parent.mkdir(parents=True, exist_ok=True)

        # Scan files
        file_list: List[Path] = []
        if manifest_path and Path(manifest_path).exists():
            with open(manifest_path, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
                for item in mdata.get("files", []):
                    fp = in_path / item["filename"]
                    if fp.exists():
                        file_list.append(fp)
        else:
            # Recursive scan
            for root, _, files in os.walk(in_path):
                for f in sorted(files):
                    if f == "pac_manifest.json":
                        continue
                    file_list.append(Path(root) / f)

        if not file_list:
            raise ValueError(f"No files found to pack in {input_dir}")

        # Compute relative paths
        entries: List[PacEntry] = []
        for fp in file_list:
            rel_name = fp.relative_to(in_path).as_posix()
            # Verify name length under target encoding
            encoded_name = encode_string(rel_name, self.encoding)
            if len(encoded_name) >= PAC_NAME_MAX_LEN:
                raise ValueError(
                    f"Filename '{rel_name}' encoded length ({len(encoded_name)} bytes) "
                    f"exceeds PAC limit of {PAC_NAME_MAX_LEN - 1} bytes."
                )
            entries.append(
                PacEntry(
                    filename=rel_name,
                    offset=0,
                    compressed_size=0,
                    uncompressed_size=0,
                    raw_name_bytes=encoded_name,
                )
            )

        # Calculate TOC Layout
        header_size = 8 if self.variant == PacVariant.V4_MAGIC else 4
        toc_size = len(entries) * PAC_ENTRY_SIZE
        data_start_offset = header_size + toc_size

        # Align start offset
        if data_start_offset % self.alignment != 0:
            data_start_offset += self.alignment - (data_start_offset % self.alignment)

        # Open output file for streaming build
        current_offset = data_start_offset
        total_files = len(entries)

        with open(out_pac, "wb") as pac_f:
            # 1. Pre-allocate / skip Header and TOC area
            pac_f.seek(data_start_offset)

            # 2. Process and write payloads
            for idx, entry in enumerate(entries):
                fp = in_path / entry.filename
                with open(fp, "rb") as raw_f:
                    raw_bytes = raw_f.read()

                uncomp_size = len(raw_bytes)
                comp_bytes, actual_method = compress_data(
                    raw_bytes, self.compression, self.compression_level
                )

                # If compression expands size or doesn't benefit, store raw
                if len(comp_bytes) >= uncomp_size and self.compression != "none":
                    comp_bytes = raw_bytes
                    comp_size = uncomp_size
                    actual_method = "none"
                else:
                    comp_size = len(comp_bytes)

                # Align current offset
                if current_offset % self.alignment != 0:
                    pad_len = self.alignment - (current_offset % self.alignment)
                    pac_f.write(b"\x00" * pad_len)
                    current_offset += pad_len

                entry.offset = current_offset
                entry.compressed_size = comp_size
                entry.uncompressed_size = uncomp_size
                entry.compression_type = actual_method

                pac_f.write(comp_bytes)
                current_offset += comp_size

                if progress_callback:
                    progress_callback(idx + 1, total_files, entry.filename)

            # 3. Rewind and write Header & TOC
            pac_f.seek(0)
            writer = BinaryWriter()

            # Write header
            if self.variant == PacVariant.V4_MAGIC:
                writer.write_bytes(PAC_MAGIC)
            writer.write_u32(len(entries))

            # Write TOC entries
            for entry in entries:
                # 64 bytes filename
                name_bytes = encode_string(entry.filename, self.encoding)
                padded_name = name_bytes + b"\x00" * (PAC_NAME_MAX_LEN - len(name_bytes))
                writer.write_bytes(padded_name)
                # uint32 offset
                writer.write_u32(entry.offset)
                # uint32 compressed_size
                writer.write_u32(entry.compressed_size)
                # uint32 uncompressed_size
                writer.write_u32(entry.uncompressed_size)

            # Write TOC to stream
            toc_bytes = writer.get_bytes()
            pac_f.write(toc_bytes)

        return PacArchive(
            variant=self.variant,
            encoding=self.encoding,
            entries=entries,
            header_size=header_size,
            total_data_size=current_offset,
        )
