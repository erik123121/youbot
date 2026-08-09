"""Upload a finished Short to YouTube via the Data API v3 (resumable upload).

Uses an OAuth refresh token (client_id + client_secret + refresh_token from the
user's own Google Cloud project) — no heavy SDK, just urllib. Returns
(video_id, None) on success or (None, reason) on failure. A dedicated "quota"
reason lets the caller back off until the daily quota resets.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
_TIMEOUT = 120


def _access_token(client_id: str, client_secret: str, refresh_token: str):
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            pass
        return None, f"token refresh failed (HTTP {exc.code}): {' '.join(detail.split())[:160]}"
    except Exception as exc:  # noqa: BLE001
        return None, f"token refresh failed: {exc}"
    token = payload.get("access_token")
    if not token:
        return None, "token refresh returned no access_token"
    return token, None


def upload_short(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    video_path: Path,
    title: str,
    description: str,
    privacy: str = "public",
    tags=None,
) -> Tuple[Optional[str], Optional[str]]:
    if not (client_id and client_secret and refresh_token):
        return None, "YouTube not configured (missing client id/secret/refresh token)"
    if not video_path.exists():
        return None, f"file not found: {video_path}"

    token, err = _access_token(client_id, client_secret, refresh_token)
    if err:
        return None, err

    metadata = {
        "snippet": {
            "title": title[:100] or "Short",
            "description": description[:4900],
            "tags": tags or ["shorts", "podcast"],
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": privacy if privacy in ("public", "unlisted", "private") else "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    meta_bytes = json.dumps(metadata).encode("utf-8")
    file_size = os.path.getsize(video_path)

    # Step 1: start a resumable session.
    init_req = urllib.request.Request(
        _UPLOAD_URL,
        data=meta_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        },
    )
    try:
        with urllib.request.urlopen(init_req, timeout=_TIMEOUT) as resp:
            location = resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return None, _http_error(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"upload init failed: {exc}"
    if not location:
        return None, "upload init returned no session URL"

    # Step 2: PUT the bytes.
    with open(video_path, "rb") as f:
        body = f.read()
    put_req = urllib.request.Request(
        location,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "video/mp4",
            "Content-Length": str(file_size),
        },
    )
    try:
        with urllib.request.urlopen(put_req, timeout=_TIMEOUT * 5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, _http_error(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"upload failed: {exc}"

    vid = result.get("id")
    if not vid:
        return None, "upload returned no video id"
    return vid, None


def _http_error(exc: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        detail = exc.read().decode("utf-8")
    except Exception:
        pass
    if exc.code == 403 and ("quotaExceeded" in detail or "uploadLimitExceeded" in detail):
        return "quota"
    return f"HTTP {exc.code}: {' '.join(detail.split())[:180]}"
