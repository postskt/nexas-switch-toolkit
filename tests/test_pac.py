# -*- coding: utf-8 -*-
"""
Unit and Integration Tests for NeXAS PAC Module
"""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from nexas_toolkit.pac.pac_builder import PacBuilder
from nexas_toolkit.pac.pac_common import PAC_MAGIC, PacVariant
from nexas_toolkit.pac.pac_extractor import PacExtractor


class TestPacModule(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_pac_test_")
        self.src_dir = Path(self.test_dir) / "source_files"
        self.src_dir.mkdir(parents=True)

        # Create mock asset files
        self.files = {
            "title.txt": "Welcome to NeXAS Cross-Platform Porter!".encode("utf-8"),
            "data/scene01.bin": b"\x00\x01\x02\x03\x04\x05" * 100,
            "images/bg01.dat": (b"IMAGE_HEADER_RGBA8888" + b"\xFF\x00\x00\xFF" * 250),
            "script/dialogue.bin": "こんにちは世界！NeXAS Switch移植テスト".encode("utf-8"),
        }

        for rel_path, content in self.files.items():
            fp = self.src_dir / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            with open(fp, "wb") as f:
                f.write(content)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_switch_zstd_pac_build_and_extract(self):
        """Test building a Switch PAC (UTF-8 filenames, Zstd compression) and extracting it."""
        out_pac = Path(self.test_dir) / "switch_archive.pac"
        builder = PacBuilder(target_platform="switch", compression="zstd", encoding="utf-8")
        archive = builder.build_from_directory(str(self.src_dir), str(out_pac))

        self.assertTrue(out_pac.exists())
        self.assertGreater(out_pac.stat().st_size, 0)
        self.assertEqual(len(archive.entries), len(self.files))

        # Check PAC Magic
        with open(out_pac, "rb") as f:
            magic = f.read(4)
            self.assertEqual(magic, PAC_MAGIC)

        # Extract and verify integrity
        extract_dir = Path(self.test_dir) / "extracted_switch"
        extractor = PacExtractor(str(out_pac))
        ext_archive = extractor.extract_all(str(extract_dir))

        self.assertEqual(len(ext_archive.entries), len(self.files))

        for rel_path, expected_content in self.files.items():
            ext_file = extract_dir / rel_path
            self.assertTrue(ext_file.exists(), f"File missing: {rel_path}")
            with open(ext_file, "rb") as f:
                actual_content = f.read()
            self.assertEqual(
                hashlib.sha256(actual_content).hexdigest(),
                hashlib.sha256(expected_content).hexdigest(),
                f"Content hash mismatch for {rel_path}",
            )

    def test_pc_zlib_pac_build_and_extract(self):
        """Test building a PC PAC (CP932 filenames, Zlib compression) and extracting it."""
        out_pac = Path(self.test_dir) / "pc_archive.pac"
        builder = PacBuilder(target_platform="pc", compression="zlib", encoding="cp932")
        builder.build_from_directory(str(self.src_dir), str(out_pac))

        self.assertTrue(out_pac.exists())

        extract_dir = Path(self.test_dir) / "extracted_pc"
        extractor = PacExtractor(str(out_pac))
        extractor.extract_all(str(extract_dir))

        for rel_path, expected_content in self.files.items():
            ext_file = extract_dir / rel_path
            self.assertTrue(ext_file.exists())
            with open(ext_file, "rb") as f:
                self.assertEqual(f.read(), expected_content)


if __name__ == "__main__":
    unittest.main()
