# youbot

A Home Assistant add-on repository.

## Add-ons

### [Youbot Shorts Clipper](youbot-clipper/)

Turns podcast episodes into vertical YouTube Shorts: an engaging 20–40s moment
goes on the top half, copyright-free GTA mega-ramp gameplay fills the bottom
half (no black bars). Works through a list of podcast channels newest-to-oldest
(never repeating an episode) and picks moments with OpenAI. Includes a web
gallery of every clip made and a single **Start** button. Runs as a standalone
add-on on an Intel NUC (amd64).

## Installing on Home Assistant

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Open the ⋮ menu (top right) → **Repositories**.
3. Add this repository URL: `https://github.com/erik123121/youbot`
4. Install **Youbot Shorts Clipper**, then start it.
5. Open it from the sidebar, or directly at `http://<your-ha-ip>:8099`.
