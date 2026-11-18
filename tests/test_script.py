# -*- coding: utf-8 -*-
"""
Unit and Integration Tests for NeXAS Script Module
Tests parsing, dumping, UTF-8 conversion, and pointer relocation.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from nexas_toolkit.script.script_converter import ScriptConverter
from nexas_toolkit.script.script_dumper import ScriptDumper
from nexas_toolkit.script.script_parser import (
    NexasScript,
    ScriptInstruction,
    ScriptParser,
    ScriptString,
)
from nexas_toolkit.utils.binary_helper import BinaryWriter, encode_string


class TestScriptModule(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_script_test_")
        self.sample_dialogues_sjis = [
            "こんにちは、NeXASの世界へようこそ！",
            "今日はNintendo Switchへの移植実験を行います。",
            "テキストオフセットの再計算とポインタ再配置をテスト中…",
        ]
        self.sample_bin_path = Path(self.test_dir) / "test_script.bin"
        self._generate_synthetic_pc_bin(self.sample_bin_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _generate_synthetic_pc_bin(self, out_path: Path):
        """Synthesize a valid PC NeXAS .bin script binary with string pointers."""
        writer = BinaryWriter()

        # 1. Header: VER01.00 + unk_table (2 entries)
        writer.write_bytes(b"VER01.00\x00")
        writer.write_u32(2)
        writer.write_u32(0x1000)
        writer.write_u32(0x2000)

        # 2. Init codes (1 instruction)
        writer.write_u32(1)
        writer.write_u32(0x0001)  # init opcode
        writer.write_u32(0x0000)  # operand

        # We will write 3 instructions in main code stream:
        # Instr 0: Display Text -> points to string 0
        # Instr 1: Display Text -> points to string 1
        # Instr 2: Display Text -> points to string 2
        # Calculate exactly where string pool will start:
        header_len = 9 + 4 + 2 * 4  # 21
        init_len = 4 + 1 * 8        # 12
        code_len = 4 + 3 * 8        # 28
        str_count_len = 4           # 4
        str_pool_start = header_len + init_len + code_len + str_count_len  # 65

        # Compute old string offsets (CP932, null-terminated, no length prefix)
        string_offsets = []
        cur_off = str_pool_start
        for s in self.sample_dialogues_sjis:
            string_offsets.append(cur_off)
            s_bytes = encode_string(s, "cp932")
            cur_off += len(s_bytes) + 1

        # 3. Write Main Codes with pointer operands
        writer.write_u32(3)
        for i in range(3):
            writer.write_u32(0x00000020)          # OP_TEXT_DISPLAY
            writer.write_u32(string_offsets[i])   # Pointer directly to string offset!

        # 4. Write Strings
        writer.write_u32(len(self.sample_dialogues_sjis))
        for s in self.sample_dialogues_sjis:
            s_bytes = encode_string(s, "cp932")
            writer.write_bytes(s_bytes)
            writer.write_u8(0)

        with open(out_path, "wb") as f:
            f.write(writer.get_bytes())

    def test_parse_pc_script(self):
        """Test parsing of PC .bin script."""
        with open(self.sample_bin_path, "rb") as f:
            data = f.read()

        parser = ScriptParser(encoding="cp932")
        script = parser.parse(data)

        self.assertEqual(script.header_type, "VER9")
        self.assertEqual(len(script.unk_table), 2)
        self.assertEqual(len(script.init_codes), 1)
        self.assertEqual(len(script.codes), 3)
        self.assertEqual(len(script.strings), 3)

        for i, s in enumerate(script.strings):
            self.assertEqual(s.text, self.sample_dialogues_sjis[i])

    def test_convert_to_switch_and_relocate_pointers(self):
        """
        Verify that conversion to Switch (.binu8):
        1. Encodes strings in UTF-8 with 4-byte length prefix.
        2. Successfully shifts and updates bytecode instruction operands to the new offsets!
        """
        with open(self.sample_bin_path, "rb") as f:
            data = f.read()

        parser = ScriptParser(encoding="cp932")
        script = parser.parse(data)

        converter = ScriptConverter()
        new_script, binu8_bytes = converter.convert_pc_to_switch(script, target_encoding="utf-8")

        self.assertTrue(new_script.is_switch)
        self.assertEqual(len(new_script.strings), 3)

        # Parse the generated .binu8 binary back to verify it conforms to standard
        u8_parser = ScriptParser(encoding="utf-8")
        parsed_u8 = u8_parser.parse(binu8_bytes)

        self.assertEqual(len(parsed_u8.strings), 3)
        for i, s in enumerate(parsed_u8.strings):
            self.assertEqual(s.text, self.sample_dialogues_sjis[i])
            self.assertTrue(s.has_length_prefix)

        # Check Opcode Relocation!
        # The main code operands in parsed_u8 MUST point to new string offsets
        for i in range(3):
            expected_new_offset = parsed_u8.strings[i].offset
            actual_operand = parsed_u8.codes[i].operand
            self.assertEqual(
                actual_operand,
                expected_new_offset,
                f"Opcode {i} operand was not properly relocated to new offset! Expected 0x{expected_new_offset:X}, got 0x{actual_operand:X}",
            )

    def test_dumper_json_roundtrip(self):
        """Test dumping to JSON, editing text, and recompiling with pointer update."""
        with open(self.sample_bin_path, "rb") as f:
            data = f.read()

        script = ScriptParser(encoding="cp932").parse(data)
        json_path = Path(self.test_dir) / "dump.json"
        ScriptDumper.dump_to_json(script, str(json_path))

        self.assertTrue(json_path.exists())

        # Load strings, modify them with Chinese localization
        strings = ScriptDumper.load_strings_from_json(str(json_path))
        strings[0] = "你好，欢迎来到NeXAS的世界！【中文汉化测试】"
        strings[1] = "今天进行Nintendo Switch跨平台移植与动态对齐验证。"
        strings[2] = "全新UTF-8长文本指针重构测试成功！"

        converter = ScriptConverter()
        rebuilt_script, rebuilt_bytes = converter.convert_pc_to_switch(
            script, target_encoding="utf-8", new_strings=strings
        )

        # Parse rebuilt binary
        rebuilt_parsed = ScriptParser(encoding="utf-8").parse(rebuilt_bytes)
        for i in range(3):
            self.assertEqual(rebuilt_parsed.strings[i].text, strings[i])
            # Check pointer alignment
            self.assertEqual(rebuilt_parsed.codes[i].operand, rebuilt_parsed.strings[i].offset)


if __name__ == "__main__":
    unittest.main()
