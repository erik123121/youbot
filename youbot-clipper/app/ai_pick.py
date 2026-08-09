"""Pick the best 20-40s moment with the OpenAI API, cheaply.

Given the timestamped transcript, a small model (default gpt-4.1-nano) returns
the start/end of the single most engaging span. Requests are kept tiny — a
compact transcript in, a few tokens of JSON out — so usage stays a fraction of
a cent per run. On any problem (no key, out of credit, network, bad response)
this returns (None, reason) so the caller falls back to the on-device scorer.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from .moments import MIN_SPAN, Moment, Segment

_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_TIMEOUT = 45
_MAX_SPAN = 40.0
_MAX_TRANSCRIPT_CHARS = 12000  # keep the request small/cheap


def _build_transcript(segments: List[Segment], max_chars: int = _MAX_TRANSCRIPT_CHARS) -> str:
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
    if len(text) > max_chars:  # very long videos: keep it bounded
        text = text[:max_chars]
    return text


def _prompt(transcript: str, duration: float, clip_seconds: int, max_count: int) -> str:
    hi = min(_MAX_SPAN, float(clip_seconds))
    return (
        "Below is a timestamped transcript of a video; each line starts with its "
        "start time in seconds in square brackets.\n\n"
        f"Find EVERY genuinely engaging, self-contained moment worth turning into "
        f"a vertical Short — up to {max_count} of them. Each must be a contiguous "
        f"span between {MIN_SPAN:.0f} and {hi:.0f} seconds long, and the spans must "
        "NOT overlap each other. Favor strong hooks, payoffs, humor, surprise, or "
        "high emotion. Only include moments that are actually good — fewer is fine. "
        "Skip intros, outros, and sponsor/ad reads. Every span must lie within the "
        f"video, which is {duration:.0f} seconds long. Order them best first.\n\n"
        "For each clip also write a punchy YouTube Short title (<= 80 chars, no "
        "hashtags, no surrounding quotes).\n\n"
        'Respond with ONLY JSON: {"clips": [{"start_seconds": number, '
        '"end_seconds": number, "reason": "short phrase", "title": "catchy title"}]}\n\n'
        "TRANSCRIPT:\n" + transcript
    )


def _call_openai(api_key: str, model: str, prompt: str) -> Tuple[Optional[dict], Optional[str]]:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert short-form video editor. "
                    "Respond only with JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    payload = None
    for attempt in range(2):  # one retry, mainly for transient 429s
        req = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                pass
            if exc.code == 429:
                # Out of credit is also a 429; retrying won't help there.
                if "insufficient_quota" in detail:
                    return None, "out of OpenAI credit (insufficient_quota)"
                if attempt == 0:
                    time.sleep(6)
                    continue
                return None, "rate limited (HTTP 429)"
            if exc.code == 401:
                return None, "invalid API key (HTTP 401)"
            snippet = " ".join(detail.split())[:140]
            return None, f"HTTP {exc.code}: {snippet}"
        except Exception as exc:  # noqa: BLE001
            return None, f"request failed: {exc}"

    if payload is None:
        return None, "no response"

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "unexpected response shape"

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


def _finalize(
    segments: List[Segment], duration: float, start: float, end: float,
    reason: str, title: str = "",
) -> Optional[Moment]:
    """Clamp/snap one span to the 20..40s rules; None if it can't be made valid."""
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end <= start:
        end = min(start + MIN_SPAN, duration)
    start, end = _snap(segments, start, end)
    if end - start < MIN_SPAN:
        end = min(start + MIN_SPAN, duration)
        start = max(0.0, end - MIN_SPAN)
    if end - start > _MAX_SPAN:
        end = start + _MAX_SPAN
    if end - start < MIN_SPAN:
        return None
    clip_text = " ".join(
        seg.text for seg in segments if seg.end > start and seg.start < end
    )[:500]
    return Moment(
        start=round(start, 2),
        end=round(end, 2),
        score=0.0,
        reason=(reason.strip()[:120] or "AI pick"),
        text=clip_text,
        title=title.strip().strip('"').strip()[:100],
    )


def pick_moments(
    segments: List[Segment], api_key: str, model: str, clip_seconds: int, max_count: int
) -> Tuple[List[Moment], Optional[str]]:
    """Return up to max_count non-overlapping engaging spans (best-first)."""
    if not segments:
        return [], "no transcript"
    duration = segments[-1].end
    transcript = _build_transcript(segments)
    prompt = _prompt(transcript, duration, clip_seconds, max_count)

    data, err = _call_openai(api_key, model, prompt)
    if err:
        return [], err

    raw = data.get("clips")
    if not isinstance(raw, list):
        return [], "no clips array in response"

    chosen: List[Moment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        moment = _finalize(
            segments, duration, start, end,
            str(item.get("reason", "AI pick")), str(item.get("title", "")),
        )
        if moment is None:
            continue
        if any(moment.start < c.end and moment.end > c.start for c in chosen):
            continue  # drop overlaps with an already-accepted clip
        chosen.append(moment)
        if len(chosen) >= max_count:
            break

    if not chosen:
        return [], "no valid clips returned"
    chosen.sort(key=lambda m: m.start)  # chronological output
    return chosen, None
