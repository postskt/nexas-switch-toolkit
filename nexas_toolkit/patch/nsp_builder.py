# -*- coding: utf-8 -*-
"""
Nintendo Switch NSP Packager Module for NeXAS Toolkit.
Based on hardware-verified Switch NSP Packaging Manual (F:\\移植\\work_yubisaki\\tools_handover\\NSP打包手册.md).

Key Hardware Verification Principles:
1. Container: 3-file NSP (program, control, meta NCA). No ticket/cert, rights_id all 0.
2. NPDM: Hardware-proven baseline (972 bytes, sha256 bbbc0e6d...). Only patch TitleID (3 places).
3. NACP: startup_user_account=1, UserSave=62MB (0x3E00000), Journal=1.5MB (0x180000), 16 UTF-8 language slots.
4. Icons: 256x256 JPEGs (icon.jpg and icon_AmericanEnglish.dat).
5. Safe Staging: Use pure ASCII directory (e.g. C:\\_nsp_pack_stage) with NTFS junction for RomFS to bypass
   hacBrewPack non-ASCII/UTF-16 path crash bug.
6. Verification: Automatically runs pack_check.py against golden reference to ensure 0 hard failures.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
from typing import Callable, List, Optional, Tuple

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
GOLDEN_NPDM_TEMPLATE = TEMPLATES_DIR / "golden.npdm"
GOLDEN_NACP_TEMPLATE = TEMPLATES_DIR / "golden_control.nacp"
DEFAULT_ICON_TEMPLATE = TEMPLATES_DIR / "default_icon.jpg"

EXTERNAL_TOOLS_CANDIDATES = [
    Path(r"F:\移植\output\nsp_build\hacbrewpack.exe"),
    Path(r"C:\yubi_pack\hacbrewpack.exe"),
    Path(r"F:\移植\tools\bin\hacpack.exe"),
]

PROD_KEYS_CANDIDATES = [
    Path(r"F:\移植\prod.keys"),
    Path(r"C:\hactool_tmp\prod.keys"),
    Path(r"F:\移植\output\nsp_build\prod.keys"),
    Path.home() / ".switch" / "prod.keys",
    Path.home() / "AppData" / "Roaming" / "Ryujinx" / "system" / "prod.keys",
    Path.home() / "AppData" / "Roaming" / "yuzu" / "keys" / "prod.keys",
]

PACK_CHECK_SCRIPT = Path(r"F:\移植\work_yubisaki\tools_handover\pack_check.py")


def patch_npdm_title_id(
    npdm_bytes: bytearray,
    title_id: int,
    name: Optional[str] = None,
    keep_broad_acid_range: bool = True,
) -> bytearray:
    """
    Patch title_id in golden 972-byte NPDM.
    Strictly following F:\\移植\\work_yubisaki\\tools_handover\\patch_npdm_titleid.py:
    Changes:
      ACI0 + 0x10 : ProgramId (u64)
      ACID + 0x10 : ProgramIdMin (u64, default 0x0100000000000000 matching golden reference)
      ACID + 0x18 : ProgramIdMax (u64, default 0x01FFFFFFFFFFFFFF matching golden reference)
      META + 0x20 : Process Name (16 bytes, optional)
    Leaves flags0 (0x07), SAC (802a002a), kernel caps (SVC whitelist) 100% intact.
    """
    data = bytearray(npdm_bytes)
    aci0 = data.find(b"ACI0")
    acid = data.find(b"ACID")
    if data[:4] != b"META" or aci0 < 0 or acid < 0:
        raise ValueError("Invalid NPDM: missing META/ACID/ACI0 headers")

    struct.pack_into("<Q", data, aci0 + 0x10, title_id)
    if keep_broad_acid_range:
        struct.pack_into("<Q", data, acid + 0x10, 0x0100000000000000)
        struct.pack_into("<Q", data, acid + 0x18, 0x01FFFFFFFFFFFFFF)
    else:
        struct.pack_into("<Q", data, acid + 0x10, title_id)
        struct.pack_into("<Q", data, acid + 0x18, title_id)

    if name:
        raw_name = name.encode("utf-8")[:15]
        data[0x20:0x30] = raw_name.ljust(16, b"\x00")

    return data


def patch_nacp(
    nacp_bytes: bytearray,
    title_id: int,
    title_name: str,
    author: str = "NeXAS Port",
    version: str = "1.0.0",
) -> bytearray:
    """
    Patch 16384-byte NACP data:
    1. 16 language slots (0x300 each: 0x200 name + 0x100 author) filled with valid UTF-8.
    2. startup_user_account at 0x3025 = 1 (Required).
    3. TitleID fields (0x3038 PresenceGroupId, 0x3070 AddOnContentBaseId, 0x3078/0x30B0 SaveDataOwnerId).
    4. UserSave = 0x3E00000 (62 MB), Journal = 0x180000 (1.5 MB).
    5. Display version at 0x3060: strictly ASCII without leading space.
    """
    data = bytearray(nacp_bytes)
    if len(data) < 0x4000:
        raise ValueError("Invalid NACP: must be at least 16384 (0x4000) bytes")

    title_enc = title_name.encode("utf-8")[:0x1FF]
    author_enc = author.encode("utf-8")[:0x0FF]

    # Fill all 16 language slots with UTF-8 title and author
    for i in range(16):
        base = i * 0x300
        # Title name (0x200 bytes)
        data[base : base + 0x200] = b"\x00" * 0x200
        data[base : base + len(title_enc)] = title_enc
        # Author / Publisher (0x100 bytes)
        auth_base = base + 0x200
        data[auth_base : auth_base + 0x100] = b"\x00" * 0x100
        data[auth_base : auth_base + len(author_enc)] = author_enc

    # startup_user_account (0x3025) = 1 (Required)
    data[0x3025] = 1

    # Supported languages bitmask (0x302C) = 0x0BFF
    struct.pack_into("<I", data, 0x302C, 0x0BFF)

    # Title ID fields (hacBrewPack and Horizon OS read Title ID from here)
    struct.pack_into("<Q", data, 0x3038, title_id)  # PresenceGroupId
    struct.pack_into("<Q", data, 0x3070, title_id + 0x1000)  # AddOnContentBaseId
    struct.pack_into("<Q", data, 0x3078, title_id)  # SaveDataOwnerId
    struct.pack_into("<Q", data, 0x30B0, title_id)  # SaveDataOwnerId 0

    # Display version (0x3060) - 16 bytes ASCII, no leading space
    clean_ver = version.strip().encode("ascii", "ignore")[:15]
    data[0x3060:0x3070] = b"\x00" * 16
    data[0x3060 : 0x3060 + len(clean_ver)] = clean_ver

    # User Account Save Data Size (0x3080) = 0x3E00000 (62MB)
    struct.pack_into("<Q", data, 0x3080, 0x3E00000)
    # User Account Save Data Journal Size (0x3088) = 0x180000 (1.5MB)
    struct.pack_into("<Q", data, 0x3088, 0x180000)
    # Device Save Data Size (0x3090) = 0
    struct.pack_into("<Q", data, 0x3090, 0)

    return data


def prepare_icon_jpeg(source_icon: Optional[Path], target_path: Path) -> None:
    """Create or resize 256x256 RGB JPEG icon."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_icon and source_icon.is_file():
        try:
            from PIL import Image

            img = Image.open(source_icon).convert("RGBA")
            side = min(img.width, img.height)
            img = img.crop((0, 0, side, side)).resize((256, 256), Image.Resampling.LANCZOS)
            bg = Image.new("RGB", (256, 256), (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            bg.save(target_path, "JPEG", quality=95, progressive=False, optimize=True)
            return
        except Exception as e:
            print(f"[NspBuilder] Pillow failed to convert icon ({e}), falling back to default.")

    # Fallback to bundled default icon
    if DEFAULT_ICON_TEMPLATE.is_file():
        shutil.copy2(DEFAULT_ICON_TEMPLATE, target_path)
    else:
        # Create minimal 256x256 white JPEG
        from PIL import Image

        Image.new("RGB", (256, 256), (70, 130, 180)).save(target_path, "JPEG")


@dataclass
class NspBuildResult:
    nsp_path: str
    size_bytes: int
    title_id: str
    verified: bool
    pack_check_output: str


class NspBuilder:
    """
    Builds hardware-compliant standalone Nintendo Switch NSP packages.
    """

    def __init__(
        self,
        hacbrewpack_path: Optional[str] = None,
        prod_keys_path: Optional[str] = None,
        pack_check_path: Optional[str] = None,
        staging_root: str = r"C:\_nsp_pack_stage",
    ):
        self.hacbrewpack_path = self._find_tool(hacbrewpack_path, EXTERNAL_TOOLS_CANDIDATES, "hacbrewpack.exe")
        self.prod_keys_path = self._find_tool(prod_keys_path, PROD_KEYS_CANDIDATES, "prod.keys")
        self.pack_check_path = Path(pack_check_path) if pack_check_path else PACK_CHECK_SCRIPT
        self.staging_root = Path(staging_root)

    def _find_tool(self, explicit: Optional[str], candidates: List[Path], name: str) -> Optional[Path]:
        if explicit and Path(explicit).is_file():
            return Path(explicit)
        for c in candidates:
            if c.is_file():
                return c
        which = shutil.which(name)
        if which:
            return Path(which)
        return None

    def build_nsp(
        self,
        romfs_dir: str,
        main_nso_path: str,
        title_id: str,
        title_name: str = "NeXAS Game",
        author: str = "NeXAS",
        version: str = "1.0.0",
        icon_path: Optional[str] = None,
        output_dir: str = "./dist",
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> NspBuildResult:
        """
        Execute full NSP packaging workflow.
        """
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(f"[NspBuilder] {msg}")

        # Validate inputs
        romfs_p = Path(romfs_dir).resolve()
        if not romfs_p.is_dir():
            raise FileNotFoundError(f"RomFS directory not found: {romfs_p}")

        main_nso_p = Path(main_nso_path).resolve()
        if not main_nso_p.is_file():
            raise FileNotFoundError(f"main NSO executable not found: {main_nso_p}")

        clean_tid = title_id.replace("0x", "").strip().upper()
        if len(clean_tid) != 16 or not clean_tid.startswith("0100"):
            raise ValueError(f"Invalid application Title ID: {title_id} (must be 16 hex chars starting with 0100)")
        tid_int = int(clean_tid, 16)

        if not self.hacbrewpack_path or not self.hacbrewpack_path.is_file():
            raise FileNotFoundError(
                f"hacbrewpack.exe not found. Looked in {EXTERNAL_TOOLS_CANDIDATES}. "
                "Please specify path or place it in F:\\移植\\output\\nsp_build\\hacbrewpack.exe"
            )

        if not self.prod_keys_path or not self.prod_keys_path.is_file():
            raise FileNotFoundError(
                f"prod.keys not found. Looked in {PROD_KEYS_CANDIDATES}. "
                "Please specify path or place it in F:\\移植\\prod.keys"
            )

        out_dest_dir = Path(output_dir).resolve()
        out_dest_dir.mkdir(parents=True, exist_ok=True)

        stage = self.staging_root
        log(f"Preparing ASCII staging environment at {stage}...")
        self._clean_staging(stage)
        stage.mkdir(parents=True, exist_ok=True)

        exefs_dir = stage / "exefs"
        control_dir = stage / "control"
        nca_dir = stage / "nca"
        nsp_dir = stage / "nsp"
        stage_romfs = stage / "romfs"

        exefs_dir.mkdir(parents=True, exist_ok=True)
        control_dir.mkdir(parents=True, exist_ok=True)
        nca_dir.mkdir(parents=True, exist_ok=True)
        nsp_dir.mkdir(parents=True, exist_ok=True)

        junction_created = False
        try:
            # Step 1: Create NTFS Junction for RomFS to avoid copying gigabytes and bypass non-ASCII path bug
            log(f"Linking RomFS via NTFS junction: {stage_romfs} -> {romfs_p}...")
            # Note: cmd /c mklink /J works across drive letters on local Windows filesystems
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(stage_romfs), str(romfs_p)],
                capture_output=True,
                text=True,
            )
            if res.returncode == 0:
                junction_created = True
                log("Junction created successfully.")
            else:
                log(f"Junction creation failed ({res.stderr.strip()}), falling back to direct copy...")
                shutil.copytree(romfs_p, stage_romfs, dirs_exist_ok=True)

            # Step 2: Prepare ExeFS (main + golden npdm with patched title_id)
            log("Configuring ExeFS with golden hardware-verified NPDM...")
            shutil.copy2(main_nso_p, exefs_dir / "main")

            if not GOLDEN_NPDM_TEMPLATE.is_file():
                raise FileNotFoundError(f"Golden NPDM template not found at {GOLDEN_NPDM_TEMPLATE}")

            golden_npdm_raw = bytearray(GOLDEN_NPDM_TEMPLATE.read_bytes())
            patched_npdm = patch_npdm_title_id(golden_npdm_raw, tid_int, name="Application")
            (exefs_dir / "main.npdm").write_bytes(patched_npdm)
            log(f"Patched main.npdm with Title ID: 0x{clean_tid}")

            # Step 3: Prepare Control (control.nacp + icons)
            log("Configuring Control (NACP & 256x256 Icons)...")
            if not GOLDEN_NACP_TEMPLATE.is_file():
                raise FileNotFoundError(f"Golden NACP template not found at {GOLDEN_NACP_TEMPLATE}")

            golden_nacp_raw = bytearray(GOLDEN_NACP_TEMPLATE.read_bytes())
            patched_nacp = patch_nacp(golden_nacp_raw, title_id=tid_int, title_name=title_name, author=author, version=version)
            (control_dir / "control.nacp").write_bytes(patched_nacp)

            icon_p = Path(icon_path) if icon_path else None
            prepare_icon_jpeg(icon_p, control_dir / "icon.jpg")
            shutil.copy2(control_dir / "icon.jpg", control_dir / "icon_AmericanEnglish.dat")

            # Copy tools to staging root to ensure clean local execution
            stage_keys = stage / "prod.keys"
            shutil.copy2(self.prod_keys_path, stage_keys)
            stage_hacbrew = stage / "hacbrewpack.exe"
            shutil.copy2(self.hacbrewpack_path, stage_hacbrew)

            # Step 4: Run hacBrewPack
            log("Executing hacBrewPack to build 3-file NSP container...")
            cmd = [
                str(stage_hacbrew),
                "-k", str(stage_keys),
                "--exefsdir", str(exefs_dir),
                "--romfsdir", str(stage_romfs),
                "--controldir", str(control_dir),
                "--ncadir", str(nca_dir),
                "--nspdir", str(nsp_dir),
                "--nologo",
            ]

            t0 = time.perf_counter()
            proc = subprocess.run(cmd, cwd=str(stage), capture_output=True, text=True)
            elapsed = time.perf_counter() - t0

            if proc.returncode != 0:
                log(f"[ERROR] hacBrewPack failed with code {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
                raise RuntimeError(f"hacBrewPack execution failed: {proc.stderr or proc.stdout}")

            log(f"hacBrewPack completed in {elapsed:.2f}s.")

            # Step 5: Locate produced NSP and move to destination
            nsp_files = list(nsp_dir.glob("*.nsp"))
            if not nsp_files:
                raise FileNotFoundError("hacBrewPack finished but no .nsp was found in output directory")

            built_nsp = nsp_files[0]
            clean_title_filename = "".join(c for c in title_name if c.isalnum() or c in (" ", "_", "-")).strip()
            final_filename = f"{clean_title_filename}[{clean_tid}][v0].nsp"
            final_dest = out_dest_dir / final_filename

            shutil.move(str(built_nsp), str(final_dest))
            final_size = final_dest.stat().st_size
            log(f"NSP generated: {final_dest.name} ({final_size / (1024*1024):.2f} MB)")

            # Step 6: Automated verification with pack_check.py
            check_out = ""
            verified = False
            if self.pack_check_path and self.pack_check_path.is_file():
                log("Running pack_check.py to verify against golden hardware reference...")
                check_cmd = [
                    sys.executable,
                    str(self.pack_check_path),
                    str(final_dest),
                    "--keys", str(self.prod_keys_path),
                ]
                check_proc = subprocess.run(check_cmd, capture_output=True, text=True)
                check_out = check_proc.stdout + check_proc.stderr
                if check_proc.returncode == 0:
                    verified = True
                    log("[PASS] pack_check: 0 hard failures! Structurally compliant with hardware golden baseline.")
                else:
                    log(f"[WARN] pack_check reported issues:\n{check_out}")
            else:
                log("pack_check.py not found; skipped automated verification.")

            return NspBuildResult(
                nsp_path=str(final_dest),
                size_bytes=final_size,
                title_id=clean_tid,
                verified=verified,
                pack_check_output=check_out,
            )

        finally:
            log("Cleaning up ASCII staging directory...")
            if junction_created:
                # Remove NTFS junction safely using rmdir without deleting real romfs content
                subprocess.run(["cmd", "/c", "rmdir", str(stage_romfs)], capture_output=True)
            self._clean_staging(stage)

    def _clean_staging(self, stage: Path) -> None:
        if not stage.exists():
            return
        stage_romfs = stage / "romfs"
        if stage_romfs.exists():
            subprocess.run(["cmd", "/c", "rmdir", str(stage_romfs)], capture_output=True)
        shutil.rmtree(stage, ignore_errors=True)
