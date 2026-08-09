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

from . import ai_pick, blacklist, compose, download, moments, storage, trending
from .config import Settings

MAX_SHORTS_PER_VIDEO = 8


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
            seen = blacklist.load(s.data_dir)
            st.set("trending", f"Found {len(ids)} candidates ({len(seen)} already used).")

            # Pick the first NEW popular video that has a usable transcript.
            chosen_info = None
            chosen_segments = None
            max_seconds = s.max_source_minutes * 60
            for idx, vid in enumerate(ids, start=1):
                if vid in seen:
                    continue  # already turned into shorts before
                st.set("scanning", f"Reading transcript ({idx}/{len(ids)})…")
                info, segments = moments.fetch_info_and_transcript(vid, s.work_dir)
                if not info:
                    continue
                dur = float(info.get("duration") or 0.0)
                if dur <= 0 or dur > max_seconds:
                    continue  # skip live/very long videos
                if not segments:
                    continue  # no captions -> can't read the dialogue, skip
                chosen_info = info
                chosen_segments = segments
                break

            if not chosen_info or not chosen_segments:
                raise RuntimeError(
                    "No new popular video with usable captions was found "
                    "(everything found was already used). Try again later."
                )

            # Find ALL good moments: one OpenAI call per run, heuristic fallback.
            picked = []
            if s.openai_api_key:
                st.set("ai", "Asking OpenAI for the best moments…")
                picked, err = ai_pick.pick_moments(
                    chosen_segments, s.openai_api_key, s.openai_model,
                    s.clip_seconds, MAX_SHORTS_PER_VIDEO,
                )
                if err:
                    st.log.append(f"  OpenAI unavailable ({err}); using on-device scorer.")
            if not picked:
                picked = moments.pick_best_spans(
                    chosen_segments, s.clip_seconds, MAX_SHORTS_PER_VIDEO
                )
            if not picked:
                raise RuntimeError("Could not find any good moments in the transcript.")
            st.set("scanning", f"Found {len(picked)} moment(s) to clip.")

            vinfo = moments.video_info_from(chosen_info)
            st.set("download", f"Downloading: {vinfo.title[:60]}")
            source = download.download_source(vinfo.id, s.work_dir)
            if not source:
                raise RuntimeError("Failed to download the source video.")

            # Render one short per moment; a single bad moment won't kill the batch.
            made = 0
            total = len(picked)
            for n, moment in enumerate(picked, start=1):
                st.set("render", f"Rendering short {n}/{total}…")
                base = f"{storage.new_basename(vinfo.id)}_{n:02d}"
                out_path = s.output_dir / f"{base}.mp4"
                try:
                    compose.render(
                        source_path=source,
                        gameplay_path=gameplay,
                        out_path=out_path,
                        clip_start=moment.start,
                        clip_duration=moment.duration,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.log.append(f"  short {n}/{total} failed: {exc}")
                    continue

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
                        "clip_index": n,
                        "clip_total": total,
                        "moment_start": moment.start,
                        "moment_end": moment.end,
                        "moment_reason": moment.reason,
                        "transcript": moment.text,
                        "duration": round(moment.duration, 2),
                        "created_at": datetime.now().isoformat(),
                    },
                )
                made += 1
                st.last_output = out_path.name

            # Clean up the (large) source download; keep only the finished shorts.
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass

            if made == 0:
                raise RuntimeError("All renders failed for the chosen video.")

            # Blacklist the video so it is never used again.
            blacklist.add(s.data_dir, vinfo.id)
            st.set("done", f"Done: made {made} short(s) from “{vinfo.title[:50]}”.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            st.error = str(exc)
            st.set("error", f"Error: {exc}")
            st.log.append(traceback.format_exc().splitlines()[-1])
        finally:
            st.running = False
            st.finished_at = datetime.now().isoformat()
