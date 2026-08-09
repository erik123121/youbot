"""Persistent list of already-used source video IDs.

Stored in the add-on's /data so it survives restarts and add-on updates. Once a
video has been turned into shorts, its ID goes here and it is never picked again
(until the list is reset).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Set


def _path(data_dir: Path) -> Path:
    return data_dir / "blacklist.json"


def load(data_dir: Path) -> Set[str]:
    p = _path(data_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                return {str(x) for x in data}
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def add(data_dir: Path, video_id: str) -> None:
    ids = load(data_dir)
    ids.add(str(video_id))
    try:
        _path(data_dir).write_text(json.dumps(sorted(ids)))
    except OSError:
        pass


def clear(data_dir: Path) -> int:
    """Delete the blacklist; return how many IDs were removed."""
    count = len(load(data_dir))
    try:
        _path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass
    return count


def count(data_dir: Path) -> int:
    return len(load(data_dir))
