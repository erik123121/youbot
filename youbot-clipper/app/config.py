"""Configuration loading.

When running as a Home Assistant add-on, options are written by the supervisor
to ``/data/options.json``. When running as a plain Docker container or during
local development, the same values can be supplied via environment variables.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

OPTIONS_PATH = Path("/data/options.json")

_DEFAULTS = {
    "gameplay_url": "https://youtu.be/ZtLrNBdXT7M",
    "trending_region": "US",
    "candidate_count": 30,
    "clip_seconds": 35,
    "output_dir": "/media/youbot",
    "max_source_minutes": 30,
}


@dataclass(frozen=True)
class Settings:
    gameplay_url: str
    trending_region: str
    candidate_count: int
    clip_seconds: int
    output_dir: Path
    max_source_minutes: int
    data_dir: Path

    @property
    def gameplay_dir(self) -> Path:
        return self.data_dir / "gameplay"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"


def _raw_options() -> dict:
    """Read HA options file if present, else fall back to environment variables."""
    if OPTIONS_PATH.exists():
        try:
            return json.loads(OPTIONS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    env = {}
    for key in _DEFAULTS:
        val = os.environ.get(key.upper())
        if val is not None:
            env[key] = val
    return env


def load_settings() -> Settings:
    raw = {**_DEFAULTS, **_raw_options()}
    data_dir = Path(os.environ.get("YOUBOT_DATA_DIR", "/data"))
    settings = Settings(
        gameplay_url=str(raw["gameplay_url"]),
        trending_region=str(raw["trending_region"]).upper(),
        candidate_count=int(raw["candidate_count"]),
        clip_seconds=max(5, min(40, int(raw["clip_seconds"]))),
        output_dir=Path(str(raw["output_dir"])),
        max_source_minutes=int(raw["max_source_minutes"]),
        data_dir=data_dir,
    )
    for d in (settings.output_dir, settings.gameplay_dir, settings.work_dir):
        d.mkdir(parents=True, exist_ok=True)
    return settings
