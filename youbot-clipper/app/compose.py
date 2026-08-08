"""Compose the vertical Short with ffmpeg.

Layout (1080x1920, no black bars):
  * Top: the source clip at full width (1080) and its natural 16:9-ish height,
    capped so it never eats more than ~60% of the frame (center-cropped if the
    clip is unusually tall).
  * Bottom: GTA gameplay scaled to *cover* the remaining height and cropped —
    so it always fills edge to edge with no letterboxing.

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
TOP_MAX_FRACTION = 0.60  # clip never taller than 60% of the frame


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


def _top_height(clip_w: int, clip_h: int) -> int:
    """Height of the top region: clip at full width, capped, kept even."""
    natural = round(OUT_W * clip_h / clip_w) if clip_w else round(OUT_W * 9 / 16)
    cap = int(OUT_H * TOP_MAX_FRACTION)
    return _even(min(natural, cap))


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
    clip_w, clip_h = probe_dimensions(source_path)
    top_h = _top_height(clip_w, clip_h)
    bottom_h = _even(OUT_H - top_h)

    gp_start = random_gameplay_start(gameplay_path, clip_duration)

    # Top: fill width, then center-crop to the top region height if the clip is
    # taller than the cap. Bottom: scale-to-cover the bottom region and crop.
    filtergraph = (
        f"[0:v]scale={OUT_W}:-2:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{top_h}:(iw-{OUT_W})/2:(ih-{top_h})/2,setsar=1[top];"
        f"[1:v]scale={OUT_W}:{bottom_h}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{bottom_h}:(iw-{OUT_W})/2:(ih-{bottom_h})/2,setsar=1[bot];"
        f"[top][bot]vstack=inputs=2[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{clip_start}", "-t", f"{clip_duration}", "-i", str(source_path),
        "-ss", f"{gp_start}", "-t", f"{clip_duration}", "-i", str(gameplay_path),
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a?",
        "-t", f"{clip_duration}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
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
