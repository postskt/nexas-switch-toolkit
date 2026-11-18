# -*- coding: utf-8 -*-
"""
Unit Tests for Media Asset Processing (Images & Audio)
"""

import math
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import wave

from PIL import Image

from nexas_toolkit.media.audio_converter import AudioConverter
from nexas_toolkit.media.image_converter import ImageConverter


class TestMediaModule(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="nexas_media_test_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_image_dimension_alignment(self):
        """Test padding non-aligned image dimensions to 16-pixel multiples for Tegra X1."""
        converter = ImageConverter(alignment=16, max_resolution=None)
        self.assertEqual(converter.align_dimension(100), 112)
        self.assertEqual(converter.align_dimension(16), 16)
        self.assertEqual(converter.align_dimension(17), 32)

        # Create a 100x70 image
        raw_img = Image.new("RGBA", (100, 70), (255, 0, 0, 255))
        processed, was_downscaled, was_padded = converter.process_image(raw_img)

        self.assertFalse(was_downscaled)
        self.assertTrue(was_padded)
        self.assertEqual(processed.size, (112, 80))

    def test_image_downscaling(self):
        """Test downscaling 4K asset to 1080p limit."""
        converter = ImageConverter(alignment=16, max_resolution=(1920, 1080))
        # 3840x2160 image
        raw_img = Image.new("RGBA", (3840, 2160), (0, 255, 0, 255))
        processed, was_downscaled, was_padded = converter.process_image(raw_img)

        self.assertTrue(was_downscaled)
        self.assertLessEqual(processed.size[0], 1920)
        self.assertLessEqual(processed.size[1], 1080)
        # Verify alignment of final output
        self.assertEqual(processed.size[0] % 16, 0)
        self.assertEqual(processed.size[1] % 16, 0)

    def test_premultiplied_alpha(self):
        """Test premultiplied alpha channel transformation."""
        # Semi-transparent pixel: R=200, G=100, B=50, A=128 (~50%)
        # Expected: R'=200*128/255=100, G'=100*128/255=50, B'=50*128/255=25
        img = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
        premult = ImageConverter.apply_premultiplied_alpha(img)
        p = premult.getpixel((0, 0))
        self.assertAlmostEqual(p[0], 100, delta=1)
        self.assertAlmostEqual(p[1], 50, delta=1)
        self.assertAlmostEqual(p[2], 25, delta=1)
        self.assertEqual(p[3], 128)

    def _create_sine_wave(self, path: Path, sample_rate: int = 22050, duration_sec: float = 0.5):
        """Generate a synthetic 16-bit PCM mono sine wave for testing."""
        n_samples = int(sample_rate * duration_sec)
        samples = []
        for i in range(n_samples):
            # 440 Hz tone
            val = int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            samples.append(val)
        raw_bytes = struct.pack(f"<{n_samples}h", *samples)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(raw_bytes)

    def test_audio_probe_and_resampling(self):
        """Test probing 22050 Hz audio and resampling to Switch 48000 Hz."""
        src_wav = Path(self.test_dir) / "test_22050.wav"
        self._create_sine_wave(src_wav, sample_rate=22050, duration_sec=0.2)

        converter = AudioConverter(target_sample_rate=48000, target_format="wav")
        info = converter.probe_wav(str(src_wav))
        self.assertIsNotNone(info)
        self.assertEqual(info.sample_rate, 22050)
        self.assertEqual(info.channels, 1)
        self.assertEqual(info.bit_depth, 16)

        dst_wav = Path(self.test_dir) / "test_48000.wav"
        res = converter.convert_file(str(src_wav), str(dst_wav))

        self.assertEqual(res.status, "success")
        self.assertTrue(dst_wav.exists())

        # Verify resampled audio info
        new_info = converter.probe_wav(str(dst_wav))
        self.assertIsNotNone(new_info)
        self.assertEqual(new_info.sample_rate, 48000)
        self.assertEqual(new_info.bit_depth, 16)
        self.assertAlmostEqual(new_info.duration_sec, 0.2, delta=0.05)


if __name__ == "__main__":
    unittest.main()
