"""Finished-clip storage: naming, metadata sidecars, and gallery listing."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional


def new_basename(video_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = "".join(c for c in video_id if c.isalnum() or c in "-_")
    return f"short_{ts}_{safe_id}"


def write_metadata(meta_path: Path, data: dict) -> None:
    meta_path.write_text(json.dumps(data, indent=2))


def list_clips(output_dir: Path) -> List[dict]:
    """Return clips newest-first, each with its metadata (if present)."""
    clips: List[dict] = []
    if not output_dir.exists():
        return clips
    for video in output_dir.glob("*.mp4"):
        meta_path = video.with_suffix(".json")
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                meta = {}
        thumb = video.with_suffix(".jpg")
        clips.append(
            {
                "filename": video.name,
                "thumbnail": thumb.name if thumb.exists() else None,
                "title": meta.get("title", video.stem),
                "channel": meta.get("channel", ""),
                "source_url": meta.get("source_url", ""),
                "created_at": meta.get("created_at")
                or datetime.fromtimestamp(video.stat().st_mtime).isoformat(),
                "duration": meta.get("duration"),
                "size_mb": round(video.stat().st_size / (1024 * 1024), 1),
            }
        )
    clips.sort(key=lambda c: c["created_at"], reverse=True)
    return clips


def safe_path(output_dir: Path, filename: str) -> Optional[Path]:
    """Resolve a user-supplied filename inside output_dir, blocking traversal."""
    candidate = (output_dir / filename).resolve()
    try:
        candidate.relative_to(output_dir.resolve())
    except ValueError:
        return None
    if candidate.exists():
        return candidate
    return None
