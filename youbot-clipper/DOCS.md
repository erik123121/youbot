# Youbot Shorts Clipper

Turns podcast episodes into vertical 9:16 Shorts: an engaging 20–40s moment is
placed on the top half, with copyright-free GTA mega-ramp gameplay filling the
bottom half so there are no black bars.

> **Personal use.** The top-half clip comes from someone else's video, so the
> result is not something to publish or monetize as-is.

## How it works

Press **Start** and the add-on:

1. Ensures the configured gameplay video is downloaded and cached (once).
2. Lists episodes from your `podcast_channels` (newest first, whole back
   catalogue), skipping any it has already used.
3. For the first new episode with a transcript, it asks OpenAI (a small, cheap
   model — one call per run) for **all** the genuinely engaging, non-overlapping
   20–`clip_seconds` moments (up to 8). Without a key, or on failure, it falls
   back to an on-device scorer.
4. Downloads **only** those moment segments (not the whole episode) and renders
   each into a Short: a 50/50 split (clip on top, gameplay on the bottom, both
   cover-cropped so there are no black bars).
5. Saves each `.mp4` (plus a thumbnail and metadata `.json`) to `output_dir`,
   then **blacklists** the episode so it is never reused.

## Opening the UI

* **In Home Assistant:** click **Shorts Clipper** in the sidebar (ingress).
* **Directly:** `http://<your-ha-ip>:8099`

## Options

| Option | Default | Description |
|---|---|---|
| `gameplay_url` | GTA mega-ramp video | Source for the bottom-half footage. Downloaded and cached once; delete the add-on's `gameplay/` cache to force a re-download after changing this. |
| `podcast_channels` | Joe Rogan, Theo Von | List of podcast YouTube channels (handles or URLs) to clip from. Add more to extend how long it can run before repeating. |
| `channel_scan_limit` | `500` | How many recent uploads to list per channel (higher reaches deeper into the back catalogue). |
| `clip_seconds` | `35` | Length of the moment (max 40). |
| `output_dir` | `/media/youbot` | Where finished Shorts are saved. `/media/...` makes them appear in the HA Media browser. |
| `max_source_minutes` | `600` | Skip episodes longer than this (a guard against multi-hour live streams). Only the chosen segments are downloaded, so length is otherwise not a problem. |
| `openai_api_key` | _(empty)_ | OpenAI API key used to pick the best moment. Leave empty to use the on-device scorer instead. One small request per run (~$0.0004 with the default model). |
| `openai_model` | `gpt-4.1-nano` | OpenAI model for moment selection. `gpt-4.1-nano` is the cheapest capable option; `gpt-4o-mini` is a slightly pricier alternative. |
| `youtube_client_id` | _(empty)_ | OAuth client ID for auto-uploading (see YouTube setup below). |
| `youtube_client_secret` | _(empty)_ | OAuth client secret. |
| `youtube_refresh_token` | _(empty)_ | OAuth refresh token for your channel. |
| `upload_privacy` | `public` | Visibility of uploaded Shorts: `public`, `unlisted`, or `private`. |
| `upload_min_hours` / `upload_max_hours` | `2` / `5` | Random delay range between uploads. |

## Autopilot (auto-upload to YouTube)

With YouTube configured, press **Start Autopilot** in the UI and the add-on will
continuously: generate a batch of Shorts from one new episode, then drip-upload
them to your channel with a random `upload_min_hours`–`upload_max_hours` delay
each, and generate the next batch when the queue empties. It resumes after a
restart. Press **Stop Autopilot** to pause.

**Note:** YouTube's free API quota allows only **~6 uploads per day**. When the
quota is hit, autopilot waits for the daily reset — so throughput tops out around
6 Shorts/day regardless of the delay setting.

### One-time YouTube setup (Device Flow — no Playground)

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a
   project and **enable the "YouTube Data API v3"** (APIs & Services → Library).
2. **OAuth consent screen**: choose *External*, fill the basics, add the
   `.../auth/youtube.upload` scope, and set the publishing status to **In
   production** (Testing mode expires the token after 7 days).
3. **Credentials → Create credentials → OAuth client ID → application type
   "TVs and Limited Input devices".** No redirect URIs needed. Copy the
   **Client ID** and **Client secret**.
4. Paste the Client ID and Client secret into the add-on config
   (`youtube_client_id`, `youtube_client_secret`), set `upload_privacy`, **Save**,
   and **Restart**.
5. In the add-on UI click **Connect YouTube**. It shows a short code and a link
   (`google.com/device`) — open the link on any device, sign in with your
   channel's Google account, enter the code, and allow. The refresh token is
   stored automatically. Then press **Start Autopilot**.

(The `youtube_refresh_token` config option still works as a manual fallback, but
the Connect button is the easy path.)
| `auto_update_addon` | `true` | On each start, pull the latest app code from the repo's `main` branch and run that. Lets you apply code fixes by just **Restarting** the add-on instead of using HA's Update button. |
| `auto_update_ytdlp` | `true` | On each start, upgrade yt-dlp to the latest release (YouTube frequently breaks older versions). |

## Updating

Two ways to get new code:

* **Just restart** the add-on — with `auto_update_addon` on (default), it fetches
  the latest `app/` code from GitHub `main` at startup. Best for code/logic fixes.
* **HA Update button** — needed only when `config.yaml` itself changes (new
  options, ports, version, icon), since Home Assistant reads that at build time.

Both self-updates are best-effort: if the machine is offline, the bundled
version is used and startup continues normally.

## Notes

* No GPU is required; rendering is ffmpeg + libx264 on the CPU.
* Only the source clip's audio is kept; gameplay is muted.
* If no trending video exposes replay data, the run reports an error — just try
  again a bit later.
