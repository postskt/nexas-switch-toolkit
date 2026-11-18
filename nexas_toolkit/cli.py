# -*- coding: utf-8 -*-
"""
NeXAS Switch Toolkit - Command Line Interface (CLI)
Automated cross-platform toolkit for NeXAS engine porting (PC -> Nintendo Switch).
"""

import argparse
import json
import os
from pathlib import Path
import sys

from .gui import start_gui_server
from .media.audio_converter import AudioConverter
from .media.image_converter import ImageConverter
from .pac.pac_builder import PacBuilder
from .pac.pac_extractor import PacExtractor
from .patch.emulator_sync import EmulatorSync
from .patch.romfs_builder import RomFsBuilder
from .patch.nsp_builder import NspBuilder
from .pipeline import PipelineConfig, PipelineRunner
from .scene.grp_map_converter import (
    GrpFile,
    GrpMapConverter,
    MapFile,
    ResolutionScaler,
    ScalingMode,
)
from .script.script_converter import ScriptConverter
from .script.script_dumper import ScriptDumper
from .script.script_parser import ScriptParser
from .script.smart_wrapper import SmartWrapper

__version__ = "2.0.0"


# ==========================================
# Phase 1 Command Handlers
# ==========================================
def handle_pac_extract(args: argparse.Namespace) -> int:
    pac_path = Path(args.input)
    if not pac_path.exists():
        print(f"[ERROR] Input PAC file not found: {pac_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.output) if args.output else pac_path.with_suffix("")
    print(f"[*] Extracting PAC Archive: {pac_path.name}")
    print(f"[*] Target Directory: {out_dir}")

    def progress(cur, total, fname):
        if args.verbose or cur % 50 == 0 or cur == total:
            print(f"  [{cur}/{total}] {fname}")

    extractor = PacExtractor(str(pac_path), encoding=args.encoding)
    archive = extractor.extract_all(
        str(out_dir), dump_manifest=not args.no_manifest, progress_callback=progress
    )

    print(
        f"[+] Successfully extracted {len(archive.entries)} files ({archive.variant.value}, {archive.encoding})."
    )
    return 0


def handle_pac_build(args: argparse.Namespace) -> int:
    in_dir = Path(args.input)
    if not in_dir.is_dir():
        print(f"[ERROR] Input directory not found: {in_dir}", file=sys.stderr)
        return 1

    out_pac = Path(args.output) if args.output else in_dir.with_suffix(".pac")
    print(f"[*] Packing directory: {in_dir}")
    print(f"[*] Target Platform: {args.platform.upper()} | Output PAC: {out_pac.name}")
    print(f"[*] Compression: {args.compression.upper()} (level {args.level}) | Alignment: {args.alignment}B")

    def progress(cur, total, fname):
        if args.verbose or cur % 50 == 0 or cur == total:
            print(f"  [{cur}/{total}] {fname}")

    builder = PacBuilder(
        target_platform=args.platform,
        compression=args.compression,
        compression_level=args.level,
        encoding=args.encoding,
        alignment=args.alignment,
    )

    archive = builder.build_from_directory(
        str(in_dir),
        str(out_pac),
        manifest_path=args.manifest,
        progress_callback=progress,
    )

    print(
        f"[+] Successfully generated PAC package: {out_pac} "
        f"({len(archive.entries)} files, {archive.total_data_size} bytes)."
    )
    return 0


def handle_script_dump(args: argparse.Namespace) -> int:
    in_script = Path(args.input)
    if not in_script.exists():
        print(f"[ERROR] Script file not found: {in_script}", file=sys.stderr)
        return 1

    with open(in_script, "rb") as f:
        data = f.read()

    parser = ScriptParser(encoding=args.encoding)
    script = parser.parse(data)
    print(
        f"[*] Parsed script '{in_script.name}': {len(script.strings)} strings, "
        f"{len(script.init_codes)} init opcodes, {len(script.codes)} main opcodes."
    )

    out_file = Path(args.output) if args.output else in_script.with_suffix(".json")
    out_format = args.format
    if not out_format:
        out_format = "txt" if out_file.suffix.lower() == ".txt" else "json"

    if out_format == "json":
        ScriptDumper.dump_to_json(script, str(out_file))
    else:
        ScriptDumper.dump_to_txt(script, str(out_file))

    print(f"[+] Dumped strings to {out_file} ({out_format.upper()} format).")
    return 0


def handle_script_convert(args: argparse.Namespace) -> int:
    in_script = Path(args.input)
    if not in_script.exists():
        print(f"[ERROR] Script file not found: {in_script}", file=sys.stderr)
        return 1

    with open(in_script, "rb") as f:
        raw_data = f.read()

    parser = ScriptParser(encoding=args.src_encoding)
    script = parser.parse(raw_data)

    # Optional smart wrap before conversion
    if args.wrap:
        print(f"[*] Applying smart word wrapping (max width: {args.max_width})...")
        wrapper = SmartWrapper(max_width=args.max_width)
        script = wrapper.wrap_script(script)

    converter = ScriptConverter()
    target_platform = args.target.lower()

    if target_platform == "switch":
        out_file = Path(args.output) if args.output else in_script.with_suffix(".binu8")
        print(f"[*] Converting PC Script -> Nintendo Switch (.binu8)...")
        new_script, new_bytes = converter.convert_pc_to_switch(
            script, target_encoding=args.dst_encoding or "utf-8"
        )
    else:
        out_file = Path(args.output) if args.output else in_script.with_suffix(".bin")
        print(f"[*] Converting Switch Script -> PC (.bin)...")
        new_script, new_bytes = converter.convert_switch_to_pc(
            script, target_encoding=args.dst_encoding or "cp932"
        )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as f:
        f.write(new_bytes)

    print(
        f"[+] Successfully converted and relocated script: {out_file} "
        f"({len(new_script.strings)} strings, size: {len(new_bytes)} bytes)."
    )
    return 0


def handle_script_compile(args: argparse.Namespace) -> int:
    template_script_path = Path(args.template)
    if not template_script_path.exists():
        print(f"[ERROR] Template script not found: {template_script_path}", file=sys.stderr)
        return 1

    trans_path = Path(args.translation)
    if not trans_path.exists():
        print(f"[ERROR] Translation file not found: {trans_path}", file=sys.stderr)
        return 1

    with open(template_script_path, "rb") as f:
        data = f.read()

    parser = ScriptParser()
    script = parser.parse(data)

    if trans_path.suffix.lower() == ".json":
        strings = ScriptDumper.load_strings_from_json(str(trans_path))
    else:
        strings = ScriptDumper.load_strings_from_txt(str(trans_path))

    # Optional wrap translated strings
    if args.wrap:
        print(f"[*] Auto-wrapping {len(strings)} translated lines (max width: {args.max_width})...")
        wrapper = SmartWrapper(max_width=args.max_width)
        strings = [wrapper.wrap_text(s) for s in strings]

    print(f"[*] Loaded {len(strings)} replacement strings from {trans_path.name}.")
    converter = ScriptConverter()

    if args.platform.lower() == "switch":
        out_file = Path(args.output) if args.output else template_script_path.with_suffix(".binu8")
        _, out_bytes = converter.convert_pc_to_switch(
            script, target_encoding="utf-8", new_strings=strings
        )
    else:
        out_file = Path(args.output) if args.output else template_script_path.with_suffix(".bin")
        _, out_bytes = converter.convert_switch_to_pc(
            script, target_encoding="cp932", new_strings=strings
        )

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as f:
        f.write(out_bytes)

    print(f"[+] Rebuilt compiled script with relocated offsets: {out_file}")
    return 0


# ==========================================
# Phase 2 Command Handlers
# ==========================================
def handle_script_wrap(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] Input file not found: {in_path}", file=sys.stderr)
        return 1

    wrapper = SmartWrapper(max_width=args.max_width, max_lines_per_box=args.max_lines)
    out_path = Path(args.output) if args.output else in_path.with_name(f"{in_path.stem}_wrapped{in_path.suffix}")

    # Case A: JSON file
    if in_path.suffix.lower() == ".json":
        with open(in_path, "r", encoding="utf-8") as f:
            jdata = json.load(f)
        for item in jdata.get("strings", []):
            if "translation" in item:
                item["translation"] = wrapper.wrap_text(item["translation"])
            elif "original" in item:
                item["original"] = wrapper.wrap_text(item["original"])
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(jdata, f, indent=2, ensure_ascii=False)
        print(f"[+] Formatted JSON strings saved to: {out_path}")
        return 0

    # Case B: Plain text / dialogue text file
    if in_path.suffix.lower() in (".txt", ".text"):
        raw_text = in_path.read_text(encoding="utf-8")
        wrapped = wrapper.wrap_text(raw_text)
        out_path.write_text(wrapped, encoding="utf-8")
        print(f"[+] Formatted text saved to: {out_path}")
        return 0

    # Case C: Binary script (.bin / .binu8)
    with open(in_path, "rb") as f:
        data = f.read()
    parser = ScriptParser(encoding=args.encoding)
    script = parser.parse(data)
    wrapped_script = wrapper.wrap_script(script)
    converter = ScriptConverter()
    if script.is_switch:
        _, out_bytes = (
            converter.convert_switch_to_pc(wrapped_script)
            if args.target == "pc"
            else converter.convert_pc_to_switch(wrapped_script, target_encoding="utf-8")
        )
    else:
        _, out_bytes = (
            converter.convert_pc_to_switch(wrapped_script, target_encoding="utf-8")
            if args.target == "switch"
            else converter.convert_switch_to_pc(wrapped_script)
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)
    print(f"[+] Wrapped binary script saved to: {out_path}")
    return 0


def _parse_resolution(res_str: str) -> tuple[int, int]:
    parts = res_str.lower().split("x")
    if len(parts) != 2:
        raise ValueError(f"Invalid resolution format: '{res_str}'. Expected format like '800x600' or '1280x720'")
    return int(parts[0]), int(parts[1])


def handle_scene_convert(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] Scene file not found: {in_path}", file=sys.stderr)
        return 1

    src_w, src_h = _parse_resolution(args.src_res)
    dst_w, dst_h = _parse_resolution(args.dst_res)
    scaler = ResolutionScaler(
        src_resolution=(src_w, src_h),
        dst_resolution=(dst_w, dst_h),
        mode=ScalingMode(args.mode),
    )

    out_path = Path(args.output) if args.output else in_path.with_name(f"{in_path.stem}_switch{in_path.suffix}")
    is_grp = in_path.suffix.lower() == ".grp" or in_path.name.lower().endswith(".grp")
    is_map = in_path.suffix.lower() == ".map" or in_path.name.lower().endswith(".map")

    raw_data = in_path.read_bytes()
    if is_grp:
        grp = GrpMapConverter.parse_grp(raw_data)
        scaled_grp = scaler.scale_grp(grp)
        out_bytes = GrpMapConverter.serialize_grp(scaled_grp, encoding=args.encoding or "utf-8")
        out_path.write_bytes(out_bytes)
        if args.dump_json:
            GrpMapConverter.dump_to_json(scaled_grp, str(out_path.with_suffix(".json")))
        print(f"[+] Scaled .grp ({src_w}x{src_h} -> {dst_w}x{dst_h}, {len(scaled_grp.entries)} sprites) -> {out_path}")
    elif is_map:
        map_f = GrpMapConverter.parse_map(raw_data)
        scaled_map = scaler.scale_map(map_f)
        out_bytes = GrpMapConverter.serialize_map(scaled_map, encoding=args.encoding or "utf-8")
        out_path.write_bytes(out_bytes)
        if args.dump_json:
            GrpMapConverter.dump_to_json(scaled_map, str(out_path.with_suffix(".json")))
        print(f"[+] Scaled .map ({src_w}x{src_h} -> {dst_w}x{dst_h}, {len(scaled_map.nodes)} nodes) -> {out_path}")
    else:
        print(f"[ERROR] Unsupported scene file type: {in_path.suffix}", file=sys.stderr)
        return 1

    return 0


def handle_romfs_pack(args: argparse.Namespace) -> int:
    title_id = args.title_id
    out_dir = Path(args.output)
    print(f"[*] Preparing Atmosphère LayeredFS RomFS Patch...")
    print(f"[*] Title ID Target: {title_id} | Output Path: {out_dir}")

    builder = RomFsBuilder(title_id=title_id, mod_name=args.mod_name)
    results = builder.build_patch_tree(
        output_dir=str(out_dir),
        pac_files=args.pac,
        scripts_dir=args.scripts,
        scenes_dir=args.scenes,
        create_zip=not args.no_zip,
    )

    print(f"[+] Successfully built Atmosphere patch tree at: {results['patch_root']}")
    print(f"[+] RomFS Directory: {results['romfs_dir']}")
    if "zip" in results:
        print(f"[+] Atmosphere Deployable ZIP: {results['zip']}")
    print(f"[+] Readme installation guide generated: {results['readme']}")
    return 0


# ==========================================
# Phase 3 Command Handlers
# ==========================================
def handle_media_convert(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] Input media path not found: {in_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = args.type.lower()

    # Image processing
    if mode in ("image", "all"):
        max_res = _parse_resolution(args.max_res) if args.max_res else None
        print(f"[*] Converting Images: Alignment={args.alignment}B | MaxRes={args.max_res} | PremultiplyAlpha={args.premultiply_alpha}")
        img_converter = ImageConverter(
            alignment=args.alignment,
            max_resolution=max_res,
            premultiply_alpha=args.premultiply_alpha,
            target_format="PNG",
        )
        if in_path.is_file():
            res = img_converter.convert_file(str(in_path), str(out_dir / f"{in_path.stem}.png"))
            print(f"  [Image] {in_path.name} -> {res.processed_size} (downscaled={res.was_downscaled}, padded={res.was_padded})")
        else:
            def img_prog(cur, tot, name):
                if args.verbose or cur % 20 == 0 or cur == tot:
                    print(f"  [Image {cur}/{tot}] {name}")
            results = img_converter.process_directory(str(in_path), str(out_dir), progress_callback=img_prog)
            success_count = sum(1 for r in results if r.status == "success")
            print(f"[+] Processed {success_count}/{len(results)} images into: {out_dir}")

    # Audio processing
    if mode in ("audio", "all"):
        print(f"[*] Converting Audio: TargetSampleRate={args.sample_rate}Hz | Format={args.audio_format.upper()}")
        audio_converter = AudioConverter(
            target_sample_rate=args.sample_rate,
            target_format=args.audio_format,
        )
        if in_path.is_file():
            out_file = out_dir / f"{in_path.stem}.{args.audio_format.lower()}"
            res = audio_converter.convert_file(str(in_path), str(out_file))
            print(f"  [Audio] {in_path.name} -> {res.target_path} (method={res.method_used})")
        else:
            def aud_prog(cur, tot, name):
                if args.verbose or cur % 20 == 0 or cur == tot:
                    print(f"  [Audio {cur}/{tot}] {name}")
            results = audio_converter.process_directory(str(in_path), str(out_dir), progress_callback=aud_prog)
            success_count = sum(1 for r in results if r.status == "success")
            print(f"[+] Processed {success_count}/{len(results)} audio tracks into: {out_dir}")

    return 0


def handle_emu_sync(args: argparse.Namespace) -> int:
    patch_dir = Path(args.patch_dir)
    if not patch_dir.exists():
        print(f"[ERROR] Patch source directory not found: {patch_dir}", file=sys.stderr)
        return 1

    sync = EmulatorSync()
    print(f"[*] Detecting local Nintendo Switch emulators...")
    detected = sync.detect_emulators()
    if detected:
        for emu in detected:
            print(f"  -> Found {emu.name}: {emu.root_dir}")
    else:
        print("  -> No default emulator directories detected in standard AppData.")

    print(f"[*] Synchronizing patch for Title ID '{args.title_id}' (Target: {args.emulator.upper()})...")
    results = sync.sync_patch(
        patch_source_dir=str(patch_dir),
        title_id=args.title_id,
        emulator_filter=args.emulator,
        mod_name=args.mod_name,
        clean_before=not args.no_clean,
        dry_run=args.dry_run,
    )

    for r in results:
        status_tag = "[DRY-RUN]" if r.status == "dry_run" else "[SYNCED]"
        print(f"{status_tag} {r.emulator_name} -> {r.target_mod_dir} ({r.copied_files_count} files, cleaned={r.cleaned_previous})")

    print("[+] Emulator sync completed successfully.")
    return 0


# ==========================================
# Phase 4 Command Handlers
# ==========================================
def handle_pipeline(args: argparse.Namespace) -> int:
    if args.config:
        cfg = PipelineConfig.from_json(args.config)
    else:
        if not args.input or not args.output:
            print("[ERROR] Either --config or (--input, --output) must be specified.", file=sys.stderr)
            return 1

        src_res = _parse_resolution(args.src_res) if args.src_res else (800, 600)
        dst_res = _parse_resolution(args.dst_res) if args.dst_res else (1280, 720)
        cfg = PipelineConfig(
            project_name=args.mod_name or "NeXAS_Switch_Port",
            title_id=args.title_id or "0100D11018A7E000",
            input_dir=args.input,
            output_dir=args.output,
            resolution_src=src_res,
            resolution_dst=dst_res,
            scaling_mode=args.scaling_mode or "letterbox",
            enable_smart_wrap=not args.no_wrap,
            image_alignment=args.alignment,
            audio_sample_rate=args.sample_rate,
            create_zip=not args.no_zip,
            sync_emulator=args.sync_emu,
            create_nsp=getattr(args, "create_nsp", False),
            main_nso_path=getattr(args, "main_nso", "") or "",
            icon_path=getattr(args, "icon", "") or "",
        )

    runner = PipelineRunner(cfg)
    result = runner.run()
    return 0 if result["status"] == "success" else 1


def handle_nsp_build(args: argparse.Namespace) -> int:
    try:
        builder = NspBuilder(
            hacbrewpack_path=args.hacbrewpack,
            prod_keys_path=args.keys,
        )
        res = builder.build_nsp(
            romfs_dir=args.romfs,
            main_nso_path=args.main,
            title_id=args.title_id or "0100D11018A7E000",
            title_name=args.title_name or "NeXAS Game",
            author=args.author or "NeXAS",
            version=args.ver or "1.0.0",
            icon_path=args.icon,
            output_dir=args.output or "./dist",
        )
        print(f"[+] NSP successfully generated at: {res.nsp_path} ({res.size_bytes / (1024*1024):.2f} MB)")
        if res.verified:
            print("[+] Hardware verification: PASSED (0 hard failures against golden reference).")
        else:
            print("[!] Hardware verification: Warning or skipped.")
        return 0
    except Exception as e:
        print(f"[ERROR] Failed to build NSP: {e}", file=sys.stderr)
        return 1


def handle_gui(args: argparse.Namespace) -> int:
    start_gui_server(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


# ==========================================
# CLI Argument Parser Construction
# ==========================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexas-tool",
        description="nexas-switch-toolkit: NeXAS Engine PC -> Nintendo Switch Cross-Platform Toolkit",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="Module commands")

    # 1. pac-extract
    p_pac_ex = subparsers.add_parser("pac-extract", help="Extract a NeXAS .pac archive")
    p_pac_ex.add_argument("input", help="Path to input .pac archive")
    p_pac_ex.add_argument("-o", "--output", help="Output extraction directory")
    p_pac_ex.add_argument("-e", "--encoding", help="Filename encoding (cp932/utf-8)")
    p_pac_ex.add_argument("--no-manifest", action="store_true", help="Do not write pac_manifest.json")
    p_pac_ex.add_argument("--verbose", action="store_true", help="Verbose progress logs")
    p_pac_ex.set_defaults(func=handle_pac_extract)

    # 2. pac-build
    p_pac_bd = subparsers.add_parser("pac-build", help="Pack a directory into a NeXAS .pac archive")
    p_pac_bd.add_argument("input", help="Directory of files to pack")
    p_pac_bd.add_argument("-o", "--output", help="Path to generated .pac file")
    p_pac_bd.add_argument(
        "-p", "--platform", choices=["switch", "pc"], default="switch", help="Target platform (default: switch)"
    )
    p_pac_bd.add_argument(
        "-c", "--compression", choices=["zstd", "zlib", "lzss", "none", "auto"], default="zstd", help="Compression algorithm"
    )
    p_pac_bd.add_argument("-l", "--level", type=int, default=3, help="Compression level (default: 3)")
    p_pac_bd.add_argument("-e", "--encoding", help="Filename encoding (utf-8 / cp932)")
    p_pac_bd.add_argument("-m", "--manifest", help="Use pac_manifest.json for file ordering")
    p_pac_bd.add_argument("-a", "--alignment", type=int, default=4, help="Payload byte alignment (default: 4)")
    p_pac_bd.add_argument("--verbose", action="store_true", help="Verbose progress logs")
    p_pac_bd.set_defaults(func=handle_pac_build)

    # 3. script-dump
    p_sc_dump = subparsers.add_parser("script-dump", help="Extract dialogue strings from .bin or .binu8")
    p_sc_dump.add_argument("input", help="Path to compiled script file (.bin / .binu8)")
    p_sc_dump.add_argument("-o", "--output", help="Output file (.json or .txt)")
    p_sc_dump.add_argument("-f", "--format", choices=["json", "txt"], help="Output format")
    p_sc_dump.add_argument("-e", "--encoding", help="Script string encoding (cp932/utf-8)")
    p_sc_dump.set_defaults(func=handle_script_dump)

    # 4. script-convert
    p_sc_conv = subparsers.add_parser(
        "script-convert", help="Directly convert PC script (.bin) to Switch script (.binu8) with offset relocation"
    )
    p_sc_conv.add_argument("input", help="Input script (.bin)")
    p_sc_conv.add_argument("-o", "--output", help="Output script (.binu8)")
    p_sc_conv.add_argument(
        "-t", "--target", choices=["switch", "pc"], default="switch", help="Target format (default: switch)"
    )
    p_sc_conv.add_argument("--src-encoding", help="Source string encoding (default: cp932)")
    p_sc_conv.add_argument("--dst-encoding", help="Target string encoding (default: utf-8)")
    p_sc_conv.add_argument("--wrap", action="store_true", help="Enable smart word wrapping to avoid overflow")
    p_sc_conv.add_argument("--max-width", type=int, default=52, help="Max line halfwidth units (default: 52)")
    p_sc_conv.set_defaults(func=handle_script_convert)

    # 5. script-compile
    p_sc_comp = subparsers.add_parser(
        "script-compile", help="Recompile translated JSON/TXT into a new .binu8 with recalculated pointers"
    )
    p_sc_comp.add_argument("-s", "--template", required=True, help="Original template script (.bin / .binu8)")
    p_sc_comp.add_argument("-t", "--translation", required=True, help="Translated text file (.json / .txt)")
    p_sc_comp.add_argument("-o", "--output", required=True, help="Output recompiled script file (.binu8)")
    p_sc_comp.add_argument(
        "-p", "--platform", choices=["switch", "pc"], default="switch", help="Target platform (default: switch)"
    )
    p_sc_comp.add_argument("--wrap", action="store_true", help="Auto-wrap translated text")
    p_sc_comp.add_argument("--max-width", type=int, default=52, help="Max line halfwidth units (default: 52)")
    p_sc_comp.set_defaults(func=handle_script_compile)

    # 6. script-wrap (Phase 2)
    p_sc_wrap = subparsers.add_parser(
        "script-wrap", help="Intelligently wrap lines in JSON, TXT, or script binaries to prevent text box overflow"
    )
    p_sc_wrap.add_argument("input", help="Path to text or script file (.json, .txt, .bin, .binu8)")
    p_sc_wrap.add_argument("-o", "--output", help="Path to output wrapped file")
    p_sc_wrap.add_argument("-w", "--max-width", type=int, default=52, help="Max line width in halfwidth units (default: 52)")
    p_sc_wrap.add_argument("-l", "--max-lines", type=int, default=3, help="Max lines per box (default: 3)")
    p_sc_wrap.add_argument("-e", "--encoding", help="Encoding for binary scripts")
    p_sc_wrap.add_argument("-t", "--target", choices=["switch", "pc"], default="switch", help="Target format if input is binary")
    p_sc_wrap.set_defaults(func=handle_script_wrap)

    # 7. scene-convert (Phase 2)
    p_scene_conv = subparsers.add_parser(
        "scene-convert", help="Convert .grp / .map coordinates for Switch screen resolutions"
    )
    p_scene_conv.add_argument("input", help="Path to .grp or .map file")
    p_scene_conv.add_argument("-o", "--output", help="Path to converted output file")
    p_scene_conv.add_argument("-s", "--src-res", default="800x600", help="Source resolution (default: 800x600)")
    p_scene_conv.add_argument("-d", "--dst-res", default="1280x720", help="Target resolution (default: 1280x720)")
    p_scene_conv.add_argument(
        "-m", "--mode", choices=["letterbox", "stretch", "crop"], default="letterbox", help="Scaling mode"
    )
    p_scene_conv.add_argument("-e", "--encoding", help="String encoding for names (default: utf-8)")
    p_scene_conv.add_argument("--dump-json", action="store_true", help="Also export converted structure to JSON")
    p_scene_conv.set_defaults(func=handle_scene_convert)

    # 8. romfs-pack (Phase 2)
    p_romfs = subparsers.add_parser(
        "romfs-pack", help="Build Atmosphere LayeredFS patch directory tree for Nintendo Switch"
    )
    p_romfs.add_argument(
        "-t", "--title-id", required=True, help="Switch Title ID (or preset: aquarium, dc4fd, aikiss)"
    )
    p_romfs.add_argument("-o", "--output", required=True, help="Output root directory for Atmosphere patch")
    p_romfs.add_argument("--pac", nargs="*", help="PAC archive files to place in romfs/")
    p_romfs.add_argument("--scripts", help="Directory of .binu8 script files to place in romfs/Script/")
    p_romfs.add_argument("--scenes", help="Directory of .grp/.map scene files to place in romfs/Scene/")
    p_romfs.add_argument("--no-zip", action="store_true", help="Do not create deployable .zip archive")
    p_romfs.add_argument("--mod-name", default="NeXAS_Switch_Mod", help="Mod display name")
    p_romfs.set_defaults(func=handle_romfs_pack)

    # 9. media-convert (Phase 3)
    p_media = subparsers.add_parser(
        "media-convert", help="Batch convert & optimize images and audio for Nintendo Switch"
    )
    p_media.add_argument("input", help="Path to media file or folder")
    p_media.add_argument("-o", "--output", required=True, help="Output destination folder")
    p_media.add_argument(
        "--type", choices=["image", "audio", "all"], default="all", help="Media type to process (default: all)"
    )
    p_media.add_argument(
        "--alignment", type=int, default=16, help="Image texture boundary alignment in pixels (default: 16)"
    )
    p_media.add_argument(
        "--max-res", default="1920x1080", help="Max resolution constraint (default: 1920x1080)"
    )
    p_media.add_argument(
        "--premultiply-alpha", action="store_true", help="Apply premultiplied alpha channel transformation"
    )
    p_media.add_argument(
        "--sample-rate", type=int, default=48000, help="Target audio sample rate in Hz (default: 48000)"
    )
    p_media.add_argument(
        "--audio-format", choices=["wav", "ogg"], default="wav", help="Target audio container format (default: wav)"
    )
    p_media.add_argument("--verbose", action="store_true", help="Verbose progress logs")
    p_media.set_defaults(func=handle_media_convert)

    # 10. emu-sync (Phase 3)
    p_emu = subparsers.add_parser(
        "emu-sync", help="Sync LayeredFS RomFS patch to local Ryujinx or Yuzu emulator"
    )
    p_emu.add_argument(
        "-t", "--title-id", required=True, help="Game Title ID (or preset keyword, e.g. aquarium)"
    )
    p_emu.add_argument(
        "-p", "--patch-dir", required=True, help="Source patch folder (containing romfs/ or atmosphere/)"
    )
    p_emu.add_argument(
        "-e", "--emulator", choices=["auto", "ryujinx", "yuzu", "all"], default="auto", help="Target emulator"
    )
    p_emu.add_argument(
        "--mod-name", default="NeXAS_Switch_Patch", help="Mod directory display name"
    )
    p_emu.add_argument("--no-clean", action="store_true", help="Do not wipe previous mod files before sync")
    p_emu.add_argument("--dry-run", action="store_true", help="Simulate synchronization without copying files")
    p_emu.set_defaults(func=handle_emu_sync)

    # 11. pipeline (Phase 4)
    p_pipe = subparsers.add_parser(
        "pipeline", help="Run end-to-end automated porting pipeline (Unpack -> Script -> Scene -> Media -> RomFS -> Zip -> Sync)"
    )
    p_pipe.add_argument("-c", "--config", help="Path to project.json configuration file")
    p_pipe.add_argument("-i", "--input", help="Source PC game or asset directory")
    p_pipe.add_argument("-o", "--output", help="Destination RomFS patch directory")
    p_pipe.add_argument("-t", "--title-id", help="Switch Title ID (or preset keyword, default: aquarium)")
    p_pipe.add_argument("-m", "--mod-name", help="Mod display name")
    p_pipe.add_argument("--src-res", default="800x600", help="Source PC resolution (default: 800x600)")
    p_pipe.add_argument("--dst-res", default="1280x720", help="Target Switch resolution (default: 1280x720)")
    p_pipe.add_argument(
        "--scaling-mode", choices=["letterbox", "stretch", "crop"], default="letterbox", help="Viewport scaling mode"
    )
    p_pipe.add_argument("--no-wrap", action="store_true", help="Disable smart word-wrapping")
    p_pipe.add_argument("--alignment", type=int, default=16, help="Texture alignment in bytes (default: 16)")
    p_pipe.add_argument("--sample-rate", type=int, default=48000, help="Audio sample rate (default: 48000)")
    p_pipe.add_argument("--no-zip", action="store_true", help="Do not create final .zip archive")
    p_pipe.add_argument("--sync-emu", action="store_true", help="Auto-sync output to detected local emulators")
    p_pipe.add_argument("--create-nsp", action="store_true", help="Build standalone installable .nsp package")
    p_pipe.add_argument("--main-nso", help="Path to base game main NSO executable (required for --create-nsp)")
    p_pipe.add_argument("--icon", help="Path to custom game icon for NSP/NACP (PNG/JPEG)")
    p_pipe.set_defaults(func=handle_pipeline)

    # 12. nsp-build
    p_nsp = subparsers.add_parser(
        "nsp-build", help="Build standalone installable Nintendo Switch NSP package (.nsp)"
    )
    p_nsp.add_argument("--romfs", required=True, help="Path to RomFS directory")
    p_nsp.add_argument("--main", required=True, help="Path to main NSO executable")
    p_nsp.add_argument("-t", "--title-id", default="0100D11018A7E000", help="Switch Title ID (16 hex chars, default: Aquarium)")
    p_nsp.add_argument("-n", "--title-name", default="NeXAS Game", help="Game display name (UTF-8)")
    p_nsp.add_argument("-a", "--author", default="NeXAS", help="Author/Publisher name (UTF-8)")
    p_nsp.add_argument("--ver", default="1.0.0", help="Display version (ASCII, default: 1.0.0)")
    p_nsp.add_argument("--icon", help="Path to custom 256x256 icon (PNG/JPEG)")
    p_nsp.add_argument("-o", "--output", default="./dist", help="Output directory for generated .nsp")
    p_nsp.add_argument("--hacbrewpack", help="Explicit path to hacbrewpack.exe")
    p_nsp.add_argument("--keys", help="Explicit path to prod.keys")
    p_nsp.set_defaults(func=handle_nsp_build)

    # 13. gui (Phase 4)
    p_gui = subparsers.add_parser(
        "gui", help="Launch local web-based visual management console (http://127.0.0.1:8899)"
    )
    p_gui.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    p_gui.add_argument("--port", type=int, default=8899, help="Port to bind (default: 8899)")
    p_gui.add_argument("--no-browser", action="store_true", help="Do not auto-open default web browser")
    p_gui.set_defaults(func=handle_gui)

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) < 2:
        parser.print_help()
        return 0
    args = parser.parse_args()
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
