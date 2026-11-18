# -*- coding: utf-8 -*-
"""
NeXAS Script Text Dumper & Importer
Supports exporting script dialogues to JSON or legacy text format (Aquarium_tools style),
and re-importing translations into the binary rebuild pipeline.
"""

import json
from pathlib import Path
import re
from typing import List, Optional

from .script_converter import ScriptConverter
from .script_parser import NexasScript, ScriptParser


class ScriptDumper:
    """
    Handles extraction of text from NeXAS scripts and merging translated text back.
    """

    @classmethod
    def dump_to_json(cls, script: NexasScript, output_path: str) -> None:
        """Dump extracted script strings to a clean JSON file."""
        entries = []
        for s in script.strings:
            entries.append(
                {
                    "index": s.index,
                    "offset": f"0x{s.offset:X}",
                    "text_offset": f"0x{s.text_offset:X}",
                    "original": s.text,
                    "translation": s.text,
                }
            )

        data = {
            "format": "switch" if script.is_switch else "pc",
            "header_type": script.header_type,
            "encoding": script.encoding,
            "count": len(entries),
            "strings": entries,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def dump_to_txt(cls, script: NexasScript, output_path: str) -> None:
        """
        Dump strings using the standard double-line marker format:
        ○%08d○
        original
        ○%08d○
        translation
        """
        with open(output_path, "w", encoding="utf-8") as f:
            for s in script.strings:
                # Escape newlines
                sanitized = s.text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
                block = f"○{s.index:08d}○\n{sanitized}\n○{s.index:08d}○\n{sanitized}\n\n"
                f.write(block)

    @classmethod
    def load_strings_from_json(cls, json_path: str) -> List[str]:
        """Load translated string list from JSON."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        strings = []
        for item in data.get("strings", []):
            # Prefer translation if non-empty, otherwise original
            trans = item.get("translation", "")
            if not trans:
                trans = item.get("original", "")
            strings.append(trans)
        return strings

    @classmethod
    def load_strings_from_txt(cls, txt_path: str) -> List[str]:
        """
        Load translated strings from double-line formatted text.
        Format:
        ○00000000○
        original
        ○00000000○
        translation
        """
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()

        pattern = re.compile(r"○(\d+)○\n(.*?)\n○\1○\n(.*?)(?=\n\n|\n○|\Z)", re.DOTALL)
        matches = pattern.findall(content)

        if not matches:
            raise ValueError(f"No valid text blocks found in {txt_path}")

        strings = []
        for match in matches:
            idx, original, translation = match
            # Unescape newlines
            trans = translation.replace("\\n", "\n").replace("\\r", "\r")
            strings.append(trans)
        return strings
