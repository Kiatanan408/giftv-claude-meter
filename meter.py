#!/usr/bin/env python3
"""
Claude Token Meter for GIFTV — final version
Real ccusage-based session %, animated walking-mascot GIF display
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv()

GIFTV_IP = os.getenv("GIFTV_IP", "192.168.1.40")
GIFTV_UPLOAD_URL = os.getenv("GIFTV_UPLOAD_URL", f"http://{GIFTV_IP}/doUpload?dir=/image/")
# NOTE: not reading GIFTV_SET_URL from .env — it's set there as
# "http://.../set?img=" (old format), which would break string-concatenation
# for the new theme= call. Build both URLs directly from GIFTV_IP instead.

# Paths
SCRIPT_DIR = Path(__file__).parent

# Official data source: claude-monitor's --statusline hook (wired into
# ~/.claude/settings.json) writes real Anthropic rate_limits straight to this
# file every time Claude Code renders its status bar. Read directly instead of
# official_state.json (26 ก.ค. 69) — that file only updates when someone runs
# `claude-monitor --once --write-state` by hand, which nothing scheduled, so it
# went stale for hours while this file kept updating live.
LATEST_STATE_FILE = Path.home() / ".claude-monitor" / "statusline" / "latest.json"
STALE_AFTER_HOURS = 2  # warn (not error) if Claude Code hasn't refreshed the file in this long
LOG_DIR = SCRIPT_DIR / "logs"
STATE_FILE = SCRIPT_DIR / "token_state.json"
WEATHER_STATE_FILE = SCRIPT_DIR / "weather_state.json"
WEATHER_CACHE_MINUTES = 45  # open-meteo forecast cache — location doesn't gate this anymore, see LOCATION_CACHE_HOURS
LOCATION_CACHE_HOURS = 24  # ipapi.co lookup cache — this machine's location basically never changes, so there's no
# reason to re-hit ipapi.co every time the (much shorter) weather cache expires. Decoupling these two is what stops
# ipapi.co's free-tier rate limit from getting hammered every 30-60 minutes.
WEATHER_RETRY_MINUTES = 10  # backoff on *failed* location lookups too — without this, a down/rate-limited ipapi.co
# gets hit every single 1-minute run forever, which is what keeps its rate limit from ever clearing
IMAGE_FILE = SCRIPT_DIR / "claude-meter.gif"

LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "meter.log"

# --- GIF animation constants ---
N_FRAMES = 40
FRAME_DURATION_MS = 70
BG_COLOR = (18, 16, 22)
MASCOT_COLOR = (232, 92, 55)
MASCOT_LIGHT = tuple(min(255, c + 40) for c in MASCOT_COLOR)  # bevel highlight (top/left faces)
MASCOT_DARK = tuple(max(0, c - 45) for c in MASCOT_COLOR)  # bevel shadow (bottom/right faces)
GROUND_SHADOW_COLOR = (10, 8, 12)
CAPYBARA_COLOR = (163, 118, 71)
CURRENT_BAR_COLOR = (232, 92, 55)
WEEKLY_BAR_COLOR = (196, 224, 90)
TRACK_COLOR = (58, 48, 78)
TEXT_COLOR = (220, 220, 220)
DIM_TEXT_COLOR = (150, 150, 160)
CARROT_COLOR = (255, 140, 0)
LEAF_COLOR = (76, 175, 80)
SUN_COLOR = (255, 205, 60)
CLOUD_COLOR = (185, 185, 200)
CLOUD_SHADOW_COLOR = (140, 140, 158)
RAIN_COLOR = (100, 150, 230)
SNOW_COLOR = (235, 235, 245)
BOLT_COLOR = (255, 220, 80)
FOG_COLOR = (150, 150, 165)
MOON_COLOR = (225, 225, 240)
STAR_COLOR = (255, 250, 210)

WALK_SCALE = 1.05  # dialed back down from 1.3 — combined with the floating weather icon above it, that felt cluttered


def log_message(msg: str):
    """Log message to file and console"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {msg}"
    print(log_entry)
    with open(LOG_FILE, "a") as f:
        f.write(log_entry + "\n")


def load_state():
    """Load last-known usage state from JSON (used as fallback if official_state.json fails)"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "current_percent": 0.0,
        "current_reset": "-",
        "weekly_percent": 0.0,
        "weekly_reset": "-",
        "last_update": None,
    }


def save_state(state):
    """Save usage state to JSON"""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_weather_state():
    """Load last-known location + weather from JSON (cache + offline fallback)"""
    if WEATHER_STATE_FILE.exists():
        try:
            with open(WEATHER_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_weather_state(state):
    with open(WEATHER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _weather_icon_category(code: int) -> str:
    """Map an Open-Meteo WMO weather_code to one of our hand-drawn icon buckets."""
    if code in (0, 1):
        return "clear"
    if code in (2, 3):
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 99:
        return "storm"
    return "cloudy"


def get_location(state: dict):
    """
    IP-based geolocation (ipapi.co — free, no API key) so the display shows
    wherever this script is actually running, instead of a hardcoded city.

    Cached in state["location"] for LOCATION_CACHE_HOURS (24h) — this is
    decoupled from the much shorter weather cache on purpose: the machine's
    location practically never changes, so re-hitting ipapi.co every time
    the 30-60min weather cache expires was hammering its free-tier rate
    limit for no reason. As long as the location cache is still valid, this
    returns straight from it without calling ipapi.co at all.

    Falls back to the last cached lat/lon/city on any failure (offline,
    rate-limited, etc.) rather than erroring, and backs off failed retries
    (WEATHER_RETRY_MINUTES) so a down/rate-limited ipapi.co doesn't get
    hit every single 1-minute run.
    """
    location = state.get("location")
    if location:
        age_hours = (datetime.now().timestamp() - location["fetched_at"]) / 3600
        if age_hours < LOCATION_CACHE_HOURS:
            return location["lat"], location["lon"], location["city"]

    last_attempt = state.get("location_last_attempt")
    if last_attempt is not None:
        attempt_age_minutes = (datetime.now().timestamp() - last_attempt) / 60
        if attempt_age_minutes < WEATHER_RETRY_MINUTES:
            log_message(
                f"Skipping location retry ({attempt_age_minutes:.0f}m since last attempt, "
                f"backing off {WEATHER_RETRY_MINUTES}m)"
            )
            if location:
                return location["lat"], location["lon"], location["city"]
            return None, None, None

    state["location_last_attempt"] = datetime.now().timestamp()
    save_weather_state(state)

    try:
        resp = requests.get("https://ipapi.co/json/", timeout=5)
        data = resp.json()
        lat, lon, city = data["latitude"], data["longitude"], data.get("city", "-")
        state["location"] = {"lat": lat, "lon": lon, "city": city, "fetched_at": datetime.now().timestamp()}
        save_weather_state(state)
        log_message(f"IP geolocation: {city} ({lat}, {lon}) — cached {LOCATION_CACHE_HOURS}h")
        return lat, lon, city
    except Exception as e:
        log_message(f"IP geolocation failed: {e}")
        if location:
            log_message("Using stale cached location")
            return location["lat"], location["lon"], location["city"]
        log_message("No cached location available yet")
        return None, None, None


def get_weather():
    """
    Current temperature + condition from Open-Meteo (free, no API key), using
    the lat/lon from get_location()'s own long-lived cache. Weather itself is
    cached separately in state["weather"] for WEATHER_CACHE_MINUTES (30-60min)
    since forecasts do need to refresh more often than location.

    Never raises on failure (no internet, API down, no location yet) — falls
    back to the last cached weather, or None if nothing has ever been cached.
    """
    state = load_weather_state()
    weather = state.get("weather")
    city = state.get("location", {}).get("city", "-")
    if weather is not None:
        age_minutes = (datetime.now().timestamp() - weather["cached_at"]) / 60
        if age_minutes < WEATHER_CACHE_MINUTES:
            log_message(f"Weather cache hit ({age_minutes:.0f}m old): {city} {weather['temp_c']}°C")
            return {**weather, "city": city}

    lat, lon, city = get_location(state)
    if lat is None:
        if weather is not None:
            log_message("Using stale cached weather (no location available)")
            return {**weather, "city": city}
        return None

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current=temperature_2m,cloud_cover,weather_code"
        )
        resp = requests.get(url, timeout=5)
        current = resp.json()["current"]
        temp_c = round(current["temperature_2m"])
        code = int(current["weather_code"])
        icon = _weather_icon_category(code)

        weather = {"temp_c": temp_c, "weather_code": code, "icon": icon, "cached_at": datetime.now().timestamp()}
        state["weather"] = weather
        save_weather_state(state)
        log_message(f"Weather: {city} {temp_c}°C, code {code} ({icon})")
        return {**weather, "city": city}
    except Exception as e:
        log_message(f"Weather fetch failed: {e} — using last cached weather")
        if weather is not None:
            return {**weather, "city": city}
        return None


def _format_countdown(reset_value, now: datetime, day_format: bool) -> str:
    """Format a reset timestamp (epoch int/float, or ISO string) as 'Xh Ym'
    (day_format=False) or 'Xd Yh' (True)."""
    if isinstance(reset_value, (int, float)):
        reset_dt = datetime.fromtimestamp(reset_value, tz=timezone.utc)
    else:
        reset_dt = datetime.fromisoformat(reset_value)
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
    delta = reset_dt - now
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    if day_format:
        days, rem_minutes = divmod(total_minutes, 60 * 24)
        hours = rem_minutes // 60
        return f"{days}d {hours}h"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def get_token_usage():
    """
    Real usage % straight from Anthropic's official rate_limits — no local
    token-count guessing. The claude-monitor --statusline hook (wired into
    ~/.claude/settings.json) writes the real payload to LATEST_STATE_FILE
    every time Claude Code renders its status bar. This function reads that
    file directly, so it's only ever as stale as the last time Claude Code
    itself was used.

    NOTE: there is no official per-model (e.g. Fable-only) weekly % — the
    rate_limits payload Anthropic sends only ever carries aggregate
    five_hour/seven_day percentages, confirmed by reading claude-monitor's
    own source (output/official.py). Not shown here for that reason.

    Freshness: if the file is older than STALE_AFTER_HOURS (Claude Code
    hasn't been used/refreshed in a while), log a warning but still show
    the last real numbers — never error out over staleness alone.

    On any read/parse failure — fall back to the last successfully-read
    values in token_state.json, not a random mock.

    Returns: dict with current_percent, current_reset, weekly_percent,
    weekly_reset, last_update
    """
    try:
        if not LATEST_STATE_FILE.exists():
            raise FileNotFoundError(f"{LATEST_STATE_FILE} does not exist yet")

        age_hours = (
            datetime.now().timestamp() - LATEST_STATE_FILE.stat().st_mtime
        ) / 3600
        if age_hours > STALE_AFTER_HOURS:
            log_message(f"WARNING: latest.json is stale data ({age_hours:.1f}h old)")

        with open(LATEST_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        rate_limits = data["rate_limits"]
        five_hour = rate_limits["five_hour"]
        seven_day = rate_limits["seven_day"]

        now = datetime.now(timezone.utc)
        current_percent = float(five_hour["used_percentage"])
        current_reset = _format_countdown(five_hour["resets_at"], now, day_format=False)
        weekly_percent = float(seven_day["used_percentage"])
        weekly_reset = _format_countdown(seven_day["resets_at"], now, day_format=True)

        state = {
            "current_percent": current_percent,
            "current_reset": current_reset,
            "weekly_percent": weekly_percent,
            "weekly_reset": weekly_reset,
            "last_update": datetime.now().isoformat(),
        }
        save_state(state)

        log_message(
            f"Token check: session {current_percent:.1f}% (official, reset {current_reset}), "
            f"weekly {weekly_percent:.1f}% (official, reset {weekly_reset})"
        )
        return state

    except Exception as e:
        log_message(f"Error reading latest.json: {e} — using last known state")
        state = load_state()
        log_message(
            f"Last known: session {state.get('current_percent', 0):.1f}%, "
            f"weekly {state.get('weekly_percent', 0):.1f}%"
        )
        return state


# Hand-drawn blocky pixel font (5 wide x 7 tall grid per glyph) — no TTF
# file needed at all, so no font-path/fallback issues on any OS. Only the
# glyphs this design actually uses are defined (uppercase letters that
# appear in CLAUDE/CURRENT/WEEKLY/RESET, digits, %, space). Add more rows
# here if new text is ever needed. '1' = filled pixel, '0' = empty.
PIXEL_FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10001", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    ":": ["00000", "00100", "00100", "00000", "00100", "00100", "00000"],
    "°": ["01100", "10010", "10010", "01100", "00000", "00000", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "·": ["00000", "00000", "00000", "01100", "01100", "00000", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00010", "00110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "%": ["10001", "10010", "00010", "00100", "01000", "01001", "10001"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}
PIXEL_FONT_COLS = 5
PIXEL_FONT_ROWS = 7


def _draw_pixel_text(draw, x, y, text, scale, color, spacing=1):
    """Draw text using the hand-made PIXEL_FONT — each glyph cell becomes a scale x scale block."""
    cursor_x = x
    for ch in text.upper():
        glyph = PIXEL_FONT.get(ch, PIXEL_FONT[" "])
        for row_idx, row in enumerate(glyph):
            for col_idx, bit in enumerate(row):
                if bit == "1":
                    px = cursor_x + col_idx * scale
                    py = y + row_idx * scale
                    draw.rectangle([(px, py), (px + scale - 1, py + scale - 1)], fill=color)
        cursor_x += (PIXEL_FONT_COLS + spacing) * scale
    return cursor_x - x  # total width drawn, in case a caller wants to center/right-align


def _pixel_text_width(text, scale, spacing=1):
    return len(text) * (PIXEL_FONT_COLS + spacing) * scale


def _draw_bar(draw, x, y, width, height, percent, fill_color):
    """Draw a track + proportional fill. percent=None draws outline only (unknown %)."""
    draw.rectangle([(x, y), (x + width, y + height)], fill=TRACK_COLOR, outline=(90, 78, 110), width=1)
    if percent is not None:
        filled = int(width * min(max(percent, 0), 100) / 100)
        if filled > 0:
            draw.rectangle([(x, y), (x + filled, y + height)], fill=fill_color)


def _draw_bevel_block(draw, x0, y0, x1, y1, base, light, dark, bevel=2):
    """
    Block with simple bevel shading, adapted from the make_mascot_3d.py
    reference: base fill, then a thin highlight strip on the top+left edges
    and a thin shadow strip on the bottom+right edges — the classic 2.5D
    "beveled button" look, at pixel-art scale.
    """
    draw.rectangle([(x0, y0), (x1, y1)], fill=base)
    draw.rectangle([(x0, y0), (x1, y0 + bevel - 1)], fill=light)
    draw.rectangle([(x0, y0), (x0 + bevel - 1, y1)], fill=light)
    draw.rectangle([(x1 - bevel + 1, y0), (x1, y1)], fill=dark)
    draw.rectangle([(x0, y1 - bevel + 1), (x1, y1)], fill=dark)


def _draw_walking_mascot(draw, x, y_baseline, leg_forward, blinking, scale=1.0):
    """
    Blocky pixel mascot at (x, y_baseline) — feet touch y_baseline. Squat
    proportions (wide, short body, no arms, 4 legs spread across the width)
    adapted from the make_mascot_squat.py reference's 7x2-unit body/leg
    layout. Body and legs use bevel-shaded blocks (light top/left, dark
    bottom/right), plus a soft ground shadow beneath the feet; eyes stay
    flat (no bevel) so they read clearly against the shaded body.
    leg_forward: alternates which diagonal pair of the 4 legs is raised,
    the walk cadence (equivalent to the old 2-leg stagger, extended to 4).
    blinking: True draws closed eyes for this frame
    scale: shrinks every dimension proportionally — scale=1.0 is the big
    walking mascot at the bottom; a small scale (with leg_forward=False,
    blinking=False) gives a static title-icon version of the same shape.
    """
    body_w, body_h = max(4, int(42 * scale)), max(2, int(14 * scale))
    body_x = x - body_w // 2
    body_y = y_baseline - body_h - max(1, int(8 * scale))  # leave room for legs below body
    bevel = max(1, round(2 * scale))

    # Soft ground shadow, drawn first (under everything) so it reads as the
    # mascot standing on the floor rather than floating — moves with x/legs
    # every frame since it's derived from the same params as the rest. Scales
    # with body_w automatically, so it's already wider on the wider body.
    shadow_w = int(body_w * 1.4)
    shadow_h = max(2, int(6 * scale))
    shadow_cy = y_baseline - max(1, int(2 * scale))
    draw.ellipse(
        [(x - shadow_w // 2, shadow_cy - shadow_h // 2), (x + shadow_w // 2, shadow_cy + shadow_h // 2)],
        fill=GROUND_SHADOW_COLOR,
    )

    # 4 legs spread across the wider body (positions match the reference's
    # gx=[0.3, 2.1, 3.9, 5.7] out of a 7-unit-wide body, as x-fractions).
    leg_w = max(1, int(5 * scale))
    leg_h = max(1, int(10 * scale))
    leg_y = body_y + body_h - max(1, int(2 * scale))
    leg_stagger = max(1, int(4 * scale))
    leg_x_fractions = (0.3 / 7, 2.1 / 7, 3.9 / 7, 5.7 / 7)
    for idx, frac in enumerate(leg_x_fractions):
        leg_x = body_x + int(frac * body_w)
        raised = (idx % 2 == 0) == leg_forward  # alternating diagonal pairs, like a 4-legged trot
        ly = leg_y - leg_stagger if raised else leg_y
        _draw_bevel_block(draw, leg_x, ly, leg_x + leg_w, ly + leg_h, MASCOT_COLOR, MASCOT_LIGHT, MASCOT_DARK, bevel)

    # Body
    _draw_bevel_block(draw, body_x, body_y, body_x + body_w, body_y + body_h, MASCOT_COLOR, MASCOT_LIGHT, MASCOT_DARK, bevel)

    # Eyes — positioned within the wider/shorter body (reference's gx=1.7/4.3
    # out of 7 units, gy=0.6 out of 2 units)
    eye_inset = max(1, int(10 * scale))
    eye_y = body_y + max(1, int(4 * scale))
    eye_size = max(1, int(5 * scale))
    if blinking:
        lw = max(1, int(2 * scale))
        draw.line([(body_x + eye_inset, eye_y + eye_size // 2), (body_x + eye_inset + eye_size, eye_y + eye_size // 2)], fill=BG_COLOR, width=lw)
        draw.line(
            [(body_x + body_w - eye_inset - eye_size, eye_y + eye_size // 2), (body_x + body_w - eye_inset, eye_y + eye_size // 2)],
            fill=BG_COLOR,
            width=lw,
        )
    else:
        draw.rectangle([(body_x + eye_inset, eye_y), (body_x + eye_inset + eye_size, eye_y + eye_size)], fill=BG_COLOR)
        draw.rectangle(
            [(body_x + body_w - eye_inset - eye_size, eye_y), (body_x + body_w - eye_inset, eye_y + eye_size)], fill=BG_COLOR
        )


def _draw_capybara(draw, x, y_baseline, scale=1.0):
    """
    Static blocky pixel capybara icon (corner decoration) — wide low body,
    small ears, blunt snout with a dark nose, carrot party hat on top.
    """
    body_w, body_h = max(4, int(42 * scale)), max(3, int(20 * scale))
    body_x = x - body_w // 2
    body_y = y_baseline - body_h - max(2, int(6 * scale))  # short stubby legs

    leg_w, leg_h = max(1, int(7 * scale)), max(1, int(6 * scale))
    leg_gap = max(1, int(3 * scale))
    leg_y = body_y + body_h - max(1, int(2 * scale))
    draw.rectangle([(body_x + leg_gap, leg_y), (body_x + leg_gap + leg_w, leg_y + leg_h)], fill=CAPYBARA_COLOR)
    draw.rectangle(
        [(body_x + body_w - leg_w - leg_gap, leg_y), (body_x + body_w - leg_gap, leg_y + leg_h)], fill=CAPYBARA_COLOR
    )

    # Body — wide and low, capybara-shaped
    draw.rectangle([(body_x, body_y), (body_x + body_w, body_y + body_h)], fill=CAPYBARA_COLOR)

    # Ears — two small squares poking above the top edge
    ear_size = max(1, int(6 * scale))
    ear_inset = max(1, int(5 * scale))
    ear_y = body_y - ear_size + max(1, int(2 * scale))
    draw.rectangle(
        [(body_x + ear_inset, ear_y), (body_x + ear_inset + ear_size, body_y + max(1, int(2 * scale)))], fill=CAPYBARA_COLOR
    )
    draw.rectangle(
        [(body_x + body_w - ear_inset - ear_size, ear_y), (body_x + body_w - ear_inset, body_y + max(1, int(2 * scale)))],
        fill=CAPYBARA_COLOR,
    )

    # Snout — blunt rectangle at the front-bottom, with a dark nose
    snout_w, snout_h = max(2, int(18 * scale)), max(1, int(6 * scale))
    snout_x = x - snout_w // 2
    snout_y = body_y + body_h - max(1, int(2 * scale))
    draw.rectangle([(snout_x, snout_y), (snout_x + snout_w, snout_y + snout_h)], fill=CAPYBARA_COLOR)
    nose_size = max(1, int(4 * scale))
    draw.rectangle(
        [(x - nose_size // 2, snout_y + snout_h - nose_size), (x + nose_size // 2, snout_y + snout_h)], fill=BG_COLOR
    )

    # Eyes
    eye_inset = max(1, int(8 * scale))
    eye_y = body_y + max(1, int(5 * scale))
    eye_size = max(1, int(4 * scale))
    draw.rectangle([(body_x + eye_inset, eye_y), (body_x + eye_inset + eye_size, eye_y + eye_size)], fill=BG_COLOR)
    draw.rectangle(
        [(body_x + body_w - eye_inset - eye_size, eye_y), (body_x + body_w - eye_inset, eye_y + eye_size)], fill=BG_COLOR
    )

    # Carrot party hat, centered on top of the head between the ears
    hat_w = max(2, int(18 * scale))
    hat_h = max(1, int(9 * scale))
    hat_base_y = body_y + max(1, int(2 * scale))
    hat_tip_y = hat_base_y - hat_h
    draw.polygon(
        [(x - hat_w // 2, hat_base_y), (x + hat_w // 2, hat_base_y), (x, hat_tip_y)],
        fill=CARROT_COLOR,
    )
    leaf_size = max(1, int(5 * scale))
    draw.polygon(
        [(x, hat_tip_y), (x - leaf_size, hat_tip_y - leaf_size), (x - leaf_size // 2, hat_tip_y)], fill=LEAF_COLOR
    )
    draw.polygon(
        [(x - 1, hat_tip_y), (x, hat_tip_y - leaf_size - 1), (x + 1, hat_tip_y)], fill=LEAF_COLOR
    )
    draw.polygon(
        [(x, hat_tip_y), (x + leaf_size, hat_tip_y - leaf_size), (x + leaf_size // 2, hat_tip_y)], fill=LEAF_COLOR
    )


WEATHER_ICON_SIZE = 14  # base square icon box every primitive below is designed against; passing a bigger
# `size` scales every offset proportionally (see _icon_sc), so the same primitives work at any icon size.


def _icon_sc(size):
    """Returns a scaling closure sc(v) for hand-drawn icon primitives, proportional to WEATHER_ICON_SIZE."""
    s = size / WEATHER_ICON_SIZE
    return lambda v: max(1, round(v * s))


def draw_sun(draw, x, y, size):
    """8-ray sun — adapted from the make_weather_set.py reference, scaled way down
    (that version used r=20-26 for a 240px cell; ours needs to read at 14-24px total)."""
    sc = _icon_sc(size)
    r = sc(4)
    cx, cy = x + sc(7), y + sc(7)
    ray = sc(6)
    for ddx, ddy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        draw.line([(cx, cy), (cx + ddx * ray, cy + ddy * ray)], fill=SUN_COLOR, width=sc(1))
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=SUN_COLOR)


def draw_cloud(draw, x, y, size):
    """Puffy 3-bump cloud with a soft shadow layer underneath, adapted from the
    reference's 5-puff-plus-shadow design (compressed to 3 puffs to fit our tiny icon box)."""
    sc = _icon_sc(size)
    puffs = [
        (x + sc(3), y + sc(7), sc(3)),
        (x + sc(7), y + sc(4), sc(4)),
        (x + sc(11), y + sc(7), sc(3)),
    ]
    shadow_dy = sc(1)
    for px, py, pr in puffs:
        draw.ellipse([(px - pr, py - pr + shadow_dy), (px + pr, py + pr + shadow_dy)], fill=CLOUD_SHADOW_COLOR)
    for px, py, pr in puffs:
        draw.ellipse([(px - pr, py - pr), (px + pr, py + pr)], fill=CLOUD_COLOR)
    draw.rectangle([(x + sc(1), y + sc(7)), (x + sc(13), y + sc(11))], fill=CLOUD_COLOR)


def draw_moon(draw, x, y, size):
    """Crescent moon — a filled circle with a background-colored circle offset over it to bite a chunk out."""
    sc = _icon_sc(size)
    r = sc(4)
    cx, cy = x + sc(6), y + sc(6)
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=MOON_COLOR)
    bite = sc(2)
    draw.ellipse([(cx - r + bite, cy - r - sc(1)), (cx + r + bite, cy + r - sc(1))], fill=BG_COLOR)


def draw_stars(draw, x, y, size):
    """Small filled dots (not crosses) — matches the reference's plain-circle stars."""
    sc = _icon_sc(size)
    r = sc(1)
    for dx, dy in ((sc(11), sc(1)), (sc(13), sc(6)), (sc(10), sc(11))):
        px, py = x + dx, y + dy
        draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=STAR_COLOR)


def draw_raindrops(draw, x, y, size):
    """Zigzag drop start heights, matching the reference's alternating-offset rain."""
    sc = _icon_sc(size)
    for i, dx in enumerate((sc(2), sc(6), sc(10))):
        y0 = y + sc(9) + (sc(2) if i % 2 == 0 else 0)
        draw.line([(x + dx, y0), (x + dx - sc(1), y0 + sc(4))], fill=RAIN_COLOR, width=sc(1))


def draw_snow(draw, x, y, size):
    """Round flakes with the same zigzag offset as draw_raindrops, per the reference."""
    sc = _icon_sc(size)
    r = sc(1)
    for i, dx in enumerate((sc(2), sc(6), sc(10))):
        y0 = y + sc(10) + (sc(2) if i % 2 == 0 else 0)
        draw.ellipse([(x + dx - r, y0 - r), (x + dx + r, y0 + r)], fill=SNOW_COLOR)


def draw_bolt(draw, x, y, size):
    """Zigzag lightning-bolt polygon, adapted from the reference's 7-point bolt shape."""
    sc = _icon_sc(size)
    draw.polygon(
        [
            (x + sc(7), y + sc(5)),
            (x + sc(4), y + sc(10)),
            (x + sc(7), y + sc(10)),
            (x + sc(5), y + sc(14)),
            (x + sc(9), y + sc(8)),
            (x + sc(6), y + sc(8)),
        ],
        fill=BOLT_COLOR,
    )


def _resolve_display_icon(code: int, hour: int) -> str:
    """
    Maps an Open-Meteo WMO weather_code plus the current hour (0-23) to one
    of 8 composed icon looks. Day is 6:00-18:00 local time — only clear and
    partly-cloudy conditions actually change look between day/night (a sun
    or a moon+stars); rain/snow/fog/storm look the same either way.
    """
    is_day = 6 <= hour < 18
    if code in (0, 1):
        return "sun" if is_day else "moon_stars"
    if code in (2, 3):
        return "sun_cloud" if is_day else "moon_cloud"
    if code in (45, 48):
        return "cloud"
    if 51 <= code <= 67:
        return "cloud_rain"
    if 71 <= code <= 77:
        return "cloud_snow"
    if 80 <= code <= 99:
        return "cloud_thunder"
    return "cloud"


def _draw_weather_icon(draw, x, y, category, size=WEATHER_ICON_SIZE):
    """
    Composes the hand-drawn primitives above into one of the 8 looks from
    _resolve_display_icon — no emoji/TTF, matching this project's
    no-external-assets rule.
    """
    sc = _icon_sc(size)
    if category == "sun":
        draw_sun(draw, x, y, size)
    elif category == "moon_stars":
        draw_moon(draw, x, y, size)
        draw_stars(draw, x, y, size)
    elif category == "sun_cloud":
        # Sun nudged up-left so it visibly peeks out from behind the cloud
        draw_sun(draw, x - sc(3), y - sc(2), size)
        draw_cloud(draw, x, y, size)
    elif category == "moon_cloud":
        draw_moon(draw, x - sc(3), y - sc(2), size)
        draw_cloud(draw, x, y, size)
    elif category == "cloud":
        draw_cloud(draw, x, y, size)
    elif category == "cloud_rain":
        draw_cloud(draw, x, y, size)
        draw_raindrops(draw, x, y, size)
    elif category == "cloud_snow":
        draw_cloud(draw, x, y, size)
        draw_snow(draw, x, y, size)
    elif category == "cloud_thunder":
        draw_cloud(draw, x, y, size)
        draw_bolt(draw, x, y, size)


TITLE_SCALE = 4
LABEL_SCALE = 3
SMALL_SCALE = 2

TITLE_H = PIXEL_FONT_ROWS * TITLE_SCALE
LABEL_H = PIXEL_FONT_ROWS * LABEL_SCALE
SMALL_H = PIXEL_FONT_ROWS * SMALL_SCALE


def draw_meter_image(state: dict, weather: dict = None) -> Path:
    """
    Draw an animated 240x240 GIF: dark background, static mascot icon + "CLAUDE"
    title (hand-drawn pixel font, no TTF/download), a small static capybara in
    the top-right corner, local date/time + weather, Current (orange) + Weekly
    (lime) bars on a purple track with inline reset countdowns, and a bigger
    mascot (carrot party hat included) walking left-right (triangle wave)
    along the bottom with alternating legs and periodic blinking.
    """
    current_percent = state.get("current_percent", 0.0)
    current_reset = state.get("current_reset", "-")
    weekly_percent = state.get("weekly_percent", 0.0)
    weekly_reset = state.get("weekly_reset", "-")

    walk_left, walk_right = 40, 200
    walk_baseline = 236
    BAR_WIDTH = 150  # reset countdown (SMALL_SCALE 2) needs ~72px beside the
    # bar for worst-case "4H 59M"/"6D 23H" — 150 leaves enough room without overflow.
    BAR_HEIGHT = 16

    CAPY_SCALE = 1.2
    CAPY_X = 210
    CAPY_BASELINE = 50

    # Live clock — system local time (no timezone dependency), so a clone of
    # this repo shows the right time on whatever machine runs it. Same text
    # baked into every frame (the GIF loops fast; it isn't meant to animate
    # within one generated image).
    now_local = datetime.now()
    datetime_text = now_local.strftime("%d %b %H:%M")

    weather_available = bool(weather and "temp_c" in weather)
    if weather_available:
        temp_text = f"{weather['temp_c']}°C"
        icon_category = _resolve_display_icon(weather["weather_code"], now_local.hour)
        log_message(f"Weather icon: code {weather['weather_code']}, hour {now_local.hour} -> '{icon_category}'")
    else:
        temp_text = None
        icon_category = None

    # Date/time row — temperature appended back onto the same line (icon
    # stays out of it, floating above the mascot instead — see below).
    # Width-checked with our own font metrics rather than assumed, so an
    # unusually long reading (e.g. a negative double-digit temp) falls back
    # to date-only instead of clipping off the right edge.
    if weather_available:
        combined_text = f"{datetime_text} · {temp_text}"
        if 6 + _pixel_text_width(combined_text, SMALL_SCALE) <= 234:
            date_line_text = combined_text
        else:
            date_line_text = datetime_text
    else:
        date_line_text = datetime_text

    weather_box_y = 6 + TITLE_H + 16  # extra room for the bigger corner capybara
    text_row_y = weather_box_y
    content_top_y = weather_box_y + SMALL_H + 6

    cur_label_y = content_top_y
    cur_bar_y = cur_label_y + LABEL_H + 2
    week_label_y = cur_bar_y + BAR_HEIGHT + 3
    week_bar_y = week_label_y + LABEL_H + 2

    # The floating weather icon (bigger than the inline date-row size) tracks
    # the walking mascot's x position every frame, but its y is constant —
    # the mascot's head height above walk_baseline never changes, only where
    # it is left-to-right. Mirrors the body_y formula inside
    # _draw_walking_mascot.
    FLOAT_ICON_SIZE = 24  # +71% vs the base 14px icon — the old inline size read as invisible above the mascot
    mascot_head_top = walk_baseline - max(2, int(14 * WALK_SCALE)) - max(1, int(8 * WALK_SCALE))
    FLOAT_GAP = 4
    float_icon_y = mascot_head_top - FLOAT_ICON_SIZE - FLOAT_GAP

    # Render the (possibly multi-primitive) icon once onto its own transparent
    # surface instead of re-invoking 2-3 drawing primitives on every one of
    # the 40 frames — each frame then just pastes this same bitmap at its
    # frame's mascot x position, which is far cheaper.
    #
    # ICON_MARGIN pads the sprite beyond FLOAT_ICON_SIZE: sun_cloud/moon_cloud
    # nudge the sun/moon up-left of the nominal box (for the "peeking out"
    # look), which would otherwise clip against a sprite sized to exactly
    # match the icon.
    ICON_MARGIN = max(4, FLOAT_ICON_SIZE // 4)
    icon_sprite = None
    if weather_available:
        sprite_size = FLOAT_ICON_SIZE + 2 * ICON_MARGIN
        icon_sprite = Image.new("RGBA", (sprite_size, sprite_size), (0, 0, 0, 0))
        _draw_weather_icon(ImageDraw.Draw(icon_sprite), ICON_MARGIN, ICON_MARGIN, icon_category, size=FLOAT_ICON_SIZE)

    frames = []
    for i in range(N_FRAMES):
        img = Image.new("RGB", (240, 240), color=BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Title row: static (non-walking) mini mascot icon + "CLAUDE" + corner capybara
        _draw_walking_mascot(draw, 16, 34, leg_forward=False, blinking=False, scale=0.55)
        _draw_pixel_text(draw, 32, 6, "CLAUDE", TITLE_SCALE, TEXT_COLOR)
        _draw_capybara(draw, CAPY_X, CAPY_BASELINE, scale=CAPY_SCALE)

        # Local date/time, with temperature appended when it fits
        _draw_pixel_text(draw, 6, text_row_y, date_line_text, SMALL_SCALE, DIM_TEXT_COLOR)

        # Current bar — reset countdown sits beside it (not its own row
        # anymore) to keep the section compact
        _draw_pixel_text(draw, 6, cur_label_y, f"CURRENT {current_percent:.0f}%", LABEL_SCALE, TEXT_COLOR)
        _draw_bar(draw, 6, cur_bar_y, BAR_WIDTH, BAR_HEIGHT, current_percent, CURRENT_BAR_COLOR)
        _draw_pixel_text(draw, 6 + BAR_WIDTH + 4, cur_bar_y + 1, current_reset, SMALL_SCALE, DIM_TEXT_COLOR)

        # Weekly bar — real official % (7-day rate_limits window)
        _draw_pixel_text(draw, 6, week_label_y, f"WEEKLY {weekly_percent:.0f}%", LABEL_SCALE, TEXT_COLOR)
        _draw_bar(draw, 6, week_bar_y, BAR_WIDTH, BAR_HEIGHT, weekly_percent, WEEKLY_BAR_COLOR)
        _draw_pixel_text(draw, 6 + BAR_WIDTH + 4, week_bar_y + 1, weekly_reset, SMALL_SCALE, DIM_TEXT_COLOR)

        # Walking mascot: triangle wave position across the N_FRAMES loop
        t = i / (N_FRAMES - 1)
        triangle = 1 - abs(2 * t - 1)  # 0 -> 1 -> 0
        walk_x = int(walk_left + triangle * (walk_right - walk_left))
        leg_forward = (i % 8) < 4
        blinking = (i % 15) in (0, 1)
        _draw_walking_mascot(draw, walk_x, walk_baseline, leg_forward, blinking, scale=WALK_SCALE)

        # Weather icon (no temperature — that's back on the date line) floats
        # above the mascot's head and tracks it left-right. Pasted from the
        # pre-rendered sprite (drawn once above) rather than redrawn per frame.
        if icon_sprite is not None:
            fx = walk_x - icon_sprite.width // 2
            fy = float_icon_y - ICON_MARGIN
            img.paste(icon_sprite, (fx, fy), icon_sprite)

        frames.append(img)

    frames[0].save(
        IMAGE_FILE,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        optimize=False,  # keep every frame distinct — optimize can merge walk frames
    )
    size_kb = IMAGE_FILE.stat().st_size / 1024
    log_message(f"Image saved: {IMAGE_FILE} ({size_kb:.1f} KB, {N_FRAMES} frames)")
    return IMAGE_FILE


def upload_to_giftv(image_path: Path) -> bool:
    """
    Upload GIF to GIFTV device.
    GIFTV's firmware sends two conflicting Content-Length headers on this
    endpoint's response (confirmed via raw socket test — real device bug,
    not a requests/urllib3 issue), so the POST response itself can't be
    trusted. The upload almost always completes server-side regardless (the
    device reads the full body before writing its broken reply) — verify
    success independently via /filelist instead of the POST response.
    """
    try:
        with open(image_path, "rb") as f:
            files = {"image": f}  # "image" field = gif; "file" field = jpg (cropped)
            requests.post(GIFTV_UPLOAD_URL, files=files, timeout=10)
    except requests.exceptions.Timeout:
        log_message("Upload timeout: GIFTV device offline?")
        return False
    except Exception as e:
        log_message(f"Upload response unreadable (expected — GIFTV header bug): {e}")

    # Verify via /filelist (lowercase; returns an HTML <table>, not JSON)
    try:
        verify_url = f"http://{GIFTV_IP}/filelist?dir=/image/"
        response = requests.get(verify_url, timeout=5)
        if image_path.name in response.text:
            log_message(f"Upload verified via filelist: {image_path.name}")
            return True
        else:
            log_message(f"Upload not confirmed: {image_path.name} not found in filelist")
            return False
    except Exception as e:
        log_message(f"Filelist verify error: {e}")
        return False


def set_theme_on_giftv(theme: int = 3) -> bool:
    """Switch GIFTV to the given theme (3 = photo album)"""
    try:
        url = f"http://{GIFTV_IP}/set?theme={theme}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            log_message(f"Theme set OK: {theme}")
            return True
        else:
            log_message(f"Theme set failed: {response.status_code}")
            return False
    except Exception as e:
        log_message(f"Theme set error: {e}")
        return False


def set_image_on_giftv(filename: str = "/image/claude-meter.gif") -> bool:
    """Tell GIFTV to display the uploaded image"""
    try:
        url = f"http://{GIFTV_IP}/set?img={filename}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            log_message(f"Image set OK: {filename}")
            return True
        else:
            log_message(f"Set image failed: {response.status_code}")
            return False
    except Exception as e:
        log_message(f"Set image error: {e}")
        return False


def main():
    """Main execution loop"""
    log_message("=== Claude Token Meter Started ===")
    log_message(f"GIFTV IP: {GIFTV_IP}")
    log_message(f"Image path: {IMAGE_FILE}")

    state = get_token_usage()
    weather = get_weather()
    draw_meter_image(state, weather)

    if upload_to_giftv(IMAGE_FILE):
        set_theme_on_giftv(3)
        set_image_on_giftv("/image/claude-meter.gif")
        log_message("=== Update complete ===")
    else:
        log_message("=== Update failed (GIFTV offline?) ===")


if __name__ == "__main__":
    main()
