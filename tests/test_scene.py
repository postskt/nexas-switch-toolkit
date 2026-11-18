# -*- coding: utf-8 -*-
"""
Unit Tests for Scene & Sprite Mapping Converter (.grp / .map)
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from nexas_toolkit.scene.grp_map_converter import (
    GrpEntry,
    GrpFile,
    GrpMapConverter,
    MapFile,
    MapHotspot,
    MapNode,
    ResolutionScaler,
    ScalingMode,
)


class TestSceneModule(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_scene_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_grp_serialization_roundtrip(self):
        """Test creating, serializing, and parsing a .grp file."""
        entries = [
            GrpEntry(id=1, name="bg_aquarium_01", x=0, y=0, width=800, height=600, origin_x=0, origin_y=0),
            GrpEntry(id=2, name="chara_aqua_stand", x=200, y=100, width=400, height=500, origin_x=200, origin_y=250),
        ]
        original_grp = GrpFile(canvas_width=800, canvas_height=600, entries=entries, version=1)

        raw_bytes = GrpMapConverter.serialize_grp(original_grp, encoding="utf-8")
        self.assertGreater(len(raw_bytes), 0)

        # Parse back
        parsed_grp = GrpMapConverter.parse_grp(raw_bytes, encoding="utf-8")
        self.assertEqual(parsed_grp.canvas_width, 800)
        self.assertEqual(parsed_grp.canvas_height, 600)
        self.assertEqual(len(parsed_grp.entries), 2)
        self.assertEqual(parsed_grp.entries[0].name, "bg_aquarium_01")
        self.assertEqual(parsed_grp.entries[1].width, 400)

    def test_map_serialization_roundtrip(self):
        """Test creating, serializing, and parsing a .map file."""
        hotspots = [
            MapHotspot(name="door_exit", x=50, y=50, width=100, height=200, target_scene_or_script="room02.binu8"),
        ]
        nodes = [
            MapNode(
                scene_id=101,
                scene_name="Room_Main",
                bg_grp_name="bg_room_01",
                script_name="scene_101.binu8",
                camera_x=0,
                camera_y=0,
                hotspots=hotspots,
            )
        ]
        original_map = MapFile(canvas_width=800, canvas_height=600, nodes=nodes, version=1)

        raw_bytes = GrpMapConverter.serialize_map(original_map, encoding="utf-8")
        self.assertGreater(len(raw_bytes), 0)

        parsed_map = GrpMapConverter.parse_map(raw_bytes, encoding="utf-8")
        self.assertEqual(parsed_map.canvas_width, 800)
        self.assertEqual(len(parsed_map.nodes), 1)
        self.assertEqual(parsed_map.nodes[0].scene_name, "Room_Main")
        self.assertEqual(len(parsed_map.nodes[0].hotspots), 1)
        self.assertEqual(parsed_map.nodes[0].hotspots[0].name, "door_exit")

    def test_resolution_scaling_letterbox(self):
        """Test letterbox coordinate scaling from 800x600 to Switch 1280x720."""
        # 800x600 -> 1280x720:
        # scale_w = 1280/800 = 1.6, scale_h = 720/600 = 1.2
        # Letterbox scale = min(1.6, 1.2) = 1.2
        # scaled_w = 800 * 1.2 = 960, scaled_h = 600 * 1.2 = 720
        # offset_x = (1280 - 960) / 2 = 160, offset_y = 0
        scaler = ResolutionScaler(
            src_resolution=(800, 600),
            dst_resolution=(1280, 720),
            mode=ScalingMode.LETTERBOX,
        )
        self.assertAlmostEqual(scaler.scale_x, 1.2)
        self.assertAlmostEqual(scaler.offset_x, 160.0)
        self.assertAlmostEqual(scaler.offset_y, 0.0)

        # Transform point (0, 0) -> (160, 0)
        nx, ny = scaler.transform_point(0, 0)
        self.assertEqual(nx, 160)
        self.assertEqual(ny, 0)

        # Transform rect (100, 100, 200, 200)
        # x' = 100 * 1.2 + 160 = 280, y' = 100 * 1.2 + 0 = 120
        # w' = 200 * 1.2 = 240, h' = 200 * 1.2 = 240
        rx, ry, rw, rh = scaler.transform_rect(100, 100, 200, 200)
        self.assertEqual(rx, 280)
        self.assertEqual(ry, 120)
        self.assertEqual(rw, 240)
        self.assertEqual(rh, 240)

    def test_grp_scaling_pipeline(self):
        """Test end-to-end Grp resolution scaling."""
        entries = [
            GrpEntry(id=1, name="sprite_01", x=100, y=100, width=200, height=200, origin_x=50, origin_y=50),
        ]
        grp = GrpFile(canvas_width=800, canvas_height=600, entries=entries)

        scaler = ResolutionScaler(src_resolution=(800, 600), dst_resolution=(1280, 720), mode=ScalingMode.STRETCH)
        # Stretch scale: sx = 1280/800 = 1.6, sy = 720/600 = 1.2
        scaled_grp = scaler.scale_grp(grp)

        self.assertEqual(scaled_grp.canvas_width, 1280)
        self.assertEqual(scaled_grp.canvas_height, 720)
        self.assertEqual(scaled_grp.entries[0].x, 160)
        self.assertEqual(scaled_grp.entries[0].y, 120)
        self.assertEqual(scaled_grp.entries[0].width, 320)
        self.assertEqual(scaled_grp.entries[0].height, 240)


if __name__ == "__main__":
    unittest.main()
