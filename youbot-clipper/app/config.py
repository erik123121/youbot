"""Configuration loading.

When running as a Home Assistant add-on, options are written by the supervisor
to ``/data/options.json``. When running as a plain Docker container or during
local development, the same values can be supplied via environment variables.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

OPTIONS_PATH = Path("/data/options.json")

_DEFAULTS = {
    "gameplay_url": "https://youtu.be/ZtLrNBdXT7M",
    "podcast_channels": [
        "https://www.youtube.com/@joerogan",
        "https://www.youtube.com/@TheoVon",
    ],
    "channel_scan_limit": 500,
    "clip_seconds": 35,
    "output_dir": "/media/youbot",
    "max_source_minutes": 600,
    "openai_api_key": "",
    "openai_model": "gpt-4.1-nano",
}


@dataclass(frozen=True)
class Settings:
    gameplay_url: str
    podcast_channels: tuple
    channel_scan_limit: int
    clip_seconds: int
    output_dir: Path
    max_source_minutes: int
    openai_api_key: str
    openai_model: str
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


def _parse_channels(value) -> tuple:
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[\n,]", value)]
    else:
        parts = [str(p).strip() for p in (value or [])]
    return tuple(p for p in parts if p)


def load_settings() -> Settings:
    raw = {**_DEFAULTS, **_raw_options()}
    data_dir = Path(os.environ.get("YOUBOT_DATA_DIR", "/data"))
    settings = Settings(
        gameplay_url=str(raw["gameplay_url"]),
        podcast_channels=_parse_channels(raw["podcast_channels"]),
        channel_scan_limit=int(raw["channel_scan_limit"]),
        clip_seconds=max(5, min(40, int(raw["clip_seconds"]))),
        output_dir=Path(str(raw["output_dir"])),
        max_source_minutes=int(raw["max_source_minutes"]),
        openai_api_key=str(raw.get("openai_api_key", "")).strip(),
        openai_model=str(raw.get("openai_model", "gpt-4.1-nano")).strip()
        or "gpt-4.1-nano",
        data_dir=data_dir,
    )
    for d in (settings.output_dir, settings.gameplay_dir, settings.work_dir):
        d.mkdir(parents=True, exist_ok=True)
    return settings
