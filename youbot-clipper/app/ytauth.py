"""YouTube OAuth via the Device Flow — for headless devices.

The user creates a "TV and Limited Input devices" OAuth client, puts its
client_id + client_secret in the add-on config, and clicks Connect YouTube.
We request a device code, they enter it at google.com/device on their phone,
and we poll until Google returns a refresh token, which we store in /data.

No redirect URIs, no OAuth Playground, no pasting a long refresh token.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from .config import Settings

_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _token_file(settings: Settings):
    return settings.data_dir / "youtube_token.json"


def stored_refresh_token(settings: Settings) -> str:
    """Refresh token from the device-flow store, falling back to config."""
    f = _token_file(settings)
    if f.exists():
        try:
            tok = json.loads(f.read_text()).get("refresh_token")
            if tok:
                return tok
        except (json.JSONDecodeError, OSError):
            pass
    return settings.youtube_refresh_token


def _post(url: str, data: dict):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8")), None  # errors carry JSON too
        except Exception:
            return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


class YouTubeAuth:
    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.pending: Optional[dict] = None  # {user_code, verification_url, ...}
        self.message = "Connected." if self.is_connected() else "Not connected."

    def is_connected(self) -> bool:
        return bool(stored_refresh_token(self.s))

    def _save(self, refresh_token: str) -> None:
        try:
            _token_file(self.s).write_text(json.dumps({"refresh_token": refresh_token}))
        except OSError:
            pass

    def disconnect(self) -> None:
        with self._lock:
            self.pending = None
            try:
                _token_file(self.s).unlink(missing_ok=True)
            except OSError:
                pass
            self.message = "Disconnected."

    def start(self) -> Tuple[bool, str]:
        if not (self.s.youtube_client_id and self.s.youtube_client_secret):
            return False, "Set youtube_client_id and youtube_client_secret in config first."
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True, "Authorization already in progress."
            data, err = _post(_DEVICE_CODE_URL, {
                "client_id": self.s.youtube_client_id, "scope": _SCOPE,
            })
            if err or not data or "device_code" in data and data.get("error"):
                return False, f"Could not start: {err or data.get('error')}"
            if "device_code" not in data:
                return False, f"Could not start: {data.get('error_description', data)}"
            self.pending = {
                "device_code": data["device_code"],
                "user_code": data.get("user_code", ""),
                "verification_url": data.get("verification_url")
                or data.get("verification_uri", "https://www.google.com/device"),
                "interval": int(data.get("interval", 5)),
                "expires_at": time.time() + int(data.get("expires_in", 1800)),
            }
            self.message = "Enter the code at the link to connect."
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()
            return True, "started"

    def _poll(self) -> None:
        p = self.pending
        if not p:
            return
        interval = p["interval"]
        while self.pending and time.time() < p["expires_at"]:
            time.sleep(interval)
            data, err = _post(_TOKEN_URL, {
                "client_id": self.s.youtube_client_id,
                "client_secret": self.s.youtube_client_secret,
                "device_code": p["device_code"],
                "grant_type": _GRANT,
            })
            if err or not data:
                continue
            if data.get("refresh_token"):
                self._save(data["refresh_token"])
                self.pending = None
                self.message = "Connected."
                return
            e = data.get("error")
            if e in ("authorization_pending",):
                continue
            if e == "slow_down":
                interval += 5
                continue
            if e in ("access_denied", "expired_token", "invalid_client", "invalid_grant"):
                self.pending = None
                self.message = f"Authorization failed: {e}"
                return
        if self.pending:
            self.pending = None
            self.message = "Code expired — try Connect again."

    def status(self) -> dict:
        pend = None
        if self.pending:
            pend = {
                "user_code": self.pending["user_code"],
                "verification_url": self.pending["verification_url"],
            }
        return {
            "connected": self.is_connected(),
            "pending": pend,
            "message": self.message,
        }
