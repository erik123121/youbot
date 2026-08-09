"""List uploads from a set of podcast YouTube channels, newest first.

We pull each channel's full "/videos" tab (up to a limit) so the pipeline can
work from the newest episode backward through the whole back catalogue as the
blacklist grows. Channels are interleaved so output alternates between shows.
"""
from __future__ import annotations

from typing import List

import yt_dlp


def _normalize(entry: str) -> str:
    """Turn a handle / URL into a channel uploads URL."""
    e = entry.strip()
    if not e:
        return ""
    if e.startswith("http"):
        url = e
    elif e.startswith("@"):
        url = f"https://www.youtube.com/{e}"
    else:
        url = f"https://www.youtube.com/@{e}"
    # Point at the uploads tab unless a specific tab/playlist was given.
    if not any(seg in url for seg in ("/videos", "/streams", "playlist", "/featured")):
        url = url.rstrip("/") + "/videos"
    return url


def _extract_ids(url: str, limit: int) -> List[str]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
        "source_address": "0.0.0.0",  # force IPv4
    }
    ids: List[str] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return ids
    for entry in (info.get("entries") or []):
        if not entry:
            continue
        if entry.get("_type") == "playlist":
            for sub in entry.get("entries") or []:
                vid = (sub or {}).get("id")
                if vid and len(vid) == 11:
                    ids.append(vid)
            continue
        vid = entry.get("id")
        if vid and len(vid) == 11:
            ids.append(vid)
    return ids


def get_channel_video_ids(channels: List[str], per_channel_limit: int) -> List[str]:
    """Interleaved, de-duplicated video IDs across all channels (newest first)."""
    lists = []
    for ch in channels:
        url = _normalize(ch)
        if url:
            lists.append(_extract_ids(url, per_channel_limit))

    interleaved: List[str] = []
    idx = 0
    while any(idx < len(lst) for lst in lists):
        for lst in lists:
            if idx < len(lst):
                interleaved.append(lst[idx])
        idx += 1

    seen = set()
    out: List[str] = []
    for vid in interleaved:
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out
