# 🤖🥕 Claude Usage Meter — GIFTV Edition

A cheap "GIFTV" desk gadget (GeekMagic SmallTV-Ultra clone) turned into a
live Claude Code usage meter — complete with a walking pixel mascot and a
static capybara mascot in the corner.

![status](https://img.shields.io/badge/status-working-brightgreen)
![platform](https://img.shields.io/badge/platform-Windows-blue)
![vibes](https://img.shields.io/badge/vibes-immaculate-orange)

---

## What it does

Every 5 minutes, a Windows script:

1. Reads **official** Claude Code rate-limit data (5-hour session % and
   7-day weekly %) — not a guess, the real numbers Anthropic sends to
   Claude Code itself.
2. Draws a 240×240 animated GIF: a title bar ("CLAUDE" + mini mascot icon
   + a static capybara), two progress bars, reset countdowns, and a bigger
   pixel mascot walking back and forth along the bottom.
3. Uploads it to the GIFTV device over the LAN and tells it to display it.

No cloud service, no Admin API key, no soldering. Just a desk gadget that
was originally a weather clock, repurposed.

---

## Hardware

| | |
|---|---|
| Device | "GIFTV" branded clone of a GeekMagic SmallTV-Ultra |
| Chip | ESP8266 (no USB data line — WiFi only, confirmed no COM port appears when plugged in) |
| Stock firmware | `Ultra-V9.0.50` |
| Screen | 1.54" 240×240 IPS |
| Network | 2.4 GHz only (5 GHz not supported) |

We did **not** flash custom firmware. Everything runs on the stock
firmware's built-in HTTP endpoints (see below). Lower risk, works
immediately, and the stock firmware turned out to support everything
needed once we found the right calls.

### Discovered stock-firmware endpoints

| Purpose | Endpoint | Method | Notes |
|---|---|---|---|
| Upload image | `/doUpload?dir=/image/` | `POST` multipart | field name **`image`** for GIF, `file` for pre-cropped JPG |
| List files | `/filelist?dir=/image/` | `GET` | ⚠️ lowercase `filelist`, returns an HTML `<table>`, **not JSON** |
| Storage info | `/space.json` | `GET` | `{total, free}` in KB |
| Set theme | `/set?theme=<1-7>` | `GET` | **theme `3` = custom image mode** on this Ultra firmware — required before `set?img` will do anything |
| Display an image | `/set?img=/image/<filename>` | `GET` | needs the **full `/image/` prefix**, and theme must already be `3` |
| Delete file | `/delete?file=<filename>` | `GET` | |
| Clear all images | `/set?clear=image` | `GET` | |
| Brightness | `/set?brt=<0-255>` | `GET` | |

**Known firmware bug:** the device's embedded web server sends two
conflicting `Content-Length` headers on the `/doUpload` response (e.g.
`3599` and `4286`). This violates RFC 7230, so `curl`, `requests`, and
raw sockets all correctly refuse to parse the response — even though the
upload itself succeeds server-side. **Workaround:** ignore the POST
response entirely and verify success by calling `/filelist?dir=/image/`
afterward and checking the filename shows up.

Big thanks to [`giovi321/smalltv-mod`](https://github.com/giovi321/smalltv-mod)
and [`Avinava/glimmer`](https://github.com/Avinava/glimmer) — two open
custom-firmware projects for this same device family — whose docs and
source were the map for figuring out the theme/custom-image relationship
and endpoint shapes even though we ended up staying on stock firmware.

---

## Getting real usage numbers (the hard part)

This took several wrong turns, kept here so nobody has to relearn them:

1. **❌ Admin API** — needs an organization-level key; not available on
   this account type.
2. **❌ `ccusage` `tokenLimitStatus.percentUsed`** — looked promising but
   is a *forecast* ("if this burn rate continues you'll hit X% by reset"),
   not "% used right now." Using it as a live gauge gave numbers 2-4×
   off from the real dashboard.
3. **❌ Back-calculating a fixed token ceiling** from `totalTokens` ÷
   (known %) — unreliable because Anthropic likely weights tokens
   unevenly (cache tokens, per-model multipliers), so the ratio drifts
   session to session. Two calibration attempts gave ceilings 30% apart.
4. **✅ `claude-monitor --statusline`** — the fix. Claude Code supports a
   `statusLine` hook in `~/.claude/settings.json` that Anthropic feeds
   with **official `rate_limits`** data on every status bar refresh.
   [`Maciek-roboblog/Claude-Code-Usage-Monitor`](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)
   can register as that hook and write the captured payload to a local
   JSON state file. This is the same number `claude.ai/settings/usage`
   shows — confirmed matching within ~1%.

**Limitation:** the hook only fires while Claude Code is actively being
used, so the numbers freeze when Claude Code isn't running. Acceptable
tradeoff — no usage happening means nothing is changing anyway.

**Also confirmed:** Anthropic does **not** expose a per-model (e.g.
Fable-only) weekly percentage to any client. The dashboard's per-model
bars are presumably server-side-only. Any "Fable %" from a local tool is
an estimate, not official — we left it out of the display for that
reason.

### `statusLine` hook config (`~/.claude/settings.json`)

```json
"statusLine": {
  "type": "command",
  "command": "claude-monitor --statusline"
}
```

State gets written to `~/.claude-monitor/statusline/latest.json` and is
merged into `official_state.json` for the meter script to read.

---

## Software stack

- **Python 3.13** + `Pillow` (GIF generation), `requests` (upload),
  `python-dotenv` (config)
- **`claude-monitor`** (pip) for the official statusline capture
- **Windows Task Scheduler** — runs `meter.py` every 5 minutes

## Files

```
giftv-claude-meter/
├── meter.py               # main script: read data → draw GIF → upload → display
├── .env                    # GIFTV_IP + endpoints (NEVER commit — see .gitignore)
├── official_state.json     # latest official rate_limits snapshot (written by the hook)
├── token_state.json        # fallback cache if official data goes stale
├── weather_state.json      # cached IP-geolocated location + weather (45 min TTL, offline fallback)
├── claude-meter.gif        # last-generated frame set (regenerated every run)
├── requirements.txt
├── setup-task.ps1          # registers the Windows Task Scheduler job
└── logs/
    └── meter.log
```

## Setup

```powershell
cd giftv-claude-meter
pip install -r requirements.txt
pip install claude-monitor --break-system-packages

# add the statusLine hook to Claude Code settings (see above), then
# open Claude Code and send one message so the hook fires at least once

# register the 5-minute scheduled task
powershell -ExecutionPolicy Bypass -File setup-task.ps1

# test once by hand
python meter.py
```

Display config on the device itself: connect to its AP the first time,
give it your 2.4 GHz WiFi (⚠️ **not** 5 GHz — the device silently ignores
5 GHz networks), then find its new LAN IP from your router's client list
or the device screen on boot.

Clock/date on the display uses your system's local timezone automatically —
no configuration needed.

Weather on the display is auto-detected from your IP address (via
[ipapi.co](https://ipapi.co) for location, then
[Open-Meteo](https://open-meteo.com) for the forecast) — no config, no API
key, no hardcoded city. It's cached for 45 minutes so a clone of this repo
doesn't hammer either free service on every 1-minute run, and if either
lookup fails (offline, rate-limited) it silently falls back to the last
cached reading instead of erroring or blanking the display.

## What's on screen

- **Header:** small mini mascot icon (carrot hat included) + "CLAUDE" title
  (hand-drawn pixel font)
- **Top-right:** a small static capybara, sitting still
- **Date/time row:** local date + time, with a small weather icon + °C
  inline when a reading is available (falls back to its own second line if
  the combined text would run off the edge)
- **Current (5h) bar** — orange, official %, reset countdown shown beside
  the bar
- **Weekly (7d) bar** — lime, official %, reset countdown shown beside the
  bar
- **Bottom strip:** a bigger version of the mascot (also wearing its carrot
  hat) walking back and forth, blinking occasionally, alternating legs
  mid-step

All hand-drawn with `Pillow` — no external image assets, no downloaded
fonts, no emoji glyphs, just rectangles/ellipses/polygons and a
triangle-wave walk cycle. Even the title font and weather icons are
hand-drawn from scratch (a 5×7 pixel bitmap font, and ~14px icon shapes for
sun/cloud/fog/rain/snow/storm).

---

## Known limitations

- Numbers freeze when Claude Code isn't actively running (statusline-hook
  dependent).
- No official per-model (Fable/Opus/Sonnet) weekly split exists anywhere,
  local or remote — not implementable without Anthropic exposing it.
- ESP8266 stock firmware only accepts JPG/GIF, not PNG (learned the hard
  way after the meter card silently failed to display for several
  iterations).
- The device's `/doUpload` response is malformed per RFC 7230; any HTTP
  client doing strict parsing needs the ignore-and-verify workaround
  above.
- `ipapi.co`'s free tier is IP-based rate limiting shared across everyone
  on the same network/ISP — on a busy connection it can return `429` even
  on your very first request. The script handles this (falls back to
  cached location, then skips weather entirely if there's no cache yet)
  but it means weather may not appear until the rate limit clears.

## Credits

- [`giovi321/smalltv-mod`](https://github.com/giovi321/smalltv-mod) and
  [`Avinava/glimmer`](https://github.com/Avinava/glimmer) — GeekMagic
  SmallTV custom-firmware projects that mapped out this device family's
  endpoints and theme system
- [`Maciek-roboblog/Claude-Code-Usage-Monitor`](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) —
  the `--statusline` official rate-limit capture that made real numbers
  possible
- [`ryoppippi/ccusage`](https://github.com/ryoppippi/ccusage) — local
  Claude Code session log parsing (used during the investigation, even
  though its `percentUsed` field turned out to be a forecast rather than
  a live gauge)

---

*Built during a very long Claude Code wait, one debugging session at a
time. 555.*
