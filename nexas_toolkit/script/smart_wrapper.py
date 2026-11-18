# -*- coding: utf-8 -*-
"""
NeXAS Smart Word-Wrapping & Text Formatter Engine
Prevents text box overflow on Nintendo Switch platforms.
Features:
- Accurate East Asian Width calculation (CJK fullwidth = 2, ASCII halfwidth = 1)
- Kinsoku Shori (Japanese/Chinese punctuation line-head/tail prohibition rules)
- English word-boundary protection (prevents hyphenless awkward word splitting)
- Dialogue and script-wide batch formatting
"""

import re
from typing import List, Optional, Set, Tuple
import unicodedata

from .script_parser import NexasScript, ScriptString


class SmartWrapper:
    """
    Intelligent text wrapper tailored for visual novel text boxes on Nintendo Switch.
    """

    # Characters forbidden at the beginning of a line (避头标点)
    LINE_START_PROHIBITED: Set[str] = {
        # CJK Punctuation & Brackets
        "、", "。", "，", "！", "？", "；", "：", "”", "’",
        "』", "」", "〉", "》", "）", "〕", "｝", "】",
        "・", "…", "—", "～", "ー", "ヽ", "ヾ", "ゝ", "ゞ", "々",
        # Halfwidth ASCII Punctuation & Brackets
        ",", ".", "!", "?", ";", ":", "\"", "'",
        ")", "]", "}", ">", "%",
    }

    # Characters forbidden at the end of a line (避尾标点)
    LINE_END_PROHIBITED: Set[str] = {
        # CJK Opening Brackets & Quotes
        "『", "「", "〈", "《", "（", "〔", "｛", "【", "“", "‘",
        # Halfwidth Opening Brackets & Quotes
        "(", "[", "{", "<", "$", "#", "@",
    }

    def __init__(
        self,
        max_width: int = 52,
        max_lines_per_box: int = 3,
        newline: str = "\n",
        full_width_val: float = 2.0,
        half_width_val: float = 1.0,
    ):
        """
        :param max_width: Maximum halfwidth character units per line (default: 52, roughly 26 CJK characters)
        :param max_lines_per_box: Maximum dialogue lines per text box before warning/paging
        :param newline: Newline character to insert (default: '\n')
        """
        self.max_width = max_width
        self.max_lines_per_box = max_lines_per_box
        self.newline = newline
        self.full_width_val = full_width_val
        self.half_width_val = half_width_val

    def get_char_width(self, ch: str) -> float:
        """
        Compute visual width of a character.
        East Asian Width: 'W', 'F' = 2, 'A' = 2 (in East Asian context), else 1.
        """
        if ch in ("\r", "\n", "\x00"):
            return 0.0

        ea = unicodedata.east_asian_width(ch)
        if ea in ("W", "F", "A"):
            return self.full_width_val
        return self.half_width_val

    def get_string_width(self, text: str) -> float:
        """Compute the total halfwidth unit width of a string."""
        return sum(self.get_char_width(c) for c in text)

    def wrap_paragraph(self, paragraph: str) -> List[str]:
        """
        Wrap a single paragraph (without existing hard newlines) into lines
        honoring width constraints, word boundaries, and Kinsoku rules.
        """
        if not paragraph:
            return [""]

        lines: List[str] = []
        cur_line = ""
        cur_width = 0.0
        i = 0
        n = len(paragraph)

        while i < n:
            ch = paragraph[i]

            # 1. Check if current character is part of an English word
            if ch.isascii() and ch.isalnum():
                # Extract full word
                word_end = i
                while word_end < n and paragraph[word_end].isascii() and paragraph[word_end].isalnum():
                    word_end += 1
                word = paragraph[i:word_end]
                word_width = self.get_string_width(word)

                # If word fits in current line
                if cur_width + word_width <= self.max_width:
                    cur_line += word
                    cur_width += word_width
                    i = word_end
                    continue

                # Word doesn't fit in current line
                if cur_line:
                    # Flush current line and start new line with this word
                    lines.append(cur_line)
                    cur_line = ""
                    cur_width = 0.0

                # If word itself exceeds max_width (huge word), break it character by character
                if word_width > self.max_width:
                    for c in word:
                        cw = self.get_char_width(c)
                        if cur_width + cw > self.max_width and cur_line:
                            lines.append(cur_line)
                            cur_line = ""
                            cur_width = 0.0
                        cur_line += c
                        cur_width += cw
                else:
                    cur_line = word
                    cur_width = word_width

                i = word_end
                continue

            # 2. General CJK or punctuation character handling
            cw = self.get_char_width(ch)

            # Check if adding this character exceeds line width
            if cur_width + cw > self.max_width and cur_line:
                # Need to wrap. Before wrapping, check Kinsoku rules:
                # Rule A: Line-start prohibited character (e.g. '。' or '！')
                # If 'ch' is prohibited at the start of a new line, it should stay with cur_line if possible,
                # or pull the previous character down with it.
                if ch in self.LINE_START_PROHIBITED:
                    if len(cur_line) > 1:
                        # Pull previous char down to new line
                        pulled_char = cur_line[-1]
                        cur_line = cur_line[:-1]
                        lines.append(cur_line)
                        cur_line = pulled_char + ch
                        cur_width = self.get_string_width(cur_line)
                        i += 1
                        continue
                    else:
                        # Only 1 char in cur_line, append anyway to prevent infinite loop
                        cur_line += ch
                        lines.append(cur_line)
                        cur_line = ""
                        cur_width = 0.0
                        i += 1
                        continue

                # Rule B: Line-end prohibited character (e.g. '（' or '「')
                # If cur_line ends with a prohibited character, pull it down to the next line.
                if cur_line and cur_line[-1] in self.LINE_END_PROHIBITED:
                    pulled_char = cur_line[-1]
                    cur_line = cur_line[:-1]
                    lines.append(cur_line)
                    cur_line = pulled_char + ch
                    cur_width = self.get_string_width(cur_line)
                    i += 1
                    continue

                # Normal wrap
                lines.append(cur_line)
                cur_line = ch
                cur_width = cw
                i += 1
                continue

            # Fits in current line
            cur_line += ch
            cur_width += cw
            i += 1

        if cur_line:
            lines.append(cur_line)

        return lines

    def wrap_text(self, text: str) -> str:
        """
        Wrap dialogue or narration text. Preserves existing hard linebreaks,
        wrapping each paragraph independently.
        """
        if not text:
            return ""

        # Normalize line endings
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_paragraphs = normalized.split("\n")

        all_wrapped_lines: List[str] = []
        for para in raw_paragraphs:
            if not para.strip():
                all_wrapped_lines.append("")
                continue
            wrapped_para_lines = self.wrap_paragraph(para)
            all_wrapped_lines.extend(wrapped_para_lines)

        return self.newline.join(all_wrapped_lines)

    def wrap_script(self, script: NexasScript) -> NexasScript:
        """
        Batch wrap all strings within a NexasScript object.
        Returns a cloned NexasScript with wrapped text strings.
        """
        new_strings: List[ScriptString] = []
        for s in script.strings:
            wrapped_text = self.wrap_text(s.text)
            new_s = ScriptString(
                index=s.index,
                offset=s.offset,
                text_offset=s.text_offset,
                length=len(wrapped_text.encode("utf-8")),
                text=wrapped_text,
                raw_bytes=wrapped_text.encode("utf-8"),
                has_length_prefix=s.has_length_prefix,
            )
            new_strings.append(new_s)

        from dataclasses import replace
        return replace(script, strings=new_strings)
