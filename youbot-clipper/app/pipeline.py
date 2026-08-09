"""Orchestrates one full Start-to-finished-Short run, with live job state.

Only one job runs at a time. The web layer reads JobState for progress polling.
"""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import captions, compose, download, moments, storage, trending
from .config import Settings

FONTS_DIR = Path(__file__).parent / "assets"


@dataclass
class JobState:
    running: bool = False
    step: str = "idle"
    message: str = "Ready."
    log: List[str] = field(default_factory=list)
    error: Optional[str] = None
    last_output: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def set(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        self.log.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "step": self.step,
            "message": self.message,
            "log": self.log[-40:],
            "error": self.error,
            "last_output": self.last_output,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Pipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = JobState()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Kick off a run in a background thread. Returns False if one is active."""
        with self._lock:
            if self.state.running:
                return False
            self.state = JobState(running=True, step="starting", message="Starting…")
            self.state.started_at = datetime.now().isoformat()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    # ------------------------------------------------------------------ run
    def _run(self) -> None:
        s = self.settings
        st = self.state
        try:
            st.set("gameplay", "Preparing GTA gameplay footage…")
            gameplay = download.ensure_gameplay(s.gameplay_url, s.gameplay_dir)
            if not gameplay:
                raise RuntimeError("Could not download/cache the gameplay video.")

            st.set("trending", "Finding popular videos…")
            ids = trending.get_trending_ids(s.trending_region, s.candidate_count)
            if not ids:
                raise RuntimeError("No popular videos found.")
            st.set("trending", f"Found {len(ids)} candidates.")

            chosen_info = None
            chosen_moment = None
            chosen_segments = []
            max_seconds = s.max_source_minutes * 60
            for idx, vid in enumerate(ids, start=1):
                st.set("scanning", f"Reading transcript & scoring ({idx}/{len(ids)})…")
                info, segments = moments.fetch_info_and_transcript(vid, s.work_dir)
                if not info:
                    continue
                dur = float(info.get("duration") or 0.0)
                if dur <= 0 or dur > max_seconds:
                    continue  # skip live/very long videos
                if not segments:
                    continue  # no captions -> can't read the dialogue, skip
                moment = moments.pick_best_span(segments, s.clip_seconds)
                if moment is None:
                    continue
                chosen_info = info
                chosen_moment = moment
                chosen_segments = segments
                break

            if not chosen_info or not chosen_moment:
                raise RuntimeError(
                    "No popular video had usable captions to pick a moment from. "
                    "Try again later."
                )
            st.set("scanning", f"Picked moment ({chosen_moment.reason}).")

            vinfo = moments.video_info_from(chosen_info)
            st.set("download", f"Downloading: {vinfo.title[:60]}")
            source = download.download_source(vinfo.id, s.work_dir)
            if not source:
                raise RuntimeError("Failed to download the source video.")

            # Build Shorts-style captions from the clipped transcript.
            st.set("captions", "Building captions…")
            cs = chosen_moment.start
            entries = [
                (
                    max(0.0, seg.start - cs),
                    min(chosen_moment.duration, seg.end - cs),
                    seg.text,
                )
                for seg in chosen_segments
                if seg.end > cs and seg.start < chosen_moment.end
            ]
            ass_path = None
            if entries:
                ass_path = s.work_dir / f"{vinfo.id}_caps.ass"
                captions.build_ass(entries, ass_path, chosen_moment.duration)

            st.set("render", "Rendering vertical Short…")
            base = storage.new_basename(vinfo.id)
            out_path = s.output_dir / f"{base}.mp4"
            compose.render(
                source_path=source,
                gameplay_path=gameplay,
                out_path=out_path,
                clip_start=chosen_moment.start,
                clip_duration=chosen_moment.duration,
                ass_path=ass_path,
                fonts_dir=FONTS_DIR,
            )
            if ass_path is not None:
                try:
                    ass_path.unlink(missing_ok=True)
                except OSError:
                    pass

            st.set("thumbnail", "Generating thumbnail…")
            try:
                compose.make_thumbnail(out_path, s.output_dir / f"{base}.jpg")
            except Exception:
                pass  # non-fatal

            storage.write_metadata(
                s.output_dir / f"{base}.json",
                {
                    "title": vinfo.title,
                    "channel": vinfo.channel,
                    "source_url": vinfo.url,
                    "source_id": vinfo.id,
                    "moment_start": chosen_moment.start,
                    "moment_end": chosen_moment.end,
                    "moment_reason": chosen_moment.reason,
                    "transcript": chosen_moment.text,
                    "duration": round(chosen_moment.duration, 2),
                    "created_at": datetime.now().isoformat(),
                },
            )

            # Clean up the (large) source download; keep only the finished short.
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass

            st.last_output = out_path.name
            st.set("done", f"Done: {out_path.name}")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            st.error = str(exc)
            st.set("error", f"Error: {exc}")
            st.log.append(traceback.format_exc().splitlines()[-1])
        finally:
            st.running = False
            st.finished_at = datetime.now().isoformat()
