"""Self-update the add-on's app/ code from the GitHub repo's main branch.

Called from run.sh on startup. Downloads the repo tarball, verifies it contains
a sane app/ tree, then atomically swaps it into /app/app and refreshes
/app/requirements.txt. On any failure it leaves the existing (baked) code in
place and exits non-zero so startup continues normally.

Usage: python3 self_update.py <tarball_url>
"""
from __future__ import annotations

import io
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

APP_DIR = Path("/app/app")
REQ_FILE = Path("/app/requirements.txt")
TIMEOUT = 30


def fail(msg: str) -> int:
    print(f"[youbot] self-update: {msg}")
    return 1


def main(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "youbot-clipper"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        return fail(f"download failed: {exc}")

    tmp = Path(tempfile.mkdtemp(prefix="youbot_update_"))
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(tmp)  # trusted source: our own repo tarball
        except Exception as exc:  # noqa: BLE001
            return fail(f"extract failed: {exc}")

        # Locate <root>/youbot-clipper inside the extracted tree.
        addon_dirs = list(tmp.glob("*/youbot-clipper"))
        if not addon_dirs:
            return fail("youbot-clipper/ not found in tarball")
        src = addon_dirs[0]
        new_app = src / "app"

        # Sanity check: the new code must at least have main.py.
        if not (new_app / "main.py").is_file():
            return fail("downloaded app/ looks incomplete (no main.py)")

        # Atomic-ish swap: stage next to the live dir, then rename.
        staging = APP_DIR.parent / "app.new"
        backup = APP_DIR.parent / "app.bak"
        for stale in (staging, backup):
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)
        shutil.copytree(new_app, staging)

        if APP_DIR.exists():
            APP_DIR.rename(backup)
        staging.rename(APP_DIR)
        shutil.rmtree(backup, ignore_errors=True)

        # Refresh requirements so run.sh can install any new deps.
        new_req = src / "requirements.txt"
        if new_req.is_file():
            shutil.copyfile(new_req, REQ_FILE)

        print("[youbot] self-update: applied latest app code from GitHub.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(fail("no tarball URL given"))
    sys.exit(main(sys.argv[1]))
