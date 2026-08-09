"""Pick the most engaging moment from a video's transcript (free, on-device).

We download the timed captions with yt-dlp, slide a 20..clip_seconds window over
them, and score each window for general engagement using content signals — no
API key, no paid service. The chosen span is snapped to caption boundaries so it
never starts or ends mid-sentence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import yt_dlp

MIN_SPAN = 20.0  # seconds (user requirement: 20s minimum)

# Preferred caption languages (English variants + auto).
_SUB_LANGS = ["en", "en-US", "en-GB", "en-orig"]

# Reaction/bracket tags that mark lively moments.
_TAG_RE = re.compile(
    r"\[(laughter|applause|cheering|laughs|laughing|music|gasps?)\]"
    r"|\((laughs|laughter|applause|cheering|gasps?)\)",
    re.IGNORECASE,
)
_ALLCAPS_RE = re.compile(r"\b[A-Z]{3,}\b")

# High-arousal words that tend to sit on engaging beats.
_AROUSAL = {
    "wow", "omg", "crazy", "insane", "unbelievable", "incredible", "amazing",
    "shocking", "shocked", "wait", "what", "no", "yes", "never", "always",
    "best", "worst", "love", "hate", "oh", "god", "seriously", "literally",
    "actually", "boom", "wtf", "damn", "hell", "stop", "look", "watch",
    "listen", "huge", "massive", "epic", "ridiculous", "perfect", "worst",
    "impossible", "finally", "suddenly", "boom", "nailed", "won", "lost",
    "died", "dead", "kill", "killed", "fight", "fire", "scary", "terrifying",
    "hilarious", "funny", "laugh", "cried", "screaming", "unreal",
}

# Sponsor / self-promo markers to avoid.
_SPONSOR = (
    "sponsor", "sponsored", "promo code", "use code", "discount code",
    "link in the description", "link in description", "check out",
    "this video is brought", "today's video is sponsored", "nordvpn",
    "raycon", "honey", "skillshare", "manscaped",
)

# Intro/outro fluff to avoid.
_BOILERPLATE = (
    "like and subscribe", "smash that like", "hit the bell", "subscribe",
    "welcome back", "what's up guys", "hey guys", "thanks for watching",
    "see you next time", "before we start", "before we get into",
)


@dataclass
class VideoInfo:
    id: str
    title: str
    channel: str
    duration: float
    url: str


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Moment:
    start: float
    end: float
    score: float
    reason: str
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def video_info_from(info: dict) -> VideoInfo:
    return VideoInfo(
        id=info.get("id", ""),
        title=info.get("title", "Untitled"),
        channel=info.get("uploader") or info.get("channel") or "Unknown",
        duration=float(info.get("duration") or 0.0),
        url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info.get('id','')}",
    )


# --------------------------------------------------------------- transcript
_CHUNK_WORDS = 5       # words per caption line (Shorts style)
_CHUNK_MAX_S = 2.4     # ...or this long, whichever comes first


def _event_words(event: dict) -> List[Tuple[float, str]]:
    """Extract (abs_time, word) tokens from one json3 event.

    Uses per-word tOffsetMs when present (auto-captions); otherwise spreads the
    words evenly across the event's duration (manual captions).
    """
    segs = event.get("segs") or []
    base = float(event.get("tStartMs", 0)) / 1000.0
    dur = float(event.get("dDurationMs", 0)) / 1000.0
    tokens = [(s.get("utf8", "") or "").strip() for s in segs]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    has_offsets = any("tOffsetMs" in s for s in segs)
    words: List[Tuple[float, str]] = []
    if has_offsets:
        for s in segs:
            w = (s.get("utf8", "") or "").strip()
            if w:
                words.append((base + float(s.get("tOffsetMs", 0)) / 1000.0, w))
    else:
        n = len(tokens)
        step = (dur / n) if (dur > 0 and n > 0) else 0.3
        for i, w in enumerate(tokens):
            words.append((base + i * step, w))
    return words


def _parse_json3(path: Path) -> List[Segment]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return []

    # 1) Build a de-rolled word stream. YouTube auto-captions re-send each line
    #    with a growing tail, so we merge each event onto the running stream by
    #    dropping the longest prefix that already appears as its suffix.
    stream: List[Tuple[float, str]] = []
    for event in data.get("events", []) or []:
        ev = _event_words(event)
        if not ev:
            continue
        toks = [w.lower() for _, w in ev]
        max_k = min(len(toks), len(stream))
        overlap = 0
        for k in range(max_k, 0, -1):
            if [w.lower() for _, w in stream[-k:]] == toks[:k]:
                overlap = k
                break
        stream.extend(ev[overlap:])

    if not stream:
        return []

    # 2) Re-chunk the clean stream into short, non-overlapping caption groups.
    segments: List[Segment] = []
    i = 0
    n = len(stream)
    while i < n:
        start = stream[i][0]
        j = i
        picked: List[str] = []
        while j < n and len(picked) < _CHUNK_WORDS and (stream[j][0] - start) <= _CHUNK_MAX_S:
            picked.append(stream[j][1])
            j += 1
        if j == i:  # safety: always advance
            picked = [stream[i][1]]
            j = i + 1
        text = " ".join(picked).strip(" ,.-")
        end = stream[j][0] if j < n else start + 1.5
        if text and end > start:
            segments.append(Segment(start=round(start, 2), end=round(end, 2), text=text))
        i = j
    return segments


def fetch_info_and_transcript(
    video_id: str, work_dir: Path
) -> Tuple[Optional[dict], List[Segment]]:
    """Return (info, transcript segments). Segments empty if no usable captions."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_base = work_dir / f"subs_{video_id}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _SUB_LANGS,
        "subtitlesformat": "json3",
        "outtmpl": str(out_base) + ".%(ext)s",
    }
    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        # Still try to use anything that got written before failing.
        pass

    # Prefer an English track; otherwise take whatever json3 landed.
    files = sorted(work_dir.glob(f"subs_{video_id}*.json3"))
    chosen = None
    for f in files:
        if any(f".{lang}." in f.name for lang in _SUB_LANGS):
            chosen = f
            break
    if chosen is None and files:
        chosen = files[0]

    segments = _parse_json3(chosen) if chosen else []

    # Clean up all sub files for this video.
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    return info, segments


# ------------------------------------------------------------------- scoring
def _score_text(text: str) -> Tuple[float, List[str]]:
    lower = text.lower()
    words = re.findall(r"[A-Za-z']+", text)
    n = max(1, len(words))
    reasons: List[str] = []
    score = 0.0

    tags = len(_TAG_RE.findall(text))
    if tags:
        score += 3.0 * tags
        reasons.append("reactions")

    excl = text.count("!")
    if excl:
        score += 1.5 * excl
        reasons.append("excitement")

    ques = text.count("?")
    if ques:
        score += 0.8 * ques
        reasons.append("questions")

    arousal = sum(1 for w in words if w.lower() in _AROUSAL)
    if arousal:
        score += 1.6 * arousal
        reasons.append("high-energy words")

    caps = len(_ALLCAPS_RE.findall(text))
    if caps:
        score += 0.8 * caps
        reasons.append("emphasis")

    # Speech density: livelier stretches pack more words in.
    score += min(n / 10.0, 6.0) * 0.5

    # Penalties for ad reads / channel boilerplate.
    if any(m in lower for m in _SPONSOR):
        score -= 8.0
    if any(m in lower for m in _BOILERPLATE):
        score -= 3.0

    return score, reasons


def pick_best_span(
    segments: List[Segment], clip_seconds: int
) -> Optional[Moment]:
    """Slide a target-length window over the transcript and return the best span."""
    if not segments:
        return None
    total = segments[-1].end
    if total < MIN_SPAN:
        return None

    target = max(MIN_SPAN, min(float(clip_seconds), 40.0))

    best: Optional[Moment] = None
    for i, seg in enumerate(segments):
        start = seg.start
        hard_end = start + target
        if hard_end > total + 2.0:
            break

        # Collect segments overlapping [start, hard_end]; snap end to a boundary.
        window: List[Segment] = []
        end = hard_end
        for s in segments[i:]:
            if s.start >= hard_end:
                break
            window.append(s)
            end = s.end
        # Ensure the snapped span stays within [MIN_SPAN, 40].
        span = end - start
        if span < MIN_SPAN:
            end = min(start + MIN_SPAN, total)
            span = end - start
        if span > 40.0:
            end = start + 40.0
        if end - start < MIN_SPAN:
            continue

        text = " ".join(s.text for s in window)
        score, reasons = _score_text(text)

        # Down-weight intros/outros.
        if start < total * 0.06 or end > total * 0.94:
            score *= 0.4

        if best is None or score > best.score:
            best = Moment(
                start=round(start, 2),
                end=round(end, 2),
                score=round(score, 2),
                reason=", ".join(dict.fromkeys(reasons)) or "dialogue density",
                text=text[:500],
            )
    return best
