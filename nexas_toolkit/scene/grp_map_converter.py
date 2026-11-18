# -*- coding: utf-8 -*-
"""
NeXAS Scene & Sprite Mapping Converter (.grp / .map)
Parses sprite layout definitions (.grp) and scene navigation maps (.map),
providing resolution adaptation (e.g. PC 800x600 -> Switch 1280x720 / 1920x1080).
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..utils.binary_helper import BinaryReader, BinaryWriter, decode_string, encode_string


class ScalingMode(str, Enum):
    LETTERBOX = "letterbox"  # Maintain aspect ratio, center with letterbox/pillarbox
    STRETCH = "stretch"      # Stretch to fill target resolution
    CROP = "crop"            # Zoom to fill and crop overflow


GRP_MAGIC = b"GRP\x00"
MAP_MAGIC = b"MAP\x00"


@dataclass
class GrpEntry:
    """Individual sprite / graphic coordinate entry in .grp file."""
    id: int
    name: str
    x: int
    y: int
    width: int
    height: int
    origin_x: int = 0
    origin_y: int = 0
    alpha: int = 255
    z_order: int = 0
    flags: int = 0


@dataclass
class GrpFile:
    """Represents a .grp graphic coordinate mapping table."""
    canvas_width: int
    canvas_height: int
    entries: List[GrpEntry] = field(default_factory=list)
    version: int = 1


@dataclass
class MapHotspot:
    """Interactive hotspot / clickable zone in a scene."""
    name: str
    x: int
    y: int
    width: int
    height: int
    target_scene_or_script: str


@dataclass
class MapNode:
    """Scene definition node in a .map file."""
    scene_id: int
    scene_name: str
    bg_grp_name: str
    script_name: str
    camera_x: int = 0
    camera_y: int = 0
    hotspots: List[MapHotspot] = field(default_factory=list)


@dataclass
class MapFile:
    """Represents a .map scene flow and camera table."""
    canvas_width: int
    canvas_height: int
    nodes: List[MapNode] = field(default_factory=list)
    version: int = 1


# ==========================================
# Coordinate Scaling Engine
# ==========================================
class ResolutionScaler:
    """
    Transforms 2D coordinates between source (PC) and destination (Switch) viewports.
    """

    def __init__(
        self,
        src_resolution: Tuple[int, int] = (800, 600),
        dst_resolution: Tuple[int, int] = (1280, 720),
        mode: ScalingMode = ScalingMode.LETTERBOX,
    ):
        self.src_w, self.src_h = src_resolution
        self.dst_w, self.dst_h = dst_resolution
        self.mode = mode

        # Compute scaling and offset parameters
        if mode == ScalingMode.STRETCH:
            self.scale_x = self.dst_w / self.src_w
            self.scale_y = self.dst_h / self.src_h
            self.offset_x = 0.0
            self.offset_y = 0.0
        elif mode == ScalingMode.LETTERBOX:
            scale = min(self.dst_w / self.src_w, self.dst_h / self.src_h)
            self.scale_x = scale
            self.scale_y = scale
            self.offset_x = (self.dst_w - self.src_w * scale) / 2.0
            self.offset_y = (self.dst_h - self.src_h * scale) / 2.0
        elif mode == ScalingMode.CROP:
            scale = max(self.dst_w / self.src_w, self.dst_h / self.src_h)
            self.scale_x = scale
            self.scale_y = scale
            self.offset_x = (self.dst_w - self.src_w * scale) / 2.0
            self.offset_y = (self.dst_h - self.src_h * scale) / 2.0
        else:
            raise ValueError(f"Unknown scaling mode: {mode}")

    def transform_point(self, x: int, y: int) -> Tuple[int, int]:
        """Convert a single point coordinate."""
        new_x = int(round(x * self.scale_x + self.offset_x))
        new_y = int(round(y * self.scale_y + self.offset_y))
        return new_x, new_y

    def transform_rect(self, x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
        """Convert a bounding box rectangle."""
        new_x, new_y = self.transform_point(x, y)
        new_w = int(round(w * self.scale_x))
        new_h = int(round(h * self.scale_y))
        return new_x, new_y, new_w, new_h

    def scale_grp(self, grp: GrpFile) -> GrpFile:
        """Apply coordinate scaling across all entries in a GrpFile."""
        scaled_entries: List[GrpEntry] = []
        for e in grp.entries:
            nx, ny, nw, nh = self.transform_rect(e.x, e.y, e.width, e.height)
            nox = int(round(e.origin_x * self.scale_x))
            noy = int(round(e.origin_y * self.scale_y))
            scaled_entries.append(
                GrpEntry(
                    id=e.id,
                    name=e.name,
                    x=nx,
                    y=ny,
                    width=nw,
                    height=nh,
                    origin_x=nox,
                    origin_y=noy,
                    alpha=e.alpha,
                    z_order=e.z_order,
                    flags=e.flags,
                )
            )

        return GrpFile(
            canvas_width=self.dst_w,
            canvas_height=self.dst_h,
            entries=scaled_entries,
            version=grp.version,
        )

    def scale_map(self, map_file: MapFile) -> MapFile:
        """Apply coordinate scaling across all nodes and hotspots in a MapFile."""
        scaled_nodes: List[MapNode] = []
        for n in map_file.nodes:
            cx, cy = self.transform_point(n.camera_x, n.camera_y)
            scaled_hotspots = []
            for hs in n.hotspots:
                hx, hy, hw, hh = self.transform_rect(hs.x, hs.y, hs.width, hs.height)
                scaled_hotspots.append(
                    MapHotspot(
                        name=hs.name,
                        x=hx,
                        y=hy,
                        width=hw,
                        height=hh,
                        target_scene_or_script=hs.target_scene_or_script,
                    )
                )

            scaled_nodes.append(
                MapNode(
                    scene_id=n.scene_id,
                    scene_name=n.scene_name,
                    bg_grp_name=n.bg_grp_name,
                    script_name=n.script_name,
                    camera_x=cx,
                    camera_y=cy,
                    hotspots=scaled_hotspots,
                )
            )

        return MapFile(
            canvas_width=self.dst_w,
            canvas_height=self.dst_h,
            nodes=scaled_nodes,
            version=map_file.version,
        )


# ==========================================
# Binary Serialization and Parser
# ==========================================
class GrpMapConverter:
    """
    Binary parser and serializer for .grp and .map files.
    """

    @classmethod
    def parse_grp(cls, data: bytes, encoding: str = "cp932") -> GrpFile:
        reader = BinaryReader(data)
        magic = reader.read_bytes(4)
        if magic != GRP_MAGIC:
            raise ValueError(f"Invalid GRP magic: {magic}")

        version = reader.read_u32()
        canvas_w = reader.read_u32()
        canvas_h = reader.read_u32()
        entry_count = reader.read_u32()

        entries = []
        for _ in range(entry_count):
            gid = reader.read_u32()
            name = reader.read_fixed_string(32, encoding=encoding)
            x = reader.read_i32()
            y = reader.read_i32()
            w = reader.read_i32()
            h = reader.read_i32()
            ox = reader.read_i32()
            oy = reader.read_i32()
            alpha = reader.read_u8()
            z_order = reader.read_u8()
            flags = reader.read_u16()
            entries.append(
                GrpEntry(
                    id=gid,
                    name=name,
                    x=x,
                    y=y,
                    width=w,
                    height=h,
                    origin_x=ox,
                    origin_y=oy,
                    alpha=alpha,
                    z_order=z_order,
                    flags=flags,
                )
            )

        return GrpFile(
            canvas_width=canvas_w, canvas_height=canvas_h, entries=entries, version=version
        )

    @classmethod
    def serialize_grp(cls, grp: GrpFile, encoding: str = "utf-8") -> bytes:
        writer = BinaryWriter()
        writer.write_bytes(GRP_MAGIC)
        writer.write_u32(grp.version)
        writer.write_u32(grp.canvas_width)
        writer.write_u32(grp.canvas_height)
        writer.write_u32(len(grp.entries))

        for e in grp.entries:
            writer.write_u32(e.id)
            writer.write_fixed_string(e.name, 32, encoding=encoding)
            writer.write_i32(e.x)
            writer.write_i32(e.y)
            writer.write_i32(e.width)
            writer.write_i32(e.height)
            writer.write_i32(e.origin_x)
            writer.write_i32(e.origin_y)
            writer.write_u8(e.alpha)
            writer.write_u8(e.z_order)
            writer.write_u16(e.flags)

        return writer.get_bytes()

    @classmethod
    def parse_map(cls, data: bytes, encoding: str = "cp932") -> MapFile:
        reader = BinaryReader(data)
        magic = reader.read_bytes(4)
        if magic != MAP_MAGIC:
            raise ValueError(f"Invalid MAP magic: {magic}")

        version = reader.read_u32()
        canvas_w = reader.read_u32()
        canvas_h = reader.read_u32()
        node_count = reader.read_u32()

        nodes = []
        for _ in range(node_count):
            sid = reader.read_u32()
            sname = reader.read_fixed_string(32, encoding=encoding)
            bg_name = reader.read_fixed_string(32, encoding=encoding)
            script_name = reader.read_fixed_string(32, encoding=encoding)
            cx = reader.read_i32()
            cy = reader.read_i32()
            hs_count = reader.read_u32()

            hotspots = []
            for _ in range(hs_count):
                hname = reader.read_fixed_string(32, encoding=encoding)
                hx = reader.read_i32()
                hy = reader.read_i32()
                hw = reader.read_i32()
                hh = reader.read_i32()
                target = reader.read_fixed_string(32, encoding=encoding)
                hotspots.append(
                    MapHotspot(
                        name=hname,
                        x=hx,
                        y=hy,
                        width=hw,
                        height=hh,
                        target_scene_or_script=target,
                    )
                )

            nodes.append(
                MapNode(
                    scene_id=sid,
                    scene_name=sname,
                    bg_grp_name=bg_name,
                    script_name=script_name,
                    camera_x=cx,
                    camera_y=cy,
                    hotspots=hotspots,
                )
            )

        return MapFile(
            canvas_width=canvas_w, canvas_height=canvas_h, nodes=nodes, version=version
        )

    @classmethod
    def serialize_map(cls, map_file: MapFile, encoding: str = "utf-8") -> bytes:
        writer = BinaryWriter()
        writer.write_bytes(MAP_MAGIC)
        writer.write_u32(map_file.version)
        writer.write_u32(map_file.canvas_width)
        writer.write_u32(map_file.canvas_height)
        writer.write_u32(len(map_file.nodes))

        for n in map_file.nodes:
            writer.write_u32(n.scene_id)
            writer.write_fixed_string(n.scene_name, 32, encoding=encoding)
            writer.write_fixed_string(n.bg_grp_name, 32, encoding=encoding)
            writer.write_fixed_string(n.script_name, 32, encoding=encoding)
            writer.write_i32(n.camera_x)
            writer.write_i32(n.camera_y)
            writer.write_u32(len(n.hotspots))

            for hs in n.hotspots:
                writer.write_fixed_string(hs.name, 32, encoding=encoding)
                writer.write_i32(hs.x)
                writer.write_i32(hs.y)
                writer.write_i32(hs.width)
                writer.write_i32(hs.height)
                writer.write_fixed_string(hs.target_scene_or_script, 32, encoding=encoding)

        return writer.get_bytes()

    @classmethod
    def dump_to_json(cls, obj: Any, output_path: str) -> None:
        """Export GrpFile or MapFile to formatted JSON for inspection or modification."""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(asdict(obj), f, indent=2, ensure_ascii=False)
