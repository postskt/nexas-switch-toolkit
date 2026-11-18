# -*- coding: utf-8 -*-
"""
Nintendo Switch Emulator Mod Sync & Deployment Engine
Automates synchronization of LayeredFS patches to local Ryujinx and Yuzu/Suyu emulator mod paths.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
from typing import Dict, List, Optional, Tuple

from .romfs_builder import RomFsBuilder


@dataclass
class EmulatorTarget:
    name: str             # e.g. 'Ryujinx', 'Yuzu', 'Suyu'
    root_dir: Path        # e.g. %APPDATA%/Ryujinx
    mods_base_dir: Path   # Base directory where mods/contents or load is stored
    structure_type: str   # 'ryujinx' or 'yuzu'


@dataclass
class SyncResult:
    emulator_name: str
    target_mod_dir: str
    copied_files_count: int
    cleaned_previous: bool
    status: str = "success"
    error_message: str = ""


class EmulatorSync:
    """
    Detects local Nintendo Switch emulators and synchronizes LayeredFS RomFS patches.
    """

    def __init__(self, custom_emulator_paths: Optional[Dict[str, str]] = None):
        self.custom_paths = custom_emulator_paths or {}

    @classmethod
    def get_appdata_dir(cls) -> Path:
        """Resolve OS application data directory."""
        system = platform.system()
        if system == "Windows":
            appdata = os.environ.get("APPDATA")
            if appdata:
                return Path(appdata)
            return Path.home() / "AppData" / "Roaming"
        elif system == "Darwin":  # macOS
            return Path.home() / "Library" / "Application Support"
        else:  # Linux
            xdg = os.environ.get("XDG_CONFIG_HOME")
            if xdg:
                return Path(xdg)
            return Path.home() / ".config"

    def detect_emulators(self) -> List[EmulatorTarget]:
        """
        Scan host machine for standard Ryujinx and Yuzu installation directories.
        """
        targets: List[EmulatorTarget] = []
        appdata = self.get_appdata_dir()
        home = Path.home()

        # 1. Check Ryujinx
        ryu_candidates = [
            appdata / "Ryujinx",
            home / ".config" / "Ryujinx",
            Path("portable/Ryujinx"),
        ]
        if "ryujinx" in self.custom_paths:
            ryu_candidates.insert(0, Path(self.custom_paths["ryujinx"]))

        for r_path in ryu_candidates:
            if r_path.is_dir():
                targets.append(
                    EmulatorTarget(
                        name="Ryujinx",
                        root_dir=r_path,
                        mods_base_dir=r_path / "mods" / "contents",
                        structure_type="ryujinx",
                    )
                )
                break

        # 2. Check Yuzu / Suyu
        yuzu_candidates = [
            appdata / "yuzu",
            home / ".local" / "share" / "yuzu",
            home / ".config" / "yuzu",
            appdata / "suyu",
        ]
        if "yuzu" in self.custom_paths:
            yuzu_candidates.insert(0, Path(self.custom_paths["yuzu"]))

        for y_path in yuzu_candidates:
            if y_path.is_dir():
                emu_name = "Suyu" if "suyu" in str(y_path).lower() else "Yuzu"
                targets.append(
                    EmulatorTarget(
                        name=emu_name,
                        root_dir=y_path,
                        mods_base_dir=y_path / "load",
                        structure_type="yuzu",
                    )
                )
                break

        return targets

    def resolve_destination(
        self, target: EmulatorTarget, title_id: str, mod_name: str
    ) -> Path:
        """
        Determine target mod directory layout based on emulator type.
        - Ryujinx: <mods_base>/<TitleID>/<ModName>/romfs
        - Yuzu:    <mods_base>/<TitleID>/<ModName>/romfs
        """
        clean_tid = RomFsBuilder.resolve_title_id(title_id)
        return target.mods_base_dir / clean_tid / mod_name / "romfs"

    def sync_patch(
        self,
        patch_source_dir: str,
        title_id: str,
        emulator_filter: str = "auto",
        mod_name: str = "NeXAS_Switch_Patch",
        clean_before: bool = True,
        dry_run: bool = False,
    ) -> List[SyncResult]:
        """
        Synchronize RomFS directory to detected emulator mod directories.
        :param patch_source_dir: Can be a direct `romfs/` folder or an `atmosphere/` patch root
        """
        clean_tid = RomFsBuilder.resolve_title_id(title_id)
        src_base = Path(patch_source_dir)

        # Locate the actual romfs directory within the source
        romfs_source = src_base
        if (src_base / "romfs").is_dir():
            romfs_source = src_base / "romfs"
        elif (src_base / "atmosphere" / "contents" / clean_tid / "romfs").is_dir():
            romfs_source = src_base / "atmosphere" / "contents" / clean_tid / "romfs"

        if not romfs_source.is_dir():
            raise FileNotFoundError(f"Could not locate valid romfs/ directory inside: {patch_source_dir}")

        all_emulators = self.detect_emulators()
        selected: List[EmulatorTarget] = []

        filter_lower = emulator_filter.lower()
        if filter_lower in ("auto", "all"):
            selected = all_emulators
        else:
            for emu in all_emulators:
                if filter_lower in emu.name.lower():
                    selected.append(emu)

        if not selected and not dry_run:
            raise FileNotFoundError(
                f"No matching Switch emulators found for filter '{emulator_filter}'. "
                f"Checked standard paths in: {self.get_appdata_dir()}"
            )

        results: List[SyncResult] = []

        # If dry run and no emulator found, provide simulated target
        if dry_run and not selected:
            simulated_target = (
                self.get_appdata_dir() / "Ryujinx" / "mods" / "contents" / clean_tid / mod_name / "romfs"
            )
            return [
                SyncResult(
                    emulator_name="Simulated",
                    target_mod_dir=str(simulated_target),
                    copied_files_count=0,
                    cleaned_previous=False,
                    status="dry_run",
                )
            ]

        for emu in selected:
            dest_romfs = self.resolve_destination(emu, clean_tid, mod_name)
            parent_mod_dir = dest_romfs.parent

            cleaned = False
            if clean_before and parent_mod_dir.exists() and not dry_run:
                shutil.rmtree(parent_mod_dir, ignore_errors=True)
                cleaned = True

            copied_count = 0
            if not dry_run:
                dest_romfs.mkdir(parents=True, exist_ok=True)
                for root, _, files in os.walk(romfs_source):
                    for f in files:
                        sp = Path(root) / f
                        rel_path = sp.relative_to(romfs_source)
                        dp = dest_romfs / rel_path
                        dp.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sp, dp)
                        copied_count += 1
            else:
                for root, _, files in os.walk(romfs_source):
                    copied_count += len(files)

            results.append(
                SyncResult(
                    emulator_name=emu.name,
                    target_mod_dir=str(dest_romfs),
                    copied_files_count=copied_count,
                    cleaned_previous=cleaned,
                    status="dry_run" if dry_run else "success",
                )
            )

        return results
