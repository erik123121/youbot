"""Extract the single most-replayed moment from a YouTube video.

yt-dlp exposes YouTube's "most replayed" graph as ``info["heatmap"]`` — a list
of ``{start_time, end_time, value}`` segments (value is 0..1). We take the
highest-value segment as the peak and build a window of up to ``clip_seconds``
centered on it, clamped to the video's bounds.

Per the project decision, this is *most-replayed only*: if a video has no
heatmap, it is not usable and the caller should move on to the next candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yt_dlp


@dataclass
class VideoInfo:
    id: str
    title: str
    channel: str
    duration: float
    url: str


@dataclass
class Moment:
    start: float
    end: float
    peak: float  # heatmap value 0..1 at the chosen segment

    @property
    def duration(self) -> float:
        return self.end - self.start


def fetch_info(video_id: str) -> Optional[dict]:
    """Return the full yt-dlp info dict for a video, or None on failure."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def video_info_from(info: dict) -> VideoInfo:
    return VideoInfo(
        id=info.get("id", ""),
        title=info.get("title", "Untitled"),
        channel=info.get("uploader") or info.get("channel") or "Unknown",
        duration=float(info.get("duration") or 0.0),
        url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id','')}",
    )


def best_moment(info: dict, clip_seconds: int) -> Optional[Moment]:
    """Pick the most-replayed moment and expand to a <= clip_seconds window.

    Returns None if the video exposes no usable heatmap.
    """
    heatmap = info.get("heatmap")
    duration = float(info.get("duration") or 0.0)
    if not heatmap or duration <= 0:
        return None

    # Highest-value segment is the most-replayed point.
    top = max(heatmap, key=lambda s: s.get("value", 0.0))
    seg_start = float(top.get("start_time", 0.0))
    seg_end = float(top.get("end_time", seg_start))
    center = (seg_start + seg_end) / 2.0

    window = min(float(clip_seconds), duration)
    start = center - window / 2.0
    end = start + window

    # Clamp to video bounds.
    if start < 0:
        start = 0.0
        end = window
    if end > duration:
        end = duration
        start = max(0.0, end - window)

    return Moment(start=round(start, 2), end=round(end, 2), peak=float(top.get("value", 0.0)))
