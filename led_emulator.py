#!/usr/bin/env python3
"""
HSV LED Emulator with live sensitivity controls.

Each mode exposes its own tunable sliders, plus three global sliders
that apply to all modes.  Changes take effect immediately.

Install:  pip install numpy pygame
Usage:    python3 led_emulator.py [--list-sources] [--source NAME]
Controls: Space / click mode button to cycle modes  |  Q / Esc to quit
"""

import colorsys, subprocess, sys, threading
import numpy as np
import pygame

# ── Audio (parec bypasses PortAudio entirely) ─────────────────────────────────
SAMPLE_RATE = 44100
CHUNK_SIZE  = 2205   # 50 ms at 44100 Hz

_lock    = threading.Lock()
_raw_amp = 0.0


def pactl_sources():
    r = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True)
    return [l.split("\t")[1] for l in r.stdout.splitlines() if len(l.split("\t")) >= 2]


def find_monitor():
    for s in pactl_sources():
        if "monitor" in s.lower():
            return s
    return None


def start_parec(source):
    return subprocess.Popen(
        ["parec", "--device", source, "--format=float32le",
         f"--rate={SAMPLE_RATE}", "--channels=1", "--latency-msec=50"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def audio_reader(proc):
    global _raw_amp
    nbytes = CHUNK_SIZE * 4
    while True:
        data = proc.stdout.read(nbytes)
        if not data:
            break
        s = np.frombuffer(data, dtype=np.float32)
        with _lock:
            _raw_amp = float(np.max(s) - np.min(s)) / 2.0


# ── Slider widget ─────────────────────────────────────────────────────────────
class Slider:
    ROW   = 26
    LBL_W = 136
    VAL_W = 56

    def __init__(self, label, x, y, w, lo, hi, default, fmt="{:.2f}"):
        self.label = label
        self.x, self.y, self.w = x, y, w
        self.lo, self.hi = lo, hi
        self.value = default
        self.fmt   = fmt
        self._drag = False
        tx = x + self.LBL_W
        tw = w - self.LBL_W - self.VAL_W
        self._track = pygame.Rect(tx, y + self.ROW // 2 - 3, tw, 6)

    @property
    def _hx(self):
        t = (self.value - self.lo) / (self.hi - self.lo)
        return self._track.x + int(t * self._track.w)

    def draw(self, surf, font):
        surf.blit(font.render(self.label, True, (145, 145, 145)), (self.x + 4, self.y + 5))
        pygame.draw.rect(surf, (45, 45, 45), self._track, border_radius=3)
        fill = pygame.Rect(self._track.x, self._track.y,
                           self._hx - self._track.x, self._track.h)
        pygame.draw.rect(surf, (75, 145, 210), fill, border_radius=3)
        pygame.draw.circle(surf, (200, 225, 255), (self._hx, self._track.centery), 8)
        surf.blit(font.render(self.fmt.format(self.value), True, (205, 205, 205)),
                  (self._track.right + 6, self.y + 5))

    def on_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            hx, hy = self._hx, self._track.centery
            if abs(event.pos[0] - hx) <= 12 and abs(event.pos[1] - hy) <= 12:
                self._drag = True
            elif self._track.collidepoint(event.pos):
                self._drag = True
                self._apply(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP:
            self._drag = False
        elif event.type == pygame.MOUSEMOTION and self._drag:
            self._apply(event.pos[0])

    def _apply(self, mx):
        t = (mx - self._track.x) / self._track.w
        self.value = self.lo + max(0.0, min(1.0, t)) * (self.hi - self.lo)


# ── Layout constants ──────────────────────────────────────────────────────────
W       = 440
LED_H   = 250
SX, SW  = 8, W - 16
ROW_H   = Slider.ROW
HDR_H   = 22

BTN_Y   = LED_H + 4
BTN_H   = 34
GLB_HDR = BTN_Y + BTN_H + 3
GLB_0   = GLB_HDR + HDR_H           # first global slider
MSL_HDR = GLB_0 + ROW_H * 3 + 5    # mode section header
MSL_0   = MSL_HDR + HDR_H           # first mode slider
H       = MSL_0 + ROW_H * 3 + 28   # window height (fits 3 mode sliders + status)

CX, CY    = W // 2, 118
LED_R     = 90
N_GLOW    = 10
GLOW_STEP = 10
BG        = (17, 17, 17)
DIV       = (42, 42, 42)

MODE_NAMES = ["BEAT_HUE", "SPECTRUM", "PULSE", "AMBIENT"]


def make_sliders():
    glb = [
        Slider("Noise Floor",  SX, GLB_0,           SW, 0.00, 0.10, 0.02, "{:.3f}"),
        Slider("Smoothing α",  SX, GLB_0 + ROW_H,   SW, 0.10, 0.90, 0.40, "{:.2f}"),
        Slider("Beat Thresh",  SX, GLB_0 + ROW_H*2, SW, 1.1,  3.0,  1.5,  "{:.1f}×"),
    ]
    mode_sl = {
        0: [  # BEAT_HUE
            Slider("Hue Drift °/f",  SX, MSL_0,          SW, 0.1,  3.0,  0.5,  "{:.1f}"),
            Slider("Beat Kick °",    SX, MSL_0 + ROW_H,  SW, 5.0,  60.0, 25.0, "{:.0f}"),
        ],
        1: [  # SPECTRUM
            Slider("Hue Range °",    SX, MSL_0,          SW, 60.0, 360.0,240.0, "{:.0f}"),
            Slider("Min Brightness", SX, MSL_0 + ROW_H,  SW, 0.0,  0.5,  0.15, "{:.2f}"),
        ],
        2: [  # PULSE
            Slider("Hue Drift °/f",  SX, MSL_0,          SW, 0.0,  1.5,  0.3,  "{:.2f}"),
            Slider("Decay Rate",     SX, MSL_0 + ROW_H,  SW, 0.70, 0.98, 0.85, "{:.3f}"),
        ],
        3: [  # AMBIENT
            Slider("Hue Drift °/f",  SX, MSL_0,          SW, 0.05, 0.8,  0.15, "{:.2f}"),
            Slider("Saturation",     SX, MSL_0 + ROW_H,  SW, 0.3,  1.0,  0.75, "{:.2f}"),
            Slider("Brightness ×",   SX, MSL_0 + ROW_H*2,SW, 0.1,  0.8,  0.45, "{:.2f}"),
        ],
    }
    return glb, mode_sl


# ── Drawing ───────────────────────────────────────────────────────────────────
def _rgb255(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def draw_led(screen, glow_surf, color, brightness):
    r, g, b = color
    glow_surf.fill((0, 0, 0, 0))
    for i in range(N_GLOW - 1, -1, -1):
        radius = LED_R + (N_GLOW - i) * GLOW_STEP
        alpha  = int(brightness * (i + 1) / (N_GLOW + 2) * 210)
        pygame.draw.circle(glow_surf, (r, g, b, alpha), (CX, CY), radius)
    screen.blit(glow_surf, (0, 0))
    pygame.draw.circle(screen, color, (CX, CY), LED_R)


def draw_header(screen, font, text, y):
    screen.blit(font.render(text, True, (90, 90, 90)), (SX + 2, y + 3))
    pygame.draw.line(screen, DIV, (SX, y + HDR_H - 2), (SX + SW, y + HDR_H - 2))


# ── Main loop ─────────────────────────────────────────────────────────────────
def run(proc):
    threading.Thread(target=audio_reader, args=(proc,), daemon=True).start()

    pygame.init()
    screen    = pygame.display.set_mode((W, H))
    pygame.display.set_caption("HSV LED Emulator")
    clock     = pygame.time.Clock()
    font_md   = pygame.font.SysFont("monospace", 13, bold=True)
    font_sm   = pygame.font.SysFont("monospace", 13)
    font_xs   = pygame.font.SysFont("monospace", 11)
    glow_surf = pygame.Surface((W, LED_H), pygame.SRCALPHA)

    glb, mode_sl = make_sliders()
    btn_rect     = pygame.Rect(SX, BTN_Y, SW, BTN_H)

    mode      = 0
    hue       = 0.0
    smooth    = 0.0
    avg       = 0.0
    pulse_val = 0.0

    while True:
        # ── Events ────────────────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    return
                if event.key == pygame.K_SPACE:
                    mode = (mode + 1) % len(MODE_NAMES)
            if event.type == pygame.MOUSEBUTTONDOWN and btn_rect.collidepoint(event.pos):
                mode = (mode + 1) % len(MODE_NAMES)
            for sl in glb + mode_sl[mode]:
                sl.on_event(event)

        # ── Read global params ────────────────────────────────────────────────
        noise_floor  = glb[0].value
        smooth_alpha = glb[1].value
        beat_mult    = glb[2].value
        msl          = mode_sl[mode]

        # ── Audio ─────────────────────────────────────────────────────────────
        with _lock:
            raw = _raw_amp
        if raw < noise_floor:
            raw = 0.0

        smooth = smooth_alpha * raw + (1.0 - smooth_alpha) * smooth
        avg    = 0.05 * raw + 0.95 * avg
        beat   = (raw > avg * beat_mult) and (raw > noise_floor * 2)

        # ── HSV per mode ──────────────────────────────────────────────────────
        if mode == 0:   # BEAT_HUE
            hue_drift = msl[0].value
            beat_kick = msl[1].value
            hue  = (hue + (hue_drift + (raw * beat_kick if beat else 0.0)) / 360.0) % 1.0
            v    = 0.05 if raw == 0 else max(0.1, min(1.0, 0.1 + smooth * 0.9))
            h, s = hue, 1.0

        elif mode == 1: # SPECTRUM
            hue_range = msl[0].value
            min_bri   = msl[1].value
            hue  = (hue_range - smooth * hue_range) / 360.0
            v    = 0.05 if raw == 0 else max(min_bri, min(1.0, min_bri + smooth * (1.0 - min_bri)))
            h, s = hue, 1.0

        elif mode == 2: # PULSE
            hue_drift = msl[0].value
            decay     = msl[1].value
            hue = (hue + hue_drift / 360.0) % 1.0
            if beat:
                pulse_val = 1.0
            pulse_val *= decay
            h, s, v = hue, 1.0, max(0.05, pulse_val)

        else:           # AMBIENT
            hue_drift = msl[0].value
            sat       = msl[1].value
            bri_scale = msl[2].value
            hue  = (hue + hue_drift / 360.0) % 1.0
            v    = max(0.08, 0.08 + smooth * bri_scale)
            h, s = hue, sat

        color = _rgb255(h, s, v)

        # ── Render ────────────────────────────────────────────────────────────
        screen.fill(BG)
        draw_led(screen, glow_surf, color, v)
        pygame.draw.line(screen, DIV, (0, LED_H), (W, LED_H))

        # Mode button
        pygame.draw.rect(screen, (32, 32, 32), btn_rect, border_radius=5)
        screen.blit(
            font_md.render(f"▶  Mode {mode}: {MODE_NAMES[mode]}   [Space / Click]",
                           True, (185, 185, 185)),
            (btn_rect.x + 8, btn_rect.y + 9),
        )

        # Section headers + sliders
        draw_header(screen, font_xs, "GLOBAL", GLB_HDR)
        draw_header(screen, font_xs, f"MODE {mode}: {MODE_NAMES[mode]}", MSL_HDR)
        for sl in glb + mode_sl[mode]:
            sl.draw(screen, font_sm)

        # Status bar
        y_st = H - 20
        screen.blit(
            font_xs.render(f"H {h*360:5.1f}°  S {s:.2f}  V {v:.2f}  amp {raw:.3f}",
                           True, (72, 72, 72)),
            (SX, y_st),
        )
        if beat:
            bt = font_xs.render("● BEAT", True, (255, 60, 80))
            screen.blit(bt, (W - bt.get_width() - 8, y_st))

        pygame.display.flip()
        clock.tick(20)

    pygame.quit()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--list-sources" in sys.argv:
        print("Available PulseAudio sources:")
        for s in pactl_sources():
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
        print("\nRe-run with: python led_emulator.py --source NAME")
        sys.exit(1)

    print(f"Capturing: {source}")
    proc = start_parec(source)
    try:
        run(proc)
    finally:
        proc.terminate()
