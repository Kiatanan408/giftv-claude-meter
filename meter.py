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
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv()

THAI_TZ = ZoneInfo("Asia/Bangkok")

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
IMAGE_FILE = SCRIPT_DIR / "claude-meter.gif"

LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "meter.log"

# --- GIF animation constants ---
N_FRAMES = 40
FRAME_DURATION_MS = 70
BG_COLOR = (18, 16, 22)
MASCOT_COLOR = (232, 92, 55)
CAPYBARA_COLOR = (163, 118, 71)
CURRENT_BAR_COLOR = (232, 92, 55)
WEEKLY_BAR_COLOR = (196, 224, 90)
TRACK_COLOR = (58, 48, 78)
TEXT_COLOR = (220, 220, 220)
DIM_TEXT_COLOR = (150, 150, 160)


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


def _draw_walking_mascot(draw, x, y_baseline, leg_forward, blinking, scale=1.0):
    """
    Blocky pixel mascot at (x, y_baseline) — feet touch y_baseline.
    leg_forward: True/False alternates which leg is forward (walk cadence)
    blinking: True draws closed eyes for this frame
    scale: shrinks every dimension proportionally — scale=1.0 is the big
    walking mascot at the bottom; a small scale (with leg_forward=False,
    blinking=False) gives a static title-icon version of the same shape.
    """
    body_w, body_h = max(2, int(30 * scale)), max(2, int(36 * scale))
    body_x = x - body_w // 2
    body_y = y_baseline - body_h - int(16 * scale)  # leave room for legs below body

    leg_w, leg_h = max(1, int(8 * scale)), max(1, int(16 * scale))
    leg_gap = max(1, int(2 * scale))
    leg_stagger = max(1, int(5 * scale))
    leg_y = body_y + body_h - max(1, int(6 * scale))
    if leg_forward:
        draw.rectangle([(body_x - leg_gap, leg_y), (body_x - leg_gap + leg_w, leg_y + leg_h)], fill=MASCOT_COLOR)
        draw.rectangle(
            [(body_x + body_w - leg_w + leg_gap, leg_y - leg_stagger), (body_x + body_w + leg_gap, leg_y + leg_h - leg_stagger)],
            fill=MASCOT_COLOR,
        )
    else:
        draw.rectangle([(body_x - leg_gap, leg_y - leg_stagger), (body_x - leg_gap + leg_w, leg_y + leg_h - leg_stagger)], fill=MASCOT_COLOR)
        draw.rectangle(
            [(body_x + body_w - leg_w + leg_gap, leg_y), (body_x + body_w + leg_gap, leg_y + leg_h)], fill=MASCOT_COLOR
        )

    # Body
    draw.rectangle([(body_x, body_y), (body_x + body_w, body_y + body_h)], fill=MASCOT_COLOR)

    # Eyes
    eye_inset = max(1, int(5 * scale))
    eye_y = body_y + max(1, int(9 * scale))
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
    small ears, blunt snout with a dark nose, capybara-brown.
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


TITLE_SCALE = 4
LABEL_SCALE = 3
SMALL_SCALE = 2


def draw_meter_image(state: dict) -> Path:
    """
    Draw an animated 240x240 GIF: dark background, static mascot icon + "CLAUDE"
    title (hand-drawn pixel font, no TTF/download), a small static capybara in
    the top-right corner, Current (orange) + Weekly (lime) bars on a purple
    track, reset countdowns, and a bigger mascot walking left-right (triangle
    wave) along the bottom with alternating legs and periodic blinking.
    """
    current_percent = state.get("current_percent", 0.0)
    current_reset = state.get("current_reset", "-")
    weekly_percent = state.get("weekly_percent", 0.0)
    weekly_reset = state.get("weekly_reset", "-")

    walk_left, walk_right = 40, 200
    walk_baseline = 236

    # Live Thai clock — same text baked into every frame (the GIF loops fast;
    # the date/time isn't meant to animate within one generated image).
    # Dropped the weekday (e.g. "SUN") to save width/height, same as the
    # font-collision fallback already noted in the README for this row.
    now_th = datetime.now(THAI_TZ)
    datetime_text = now_th.strftime("%d %b %H:%M")

    frames = []
    for i in range(N_FRAMES):
        img = Image.new("RGB", (240, 240), color=BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Title row: static (non-walking) mini mascot icon + "CLAUDE" + corner capybara
        _draw_walking_mascot(draw, 16, 34, leg_forward=False, blinking=False, scale=0.55)
        _draw_pixel_text(draw, 32, 6, "CLAUDE", TITLE_SCALE, TEXT_COLOR)
        _draw_capybara(draw, 220, 34, scale=0.5)

        # Thai local date/time, right under the title
        _draw_pixel_text(draw, 52, 38, datetime_text, SMALL_SCALE, DIM_TEXT_COLOR)

        # Current bar (shifted down 19px from its original position to make
        # room for the date/time row above; bar height trimmed 18->16 to
        # keep clearance above the walking mascot at the bottom)
        _draw_pixel_text(draw, 6, 55, f"CURRENT {current_percent:.0f}%", LABEL_SCALE, TEXT_COLOR)
        _draw_bar(draw, 6, 79, 228, 16, current_percent, CURRENT_BAR_COLOR)
        _draw_pixel_text(draw, 6, 98, f"RESET {current_reset}", SMALL_SCALE, DIM_TEXT_COLOR)

        # Weekly bar — real official % (7-day rate_limits window)
        _draw_pixel_text(draw, 6, 116, f"WEEKLY {weekly_percent:.0f}%", LABEL_SCALE, TEXT_COLOR)
        _draw_bar(draw, 6, 140, 228, 16, weekly_percent, WEEKLY_BAR_COLOR)
        _draw_pixel_text(draw, 6, 159, f"RESET {weekly_reset}", SMALL_SCALE, DIM_TEXT_COLOR)

        # Walking mascot: triangle wave position across the N_FRAMES loop
        t = i / (N_FRAMES - 1)
        triangle = 1 - abs(2 * t - 1)  # 0 -> 1 -> 0
        walk_x = int(walk_left + triangle * (walk_right - walk_left))
        leg_forward = (i % 8) < 4
        blinking = (i % 15) in (0, 1)
        _draw_walking_mascot(draw, walk_x, walk_baseline, leg_forward, blinking)

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
    draw_meter_image(state)

    if upload_to_giftv(IMAGE_FILE):
        set_theme_on_giftv(3)
        set_image_on_giftv("/image/claude-meter.gif")
        log_message("=== Update complete ===")
    else:
        log_message("=== Update failed (GIFTV offline?) ===")


if __name__ == "__main__":
    main()
