"""Autopilot: generate a batch, drip-upload the clips to YouTube with a random
2-5h delay each, and when the queue empties, generate the next batch. Forever.

State (queue + schedule) is persisted to /data so it survives restarts.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import pipeline, uploader, ytauth
from .config import Settings


class Autopilot:
    def __init__(self, settings: Settings):
        self.s = settings
        self.state_path = settings.data_dir / "autopilot.json"
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._fail_counts: dict = {}
        self.gen_state = pipeline.JobState()
        self.generating = False
        self.enabled = False
        self.queue: list = []
        self.next_upload_at: Optional[float] = None
        self.uploaded = 0
        self.last_result = "Idle."
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.state_path.exists():
            try:
                d = json.loads(self.state_path.read_text())
                self.enabled = bool(d.get("enabled", False))
                self.queue = list(d.get("queue", []))
                self.next_upload_at = d.get("next_upload_at")
                self.uploaded = int(d.get("uploaded", 0))
                self.last_result = d.get("last_result", "Idle.")
            except (json.JSONDecodeError, OSError, ValueError):
                pass

    def _save(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(
                    {
                        "enabled": self.enabled,
                        "queue": self.queue,
                        "next_upload_at": self.next_upload_at,
                        "uploaded": self.uploaded,
                        "last_result": self.last_result,
                    },
                    indent=2,
                )
            )
        except OSError:
            pass

    # -------------------------------------------------------------- controls
    def start(self) -> None:
        with self._lock:
            self.enabled = True
            self._save()
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.enabled = False
            self.last_result = "Stopped."
            self._save()

    def resume_if_enabled(self) -> None:
        if self.enabled:
            self.start()

    def status(self) -> dict:
        nxt = None
        if self.next_upload_at:
            nxt = datetime.fromtimestamp(self.next_upload_at).isoformat()
        return {
            "enabled": self.enabled,
            "generating": self.generating,
            "queue_len": len(self.queue),
            "next_upload_at": nxt,
            "uploaded": self.uploaded,
            "last_result": self.last_result,
            "gen": self.gen_state.snapshot() if self.generating else None,
        }

    # ------------------------------------------------------------------ loop
    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and self.enabled:
            time.sleep(min(20.0, end - time.time()))

    # ~2-3 uploads/day. Hardcoded (not the config delay fields) so a stale saved
    # value can't change the cadence — 24h/12h = 2/day, 24h/8h = 3/day.
    _MIN_HOURS = 8.0
    _MAX_HOURS = 12.0

    def _delay_seconds(self) -> float:
        return random.uniform(self._MIN_HOURS, self._MAX_HOURS) * 3600.0

    def _generate(self) -> None:
        self.generating = True
        self.gen_state = pipeline.JobState(running=True, step="starting",
                                           message="Generating a new batch…")
        try:
            made = pipeline.generate_batch(self.s, self.gen_state)
            self.queue.extend(made)
            self.gen_state.set("done", f"Queued {len(made)} clip(s).")
            self.last_result = f"Generated {len(made)} clip(s); uploading on schedule."
            self.next_upload_at = time.time()  # upload the first one shortly
        except Exception as exc:  # noqa: BLE001
            self.gen_state.set("error", f"Generation failed: {exc}")
            self.last_result = f"Generation failed: {exc}"
            self.next_upload_at = time.time() + 1800  # retry in 30 min
        finally:
            self.gen_state.running = False
            self.generating = False
            self._save()

    def _upload_next(self) -> None:
        clip = self.queue[0]
        path = Path(clip["path"])
        if not path.exists():
            self.queue.pop(0)
            self._save()
            return

        vid, err = uploader.upload_short(
            self.s.youtube_client_id, self.s.youtube_client_secret,
            ytauth.stored_refresh_token(self.s), path, clip.get("title", "Short"),
            clip.get("description", ""), self.s.upload_privacy,
        )
        if vid:
            self.queue.pop(0)
            self.uploaded += 1
            self._fail_counts.pop(clip["path"], None)
            self.last_result = f"Uploaded “{clip.get('title','')[:50]}” → youtu.be/{vid}"
            self._mark_uploaded(path, vid)
            self.next_upload_at = time.time() + self._delay_seconds()
        elif err == "quota":
            self.last_result = "Daily upload quota reached; waiting for reset."
            self.next_upload_at = time.time() + 3600  # re-check hourly
        else:
            n = self._fail_counts.get(clip["path"], 0) + 1
            self._fail_counts[clip["path"]] = n
            if n >= 3:
                self.queue.pop(0)
                self.last_result = f"Skipped a clip after 3 upload errors: {err}"
                self.next_upload_at = time.time()
            else:
                self.last_result = f"Upload error ({err}); retrying."
                self.next_upload_at = time.time() + 900  # 15 min
        self._save()

    def _mark_uploaded(self, video_path: Path, youtube_id: str) -> None:
        meta = video_path.with_suffix(".json")
        if not meta.exists():
            return
        try:
            d = json.loads(meta.read_text())
            d["youtube_id"] = youtube_id
            d["uploaded_at"] = datetime.now().isoformat()
            meta.write_text(json.dumps(d, indent=2))
        except (json.JSONDecodeError, OSError):
            pass

    def _loop(self) -> None:
        while self.enabled:
            try:
                if not self.queue:
                    self._generate()
                    if not self.queue:
                        self._sleep(1800)  # nothing produced; wait before retry
                    continue

                now = time.time()
                if self.next_upload_at and now < self.next_upload_at:
                    self._sleep(min(60.0, self.next_upload_at - now))
                    continue

                self._upload_next()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self.last_result = f"Autopilot error: {exc}"
                self._save()
                self._sleep(300)
