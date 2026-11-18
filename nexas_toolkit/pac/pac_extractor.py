# -*- coding: utf-8 -*-
"""
NeXAS PAC Archive Extractor
Supports reading both PC and Switch PAC archives, resolving TOC, and decompressing payloads.
"""

import json
import os
from pathlib import Path
from typing import Callable, Optional

from ..utils.binary_helper import BinaryReader, decode_string
from ..utils.compression import decompress_data, detect_compression
from .pac_common import (
    PAC_ENTRY_SIZE,
    PAC_MAGIC,
    PAC_MAGIC_ALT,
    PAC_NAME_MAX_LEN,
    PacArchive,
    PacEntry,
    PacVariant,
)


class PacExtractor:
    """
    Parser and extractor for NeXAS PAC packages.
    """

    def __init__(self, pac_path: str, encoding: Optional[str] = None):
        self.pac_path = Path(pac_path)
        if not self.pac_path.exists():
            raise FileNotFoundError(f"PAC file not found: {pac_path}")

        self.forced_encoding = encoding
        self.archive: Optional[PacArchive] = None

    def parse_header(self) -> PacArchive:
        """Parse TOC table without immediately extracting all files."""
        with open(self.pac_path, "rb") as f:
            reader = BinaryReader(f)
            total_size = reader.size()

            if total_size < 8:
                raise ValueError("File is too small to be a valid PAC archive")

            # Check magic
            magic = reader.read_bytes(4)
            if magic in (PAC_MAGIC, PAC_MAGIC_ALT):
                variant = PacVariant.V4_MAGIC
                file_count = reader.read_u32()
                header_size = 8
            else:
                # Could be V1 without magic (file_count directly at offset 0)
                reader.seek(0)
                file_count = reader.read_u32()
                variant = PacVariant.V1_NO_MAGIC
                header_size = 4

            # Sanity check file_count
            expected_toc_size = file_count * PAC_ENTRY_SIZE
            if expected_toc_size + header_size > total_size:
                # Fallback: maybe file_count was invalid in V1 assumption
                raise ValueError(
                    f"Corrupted PAC header: file_count {file_count} results in invalid TOC size"
                )

            # Detect encoding across all entry names if not specified
            raw_entries = []
            all_name_bytes = []
            for _ in range(file_count):
                raw_name = reader.read_bytes(PAC_NAME_MAX_LEN)
                offset = reader.read_u32()
                comp_size = reader.read_u32()
                uncomp_size = reader.read_u32()

                # Clean null terminator
                null_pos = raw_name.find(b"\x00")
                trimmed_name = raw_name[:null_pos] if null_pos != -1 else raw_name
                all_name_bytes.append(trimmed_name)
                raw_entries.append((trimmed_name, offset, comp_size, uncomp_size))

            detected_encoding = self._detect_encoding(all_name_bytes) if not self.forced_encoding else self.forced_encoding

            # Build PacEntry objects
            entries = []
            for raw_name, offset, comp_size, uncomp_size in raw_entries:
                fname = decode_string(raw_name, detected_encoding)
                # Some engines store (uncomp_size, comp_size) reversed in edge variants
                # Detect and normalize:
                if comp_size > uncomp_size and uncomp_size > 0:
                    # Likely reversed
                    c_size = uncomp_size
                    u_size = comp_size
                else:
                    c_size = comp_size
                    u_size = uncomp_size

                entry = PacEntry(
                    filename=fname,
                    offset=offset,
                    compressed_size=c_size,
                    uncompressed_size=u_size,
                    raw_name_bytes=raw_name,
                )
                entries.append(entry)

            self.archive = PacArchive(
                variant=variant,
                encoding=detected_encoding,
                entries=entries,
                header_size=header_size,
                total_data_size=total_size,
            )
            return self.archive

    def _detect_encoding(self, names_bytes: list) -> str:
        """Auto-detect whether filenames are UTF-8 (Switch) or Shift-JIS/CP932 (PC)."""
        combined = b"".join(names_bytes)
        try:
            combined.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "cp932"

    def extract_entry(self, entry: PacEntry, f_stream) -> bytes:
        """Extract and decompress a single entry's payload."""
        f_stream.seek(entry.offset)
        raw_payload = f_stream.read(entry.compressed_size)
        if len(raw_payload) < entry.compressed_size:
            raise EOFError(
                f"Unexpected EOF reading {entry.filename}: expected {entry.compressed_size} bytes, got {len(raw_payload)}"
            )

        if not entry.is_compressed:
            entry.compression_type = "none"
            return raw_payload

        # Detect compression
        entry.compression_type = detect_compression(raw_payload, entry.uncompressed_size)
        decompressed = decompress_data(
            raw_payload, entry.uncompressed_size, entry.compression_type
        )
        return decompressed

    def extract_all(
        self,
        output_dir: str,
        dump_manifest: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> PacArchive:
        """
        Extract all files from PAC into output directory.
        """
        if not self.archive:
            self.parse_header()

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        manifest_data = {
            "archive_name": self.pac_path.name,
            "variant": self.archive.variant.value,
            "encoding": self.archive.encoding,
            "file_count": len(self.archive.entries),
            "files": [],
        }

        total_files = len(self.archive.entries)
        with open(self.pac_path, "rb") as f:
            for idx, entry in enumerate(self.archive.entries):
                if progress_callback:
                    progress_callback(idx + 1, total_files, entry.filename)

                data = self.extract_entry(entry, f)
                target_file = out_path / entry.filename
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with open(target_file, "wb") as out_f:
                    out_f.write(data)

                manifest_data["files"].append(
                    {
                        "filename": entry.filename,
                        "offset": entry.offset,
                        "compressed_size": entry.compressed_size,
                        "uncompressed_size": entry.uncompressed_size,
                        "compression_type": entry.compression_type,
                    }
                )

        if dump_manifest:
            manifest_file = out_path / "pac_manifest.json"
            with open(manifest_file, "w", encoding="utf-8") as mf:
                json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

        return self.archive
