"""Build a Shorts-style ASS subtitle file from the clipped transcript.

Big bold white text with a heavy outline, uppercased, anchored in the lower
third (over the gameplay). Times are relative to the start of the clip.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

# Entry = (start_sec, end_sec, text), relative to the clip start.
Entry = Tuple[float, float, str]

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Short,Liberation Sans,66,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""


def _ts(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    text = text.replace("{", "(").replace("}", ")")
    text = " ".join(text.split())  # collapse whitespace/newlines
    return text.upper()


def build_ass(entries: List[Entry], out_path: Path, max_seconds: float) -> Path:
    lines = [_HEADER.rstrip("\n")]
    for start, end, text in entries:
        end = min(end, max_seconds)
        if end <= start:
            continue
        cleaned = _clean(text)
        if not cleaned:
            continue
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Short,,0,0,0,,{cleaned}"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
