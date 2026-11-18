# -*- coding: utf-8 -*-
"""
Unit Tests for Emulator Synchronization Engine (Ryujinx / Yuzu / Suyu)
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from nexas_toolkit.patch.emulator_sync import EmulatorSync, EmulatorTarget


class TestEmulatorSync(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_emu_test_")

        # Mock Ryujinx directory structure
        self.mock_ryu_dir = Path(self.test_dir) / "Ryujinx"
        self.mock_ryu_mods = self.mock_ryu_dir / "mods" / "contents"
        self.mock_ryu_mods.mkdir(parents=True)

        # Mock Yuzu directory structure
        self.mock_yuzu_dir = Path(self.test_dir) / "yuzu"
        self.mock_yuzu_load = self.mock_yuzu_dir / "load"
        self.mock_yuzu_load.mkdir(parents=True)

        # Mock LayeredFS Patch
        self.mock_patch_dir = Path(self.test_dir) / "my_patch"
        self.mock_romfs = self.mock_patch_dir / "romfs"
        self.mock_romfs.mkdir(parents=True)
        (self.mock_romfs / "data.pac").write_bytes(b"MOCK_PAC")
        (self.mock_romfs / "Script").mkdir()
        (self.mock_romfs / "Script" / "main.binu8").write_text("dummy_script", encoding="utf-8")

        self.custom_paths = {
            "ryujinx": str(self.mock_ryu_dir),
            "yuzu": str(self.mock_yuzu_dir),
        }
        self.sync = EmulatorSync(custom_emulator_paths=self.custom_paths)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_detect_mock_emulators(self):
        """Test scanning and detecting emulator roots."""
        detected = self.sync.detect_emulators()
        names = [e.name for e in detected]
        self.assertIn("Ryujinx", names)
        self.assertIn("Yuzu", names)

    def test_sync_to_ryujinx(self):
        """Test synchronizing patch to Ryujinx mods/contents layout."""
        results = self.sync.sync_patch(
            patch_source_dir=str(self.mock_patch_dir),
            title_id="aquarium",  # Resolves to 0100D11018A7E000
            emulator_filter="ryujinx",
            mod_name="TestMod_Aqua",
            dry_run=False,
        )

        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res.status, "success")
        self.assertEqual(res.emulator_name, "Ryujinx")
        self.assertGreater(res.copied_files_count, 0)

        # Expected destination: <Ryujinx>/mods/contents/0100D11018A7E000/TestMod_Aqua/romfs/
        expected_dest = (
            self.mock_ryu_mods / "0100D11018A7E000" / "TestMod_Aqua" / "romfs"
        )
        self.assertTrue(expected_dest.is_dir())
        self.assertTrue((expected_dest / "data.pac").exists())
        self.assertTrue((expected_dest / "Script" / "main.binu8").exists())

    def test_sync_to_yuzu(self):
        """Test synchronizing patch to Yuzu load layout."""
        results = self.sync.sync_patch(
            patch_source_dir=str(self.mock_patch_dir),
            title_id="0100412014A86000",
            emulator_filter="yuzu",
            mod_name="TestMod_DC4",
            dry_run=False,
        )

        self.assertEqual(len(results), 1)
        expected_dest = (
            self.mock_yuzu_load / "0100412014A86000" / "TestMod_DC4" / "romfs"
        )
        self.assertTrue(expected_dest.is_dir())
        self.assertTrue((expected_dest / "data.pac").exists())

    def test_dry_run_mode(self):
        """Test simulation dry-run mode (no files copied)."""
        results = self.sync.sync_patch(
            patch_source_dir=str(self.mock_patch_dir),
            title_id="aquarium",
            emulator_filter="ryujinx",
            mod_name="DryRunMod",
            dry_run=True,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "dry_run")
        dest_path = Path(results[0].target_mod_dir)
        self.assertFalse(dest_path.exists())


if __name__ == "__main__":
    unittest.main()
