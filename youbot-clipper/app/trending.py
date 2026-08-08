"""Find currently-popular YouTube videos by scraping the trending feed with yt-dlp.

No API key is used. We pull a flat list of trending video IDs, then hand them
back to the pipeline which inspects each for "most replayed" data.
"""
from __future__ import annotations

from typing import List

import yt_dlp

TRENDING_URL = "https://www.youtube.com/feed/trending"


def get_trending_ids(region: str = "US", limit: int = 30) -> List[str]:
    """Return a list of trending video IDs, most-trending first.

    ``geo_bypass_country`` nudges YouTube toward the requested region's feed.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
        "geo_bypass": True,
        "geo_bypass_country": region,
    }
    ids: List[str] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(TRENDING_URL, download=False)
        for entry in info.get("entries", []) or []:
            if not entry:
                continue
            # A trending "tab" can nest sub-playlists; unwrap one level if needed.
            if entry.get("_type") == "playlist":
                for sub in entry.get("entries", []) or []:
                    vid = (sub or {}).get("id")
                    if vid:
                        ids.append(vid)
            else:
                vid = entry.get("id")
                if vid:
                    ids.append(vid)
    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for vid in ids:
        if vid not in seen:
            seen.add(vid)
            unique.append(vid)
    return unique[:limit]
