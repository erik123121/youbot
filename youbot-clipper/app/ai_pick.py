"""Pick the best 20-40s moment with Google's Gemini API (free tier).

Given the timestamped transcript, Gemini returns the start/end of the single
most engaging span. On any problem (no key, network error, bad response) this
returns (None, reason) so the caller can fall back to the on-device scorer.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from .moments import MIN_SPAN, Moment, Segment

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TIMEOUT = 45
_MAX_SPAN = 40.0


def _mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _build_transcript(segments: List[Segment], max_chars: int = 24000) -> str:
    """Compact timestamped transcript: one line per ~8s of dialogue."""
    lines: List[str] = []
    bucket_start = None
    bucket_words: List[str] = []

    def flush():
        if bucket_words and bucket_start is not None:
            lines.append(f"[{bucket_start:.1f}] {' '.join(bucket_words)}")

    for seg in segments:
        if bucket_start is None:
            bucket_start = seg.start
        bucket_words.append(seg.text)
        if seg.end - bucket_start >= 8.0:
            flush()
            bucket_start = None
            bucket_words = []
    flush()

    text = "\n".join(lines)
    if len(text) > max_chars:  # keep the request small for very long videos
        text = text[:max_chars]
    return text


def _prompt(transcript: str, duration: float, clip_seconds: int) -> str:
    hi = min(_MAX_SPAN, float(clip_seconds))
    return (
        "You are an expert short-form video editor. Below is a timestamped "
        "transcript of a video; each line starts with the start time in seconds "
        "in square brackets.\n\n"
        f"Pick the SINGLE most engaging, self-contained moment to turn into a "
        f"vertical Short. It must be a contiguous span between {MIN_SPAN:.0f} and "
        f"{hi:.0f} seconds long. Favor a strong hook, payoff, humor, surprise, or "
        "high emotion. Avoid intros, outros, and sponsor/ad reads. The span must "
        f"lie within the video, which is {duration:.0f} seconds long.\n\n"
        "Respond with ONLY JSON of the form "
        '{\"start_seconds\": number, \"end_seconds\": number, \"reason\": \"short phrase\"}.\n\n'
        "TRANSCRIPT:\n" + transcript
    )


def _call_gemini(api_key: str, model: str, prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    url = _ENDPOINT.format(model=model) + f"?key={api_key}"
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:
            pass
        return None, f"HTTP {exc.code} {detail}".strip()
    except Exception as exc:  # noqa: BLE001
        return None, f"request failed: {exc}"

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None, "unexpected response shape"

    # responseMimeType=json should give clean JSON, but be forgiving.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None, "no JSON in response"
    try:
        return json.loads(match.group(0)), None
    except json.JSONDecodeError:
        return None, "could not parse JSON"


def _snap(segments: List[Segment], start: float, end: float) -> Tuple[float, float]:
    """Snap to the nearest caption boundaries for clean entry/exit."""
    starts = [s.start for s in segments]
    ends = [s.end for s in segments]
    snapped_start = min(starts, key=lambda x: abs(x - start))
    snapped_end = min(ends, key=lambda x: abs(x - end))
    return snapped_start, snapped_end


def pick_moment(
    segments: List[Segment], api_key: str, model: str, clip_seconds: int
) -> Tuple[Optional[Moment], Optional[str]]:
    if not segments:
        return None, "no transcript"
    duration = segments[-1].end
    transcript = _build_transcript(segments)
    prompt = _prompt(transcript, duration, clip_seconds)

    data, err = _call_gemini(api_key, model, prompt)
    if err:
        return None, err
    try:
        start = float(data["start_seconds"])
        end = float(data["end_seconds"])
    except (KeyError, TypeError, ValueError):
        return None, "missing start/end"
    reason = str(data.get("reason", "AI pick")).strip()[:120] or "AI pick"

    # Clamp + enforce the 20..40s length rules.
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end <= start:
        end = min(start + MIN_SPAN, duration)
    start, end = _snap(segments, start, end)
    span = end - start
    if span < MIN_SPAN:
        end = min(start + MIN_SPAN, duration)
        start = max(0.0, end - MIN_SPAN)
    if end - start > _MAX_SPAN:
        end = start + _MAX_SPAN
    if end - start < MIN_SPAN:
        return None, "span too short after clamping"

    # Text of the chosen span, for the metadata sidecar.
    clip_text = " ".join(
        seg.text for seg in segments if seg.end > start and seg.start < end
    )[:500]
    return (
        Moment(
            start=round(start, 2),
            end=round(end, 2),
            score=0.0,
            reason=reason,
            text=clip_text,
        ),
        None,
    )
