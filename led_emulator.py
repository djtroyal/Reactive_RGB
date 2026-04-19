#!/usr/bin/env python3
"""
HSV LED Emulator — Linux (PulseAudio / PipeWire)

Captures system audio from the monitor source and renders a glowing HSV
circle in a pygame window, mirroring the four modes in HSV_music.ino.

Install:
    pip install sounddevice numpy pygame
    # Arch: sudo pacman -S python-sounddevice python-numpy python-pygame

Usage:
    python3 led_emulator.py

Space / click anywhere to cycle modes.  Q or Esc to quit.
"""

import colorsys
import threading

import numpy as np
import pygame
import sounddevice as sd

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 44100
BLOCK_SIZE  = 2205    # 50 ms at 44100 Hz — matches Arduino SAMPLE_MS
NOISE_FLOOR = 0.02    # ≈ 20/1023 (same ratio as the Arduino sketch)

_lock    = threading.Lock()
_raw_amp = 0.0


def audio_callback(indata, frames, time, status):
    global _raw_amp
    ptp = float(np.max(indata) - np.min(indata)) / 2.0  # peak-to-peak → 0–1
    with _lock:
        _raw_amp = ptp


def find_monitor():
    """Return the device index of the best monitor source, or None."""
    devices  = sd.query_devices()
    hostapis = sd.query_hostapis()

    # Prefer a PulseAudio/PipeWire host-API device with "monitor" in the name
    pulse_api = next(
        (i for i, a in enumerate(hostapis) if "pulse" in a["name"].lower()), None
    )
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and "monitor" in d["name"].lower():
            if pulse_api is None or d["hostapi"] == pulse_api:
                return i

    # Fallback: any input on the PulseAudio host API (default monitor)
    if pulse_api is not None:
        api_default = hostapis[pulse_api]["default_input_device"]
        if api_default >= 0:
            return api_default

    return None


def list_devices():
    print("\nAvailable input devices:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            print(f"  [{i:2d}]  {d['name']}  ({api})")
    print("\nRe-run with:  python led_emulator.py --device N")


# ── Display constants ─────────────────────────────────────────────────────────
W, H        = 420, 460
CX, CY      = W // 2, 185   # LED circle centre
LED_R       = 110            # LED circle radius
N_GLOW      = 10             # glow ring count
GLOW_STEP   = 12             # px between glow rings

MODE_NAMES  = ["BEAT_HUE", "SPECTRUM", "PULSE", "AMBIENT"]
BG          = (17, 17, 17)


def _rgb255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_frame(screen, glow_surf, color, brightness, mode, h, s, v, raw, beat):
    screen.fill(BG)

    r, g, b = color

    # Glow — alpha-blended rings radiating outward from the LED edge
    glow_surf.fill((0, 0, 0, 0))
    for i in range(N_GLOW - 1, -1, -1):   # outermost first
        radius = LED_R + (N_GLOW - i) * GLOW_STEP
        alpha  = int(brightness * (i + 1) / (N_GLOW + 2) * 210)
        pygame.draw.circle(glow_surf, (r, g, b, alpha), (CX, CY), radius)
    screen.blit(glow_surf, (0, 0))

    # LED body
    pygame.draw.circle(screen, color, (CX, CY), LED_R)

    # Mode label
    mode_txt = FONT_LG.render(
        f"Mode {mode}: {MODE_NAMES[mode]}  [Space / Click]", True, (180, 180, 180)
    )
    screen.blit(mode_txt, (W // 2 - mode_txt.get_width() // 2, H - 75))

    # HSV readout
    hsv_txt = FONT_SM.render(
        f"H {h*360:5.1f}°  S {s:.2f}  V {v:.2f}  amp {raw:.3f}",
        True, (90, 90, 90),
    )
    screen.blit(hsv_txt, (W // 2 - hsv_txt.get_width() // 2, H - 48))

    # Beat flash
    if beat:
        beat_txt = FONT_LG.render("● BEAT", True, (255, 60, 80))
        screen.blit(beat_txt, (W // 2 - beat_txt.get_width() // 2, H - 22))

    pygame.display.flip()


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(device):
    global FONT_LG, FONT_SM

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("HSV LED Emulator")
    clock      = pygame.time.Clock()
    FONT_LG    = pygame.font.SysFont("monospace", 15)
    FONT_SM    = pygame.font.SysFont("monospace", 13)
    glow_surf  = pygame.Surface((W, H), pygame.SRCALPHA)

    mode      = 0
    hue       = 0.0
    smooth    = 0.0
    avg       = 0.0
    pulse_val = 0.0

    with sd.InputStream(device=device, channels=1, samplerate=SAMPLE_RATE,
                        blocksize=BLOCK_SIZE, callback=audio_callback, dtype="float32"):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return
                    if event.key == pygame.K_SPACE:
                        mode = (mode + 1) % len(MODE_NAMES)
                if event.type == pygame.MOUSEBUTTONDOWN:
                    mode = (mode + 1) % len(MODE_NAMES)

            with _lock:
                raw = _raw_amp
            if raw < NOISE_FLOOR:
                raw = 0.0

            # EMA smoothing — same coefficients as HSV_music.ino
            smooth = 0.4 * raw  + 0.6 * smooth
            avg    = 0.05 * raw + 0.95 * avg
            beat   = (raw > avg * 1.5) and (raw > NOISE_FLOOR * 2)

            if mode == 0:    # BEAT_HUE
                hue   = (hue + (0.5 + (raw * 25.0 if beat else 0.0)) / 360.0) % 1.0
                v     = 0.05 if raw == 0 else max(0.1,  min(1.0, 0.1  + smooth * 0.9))
                h, s  = hue, 1.0

            elif mode == 1:  # SPECTRUM
                hue  = (240.0 - smooth * 240.0) / 360.0
                v    = 0.05 if raw == 0 else max(0.15, min(1.0, 0.15 + smooth * 0.85))
                h, s = hue, 1.0

            elif mode == 2:  # PULSE
                hue = (hue + 0.3 / 360.0) % 1.0
                if beat:
                    pulse_val = 1.0
                pulse_val *= 0.85   # ~300 ms decay at 20 Hz
                h, s, v = hue, 1.0, max(0.05, pulse_val)

            else:            # AMBIENT
                hue  = (hue + 0.15 / 360.0) % 1.0
                v    = max(0.08, min(0.53, 0.08 + smooth * 0.45))
                h, s = hue, 0.75

            draw_frame(screen, glow_surf, _rgb255(h, s, v), v, mode, h, s, v, raw, beat)
            clock.tick(20)   # 20 Hz — matches Arduino loop rate

    pygame.quit()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if "--list-devices" in sys.argv:
        list_devices()
        sys.exit(0)

    explicit = None
    if "--device" in sys.argv:
        idx = sys.argv.index("--device")
        try:
            explicit = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("Usage: python led_emulator.py --device N")
            sys.exit(1)

    device = explicit if explicit is not None else find_monitor()

    if device is None:
        print("Could not find a monitor/loopback input device.")
        list_devices()
        sys.exit(1)

    print(f"Capturing: [{device}] {sd.query_devices(device)['name']}")
    run(device)
