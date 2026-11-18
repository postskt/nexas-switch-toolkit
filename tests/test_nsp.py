# -*- coding: utf-8 -*-
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

from nexas_toolkit.patch.nsp_builder import (
    GOLDEN_NACP_TEMPLATE,
    GOLDEN_NPDM_TEMPLATE,
    NspBuilder,
    patch_nacp,
    patch_npdm_title_id,
    prepare_icon_jpeg,
)


class TestNspBuilder(unittest.TestCase):
    """Test suite for Nintendo Switch NSP packager."""

    def test_golden_templates_exist_and_valid(self):
        self.assertTrue(GOLDEN_NPDM_TEMPLATE.is_file(), "golden.npdm must exist")
        self.assertEqual(GOLDEN_NPDM_TEMPLATE.stat().st_size, 972, "golden.npdm must be 972 bytes")

        self.assertTrue(GOLDEN_NACP_TEMPLATE.is_file(), "golden_control.nacp must exist")
        self.assertEqual(GOLDEN_NACP_TEMPLATE.stat().st_size, 16384, "golden_control.nacp must be 16384 bytes")

    def test_patch_npdm_title_id(self):
        raw = bytearray(GOLDEN_NPDM_TEMPLATE.read_bytes())
        target_tid = 0x0100D11018A7E000
        patched = patch_npdm_title_id(raw, target_tid, name="Aquarium")

        self.assertEqual(len(patched), 972)
        self.assertEqual(patched[:4], b"META")

        aci0_off = patched.find(b"ACI0")
        acid_off = patched.find(b"ACID")
        self.assertGreater(aci0_off, 0)
        self.assertGreater(acid_off, 0)

        # Check TitleID patched at ACI0 + 0x10
        aci0_tid = struct.unpack_from("<Q", patched, aci0_off + 0x10)[0]
        self.assertEqual(aci0_tid, target_tid)

        # Check ProgramIdMin/Max is golden broad range (0x0100000000000000 .. 0x01FFFFFFFFFFFFFF)
        acid_min = struct.unpack_from("<Q", patched, acid_off + 0x10)[0]
        acid_max = struct.unpack_from("<Q", patched, acid_off + 0x18)[0]
        self.assertEqual(acid_min, 0x0100000000000000)
        self.assertEqual(acid_max, 0x01FFFFFFFFFFFFFF)

        # Check name patched at 0x20
        name_bytes = patched[0x20:0x30].rstrip(b"\x00")
        self.assertEqual(name_bytes, b"Aquarium")

        # Crucial: Check flags0 (0x0C) is still 0x07 (64-bit + AddressSpace 3)
        flags0 = patched[0x0C]
        self.assertEqual(flags0, 0x07)

    def test_patch_nacp(self):
        raw = bytearray(GOLDEN_NACP_TEMPLATE.read_bytes())
        target_tid = 0x0100D11018A7E000
        patched = patch_nacp(
            raw,
            title_id=target_tid,
            title_name="あくありうむ。",
            author="ENTERGRAM",
            version="1.0.0",
        )

        self.assertEqual(len(patched), 16384)

        # Title ID check in NACP (0x3038, 0x3078, 0x30B0)
        self.assertEqual(struct.unpack_from("<Q", patched, 0x3038)[0], target_tid)
        self.assertEqual(struct.unpack_from("<Q", patched, 0x3078)[0], target_tid)
        self.assertEqual(struct.unpack_from("<Q", patched, 0x30B0)[0], target_tid)

        # Slot 0 title check
        slot0_title = patched[:0x200].rstrip(b"\x00").decode("utf-8")
        self.assertEqual(slot0_title, "あくありうむ。")

        # Slot 0 author check
        slot0_author = patched[0x200:0x300].rstrip(b"\x00").decode("utf-8")
        self.assertEqual(slot0_author, "ENTERGRAM")

        # startup_user_account (0x3025) must be 1
        self.assertEqual(patched[0x3025], 1)

        # User save data size (0x3080) = 0x3E00000 (62MB)
        user_save = struct.unpack_from("<Q", patched, 0x3080)[0]
        self.assertEqual(user_save, 0x3E00000)

        # Journal size (0x3088) = 0x180000 (1.5MB)
        journal = struct.unpack_from("<Q", patched, 0x3088)[0]
        self.assertEqual(journal, 0x180000)

        # Version string without leading space
        ver_bytes = patched[0x3060:0x3070].rstrip(b"\x00")
        self.assertEqual(ver_bytes, b"1.0.0")

    def test_prepare_icon_jpeg(self):
        with tempfile.TemporaryDirectory() as td:
            out_icon = Path(td) / "icon.jpg"
            prepare_icon_jpeg(None, out_icon)
            self.assertTrue(out_icon.is_file())
            self.assertGreater(out_icon.stat().st_size, 1000)

    def test_real_nsp_packaging_if_tools_available(self):
        hacbrew_path = Path(r"F:\移植\output\nsp_build\hacbrewpack.exe")
        keys_path = Path(r"F:\移植\prod.keys")
        main_nso = Path(r"F:\移植\output\nsp_build\exefs\main")

        if not (hacbrew_path.is_file() and keys_path.is_file() and main_nso.is_file()):
            self.skipTest("hacbrewpack.exe, prod.keys or main NSO not available on host")

        with tempfile.TemporaryDirectory() as td:
            romfs_dir = Path(td) / "romfs"
            romfs_dir.mkdir()
            (romfs_dir / "test.txt").write_text("NeXAS NSP Test payload", encoding="utf-8")

            out_dist = Path(td) / "dist"
            builder = NspBuilder(
                hacbrewpack_path=str(hacbrew_path),
                prod_keys_path=str(keys_path),
                staging_root=str(Path(r"C:\_test_nsp_stage")),
            )

            res = builder.build_nsp(
                romfs_dir=str(romfs_dir),
                main_nso_path=str(main_nso),
                title_id="0100D11018A7E000",
                title_name="TestAquarium",
                author="Entergram",
                version="1.0.0",
                output_dir=str(out_dist),
            )

            self.assertTrue(Path(res.nsp_path).is_file())
            self.assertTrue(res.nsp_path.endswith(".nsp"))
            self.assertGreater(res.size_bytes, 10000)
            self.assertEqual(res.title_id, "0100D11018A7E000")
            # Must pass hard structural verification!
            self.assertTrue(res.verified, f"pack_check must pass! Log:\n{res.pack_check_output}")


if __name__ == "__main__":
    unittest.main()
