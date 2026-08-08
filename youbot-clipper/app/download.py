"""Download helpers built on yt-dlp.

Two jobs:
  * download the trending source video to the work dir, and
  * download + cache the configured gameplay video once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yt_dlp

# Prefer <=1080 but never hard-fail on container/codec: take the best video of
# any codec + best audio, and let ffmpeg remux to mp4. The trailing fallbacks
# ("bv*+ba", "b") guarantee we pick something whenever any format exists.
_FORMAT = (
    "bestvideo[height<=1080]+bestaudio/best[height<=1080]/"
    "bestvideo+bestaudio/best/bv*+ba/b"
)


def _download(url: str, out_path: Path, with_audio: bool = True) -> Optional[Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out_path.with_suffix("")) + ".%(ext)s",
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    # yt-dlp appends the real extension; find what it produced.
    for candidate in out_path.parent.glob(out_path.stem + ".*"):
        if candidate.suffix.lower() in (".mp4", ".mkv", ".webm"):
            return candidate
    return None


def download_source(video_id: str, work_dir: Path) -> Optional[Path]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    return _download(url, work_dir / f"source_{video_id}", with_audio=True)


def ensure_gameplay(url: str, gameplay_dir: Path) -> Optional[Path]:
    """Download the gameplay video once and cache it. Reuses the cache on later runs."""
    cached = sorted(gameplay_dir.glob("gameplay.*"))
    for c in cached:
        if c.suffix.lower() in (".mp4", ".mkv", ".webm") and c.stat().st_size > 0:
            return c
    return _download(url, gameplay_dir / "gameplay", with_audio=False)
