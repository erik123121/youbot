FROM python:3.12-slim-bookworm

# ffmpeg (+ffprobe) is the core of the render pipeline; ca-certificates for HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY run.sh /app/run.sh
COPY self_update.py /app/self_update.py
RUN chmod +x /app/run.sh

# run.sh refreshes yt-dlp on start (see auto_update_ytdlp) then launches the app.
# Reads /data/options.json when run as an HA add-on; falls back to env vars otherwise.
CMD ["/app/run.sh"]
