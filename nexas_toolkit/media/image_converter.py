# -*- coding: utf-8 -*-
"""
NeXAS Image Asset Batch Processor for Nintendo Switch
Handles:
- Format conversion (BMP, TGA, PNG -> Switch optimized PNG/RGBA)
- Resolution downsampling (avoiding Switch Tegra X1 4GB unified RAM / VRAM OOM)
- Hardware texture cache boundary alignment (16/32-byte alignment)
- Premultiplied Alpha processing (preventing dark/bright fringe artifacts)
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class ImageConvertResult:
    source_path: str
    target_path: str
    original_size: Tuple[int, int]
    processed_size: Tuple[int, int]
    was_downscaled: bool
    was_padded: bool
    status: str = "success"
    error_message: str = ""


class ImageConverter:
    """
    Image conversion and texture optimization engine for Nintendo Switch.
    """

    SUPPORTED_EXTENSIONS = {".png", ".bmp", ".tga", ".jpg", ".jpeg", ".webp"}

    def __init__(
        self,
        alignment: int = 16,
        max_resolution: Optional[Tuple[int, int]] = (1920, 1080),
        premultiply_alpha: bool = False,
        target_format: str = "PNG",
    ):
        """
        :param alignment: Texture dimension alignment (default: 16 bytes for Tegra X1)
        :param max_resolution: (max_w, max_h) constraint, e.g. (1920, 1080) or (1280, 720)
        :param premultiply_alpha: Whether to apply premultiplied alpha
        :param target_format: Output image format ('PNG', 'WEBP', etc.)
        """
        if not HAS_PIL:
            raise ImportError("Pillow library is required for ImageConverter. Please install pillow.")

        self.alignment = alignment
        self.max_resolution = max_resolution
        self.premultiply_alpha = premultiply_alpha
        self.target_format = target_format.upper()

    def align_dimension(self, val: int) -> int:
        """Align dimension to the nearest multiple of alignment."""
        if self.alignment <= 1:
            return val
        remainder = val % self.alignment
        return val if remainder == 0 else val + (self.alignment - remainder)

    def process_image(self, img: "Image.Image") -> Tuple["Image.Image", bool, bool]:
        """
        Process single PIL Image: downscale if necessary, apply premultiplied alpha, pad to alignment.
        Returns: (processed_image, was_downscaled, was_padded)
        """
        orig_w, orig_h = img.size
        cur_w, cur_h = orig_w, orig_h
        was_downscaled = False
        was_padded = False

        # Ensure RGBA mode for transparency
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # 1. Downscale if exceeds max_resolution
        if self.max_resolution:
            max_w, max_h = self.max_resolution
            # Ensure effective target bound accounts for alignment
            if self.alignment > 1:
                eff_max_w = (max_w // self.alignment) * self.alignment
                eff_max_h = (max_h // self.alignment) * self.alignment
            else:
                eff_max_w, eff_max_h = max_w, max_h

            if cur_w > eff_max_w or cur_h > eff_max_h:
                scale = min(eff_max_w / cur_w, eff_max_h / cur_h)
                new_w = max(1, int(round(cur_w * scale)))
                new_h = max(1, int(round(cur_h * scale)))
                resample_filter = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
                img = img.resize((new_w, new_h), resample_filter)
                cur_w, cur_h = new_w, new_h
                was_downscaled = True

        # 2. Premultiply alpha if requested
        if self.premultiply_alpha:
            img = self.apply_premultiplied_alpha(img)

        # 3. Alignment padding
        aligned_w = self.align_dimension(cur_w)
        aligned_h = self.align_dimension(cur_h)
        if aligned_w != cur_w or aligned_h != cur_h:
            padded_img = Image.new("RGBA", (aligned_w, aligned_h), (0, 0, 0, 0))
            padded_img.paste(img, (0, 0))
            img = padded_img
            was_padded = True

        return img, was_downscaled, was_padded

    @classmethod
    def apply_premultiplied_alpha(cls, img: "Image.Image") -> "Image.Image":
        """
        Pre-multiply RGB channels by the Alpha channel to prevent bilinear filtering fringe.
        R' = R * A / 255
        G' = G * A / 255
        B' = B * A / 255
        """
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        pixels = bytearray(img.tobytes())
        for i in range(0, len(pixels), 4):
            a = pixels[i + 3]
            if a == 0:
                pixels[i] = 0
                pixels[i + 1] = 0
                pixels[i + 2] = 0
            elif a < 255:
                alpha_factor = a / 255.0
                pixels[i] = int(pixels[i] * alpha_factor)
                pixels[i + 1] = int(pixels[i + 1] * alpha_factor)
                pixels[i + 2] = int(pixels[i + 2] * alpha_factor)

        return Image.frombytes("RGBA", img.size, bytes(pixels))

    def convert_file(self, src_path: str, dst_path: str) -> ImageConvertResult:
        """Convert a single image file."""
        src_p = Path(src_path)
        dst_p = Path(dst_path)
        dst_p.parent.mkdir(parents=True, exist_ok=True)

        try:
            with Image.open(src_p) as raw_img:
                orig_size = raw_img.size
                processed, downscaled, padded = self.process_image(raw_img)
                processed.save(dst_p, format=self.target_format)

            return ImageConvertResult(
                source_path=str(src_p),
                target_path=str(dst_p),
                original_size=orig_size,
                processed_size=processed.size,
                was_downscaled=downscaled,
                was_padded=padded,
                status="success",
            )
        except Exception as e:
            return ImageConvertResult(
                source_path=str(src_p),
                target_path=str(dst_p),
                original_size=(0, 0),
                processed_size=(0, 0),
                was_downscaled=False,
                was_padded=False,
                status="error",
                error_message=str(e),
            )

    def process_directory(
        self,
        src_dir: str,
        dst_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[ImageConvertResult]:
        """
        Recursively process all supported images in src_dir and save to dst_dir.
        """
        src_root = Path(src_dir)
        dst_root = Path(dst_dir)

        image_files: List[Path] = []
        for root, _, files in os.walk(src_root):
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    image_files.append(Path(root) / f)

        results: List[ImageConvertResult] = []
        total = len(image_files)

        ext_target = f".{self.target_format.lower()}"
        for idx, img_path in enumerate(image_files):
            rel_path = img_path.relative_to(src_root)
            out_file = dst_root / rel_path.with_suffix(ext_target)

            if progress_callback:
                progress_callback(idx + 1, total, str(rel_path))

            res = self.convert_file(str(img_path), str(out_file))
            results.append(res)

        return results
