#!/usr/bin/env python3
"""
HSV LED Emulator — Linux (PulseAudio / PipeWire)

Uses parec (ships with pipewire-pulse / libpulse) to capture system audio
directly — no PortAudio or sounddevice needed.

Install:
    pip install numpy pygame
    # Arch: sudo pacman -S python-numpy python-pygame
    # parec is already present if pipewire-pulse is installed

Usage:
    python3 led_emulator.py                    # auto-detect monitor source
    python3 led_emulator.py --list-sources     # print available sources
    python3 led_emulator.py --source NAME      # pick a source by name

Space / click to cycle modes.  Q or Esc to quit.
"""

import colorsys
import subprocess
import sys
import threading

import numpy as np
import pygame

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 44100
CHUNK_SIZE  = 2205    # 50 ms at 44100 Hz — matches Arduino SAMPLE_MS
NOISE_FLOOR = 0.02    # normalised amplitude threshold

_lock    = threading.Lock()
_raw_amp = 0.0


def pactl_sources():
    """Return source names reported by pactl."""
    r = subprocess.run(["pactl", "list", "short", "sources"],
                       capture_output=True, text=True)
    names = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def find_monitor():
    """Return the name of the first PulseAudio monitor source, or None."""
    for name in pactl_sources():
        if "monitor" in name.lower():
            return name
    return None


def start_parec(source):
    return subprocess.Popen(
        ["parec", "--device", source,
         "--format=float32le", f"--rate={SAMPLE_RATE}",
         "--channels=1", "--latency-msec=50"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )


def audio_reader(proc):
    """Background thread: read parec output and update shared amplitude."""
    global _raw_amp
    nbytes = CHUNK_SIZE * 4   # float32 = 4 bytes per sample
    while True:
        data = proc.stdout.read(nbytes)
        if not data:
            break
        samples = np.frombuffer(data, dtype=np.float32)
        ptp = float(np.max(samples) - np.min(samples)) / 2.0
        with _lock:
            _raw_amp = ptp


# ── Display constants ─────────────────────────────────────────────────────────
W, H        = 420, 460
CX, CY      = W // 2, 185
LED_R       = 110
N_GLOW      = 10
GLOW_STEP   = 12
MODE_NAMES  = ["BEAT_HUE", "SPECTRUM", "PULSE", "AMBIENT"]
BG          = (17, 17, 17)


def _rgb255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_frame(screen, glow_surf, color, brightness, mode, h, s, v, raw, beat):
    screen.fill(BG)

    r, g, b = color
    glow_surf.fill((0, 0, 0, 0))
    for i in range(N_GLOW - 1, -1, -1):
        radius = LED_R + (N_GLOW - i) * GLOW_STEP
        alpha  = int(brightness * (i + 1) / (N_GLOW + 2) * 210)
        pygame.draw.circle(glow_surf, (r, g, b, alpha), (CX, CY), radius)
    screen.blit(glow_surf, (0, 0))
    pygame.draw.circle(screen, color, (CX, CY), LED_R)

    mode_txt = FONT_LG.render(
        f"Mode {mode}: {MODE_NAMES[mode]}  [Space / Click]", True, (180, 180, 180)
    )
    screen.blit(mode_txt, (W // 2 - mode_txt.get_width() // 2, H - 75))

    hsv_txt = FONT_SM.render(
        f"H {h*360:5.1f}°  S {s:.2f}  V {v:.2f}  amp {raw:.3f}",
        True, (90, 90, 90),
    )
    screen.blit(hsv_txt, (W // 2 - hsv_txt.get_width() // 2, H - 48))

    if beat:
        beat_txt = FONT_LG.render("● BEAT", True, (255, 60, 80))
        screen.blit(beat_txt, (W // 2 - beat_txt.get_width() // 2, H - 22))

    pygame.display.flip()


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(proc):
    global FONT_LG, FONT_SM

    t = threading.Thread(target=audio_reader, args=(proc,), daemon=True)
    t.start()

    pygame.init()
    screen    = pygame.display.set_mode((W, H))
    pygame.display.set_caption("HSV LED Emulator")
    clock     = pygame.time.Clock()
    FONT_LG   = pygame.font.SysFont("monospace", 15)
    FONT_SM   = pygame.font.SysFont("monospace", 13)
    glow_surf = pygame.Surface((W, H), pygame.SRCALPHA)

    mode      = 0
    hue       = 0.0
    smooth    = 0.0
    avg       = 0.0
    pulse_val = 0.0

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
            pulse_val *= 0.85
            h, s, v = hue, 1.0, max(0.05, pulse_val)

        else:            # AMBIENT
            hue  = (hue + 0.15 / 360.0) % 1.0
            v    = max(0.08, min(0.53, 0.08 + smooth * 0.45))
            h, s = hue, 0.75

        draw_frame(screen, glow_surf, _rgb255(h, s, v), v, mode, h, s, v, raw, beat)
        clock.tick(20)

    pygame.quit()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--list-sources" in sys.argv:
        sources = pactl_sources()
        print("Available PulseAudio sources:")
        for s in sources:
            print(f"  {s}")
        sys.exit(0)

    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        try:
            source = sys.argv[idx + 1]
        except IndexError:
            print("Usage: python led_emulator.py --source NAME")
            sys.exit(1)
    else:
        source = find_monitor()

    if source is None:
        print("No monitor source found. Available sources:")
        for s in pactl_sources():
            print(f"  {s}")
        print("\nRe-run with:  python led_emulator.py --source NAME")
        sys.exit(1)

    print(f"Capturing: {source}")
    proc = start_parec(source)
    try:
        run(proc)
    finally:
        proc.terminate()
