# -*- coding: utf-8 -*-
"""
NeXAS PAC Archive Definitions and Structures
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class PacVariant(str, Enum):
    """PAC Archive Format Variants"""
    V4_MAGIC = "v4_magic"    # Starts with 'PAC\0', used in Baldr Sky, Switch NeXAS ports
    V1_NO_MAGIC = "v1_raw"   # Starts directly with file_count uint32 (Baldr Force EXE, older PC)


PAC_MAGIC = b"PAC\x00"
PAC_MAGIC_ALT = b"PAC\x01"
PAC_NAME_MAX_LEN = 64
PAC_ENTRY_SIZE = 76  # 64 (name) + 4 (offset) + 4 (comp_size) + 4 (uncomp_size)


@dataclass
class PacEntry:
    """Individual file entry metadata within the PAC Table of Contents (TOC)."""
    filename: str
    offset: int
    compressed_size: int
    uncompressed_size: int
    compression_type: str = "auto"
    raw_name_bytes: bytes = field(default=b"", repr=False)
    data: Optional[bytes] = field(default=None, repr=False)

    @property
    def is_compressed(self) -> bool:
        return self.compressed_size < self.uncompressed_size


@dataclass
class PacArchive:
    """Represents a parsed or constructed NeXAS PAC archive."""
    variant: PacVariant
    encoding: str
    entries: List[PacEntry] = field(default_factory=list)
    header_size: int = 0
    total_data_size: int = 0
