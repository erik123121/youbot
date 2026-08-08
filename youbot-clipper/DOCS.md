# Youbot Shorts Clipper

Turns a currently-trending YouTube video into a vertical 9:16 Short: the most
replayed moment (≤40s) is placed on top, with copyright-free GTA mega-ramp
gameplay filling the bottom half so there are no black bars.

> **Personal use.** The top-half clip comes from someone else's video, so the
> result is not something to publish or monetize as-is.

## How it works

Press **Start** and the add-on:

1. Ensures the configured gameplay video is downloaded and cached (once).
2. Scrapes YouTube's trending feed (no API key).
3. For each trending video, reads YouTube's "most replayed" graph and picks the
   single highest-replay moment, building a window of up to `clip_seconds`.
   Videos with no replay data are skipped.
4. Downloads that video and renders the split-screen Short with ffmpeg.
5. Saves the `.mp4` (plus a thumbnail and a metadata `.json`) to `output_dir`.

## Opening the UI

* **In Home Assistant:** click **Shorts Clipper** in the sidebar (ingress).
* **Directly:** `http://<your-ha-ip>:8099`

## Options

| Option | Default | Description |
|---|---|---|
| `gameplay_url` | GTA mega-ramp video | Source for the bottom-half footage. Downloaded and cached once; delete the add-on's `gameplay/` cache to force a re-download after changing this. |
| `trending_region` | `US` | Region code used to bias the trending feed. |
| `candidate_count` | `30` | How many trending videos to scan for replay data before giving up. |
| `clip_seconds` | `35` | Length of the moment (max 40). |
| `output_dir` | `/media/youbot` | Where finished Shorts are saved. `/media/...` makes them appear in the HA Media browser. |
| `max_source_minutes` | `30` | Skip source videos longer than this (avoids live streams / very long uploads). |

## Notes

* No GPU is required; rendering is ffmpeg + libx264 on the CPU.
* Only the source clip's audio is kept; gameplay is muted.
* If no trending video exposes replay data, the run reports an error — just try
  again a bit later.
