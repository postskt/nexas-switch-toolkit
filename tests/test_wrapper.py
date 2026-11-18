# -*- coding: utf-8 -*-
"""
Unit Tests for Smart Word-Wrapping & Text Formatting
"""

import unittest

from nexas_toolkit.script.smart_wrapper import SmartWrapper


class TestSmartWrapper(unittest.TestCase):

    def setUp(self):
        # 20 halfwidth units = 10 fullwidth CJK chars per line
        self.wrapper = SmartWrapper(max_width=20, max_lines_per_box=3)

    def test_east_asian_width_calculation(self):
        """Test halfwidth vs fullwidth character width."""
        self.assertEqual(self.wrapper.get_char_width("A"), 1.0)
        self.assertEqual(self.wrapper.get_char_width("1"), 1.0)
        self.assertEqual(self.wrapper.get_char_width(" "), 1.0)
        self.assertEqual(self.wrapper.get_char_width("中"), 2.0)
        self.assertEqual(self.wrapper.get_char_width("あ"), 2.0)
        self.assertEqual(self.wrapper.get_char_width("！"), 2.0)
        self.assertEqual(self.wrapper.get_string_width("Hello世界"), 5 * 1.0 + 2 * 2.0)

    def test_cjk_line_wrapping(self):
        """Test wrapping long CJK sentences within max_width."""
        # 15 CJK chars = 30 units, should wrap into 2 lines
        text = "あいうえおかきくけこさしすせそ"
        wrapped = self.wrapper.wrap_text(text)
        lines = wrapped.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "あいうえおかきくけこ")
        self.assertEqual(lines[1], "さしすせそ")

    def test_kinsoku_shori_line_start(self):
        """Test that line-start prohibited punctuation (e.g. '。') is not placed at line head."""
        # 10 chars, then '。' at char 11.
        # "あいうえおかきくけこ。" -> total 22 units.
        # Without kinsoku: Line 1 = "あいうえおかきくけこ", Line 2 = "。" (illegal!)
        # With kinsoku: Line 1 = "あいうえおかきくけ", Line 2 = "こ。"
        text = "あいうえおかきくけこ。"
        wrapped = self.wrapper.wrap_text(text)
        lines = wrapped.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertFalse(lines[1].startswith("。"))
        self.assertEqual(lines[0], "あいうえおかきくけ")
        self.assertEqual(lines[1], "こ。")

    def test_english_word_boundary_protection(self):
        """Test that English words are not cut in half across lines."""
        wrapper = SmartWrapper(max_width=15)
        # "Hello Nintendo Switch" -> "Hello " (6) + "Nintendo" (8) = 14 fits in line 1.
        # "Switch" (6) wraps to line 2.
        text = "Hello Nintendo Switch"
        wrapped = wrapper.wrap_text(text)
        lines = wrapped.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "Hello Nintendo ")
        self.assertEqual(lines[1], "Switch")

    def test_preserve_hard_newlines(self):
        """Test that manual newlines already in the text are preserved."""
        text = "第一行文本\n第二行短文本\n第三行稍微长一点的测试文本"
        wrapped = self.wrapper.wrap_text(text)
        lines = wrapped.split("\n")
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual(lines[0], "第一行文本")
        self.assertEqual(lines[1], "第二行短文本")


if __name__ == "__main__":
    unittest.main()
