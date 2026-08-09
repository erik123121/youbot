"""Compose the vertical Short with ffmpeg.

Layout (1080x1920, no black bars): a 50/50 split.
  * Top half: the source clip scaled to *cover* 1080x960 and center-cropped, so
    it fills the top half and reads as the main content.
  * Bottom half: GTA gameplay scaled to *cover* 1080x960 and cropped.

Only the source clip's audio is kept; gameplay is muted.
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path
from typing import Tuple

OUT_W = 1080
OUT_H = 1920
FPS = 30
TOP_FRACTION = 0.5  # clip occupies the top half


def _even(n: int) -> int:
    return n - (n % 2)


def probe_dimensions(path: Path) -> Tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def probe_has_audio(path: Path) -> bool:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return bool(json.loads(out.stdout).get("streams"))
    except (json.JSONDecodeError, ValueError):
        return False


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def random_gameplay_start(gameplay_path: Path, need_seconds: float) -> float:
    total = probe_duration(gameplay_path)
    if total <= need_seconds:
        return 0.0
    return round(random.uniform(0.0, total - need_seconds), 2)


def render(
    source_path: Path,
    gameplay_path: Path,
    out_path: Path,
    clip_start: float,
    clip_duration: float,
) -> Path:
    top_h = _even(int(OUT_H * TOP_FRACTION))
    bottom_h = _even(OUT_H - top_h)

    gp_start = random_gameplay_start(gameplay_path, clip_duration)
    has_audio = probe_has_audio(source_path)

    # Normalize both branches to a common CFR and reset PTS to 0 before stacking.
    # Without fps normalization + setpts, stacking two seeked inputs produces
    # broken timestamps (a file that won't play) and a bloated, slow encode.
    # Both halves scale-to-cover their region and center-crop (no black bars).
    filtergraph = (
        f"[0:v]scale={OUT_W}:{top_h}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{top_h}:(iw-{OUT_W})/2:(ih-{top_h})/2,"
        f"setsar=1,fps={FPS},setpts=PTS-STARTPTS[top];"
        f"[1:v]scale={OUT_W}:{bottom_h}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{bottom_h}:(iw-{OUT_W})/2:(ih-{bottom_h})/2,"
        f"setsar=1,fps={FPS},setpts=PTS-STARTPTS[bot];"
        f"[top][bot]vstack=inputs=2,format=yuv420p[v]"
    )
    if has_audio:
        filtergraph += ";[0:a]aresample=async=1,asetpts=PTS-STARTPTS[a]"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{clip_start}", "-t", f"{clip_duration}", "-i", str(source_path),
        "-ss", f"{gp_start}", "-t", f"{clip_duration}", "-i", str(gameplay_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-t", f"{clip_duration}",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg render failed: " + " | ".join(tail))
    return out_path


def make_thumbnail(video_path: Path, thumb_path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "1", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3", str(thumb_path),
        ],
        check=True, capture_output=True, text=True,
    )
    return thumb_path
