# -*- coding: utf-8 -*-
"""
End-to-End Pipeline & GUI Tests for nexas-switch-toolkit (Phase 4 Final)
Validates:
1. PipelineConfig serialization and deserialization (JSON config support)
2. PipelineRunner end-to-end asset porting:
   - PAC unpack -> script smart-wrap + UTF8 relocation -> scene scaling ->
     media 16B alignment + 48kHz audio resampling -> Switch PAC repack ->
     RomFS LayeredFS assembly -> Zip generation -> Emulator Sync
3. Loose directory tree ingestion (without PAC archives)
4. CLI pipeline subcommand execution
5. GUI WebServerState and API response mechanics
"""

import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

from PIL import Image

from nexas_toolkit.gui import WebServerState, WebGuiRequestHandler
from nexas_toolkit.pac.pac_builder import PacBuilder
from nexas_toolkit.pipeline import PipelineConfig, PipelineRunner
from nexas_toolkit.scene.grp_map_converter import GrpEntry, GrpFile, GrpMapConverter
from nexas_toolkit.utils.binary_helper import BinaryWriter, encode_string


class TestPipelineModule(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_pipeline_test_")
        self.cwd = Path(__file__).resolve().parent.parent

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_pc_script(self, out_path: Path):
        """Creates a valid binary NeXAS PC script (.bin) with Shift-JIS text and pointers."""
        writer = BinaryWriter()
        writer.write_bytes(b"VER01.00\x00")
        writer.write_u32(1)  # 1 section
        writer.write_u32(0x9999)
        writer.write_u32(1)  # 1 init code
        writer.write_u32(0x01)
        writer.write_u32(0x00)
        writer.write_u32(1)  # 1 code instruction referencing string offset
        str_start_off = (9 + 4 + 4) + (4 + 8) + (4 + 8) + 4
        writer.write_u32(0x20)
        writer.write_u32(str_start_off)
        writer.write_u32(1)  # 1 string count
        # Long Japanese sentence to test both conversion and smart wrapping
        text = "あくありうむへようこそ！これはテスト用の長文テキストです。画面の境界を超えないように折り返されます。"
        writer.write_bytes(encode_string(text, "cp932") + b"\x00")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(writer.get_bytes())

    def _create_mock_grp(self, out_path: Path):
        """Creates a valid .grp scene file with sprite coordinates."""
        grp = GrpFile(
            canvas_width=800,
            canvas_height=600,
            entries=[
                GrpEntry(id=1, name="bg_aquarium", x=100, y=200, width=300, height=400, z_order=0, flags=1),
                GrpEntry(id=2, name="ch_aqua_smile", x=200, y=100, width=400, height=500, z_order=1, flags=0),
            ],
        )
        raw_bytes = GrpMapConverter.serialize_grp(grp)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)

    def _create_mock_image(self, out_path: Path, width: int = 100, height: int = 70):
        """Creates a test RGBA image with non-16-aligned dimensions."""
        img = Image.new("RGBA", (width, height), (100, 150, 200, 200))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), format="PNG")

    def _create_mock_wav(self, out_path: Path, sample_rate: int = 44100, duration_sec: float = 0.05):
        """Creates a valid 44.1kHz 16-bit PCM WAV file."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        num_samples = int(sample_rate * duration_sec)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            raw_frames = bytearray()
            for i in range(num_samples):
                val = int(16000 * (i % 20 - 10) / 10.0)
                raw_frames.extend(struct.pack("<h", val))
            wf.writeframes(bytes(raw_frames))

    def test_pipeline_config_serialization(self):
        """Test PipelineConfig JSON export and import."""
        cfg = PipelineConfig(
            project_name="TestProject",
            title_id="0100D11018A7E000",
            resolution_src=(800, 600),
            resolution_dst=(1280, 720),
            scaling_mode="stretch",
            enable_smart_wrap=True,
            max_line_width=48,
        )
        json_path = Path(self.test_dir) / "project.json"
        cfg.to_json(str(json_path))
        self.assertTrue(json_path.exists())

        loaded_cfg = PipelineConfig.from_json(str(json_path))
        self.assertEqual(loaded_cfg.project_name, "TestProject")
        self.assertEqual(loaded_cfg.title_id, "0100D11018A7E000")
        self.assertEqual(loaded_cfg.resolution_src, (800, 600))
        self.assertEqual(loaded_cfg.resolution_dst, (1280, 720))
        self.assertEqual(loaded_cfg.scaling_mode, "stretch")
        self.assertEqual(loaded_cfg.max_line_width, 48)

    def test_pipeline_full_workflow_with_pac(self):
        """
        Complete end-to-end integration test:
        1. PC project with packed PAC, script, scene, media
        2. Execute PipelineRunner
        3. Verify all 7 stages and outputs
        """
        in_dir = Path(self.test_dir) / "pc_game"
        out_dir = Path(self.test_dir) / "switch_output"
        in_dir.mkdir(parents=True)

        # 1. Build an input data.pac containing images
        pac_src = Path(self.test_dir) / "pac_src"
        pac_src.mkdir(parents=True)
        self._create_mock_image(pac_src / "event_cg01.png", 100, 70)
        pac_builder = PacBuilder(target_platform="pc", compression="zlib", encoding="cp932")
        pac_builder.build_from_directory(str(pac_src), str(in_dir / "data.pac"))

        # 2. Add loose script, scene and audio to PC project
        self._create_mock_pc_script(in_dir / "Script" / "001.bin")
        self._create_mock_grp(in_dir / "Scene" / "scene01.grp")
        self._create_mock_wav(in_dir / "Bgm" / "bgm01.wav", sample_rate=44100)

        # 3. Configure and execute pipeline
        logs = []
        progress_records = []

        def on_log(msg: str, lvl: str):
            logs.append(f"[{lvl}] {msg}")

        def on_progress(p: float, desc: str):
            progress_records.append((p, desc))

        config = PipelineConfig(
            project_name="Aquarium_Switch_Port",
            title_id="0100D11018A7E000",
            input_dir=str(in_dir),
            output_dir=str(out_dir),
            resolution_src=(800, 600),
            resolution_dst=(1280, 720),
            scaling_mode="stretch",
            enable_smart_wrap=True,
            max_line_width=40,
            image_alignment=16,
            audio_sample_rate=48000,
            repack_pac=True,
            create_zip=True,
            sync_emulator=False,
        )

        runner = PipelineRunner(config, log_callback=on_log, progress_callback=on_progress)
        res = runner.run()

        # 4. Verify pipeline results
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["total_duration_sec"], 0.0)
        self.assertGreaterEqual(len(res["reports"]), 5)
        self.assertTrue(any(p == 1.0 for p, _ in progress_records))

        # Check RomFS outputs
        romfs_dir = out_dir / "atmosphere" / "contents" / "0100D11018A7E000" / "romfs"
        self.assertTrue(romfs_dir.exists())

        # Script converted to .binu8
        binu8_path = romfs_dir / "Script" / "001.binu8"
        self.assertTrue(binu8_path.exists())
        binu8_data = binu8_path.read_bytes()
        # Verify UTF-8 text present in output
        self.assertIn("あくありうむ".encode("utf-8"), binu8_data)

        # Scene .grp converted and scaled (800x600 -> 1280x720 stretch = 1.6x, 1.2x)
        grp_path = romfs_dir / "Scene" / "scene01.grp"
        self.assertTrue(grp_path.exists())
        scaled_grp = GrpMapConverter.parse_grp(grp_path.read_bytes())
        # x=100*1.6=160, y=200*1.2=240, w=300*1.6=480, h=400*1.2=480
        self.assertEqual(scaled_grp.entries[0].x, 160)
        self.assertEqual(scaled_grp.entries[0].y, 240)
        self.assertEqual(scaled_grp.entries[0].width, 480)
        self.assertEqual(scaled_grp.entries[0].height, 480)

        # Repacked PAC present in RomFS
        repacked_pac = romfs_dir / "data.pac"
        self.assertTrue(repacked_pac.exists())

        # LayeredFS Zip archive created
        zip_path = out_dir / "0100D11018A7E000_Atmosphere_LayeredFS.zip"
        self.assertTrue(zip_path.exists())
        self.assertGreater(zip_path.stat().st_size, 0)

        # Intermediate .staging directory cleaned up
        self.assertFalse((out_dir / ".staging").exists())

    def test_pipeline_loose_directory_ingestion(self):
        """Test pipeline when input contains loose assets instead of .pac files."""
        in_dir = Path(self.test_dir) / "loose_game"
        out_dir = Path(self.test_dir) / "loose_out"
        in_dir.mkdir(parents=True)

        self._create_mock_pc_script(in_dir / "test.bin")
        self._create_mock_image(in_dir / "img1.png", 64, 64)

        config = PipelineConfig(
            project_name="LooseTest",
            title_id="0100123456789000",
            input_dir=str(in_dir),
            output_dir=str(out_dir),
            create_zip=False,
            repack_pac=False,
        )
        runner = PipelineRunner(config)
        res = runner.run()

        self.assertEqual(res["status"], "success")
        romfs_dir = out_dir / "atmosphere" / "contents" / "0100123456789000" / "romfs"
        self.assertTrue((romfs_dir / "Script" / "test.binu8").exists())

    def test_pipeline_missing_input_raises_error(self):
        """Test pipeline raises FileNotFoundError on nonexistent input."""
        config = PipelineConfig(
            input_dir=str(Path(self.test_dir) / "nonexistent_dir"),
            output_dir=str(Path(self.test_dir) / "out"),
        )
        runner = PipelineRunner(config)
        with self.assertRaises(FileNotFoundError):
            runner.run()

    def test_cli_pipeline_subcommand(self):
        """Test invoking `pipeline` via CLI subprocess."""
        in_dir = Path(self.test_dir) / "cli_pc"
        out_dir = Path(self.test_dir) / "cli_out"
        in_dir.mkdir(parents=True)
        self._create_mock_pc_script(in_dir / "test_cli.bin")

        cmd = [
            sys.executable, "-m", "nexas_toolkit.cli",
            "pipeline",
            "-i", str(in_dir),
            "-o", str(out_dir),
            "-t", "0100D11018A7E000",
            "-m", "TestCLIProject",
            "--no-zip",
        ]
        res = subprocess.run(cmd, cwd=str(self.cwd), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"CLI pipeline failed: {res.stderr}")
        self.assertTrue((out_dir / "atmosphere" / "contents" / "0100D11018A7E000" / "romfs" / "Script" / "test_cli.binu8").exists())

    def test_gui_server_state(self):
        """Test WebServerState state tracking and reset functions."""
        WebServerState.reset()
        self.assertTrue(WebServerState.is_running)
        self.assertEqual(WebServerState.progress, 0.0)

        WebServerState.add_log("Testing log message", "INFO")
        self.assertEqual(len(WebServerState.logs), 1)
        self.assertEqual(WebServerState.logs[0]["message"], "Testing log message")
        self.assertEqual(WebServerState.logs[0]["level"], "INFO")

        WebServerState.generated_zip = "test.zip"
        self.assertEqual(WebServerState.generated_zip, "test.zip")

    def test_gui_http_api_endpoints(self):
        """Test WebGuiRequestHandler endpoints over actual local socket."""
        from http.server import HTTPServer
        import threading
        import urllib.request

        server = HTTPServer(("127.0.0.1", 0), WebGuiRequestHandler)
        port = server.server_address[1]

        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            # 1. Test GET /
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("NeXAS Switch Toolkit", html)

            # 2. Test GET /api/status
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status") as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("is_running", data)
                self.assertIn("logs", data)

            # 3. Test GET /api/presets
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/presets") as resp:
                self.assertEqual(resp.status, 200)
                presets = json.loads(resp.read().decode("utf-8"))
                self.assertIn("aquarium", presets)
                self.assertEqual(presets["aquarium"]["title_id"], "0100D11018A7E000")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
