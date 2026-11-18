# -*- coding: utf-8 -*-
"""
Atmosphère LayeredFS RomFS Patch Builder for Nintendo Switch
Automatically structures modded files into the standard Atmosphere directory layout:
atmosphere/contents/<TITLE_ID>/romfs/...
"""

import json
import os
from pathlib import Path
import re
import shutil
from typing import Dict, List, Optional
import zipfile


class RomFsBuilder:
    """
    Automates the construction of Nintendo Switch Atmosphere LayeredFS patch structures.
    """

    # Well-known NeXAS Engine Nintendo Switch Game Title IDs
    KNOWN_TITLE_PRESETS: Dict[str, str] = {
        "aquarium": "0100D11018A7E000",   # Aquarium (AQUASTYLE / Entergram)
        "dc4fd": "0100412014A86000",      # D.C.4 Fortunate Departures
        "aikiss": "010065900EE78000",     # Aikiss 1
        "aikiss2": "0100F83013854000",    # Aikiss 2
        "aikiss3": "010074F01657A000",    # Aikiss 3
    }

    def __init__(self, title_id: str, mod_name: str = "NeXAS_Switch_Patch"):
        """
        :param title_id: 16-character hexadecimal Title ID (or preset keyword like 'aquarium')
        :param mod_name: Human-readable mod identifier
        """
        resolved_id = self.resolve_title_id(title_id)
        if not self.validate_title_id(resolved_id):
            raise ValueError(
                f"Invalid Nintendo Switch Title ID: '{title_id}' "
                f"(Resolved: '{resolved_id}'). Must be 16-character hex string."
            )
        self.title_id = resolved_id.upper()
        self.mod_name = mod_name

    @classmethod
    def resolve_title_id(cls, identifier: str) -> str:
        """Resolve preset names or clean hex string."""
        clean = identifier.strip().lower()
        if clean in cls.KNOWN_TITLE_PRESETS:
            return cls.KNOWN_TITLE_PRESETS[clean]
        return identifier.strip().upper()

    @classmethod
    def validate_title_id(cls, tid: str) -> bool:
        """Check if string is a valid 16-character hexadecimal Title ID."""
        return bool(re.fullmatch(r"^[0-9A-Fa-f]{16}$", tid))

    def build_patch_tree(
        self,
        output_dir: str,
        pac_files: Optional[List[str]] = None,
        scripts_dir: Optional[str] = None,
        scenes_dir: Optional[str] = None,
        extra_dirs: Optional[Dict[str, str]] = None,
        create_zip: bool = True,
    ) -> Dict[str, Path]:
        """
        Build Atmosphere directory hierarchy:
        <output_dir>/atmosphere/contents/<TITLE_ID>/romfs/...
        """
        out_base = Path(output_dir)
        patch_root = out_base / "atmosphere" / "contents" / self.title_id
        romfs_dir = patch_root / "romfs"
        romfs_dir.mkdir(parents=True, exist_ok=True)

        copied_items: List[Dict[str, str]] = []

        # 1. Copy PAC archives into romfs root
        if pac_files:
            for pac in pac_files:
                pac_p = Path(pac)
                if pac_p.exists():
                    target_pac = romfs_dir / pac_p.name
                    shutil.copy2(pac_p, target_pac)
                    copied_items.append({"source": str(pac_p), "target": f"romfs/{pac_p.name}"})

        # 2. Copy scripts into romfs/Script/
        if scripts_dir:
            sc_src = Path(scripts_dir)
            if sc_src.is_dir():
                sc_target = romfs_dir / "Script"
                sc_target.mkdir(parents=True, exist_ok=True)
                for root, _, files in os.walk(sc_src):
                    for f in files:
                        fp = Path(root) / f
                        rel_p = fp.relative_to(sc_src)
                        dest = sc_target / rel_p
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fp, dest)
                        copied_items.append({"source": str(fp), "target": f"romfs/Script/{rel_p.as_posix()}"})

        # 3. Copy scene assets into romfs/Scene/
        if scenes_dir:
            scene_src = Path(scenes_dir)
            if scene_src.is_dir():
                scene_target = romfs_dir / "Scene"
                scene_target.mkdir(parents=True, exist_ok=True)
                for root, _, files in __import__("os").walk(scene_src):
                    for f in files:
                        fp = Path(root) / f
                        rel_p = fp.relative_to(scene_src)
                        dest = scene_target / rel_p
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fp, dest)
                        copied_items.append({"source": str(fp), "target": f"romfs/Scene/{rel_p.as_posix()}"})

        # 4. Copy custom extra directories
        if extra_dirs:
            for src_folder, target_rel in extra_dirs.items():
                s_p = Path(src_folder)
                if s_p.is_dir():
                    t_dir = romfs_dir / target_rel
                    t_dir.mkdir(parents=True, exist_ok=True)
                    for root, _, files in __import__("os").walk(s_p):
                        for f in files:
                            fp = Path(root) / f
                            rel_p = fp.relative_to(s_p)
                            dest = t_dir / rel_p
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(fp, dest)
                            copied_items.append({"source": str(fp), "target": f"romfs/{target_rel}/{rel_p.as_posix()}"})

        # 5. Write Manifest and Installation Guide
        manifest_path = patch_root / "patch_manifest.json"
        manifest_data = {
            "title_id": self.title_id,
            "mod_name": self.mod_name,
            "total_files": len(copied_items),
            "files": copied_items,
        }
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

        readme_path = out_base / "README_INSTALL.txt"
        readme_content = (
            f"==========================================================\n"
            f" Nintendo Switch Atmosphère LayeredFS Patch\n"
            f" Title ID : {self.title_id}\n"
            f" Mod Name : {self.mod_name}\n"
            f"==========================================================\n\n"
            f"【实机安装说明 / Installation Guide】:\n"
            f"1. 将生成的 'atmosphere' 文件夹直接解压并复制至 Nintendo Switch 的 microSD 卡根目录。\n"
            f"2. 最终层级为：sdmc:/atmosphere/contents/{self.title_id}/romfs/...\n"
            f"3. 启动 Atmosphère 引导系统进入游戏，LayeredFS 补丁将自动生效。\n\n"
            f"【模拟器测试说明 / Emulators (Yuzu / Ryujinx)】:\n"
            f"- Ryujinx: 右键游戏 -> Open Atmosphere Mods Directory -> 放置生成的文件夹\n"
            f"- Yuzu: 右键游戏 -> Open Mod Data Location -> 放置生成的文件夹\n"
        )
        readme_path.write_text(readme_content, encoding="utf-8")

        result = {
            "patch_root": patch_root,
            "romfs_dir": romfs_dir,
            "manifest": manifest_path,
            "readme": readme_path,
        }

        # 6. Optional ZIP archive creation
        if create_zip:
            zip_filename = out_base / f"{self.title_id}_Atmosphere_LayeredFS.zip"
            with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # Add atmosphere directory
                for root, _, files in __import__("os").walk(out_base / "atmosphere"):
                    for f in files:
                        fp = Path(root) / f
                        arcname = fp.relative_to(out_base).as_posix()
                        zf.write(fp, arcname=arcname)
                # Add readme
                zf.write(readme_path, arcname="README_INSTALL.txt")
            result["zip"] = zip_filename

        return result
