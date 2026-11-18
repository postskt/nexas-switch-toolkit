# -*- coding: utf-8 -*-
"""
End-to-End CLI Tests for nexas-switch-toolkit
Tests all CLI subcommands:
- pac-extract, pac-build
- script-dump, script-convert, script-compile
- script-wrap, scene-convert, romfs-pack (Phase 2)
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from nexas_toolkit.scene.grp_map_converter import GrpEntry, GrpFile, GrpMapConverter
from nexas_toolkit.utils.binary_helper import BinaryWriter, encode_string
from nexas_toolkit.utils.compression import LZSS


class TestCliEndToEnd(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_cli_test_")
        self.cli_entry = [sys.executable, "-m", "nexas_toolkit.cli"]
        self.cwd = Path(__file__).resolve().parent.parent

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_lzss_roundtrip(self):
        """Test pure Python LZSS compression & decompression engine."""
        original = (
            b"The quick brown fox jumps over the lazy dog. "
            b"NeXAS Engine LZSS compression test. "
            b"Repeat: The quick brown fox jumps over the lazy dog." * 5
        )
        compressed = LZSS.compress(original)
        self.assertLess(len(compressed), len(original))
        decompressed = LZSS.decompress(compressed, uncompressed_size=len(original))
        self.assertEqual(decompressed, original)

    def test_cli_pac_workflow(self):
        """Test `pac-build` and `pac-extract` via CLI commands."""
        pack_dir = Path(self.test_dir) / "assets"
        pack_dir.mkdir()
        (pack_dir / "file1.txt").write_text("NeXAS Switch Port Test File 1", encoding="utf-8")
        (pack_dir / "subfolder").mkdir()
        (pack_dir / "subfolder" / "scene.dat").write_bytes(b"\xAA\xBB\xCC\xDD" * 200)

        out_pac = Path(self.test_dir) / "output.pac"

        # 1. Test pac-build
        cmd_build = self.cli_entry + [
            "pac-build",
            str(pack_dir),
            "-o",
            str(out_pac),
            "-p",
            "switch",
            "-c",
            "zstd",
        ]
        res = subprocess.run(cmd_build, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"pac-build failed: {res.stderr}")
        self.assertTrue(out_pac.exists())

        # 2. Test pac-extract
        extract_dir = Path(self.test_dir) / "extracted"
        cmd_extract = self.cli_entry + [
            "pac-extract",
            str(out_pac),
            "-o",
            str(extract_dir),
        ]
        res = subprocess.run(cmd_extract, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"pac-extract failed: {res.stderr}")
        self.assertTrue((extract_dir / "file1.txt").exists())
        self.assertTrue((extract_dir / "subfolder" / "scene.dat").exists())
        self.assertTrue((extract_dir / "pac_manifest.json").exists())

    def test_cli_script_workflow(self):
        """Test `script-dump`, `script-convert`, and `script-compile` via CLI."""
        bin_path = Path(self.test_dir) / "test.bin"
        writer = BinaryWriter()
        writer.write_bytes(b"VER01.00\x00")
        writer.write_u32(1)
        writer.write_u32(0x9999)
        writer.write_u32(1)  # 1 init code
        writer.write_u32(0x01)
        writer.write_u32(0x00)
        writer.write_u32(1)  # 1 main code
        str_start_off = (9 + 4 + 4) + (4 + 8) + (4 + 8) + 4
        writer.write_u32(0x20)
        writer.write_u32(str_start_off)
        writer.write_u32(1)  # 1 string
        writer.write_bytes(encode_string("アクアリウム", "cp932") + b"\x00")
        bin_path.write_bytes(writer.get_bytes())

        # 2. Test script-dump
        json_out = Path(self.test_dir) / "dump.json"
        cmd_dump = self.cli_entry + ["script-dump", str(bin_path), "-o", str(json_out), "-f", "json"]
        res = subprocess.run(cmd_dump, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"script-dump failed: {res.stderr}")
        self.assertTrue(json_out.exists())

        # 3. Test script-convert directly
        binu8_out = Path(self.test_dir) / "converted.binu8"
        cmd_conv = self.cli_entry + ["script-convert", str(bin_path), "-o", str(binu8_out), "-t", "switch"]
        res = subprocess.run(cmd_conv, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"script-convert failed: {res.stderr}")
        self.assertTrue(binu8_out.exists())

        # 4. Test script-compile with edited text
        txt_out = Path(self.test_dir) / "dump.txt"
        cmd_dump_txt = self.cli_entry + ["script-dump", str(bin_path), "-o", str(txt_out), "-f", "txt"]
        res = subprocess.run(cmd_dump_txt, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        txt_out.write_text(
            "○00000000○\nアクアリウム\n○00000000○\n水族馆Aquarium【Switch移植汉化】\n\n",
            encoding="utf-8",
        )

        compiled_binu8 = Path(self.test_dir) / "compiled.binu8"
        cmd_comp = self.cli_entry + [
            "script-compile",
            "-s",
            str(bin_path),
            "-t",
            str(txt_out),
            "-o",
            str(compiled_binu8),
            "-p",
            "switch",
        ]
        res = subprocess.run(cmd_comp, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"script-compile failed: {res.stderr}")
        self.assertTrue(compiled_binu8.exists())

    def test_cli_script_wrap(self):
        """Test `script-wrap` CLI command on text file."""
        txt_path = Path(self.test_dir) / "long_text.txt"
        txt_path.write_text("这是一段非常长的中文剧情对白文本，用于测试在Switch小屏幕上的智能换行与避免爆框效果。", encoding="utf-8")

        out_wrapped = Path(self.test_dir) / "wrapped.txt"
        cmd = self.cli_entry + ["script-wrap", str(txt_path), "-o", str(out_wrapped), "-w", "20"]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"script-wrap failed: {res.stderr}")
        self.assertTrue(out_wrapped.exists())
        wrapped_lines = out_wrapped.read_text(encoding="utf-8").split("\n")
        self.assertGreater(len(wrapped_lines), 1)

    def test_cli_scene_convert(self):
        """Test `scene-convert` CLI command on .grp file."""
        grp_path = Path(self.test_dir) / "test.grp"
        entries = [GrpEntry(id=1, name="bg01", x=50, y=50, width=400, height=300)]
        original_grp = GrpFile(canvas_width=800, canvas_height=600, entries=entries)
        grp_path.write_bytes(GrpMapConverter.serialize_grp(original_grp))

        out_grp = Path(self.test_dir) / "scaled.grp"
        cmd = self.cli_entry + [
            "scene-convert",
            str(grp_path),
            "-o",
            str(out_grp),
            "-s",
            "800x600",
            "-d",
            "1280x720",
            "-m",
            "letterbox",
            "--dump-json",
        ]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"scene-convert failed: {res.stderr}")
        self.assertTrue(out_grp.exists())
        self.assertTrue(out_grp.with_suffix(".json").exists())

    def test_cli_romfs_pack(self):
        """Test `romfs-pack` CLI command generating Atmosphere LayeredFS tree."""
        mock_pac = Path(self.test_dir) / "game.pac"
        mock_pac.write_bytes(b"PAC_DUMMY")

        scripts_dir = Path(self.test_dir) / "scripts_in"
        scripts_dir.mkdir()
        (scripts_dir / "main.binu8").write_text("binu8_data", encoding="utf-8")

        patch_output = Path(self.test_dir) / "patch_dest"
        cmd = self.cli_entry + [
            "romfs-pack",
            "-t",
            "aquarium",
            "-o",
            str(patch_output),
            "--pac",
            str(mock_pac),
            "--scripts",
            str(scripts_dir),
            "--mod-name",
            "Aquarium_CLI_Mod",
        ]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"romfs-pack failed: {res.stderr}")

        romfs_target = patch_output / "atmosphere" / "contents" / "0100D11018A7E000" / "romfs"
        self.assertTrue((romfs_target / "game.pac").exists())
        self.assertTrue((romfs_target / "Script" / "main.binu8").exists())
        self.assertTrue((patch_output / "0100D11018A7E000_Atmosphere_LayeredFS.zip").exists())

    def test_cli_media_convert(self):
        """Test `media-convert` CLI command for image alignment and audio resample."""
        from PIL import Image

        # Create a mock image
        img_dir = Path(self.test_dir) / "media_in"
        img_dir.mkdir()
        img_path = img_dir / "test.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(img_path)

        out_media = Path(self.test_dir) / "media_out"
        cmd = self.cli_entry + [
            "media-convert",
            str(img_dir),
            "-o",
            str(out_media),
            "--type",
            "image",
            "--alignment",
            "16",
        ]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"media-convert failed: {res.stderr}")
        self.assertTrue((out_media / "test.png").exists())
        with Image.open(out_media / "test.png") as converted_img:
            self.assertEqual(converted_img.size, (112, 112))

    def test_cli_emu_sync(self):
        """Test `emu-sync` CLI command with dry-run."""
        patch_dir = Path(self.test_dir) / "emu_patch"
        (patch_dir / "romfs").mkdir(parents=True)
        (patch_dir / "romfs" / "data.pac").write_bytes(b"TEST_PAC")

        cmd = self.cli_entry + [
            "emu-sync",
            "-t",
            "aquarium",
            "-p",
            str(patch_dir),
            "--dry-run",
        ]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"emu-sync failed: {res.stderr}")
        self.assertIn("[DRY-RUN]", res.stdout)


if __name__ == "__main__":
    unittest.main()
