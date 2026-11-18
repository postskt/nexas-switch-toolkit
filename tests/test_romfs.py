# -*- coding: utf-8 -*-
"""
Unit Tests for Atmosphère RomFS LayeredFS Patch Builder
"""

from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from nexas_toolkit.patch.romfs_builder import RomFsBuilder


class TestRomFsBuilder(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_romfs_test_")
        self.mock_pac = Path(self.test_dir) / "data.pac"
        self.mock_pac.write_bytes(b"PAC_TEST_ARCHIVE")

        self.mock_scripts_dir = Path(self.test_dir) / "test_scripts"
        self.mock_scripts_dir.mkdir()
        (self.mock_scripts_dir / "aqu_01_01.binu8").write_text("dummy_script_u8", encoding="utf-8")
        (self.mock_scripts_dir / "aqu_01_02.binu8").write_text("dummy_script_u8_2", encoding="utf-8")

        self.mock_scene_dir = Path(self.test_dir) / "test_scenes"
        self.mock_scene_dir.mkdir()
        (self.mock_scene_dir / "main.grp").write_bytes(b"GRP_TEST")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_title_id_validation_and_preset(self):
        """Test Title ID preset resolution and hex validation."""
        # Preset aquarium
        builder_aqua = RomFsBuilder("aquarium")
        self.assertEqual(builder_aqua.title_id, "0100D11018A7E000")

        # Preset dc4fd
        builder_dc4 = RomFsBuilder("dc4fd")
        self.assertEqual(builder_dc4.title_id, "0100412014A86000")

        # Valid 16-hex custom Title ID
        builder_custom = RomFsBuilder("0100123456789ABC")
        self.assertEqual(builder_custom.title_id, "0100123456789ABC")

        # Invalid Title ID
        with self.assertRaises(ValueError):
            RomFsBuilder("INVALID_ID_TOO_SHORT")
        with self.assertRaises(ValueError):
            RomFsBuilder("0100123456789XYZ")  # Contains non-hex character 'Z'

    def test_build_patch_tree(self):
        """Test generating Atmosphere directory tree structure and ZIP archive."""
        out_patch_dir = Path(self.test_dir) / "patch_output"
        builder = RomFsBuilder("aquarium", mod_name="Aquarium_CN_v1.0")

        result = builder.build_patch_tree(
            output_dir=str(out_patch_dir),
            pac_files=[str(self.mock_pac)],
            scripts_dir=str(self.mock_scripts_dir),
            scenes_dir=str(self.mock_scene_dir),
            create_zip=True,
        )

        expected_romfs = (
            out_patch_dir / "atmosphere" / "contents" / "0100D11018A7E000" / "romfs"
        )
        self.assertTrue(expected_romfs.is_dir())
        self.assertTrue((expected_romfs / "data.pac").exists())
        self.assertTrue((expected_romfs / "Script" / "aqu_01_01.binu8").exists())
        self.assertTrue((expected_romfs / "Scene" / "main.grp").exists())

        # Check README and manifest
        self.assertTrue(result["readme"].exists())
        self.assertTrue(result["manifest"].exists())

        # Check ZIP
        zip_path = result["zip"]
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("atmosphere/contents/0100D11018A7E000/romfs/data.pac", namelist)
            self.assertIn("atmosphere/contents/0100D11018A7E000/romfs/Script/aqu_01_01.binu8", namelist)
            self.assertIn("README_INSTALL.txt", namelist)


if __name__ == "__main__":
    unittest.main()
