# -*- coding: utf-8 -*-
"""
NeXAS Universal Cross-Platform Porting Pipeline (PC -> Nintendo Switch)
Coordinates the complete end-to-end asset porting workflow:
1. Scan & Unpack PAC Archives
2. Script Processing (Smart Word-Wrap + SJIS->UTF8 + Pointer Relocation)
3. Scene & Sprite Viewport Scaling (.grp / .map)
4. Media Processing (Image 16B Alignment + Downscale + Audio 48kHz Resampling)
5. Repack Switch PAC Archives (Zstd + UTF-8)
6. Atmosphère LayeredFS RomFS Packaging + Zip Export
7. Local Emulator Sync (Ryujinx / Yuzu)
"""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .media.audio_converter import AudioConverter
from .media.image_converter import ImageConverter
from .pac.pac_builder import PacBuilder
from .pac.pac_extractor import PacExtractor
from .patch.emulator_sync import EmulatorSync
from .patch.romfs_builder import RomFsBuilder
from .patch.nsp_builder import NspBuilder, NspBuildResult
from .scene.grp_map_converter import GrpMapConverter, ResolutionScaler, ScalingMode
from .script.script_converter import ScriptConverter
from .script.script_parser import ScriptParser
from .script.smart_wrapper import SmartWrapper


@dataclass
class PipelineConfig:
    """Full configuration for a NeXAS Switch porting project."""
    project_name: str = "NeXAS_Switch_Port"
    title_id: str = "0100D11018A7E000"  # Default Aquarium
    input_dir: str = ""
    output_dir: str = ""
    target_platform: str = "switch"
    resolution_src: Tuple[int, int] = (800, 600)
    resolution_dst: Tuple[int, int] = (1280, 720)
    scaling_mode: str = "letterbox"
    enable_smart_wrap: bool = True
    max_line_width: int = 52
    max_lines_per_box: int = 3
    image_alignment: int = 16
    image_max_res: Tuple[int, int] = (1920, 1080)
    premultiply_alpha: bool = True
    audio_sample_rate: int = 48000
    audio_format: str = "wav"
    repack_pac: bool = True
    create_zip: bool = True
    sync_emulator: bool = False
    emulator_target: str = "auto"
    create_nsp: bool = False
    main_nso_path: str = ""
    title_name: str = "NeXAS Game"
    author: str = "NeXAS"
    version: str = "1.0.0"
    icon_path: str = ""

    @classmethod
    def from_json(cls, json_path: str) -> "PipelineConfig":
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Normalize tuples
        if "resolution_src" in data:
            data["resolution_src"] = tuple(data["resolution_src"])
        if "resolution_dst" in data:
            data["resolution_dst"] = tuple(data["resolution_dst"])
        if "image_max_res" in data and data["image_max_res"]:
            data["image_max_res"] = tuple(data["image_max_res"])
        return cls(**data)

    def to_json(self, json_path: str) -> None:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)


@dataclass
class PipelineStageReport:
    stage_name: str
    duration_sec: float
    items_processed: int
    status: str = "success"
    details: str = ""


class PipelineRunner:
    """
    Executes the comprehensive automated NeXAS cross-platform porting pipeline.
    """

    def __init__(
        self,
        config: PipelineConfig,
        log_callback: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ):
        self.config = config
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.reports: List[PipelineStageReport] = []
        self.is_aborted = False

    def log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)
        else:
            print(f"[{level}] {message}")

    def update_progress(self, progress: float, stage_desc: str) -> None:
        if self.progress_callback:
            self.progress_callback(min(1.0, max(0.0, progress)), stage_desc)

    def run(self) -> Dict[str, Any]:
        """Run all pipeline stages sequentially."""
        total_start = time.perf_counter()
        in_p = Path(self.config.input_dir)
        out_p = Path(self.config.output_dir)

        if not in_p.exists():
            raise FileNotFoundError(f"Input directory not found: {in_p}")

        out_p.mkdir(parents=True, exist_ok=True)
        work_staging = out_p / ".staging"
        if work_staging.exists():
            shutil.rmtree(work_staging, ignore_errors=True)
        work_staging.mkdir(parents=True, exist_ok=True)

        self.log(f"=== Starting NeXAS Switch Porting Pipeline: {self.config.project_name} ===")
        self.log(f"Target Title ID: {self.config.title_id} | Platform: {self.config.target_platform.upper()}")

        try:
            # ----------------------------------------------------
            # Stage 1: Asset Ingestion & PAC Extraction (0% - 15%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.05, "Stage 1: Scanning & Ingesting Assets")
            unpacked_dir = work_staging / "unpacked"
            unpacked_dir.mkdir(parents=True, exist_ok=True)
            extracted_pac_count = 0

            # Find all PAC files in input directory
            pac_files = []
            for root, _, files in os.walk(in_p):
                for f in files:
                    if f.lower().endswith(".pac"):
                        pac_files.append(Path(root) / f)

            # Ingest all loose asset items (directories and files) that are not .pac archives
            for item in in_p.iterdir():
                if item.is_dir():
                    shutil.copytree(item, unpacked_dir / item.name, dirs_exist_ok=True)
                elif item.is_file() and not item.name.lower().endswith(".pac"):
                    shutil.copy2(item, unpacked_dir / item.name)

            if pac_files:
                for pac in pac_files:
                    self.log(f"Unpacking PAC Archive: {pac.name}...")
                    extractor = PacExtractor(str(pac))
                    extractor.extract_all(str(unpacked_dir / pac.stem))
                    extracted_pac_count += 1
            else:
                self.log("No .pac archives found. Ingested loose asset directory tree directly.")

            self.reports.append(
                PipelineStageReport(
                    stage_name="PAC Extraction / Ingestion",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=extracted_pac_count if pac_files else 1,
                    details=f"Extracted {extracted_pac_count} PACs" if pac_files else "Direct folder ingested",
                )
            )

            # ----------------------------------------------------
            # Stage 2: Script Processing (15% - 40%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.20, "Stage 2: Script Relocation & Smart Wrapping")
            scripts_converted_dir = work_staging / "converted_scripts"
            scripts_converted_dir.mkdir(parents=True, exist_ok=True)

            script_files = []
            for root, _, files in os.walk(unpacked_dir):
                for f in files:
                    if f.lower().endswith(".bin") and not f.lower().endswith(".binu8"):
                        script_files.append(Path(root) / f)

            wrapper = SmartWrapper(
                max_width=self.config.max_line_width,
                max_lines_per_box=self.config.max_lines_per_box,
            ) if self.config.enable_smart_wrap else None

            converter = ScriptConverter()
            for sc_path in script_files:
                self.log(f"Processing Script: {sc_path.name}")
                with open(sc_path, "rb") as f:
                    raw_data = f.read()

                try:
                    parser = ScriptParser(encoding="cp932")
                    script_obj = parser.parse(raw_data)

                    # Optional smart wrap
                    if wrapper:
                        script_obj = wrapper.wrap_script(script_obj)

                    # Relocate and convert to Switch .binu8
                    _, u8_bytes = converter.convert_pc_to_switch(script_obj, target_encoding="utf-8")
                    out_u8 = scripts_converted_dir / sc_path.with_suffix(".binu8").name
                    out_u8.write_bytes(u8_bytes)
                except Exception as e:
                    self.log(f"Warning: Script '{sc_path.name}' parsed as non-standard script: {e}", "WARN")

            self.reports.append(
                PipelineStageReport(
                    stage_name="Script Relocation & Encoding",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=len(script_files),
                    details=f"Converted {len(script_files)} scripts to .binu8",
                )
            )

            # ----------------------------------------------------
            # Stage 3: Scene & Sprite Viewport Scaling (40% - 55%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.45, "Stage 3: Scene & Sprite Scaling (.grp/.map)")
            scenes_converted_dir = work_staging / "converted_scenes"
            scenes_converted_dir.mkdir(parents=True, exist_ok=True)

            scaler = ResolutionScaler(
                src_resolution=self.config.resolution_src,
                dst_resolution=self.config.resolution_dst,
                mode=ScalingMode(self.config.scaling_mode),
            )

            scene_count = 0
            for root, _, files in os.walk(unpacked_dir):
                for f in files:
                    fp = Path(root) / f
                    if f.lower().endswith(".grp"):
                        try:
                            grp = GrpMapConverter.parse_grp(fp.read_bytes())
                            scaled_grp = scaler.scale_grp(grp)
                            out_grp = scenes_converted_dir / f
                            out_grp.write_bytes(GrpMapConverter.serialize_grp(scaled_grp))
                            scene_count += 1
                        except Exception as e:
                            self.log(f"Failed to scale .grp {f}: {e}", "WARN")
                    elif f.lower().endswith(".map"):
                        try:
                            mp = GrpMapConverter.parse_map(fp.read_bytes())
                            scaled_map = scaler.scale_map(mp)
                            out_map = scenes_converted_dir / f
                            out_map.write_bytes(GrpMapConverter.serialize_map(scaled_map))
                            scene_count += 1
                        except Exception as e:
                            self.log(f"Failed to scale .map {f}: {e}", "WARN")

            self.reports.append(
                PipelineStageReport(
                    stage_name="Scene/Sprite Viewport Adaptation",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=scene_count,
                    details=f"Scaled {scene_count} scene mapping files",
                )
            )

            # ----------------------------------------------------
            # Stage 4: Media Asset Pipeline (55% - 75%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.60, "Stage 4: Media Optimization (Image & Audio)")
            media_converted_dir = work_staging / "converted_media"
            media_converted_dir.mkdir(parents=True, exist_ok=True)

            img_conv = ImageConverter(
                alignment=self.config.image_alignment,
                max_resolution=self.config.image_max_res,
                premultiply_alpha=self.config.premultiply_alpha,
                target_format="PNG",
            )
            aud_conv = AudioConverter(
                target_sample_rate=self.config.audio_sample_rate,
                target_format=self.config.audio_format,
            )

            img_results = img_conv.process_directory(str(unpacked_dir), str(media_converted_dir / "Image"))
            aud_results = aud_conv.process_directory(str(unpacked_dir), str(media_converted_dir / "Audio"))

            self.reports.append(
                PipelineStageReport(
                    stage_name="Media Asset Pipeline",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=len(img_results) + len(aud_results),
                    details=f"Optimized {len(img_results)} images, {len(aud_results)} audio tracks",
                )
            )

            # ----------------------------------------------------
            # Stage 5: Repack Switch PAC Archives (75% - 85%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.80, "Stage 5: Repacking Switch PAC Archives")
            repacked_pac_dir = work_staging / "repacked_pacs"
            repacked_pac_dir.mkdir(parents=True, exist_ok=True)
            repacked_pacs: List[str] = []

            if self.config.repack_pac:
                # Pack converted media & assets if pacs existed
                pac_builder = PacBuilder(
                    target_platform="switch",
                    compression="zstd",
                    encoding="utf-8",
                    alignment=4,
                )
                # If there are converted images, bundle them into data.pac
                if (media_converted_dir / "Image").is_dir() and any((media_converted_dir / "Image").iterdir()):
                    out_pac = repacked_pac_dir / "data.pac"
                    pac_builder.build_from_directory(str(media_converted_dir / "Image"), str(out_pac))
                    repacked_pacs.append(str(out_pac))
                    self.log(f"Repacked Switch PAC: {out_pac.name}")

            self.reports.append(
                PipelineStageReport(
                    stage_name="Switch PAC Repackaging",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=len(repacked_pacs),
                    details=f"Repacked {len(repacked_pacs)} PAC archives",
                )
            )

            # ----------------------------------------------------
            # Stage 6: LayeredFS RomFS Packaging (85% - 95%)
            # ----------------------------------------------------
            t0 = time.perf_counter()
            self.update_progress(0.90, "Stage 6: Atmosphère LayeredFS Patch Assembly")
            romfs_builder = RomFsBuilder(
                title_id=self.config.title_id,
                mod_name=self.config.project_name,
            )

            patch_results = romfs_builder.build_patch_tree(
                output_dir=str(out_p),
                pac_files=repacked_pacs,
                scripts_dir=str(scripts_converted_dir),
                scenes_dir=str(scenes_converted_dir),
                create_zip=self.config.create_zip,
            )

            self.reports.append(
                PipelineStageReport(
                    stage_name="Atmosphere RomFS Assembly",
                    duration_sec=time.perf_counter() - t0,
                    items_processed=1,
                    details=f"Created LayeredFS tree at {patch_results['romfs_dir']}",
                )
            )

            # ----------------------------------------------------
            # Stage 7: Local Emulator Synchronization (95% - 100%)
            # ----------------------------------------------------
            sync_results_list = []
            if self.config.sync_emulator:
                t0 = time.perf_counter()
                self.update_progress(0.98, "Stage 7: Synchronizing with Switch Emulators")
                emu_sync = EmulatorSync()
                try:
                    sync_results_list = emu_sync.sync_patch(
                        patch_source_dir=str(out_p),
                        title_id=self.config.title_id,
                        emulator_filter=self.config.emulator_target,
                        mod_name=self.config.project_name,
                        clean_before=True,
                    )
                    for sr in sync_results_list:
                        self.log(f"Synced to {sr.emulator_name}: {sr.copied_files_count} files")
                except Exception as e:
                    self.log(f"Emulator sync notice: {e}", "WARN")

                self.reports.append(
                    PipelineStageReport(
                        stage_name="Emulator Synchronization",
                        duration_sec=time.perf_counter() - t0,
                        items_processed=len(sync_results_list),
                        details=f"Synced to {len(sync_results_list)} emulators",
                    )
                )

            # ----------------------------------------------------
            # Stage 8: Standalone NSP Packaging (Optional)
            # ----------------------------------------------------
            nsp_result_dict = {}
            if self.config.create_nsp:
                t0 = time.perf_counter()
                self.update_progress(0.99, "Stage 8: Packaging Standalone Nintendo Switch NSP")
                if not self.config.main_nso_path:
                    self.log("NSP creation requested but 'main_nso_path' is not provided. Skipping NSP step.", "WARN")
                else:
                    try:
                        builder = NspBuilder()
                        nsp_res = builder.build_nsp(
                            romfs_dir=str(patch_results["romfs_dir"]),
                            main_nso_path=self.config.main_nso_path,
                            title_id=self.config.title_id,
                            title_name=self.config.title_name or self.config.project_name,
                            author=self.config.author,
                            version=self.config.version,
                            icon_path=self.config.icon_path if self.config.icon_path else None,
                            output_dir=str(out_p),
                            log_callback=lambda msg: self.log(msg, "INFO"),
                        )
                        nsp_result_dict = asdict(nsp_res)
                        self.reports.append(
                            PipelineStageReport(
                                stage_name="NSP Standalone Packaging",
                                duration_sec=time.perf_counter() - t0,
                                items_processed=1,
                                details=f"Built NSP at {nsp_res.nsp_path} (Verified={nsp_res.verified})",
                            )
                        )
                    except Exception as e:
                        self.log(f"NSP Packaging failed: {e}", "ERROR")
                        self.reports.append(
                            PipelineStageReport(
                                stage_name="NSP Standalone Packaging",
                                duration_sec=time.perf_counter() - t0,
                                items_processed=0,
                                status="error",
                                details=str(e),
                            )
                        )

            # Finish up
            total_time = time.perf_counter() - total_start
            self.update_progress(1.0, "Pipeline Execution Completed Successfully!")
            self.log(f"=== Pipeline Completed in {total_time:.2f}s ===", "SUCCESS")

            return {
                "status": "success",
                "total_duration_sec": total_time,
                "reports": [asdict(r) for r in self.reports],
                "patch_results": {k: str(v) for k, v in patch_results.items()},
                "sync_results": [asdict(s) for s in sync_results_list],
                "nsp_results": nsp_result_dict,
            }

        finally:
            # Clean intermediate staging folder
            shutil.rmtree(work_staging, ignore_errors=True)
