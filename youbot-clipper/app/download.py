"""Download helpers built on yt-dlp.

Jobs:
  * download a full source video, or just a time-range segment of one, and
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


def _download(url: str, out_path: Path, extra: Optional[dict] = None) -> Optional[Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": _FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out_path.with_suffix("")) + ".%(ext)s",
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        # Force IPv4: containers often have broken IPv6, which yt-dlp otherwise
        # picks and fails on with "[Errno 101] Network is unreachable".
        "source_address": "0.0.0.0",
        # The android/web player clients avoid most "HTTP 403 Forbidden" blocks
        # that hit the default web client.
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if extra:
        opts.update(extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    # yt-dlp appends the real extension; find what it produced.
    for candidate in out_path.parent.glob(out_path.stem + ".*"):
        if candidate.suffix.lower() in (".mp4", ".mkv", ".webm"):
            return candidate
    return None


def download_source(video_id: str, work_dir: Path) -> Optional[Path]:
    """Download a full source video; None on failure (so we can try the next)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        return _download(url, work_dir / f"source_{video_id}")
    except Exception:
        return None


def download_segment(
    video_id: str, start: float, end: float, out_path: Path
) -> Optional[Path]:
    """Download ONLY the [start, end] time range of a video (exact, re-cut to
    keyframes). Keeps downloads tiny regardless of episode length. None on error.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        from yt_dlp.utils import download_range_func

        extra = {
            "download_ranges": download_range_func(None, [(float(start), float(end))]),
            "force_keyframes_at_cuts": True,
        }
        return _download(url, out_path, extra)
    except Exception:
        return None


def ensure_gameplay(url: str, gameplay_dir: Path) -> Optional[Path]:
    """Download the gameplay video once and cache it. Reuses the cache on later runs."""
    cached = sorted(gameplay_dir.glob("gameplay.*"))
    for c in cached:
        if c.suffix.lower() in (".mp4", ".mkv", ".webm") and c.stat().st_size > 0:
            return c
    return _download(url, gameplay_dir / "gameplay")
