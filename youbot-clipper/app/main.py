"""FastAPI web app: gallery + Start button.

All frontend URLs are relative so the same server works both when accessed
directly on the mapped port (http://<host>:8099) and through Home Assistant
ingress (which serves the app under a token-prefixed path).
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import blacklist, storage
from .config import load_settings
from .pipeline import Pipeline

settings = load_settings()
pipeline = Pipeline(settings)

app = FastAPI(title="Youbot Shorts Clipper")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
_CHUNK = 1024 * 1024


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
def status():
    return JSONResponse(pipeline.state.snapshot())


@app.get("/api/config")
def config():
    s = settings
    return {
        "gameplay_url": s.gameplay_url,
        "trending_region": s.trending_region,
        "clip_seconds": s.clip_seconds,
        "output_dir": str(s.output_dir),
    }


@app.post("/api/start")
def start():
    if pipeline.start():
        return {"ok": True, "message": "Started."}
    return JSONResponse(
        {"ok": False, "message": "A job is already running."}, status_code=409
    )


@app.get("/api/clips")
def clips():
    return {"clips": storage.list_clips(settings.output_dir)}


@app.get("/api/blacklist")
def blacklist_info():
    return {"count": blacklist.count(settings.data_dir)}


@app.post("/api/blacklist/reset")
def blacklist_reset():
    return {"ok": True, "cleared": blacklist.clear(settings.data_dir)}


@app.get("/thumbs/{filename}")
def thumb(filename: str):
    path = storage.safe_path(settings.output_dir, filename)
    if not path or path.suffix.lower() != ".jpg":
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/clips/{filename}")
def clip_file(filename: str, request: Request):
    path = storage.safe_path(settings.output_dir, filename)
    if not path or path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=404)

    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(path, media_type=content_type)

    match = _RANGE_RE.match(range_header)
    if not match:
        return FileResponse(path, media_type=content_type)

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1
    end = min(end, file_size - 1)
    if start > end:
        raise HTTPException(status_code=416)
    length = end - start + 1

    def iterator():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        iterator(), status_code=206, media_type=content_type, headers=headers
    )


@app.get("/health")
def health():
    return Response(status_code=204)
