"""Find currently-popular YouTube videos without an API key.

YouTube removed the global Trending page in 2025 (``/feed/trending`` now
redirects to the homepage), so we approximate "currently popular" by searching
a handful of big categories filtered to the last week and returning those video
IDs. The pipeline then inspects each for "most replayed" data and picks one.
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote_plus

import yt_dlp

# Broad categories that always have fresh, popular uploads. Searched with a
# "this week" filter so results skew to what's popular right now.
SEED_QUERIES = [
    "music",
    "gaming",
    "sports highlights",
    "movie trailer",
    "news",
    "funny",
]

# YouTube results-page filter code for "Upload date: this week".
_THIS_WEEK_SP = "EgIIAw%3D%3D"

# Legacy trending feed — kept as a best-effort first try in case a given region
# still serves it; failures are swallowed.
_LEGACY_TRENDING = "https://www.youtube.com/feed/trending"


def _extract_ids(target: str, limit: int, region: str) -> List[str]:
    """Flat-extract video IDs from a playlist/search URL or ytsearch query."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
        "geo_bypass": True,
        "geo_bypass_country": region,
        "source_address": "0.0.0.0",  # force IPv4 (avoid broken-IPv6 failures)
    }
    ids: List[str] = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
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
        if vid and len(vid) == 11:  # looks like a real video ID (not channel/playlist)
            ids.append(vid)
    return ids


def _search_ids(query: str, limit: int, region: str) -> List[str]:
    """Popular recent results for a query; falls back to plain ytsearch."""
    url = (
        f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        f"&sp={_THIS_WEEK_SP}"
    )
    ids = _extract_ids(url, limit, region)
    if not ids:
        ids = _extract_ids(f"ytsearch{limit}:{query}", limit, region)
    return ids


def get_trending_ids(
    region: str = "US",
    limit: int = 30,
    queries: Optional[List[str]] = None,
) -> List[str]:
    """Return up to ``limit`` popular-recent video IDs, interleaved by category."""
    queries = queries or SEED_QUERIES
    collected: List[str] = []

    # Best-effort: legacy trending feed first (usually a no-op now).
    collected += _extract_ids(_LEGACY_TRENDING, limit, region)

    per_query = max(6, (limit // max(1, len(queries))) + 4)
    lists = [_search_ids(q, per_query, region) for q in queries]

    # Round-robin interleave so the candidate list is varied, not all-music.
    idx = 0
    while any(idx < len(lst) for lst in lists):
        for lst in lists:
            if idx < len(lst):
                collected.append(lst[idx])
        idx += 1

    # De-duplicate while preserving order.
    seen = set()
    unique: List[str] = []
    for vid in collected:
        if vid not in seen:
            seen.add(vid)
            unique.append(vid)
    return unique[:limit]
