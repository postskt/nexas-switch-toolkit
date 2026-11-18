# -*- coding: utf-8 -*-
"""
NeXAS Audio Asset Batch Processor for Nintendo Switch
Handles:
- Resampling audio to Switch native hardware mixer standard (48000 Hz / 48kHz, 16-bit)
- Channels normalization (Mono / Stereo)
- Automated transcoding using FFmpeg (if available) with pure-Python wave resampler fallback
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Callable, Dict, List, Optional, Tuple
import wave


@dataclass
class AudioInfo:
    channels: int
    sample_rate: int
    bit_depth: int
    duration_sec: float
    format: str


@dataclass
class AudioConvertResult:
    source_path: str
    target_path: str
    orig_info: Optional[AudioInfo]
    new_info: Optional[AudioInfo]
    method_used: str  # 'ffmpeg' or 'python_wave'
    status: str = "success"
    error_message: str = ""


class AudioConverter:
    """
    Audio converter ensuring compatibility with Nintendo Switch hardware audio mixer (48000 Hz).
    """

    SUPPORTED_EXTENSIONS = {".wav", ".ogg", ".mp3", ".flac", ".aac", ".wma"}

    def __init__(
        self,
        target_sample_rate: int = 48000,
        target_format: str = "wav",
        channels: Optional[int] = None,  # None means preserve
        ffmpeg_bin: Optional[str] = None,
    ):
        self.target_sample_rate = target_sample_rate
        self.target_format = target_format.lower()
        self.channels = channels
        self.ffmpeg_path = ffmpeg_bin or self._find_ffmpeg()

    def _find_ffmpeg(self) -> Optional[str]:
        """Locate ffmpeg binary in environment PATH."""
        found = shutil.which("ffmpeg")
        if found:
            return found
        # Check common Windows paths
        common_paths = [
            r"F:\移植\tools\bin\ffmpeg.cmd",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        for p in common_paths:
            if os.path.exists(p):
                return p
        return None

    def probe_wav(self, file_path: str) -> Optional[AudioInfo]:
        """Probe WAV file headers using Python standard wave module."""
        try:
            with wave.open(file_path, "rb") as wf:
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                fr = wf.getframerate()
                nf = wf.getnframes()
                dur = nf / float(fr) if fr > 0 else 0.0
                return AudioInfo(
                    channels=ch,
                    sample_rate=fr,
                    bit_depth=sw * 8,
                    duration_sec=dur,
                    format="WAV",
                )
        except Exception:
            return None

    def resample_wav_pure_python(self, src_path: str, dst_path: str) -> None:
        """
        Pure Python fallback for 16-bit PCM WAV resampling to 48000 Hz via linear interpolation.
        Zero third-party library dependency.
        """
        with wave.open(src_path, "rb") as wf:
            src_channels = wf.getnchannels()
            src_sampwidth = wf.getsampwidth()
            src_framerate = wf.getframerate()
            src_nframes = wf.getnframes()
            raw_frames = wf.readframes(src_nframes)

        target_rate = self.target_sample_rate
        out_channels = self.channels or src_channels

        # If already matching target rate, bit depth, and channels, just copy/rewrite
        if src_framerate == target_rate and src_sampwidth == 2 and out_channels == src_channels:
            shutil.copy2(src_path, dst_path)
            return

        # Decode PCM frames into signed 16-bit integers
        samples: List[int] = []
        if src_sampwidth == 2:
            count = len(raw_frames) // 2
            samples = list(struct.unpack(f"<{count}h", raw_frames))
        elif src_sampwidth == 1:
            # 8-bit unsigned PCM (0-255) -> 16-bit signed (-32768 to 32767)
            for b in raw_frames:
                sample_16 = (b - 128) * 256
                samples.append(sample_16)
        else:
            raise ValueError(f"Unsupported sample width ({src_sampwidth} bytes) for pure-Python fallback")

        # Split into channel streams
        total_src_frames = len(samples) // src_channels
        channel_data: List[List[int]] = [[] for _ in range(src_channels)]
        for i in range(total_src_frames):
            for c in range(src_channels):
                channel_data[c].append(samples[i * src_channels + c])

        # If converting stereo -> mono or mono -> stereo
        if out_channels == 1 and src_channels == 2:
            # Downmix to mono
            mono_data = [int((channel_data[0][i] + channel_data[1][i]) / 2) for i in range(total_src_frames)]
            channel_data = [mono_data]
            src_channels = 1
        elif out_channels == 2 and src_channels == 1:
            # Duplicate mono to stereo
            channel_data = [channel_data[0], channel_data[0]]
            src_channels = 2

        # Resample each channel via linear interpolation
        ratio = target_rate / float(src_framerate)
        target_nframes = int(round(total_src_frames * ratio))
        resampled_channels: List[List[int]] = []

        for c in range(out_channels):
            src_chan = channel_data[c]
            new_chan: List[int] = []
            for t in range(target_nframes):
                src_pos = t / ratio
                idx = int(src_pos)
                frac = src_pos - idx

                if idx + 1 < total_src_frames:
                    val = src_chan[idx] * (1.0 - frac) + src_chan[idx + 1] * frac
                elif idx < total_src_frames:
                    val = src_chan[idx]
                else:
                    val = 0.0

                # Clamp to 16-bit signed range
                clamped = max(-32768, min(32767, int(round(val))))
                new_chan.append(clamped)
            resampled_channels.append(new_chan)

        # Interleave channels
        out_samples: List[int] = []
        for i in range(target_nframes):
            for c in range(out_channels):
                out_samples.append(resampled_channels[c][i])

        out_bytes = struct.pack(f"<{len(out_samples)}h", *out_samples)

        with wave.open(dst_path, "wb") as out_wf:
            out_wf.setnchannels(out_channels)
            out_wf.setsampwidth(2)  # 16-bit
            out_wf.setframerate(target_rate)
            out_wf.writeframes(out_bytes)

    def convert_file(self, src_path: str, dst_path: str) -> AudioConvertResult:
        """Convert an audio file to Switch standard 48000 Hz."""
        src_p = Path(src_path)
        dst_p = Path(dst_path)
        dst_p.parent.mkdir(parents=True, exist_ok=True)

        orig_info = self.probe_wav(str(src_p))

        # 1. If FFmpeg is available, use it for universal multi-format transcoding
        if self.ffmpeg_path:
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(src_p),
                "-ar",
                str(self.target_sample_rate),
            ]
            if self.channels:
                cmd.extend(["-ac", str(self.channels)])

            if self.target_format == "wav":
                cmd.extend(["-c:a", "pcm_s16le"])
            elif self.target_format == "ogg":
                cmd.extend(["-c:a", "libvorbis", "-q:a", "5"])

            cmd.append(str(dst_p))

            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                new_info = self.probe_wav(str(dst_p))
                return AudioConvertResult(
                    source_path=str(src_p),
                    target_path=str(dst_p),
                    orig_info=orig_info,
                    new_info=new_info,
                    method_used="ffmpeg",
                    status="success",
                )

        # 2. Fallback to pure Python WAV resampler if src is WAV
        if src_p.suffix.lower() == ".wav" and self.target_format == "wav":
            try:
                self.resample_wav_pure_python(str(src_p), str(dst_p))
                new_info = self.probe_wav(str(dst_p))
                return AudioConvertResult(
                    source_path=str(src_p),
                    target_path=str(dst_p),
                    orig_info=orig_info,
                    new_info=new_info,
                    method_used="python_wave",
                    status="success",
                )
            except Exception as e:
                return AudioConvertResult(
                    source_path=str(src_p),
                    target_path=str(dst_p),
                    orig_info=orig_info,
                    new_info=None,
                    method_used="python_wave",
                    status="error",
                    error_message=str(e),
                )

        return AudioConvertResult(
            source_path=str(src_p),
            target_path=str(dst_p),
            orig_info=orig_info,
            new_info=None,
            method_used="none",
            status="error",
            error_message="FFmpeg not found and input is not standard uncompressed WAV",
        )

    def process_directory(
        self,
        src_dir: str,
        dst_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[AudioConvertResult]:
        """Recursively process all audio files in directory."""
        src_root = Path(src_dir)
        dst_root = Path(dst_dir)

        audio_files: List[Path] = []
        for root, _, files in os.walk(src_root):
            for f in files:
                if Path(f).suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    audio_files.append(Path(root) / f)

        results: List[AudioConvertResult] = []
        total = len(audio_files)

        target_ext = f".{self.target_format}"
        for idx, af in enumerate(audio_files):
            rel_p = af.relative_to(src_root)
            out_file = dst_root / rel_p.with_suffix(target_ext)

            if progress_callback:
                progress_callback(idx + 1, total, str(rel_p))

            res = self.convert_file(str(af), str(out_file))
            results.append(res)

        return results
