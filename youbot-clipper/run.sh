#!/usr/bin/env sh
# Add-on entrypoint.
#
# On every (re)start this can self-update from GitHub so you rarely need HA's
# Update button:
#   * auto_update_addon  (default true): pull the latest app code from the
#     repo's main branch and run that instead of the code baked into the image.
#   * auto_update_ytdlp  (default true): upgrade yt-dlp to the latest release.
#
# Both are best-effort: if the box is offline (or GitHub is unreachable) the
# step is skipped and the bundled version keeps working. NOTE: changes to
# config.yaml (options, ports, version, icon) are read by Home Assistant only
# during a real add-on update/build, so those still need the HA Update button;
# everything under app/ updates on restart.

REPO_TARBALL="https://github.com/erik123121/youbot/archive/refs/heads/main.tar.gz"

read_opt() {
  # read_opt <key> <default> -> prints true/false
  python3 - "$1" "$2" <<'PY' 2>/dev/null || echo "$2"
import json, os, sys
key, default = sys.argv[1], sys.argv[2]
try:
    p = "/data/options.json"
    v = json.load(open(p)).get(key, default == "true") if os.path.exists(p) else (default == "true")
except Exception:
    v = default == "true"
print("true" if v else "false")
PY
}

AUTO_ADDON=$(read_opt auto_update_addon true)
AUTO_YTDLP=$(read_opt auto_update_ytdlp true)

if [ "$AUTO_ADDON" = "true" ]; then
  echo "[youbot] Checking GitHub for the latest add-on code..."
  if python3 /app/self_update.py "$REPO_TARBALL"; then
    # Pick up any dependency changes that came with the new code.
    pip install --no-cache-dir -r /app/requirements.txt >/dev/null 2>&1 || true
  else
    echo "[youbot] Add-on self-update skipped (offline or blocked) — using bundled code."
  fi
else
  echo "[youbot] auto_update_addon disabled — using bundled code."
fi

if [ "$AUTO_YTDLP" = "true" ]; then
  echo "[youbot] Updating yt-dlp to the latest release..."
  if pip install -U --no-cache-dir yt-dlp >/dev/null 2>&1; then
    echo "[youbot] yt-dlp is now: $(yt-dlp --version 2>/dev/null || echo unknown)"
  else
    echo "[youbot] yt-dlp update skipped (offline or blocked)."
  fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8099
