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

from . import ai_pick, blacklist, channels, compose, download, moments, storage
from .config import Settings

MAX_SHORTS_PER_VIDEO = 8
MAX_SCAN_ATTEMPTS = 40  # episodes to inspect before giving up in one run
# Generous upper bound so normal 1-3h podcasts always pass; only guards against
# absurd multi-hour livestreams. Not tied to config (segment downloads make
# episode length irrelevant), so a stale saved max_source_minutes can't skip
# every real episode.
MAX_EPISODE_SECONDS = 6 * 3600


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

            st.set("channels", "Finding podcast episodes…")
            ids = channels.get_channel_video_ids(
                list(s.podcast_channels), s.channel_scan_limit
            )
            if not ids:
                raise RuntimeError(
                    "No episodes found. Check the podcast_channels list in config."
                )
            seen = blacklist.load(s.data_dir)
            st.set("channels", f"Found {len(ids)} episodes ({len(seen)} already used).")

            # Pick the first NEW episode that has a transcript AND is downloadable.
            # A tiny 3s probe verifies access before we spend an OpenAI call.
            # Cap how many we inspect so a bad streak never grinds through the pool.
            chosen_segments = None
            vinfo = None
            attempts = 0
            for vid in ids:
                if vid in seen:
                    continue  # already turned into shorts before
                attempts += 1
                if attempts > MAX_SCAN_ATTEMPTS:
                    st.log.append(
                        f"  gave up after inspecting {MAX_SCAN_ATTEMPTS} episodes."
                    )
                    break
                st.set("scanning", f"Checking episode {attempts}…")
                info, segments = moments.fetch_info_and_transcript(vid, s.work_dir)
                if not info:
                    st.log.append(f"  #{attempts} {vid}: couldn't read info; skip.")
                    continue
                dur = float(info.get("duration") or 0.0)
                if dur <= 0 or dur > MAX_EPISODE_SECONDS:
                    st.log.append(f"  #{attempts} {vid}: duration {int(dur)}s out of range; skip.")
                    continue
                if not segments:
                    st.log.append(f"  #{attempts} {vid}: no captions; skip.")
                    continue

                v = moments.video_info_from(info)
                probe = download.download_segment(
                    vid, 0.0, min(3.0, dur), s.work_dir / f"probe_{vid}"
                )
                if not probe:
                    st.log.append(f"  #{attempts} {vid}: not downloadable; skip.")
                    continue
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    pass

                st.log.append(f"  #{attempts} {v.title[:50]}: usable ✓")
                chosen_segments, vinfo = segments, v
                break

            if not vinfo or not chosen_segments:
                raise RuntimeError(
                    f"No usable new episode in the first {attempts} checked "
                    "(no captions / not downloadable / already used). See the log "
                    "for per-episode reasons."
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

            # For each moment: download just that segment, then render.
            made = 0
            total = len(picked)
            for n, moment in enumerate(picked, start=1):
                st.set("render", f"Clip {n}/{total}: downloading segment…")
                seg = download.download_segment(
                    vinfo.id, moment.start, moment.end,
                    s.work_dir / f"seg_{vinfo.id}_{n:02d}",
                )
                if not seg:
                    st.log.append(f"  segment {n}/{total} download failed; skipping.")
                    continue

                seg_dur = compose.probe_duration(seg) or moment.duration
                st.set("render", f"Clip {n}/{total}: rendering…")
                base = f"{storage.new_basename(vinfo.id)}_{n:02d}"
                out_path = s.output_dir / f"{base}.mp4"
                try:
                    compose.render(
                        source_path=seg,
                        gameplay_path=gameplay,
                        out_path=out_path,
                        clip_start=0.0,
                        clip_duration=seg_dur,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.log.append(f"  clip {n}/{total} render failed: {exc}")
                    continue
                finally:
                    try:
                        seg.unlink(missing_ok=True)
                    except OSError:
                        pass

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
                        "duration": round(seg_dur, 2),
                        "created_at": datetime.now().isoformat(),
                    },
                )
                made += 1
                st.last_output = out_path.name

            if made == 0:
                raise RuntimeError("All clips failed for the chosen episode.")

            # Blacklist the episode so it is never used again.
            blacklist.add(s.data_dir, vinfo.id)
            st.set("done", f"Done: made {made} short(s) from “{vinfo.title[:50]}”.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            st.error = str(exc)
            st.set("error", f"Error: {exc}")
            st.log.append(traceback.format_exc().splitlines()[-1])
        finally:
            st.running = False
            st.finished_at = datetime.now().isoformat()
