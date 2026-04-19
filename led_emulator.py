#!/usr/bin/env python3
"""
HSV LED Emulator — Linux (PulseAudio / PipeWire)

Captures system audio from the monitor source (what's actually playing) and
displays the computed HSV colour in a window, mirroring the four modes in
HSV_music.ino at the same 20 Hz update rate.

Install deps:
    pip install sounddevice numpy
    sudo apt install python3-tk   # if tkinter is missing

Usage:
    python3 led_emulator.py

Click the mode button (or press Space) to cycle through modes.
"""

import colorsys
import threading
import tkinter as tk

import numpy as np
import sounddevice as sd

# ── Audio ─────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 44100
BLOCK_SIZE  = 2205    # 50 ms at 44100 Hz — matches Arduino SAMPLE_MS
NOISE_FLOOR = 0.02    # ≈ 20/1023 (same ratio as the Arduino sketch)

# ── Shared state (audio thread → UI thread) ───────────────────────────────────
_lock    = threading.Lock()
_raw_amp = 0.0


def audio_callback(indata, frames, time, status):
    """Called by sounddevice on each audio block. Computes peak-to-peak amplitude."""
    global _raw_amp
    ptp = float(np.max(indata) - np.min(indata)) / 2.0  # normalise to 0–1
    with _lock:
        _raw_amp = ptp


def find_monitor():
    """Return the device index of the first PulseAudio/PipeWire monitor source."""
    for i, d in enumerate(sd.query_devices()):
        if "monitor" in d["name"].lower() and d["max_input_channels"] > 0:
            return i
    return None  # fall back to default mic


# ── Layout constants ──────────────────────────────────────────────────────────
W, H       = 400, 420
CX, CY     = W // 2, 155   # LED circle centre
LED_R      = 100            # LED circle radius
N_RINGS    = 6              # glow halo ring count
GLOW_SPAN  = 55             # px the halo extends beyond LED_R at outermost ring

MODE_NAMES = ["BEAT_HUE", "SPECTRUM", "PULSE", "AMBIENT"]


# ── Emulator UI ───────────────────────────────────────────────────────────────
class Emulator:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("HSV LED Emulator")
        root.geometry(f"{W}x{H}")
        root.configure(bg="#111")
        root.resizable(False, False)

        # ── Canvas ────────────────────────────────────────────────────────────
        self.canvas = tk.Canvas(root, width=W, height=H - 110,
                                bg="#111", highlightthickness=0)
        self.canvas.pack()

        # Glow rings — drawn first (outermost → innermost) so LED sits on top
        step = GLOW_SPAN // N_RINGS
        self.rings = []
        for i in range(N_RINGS):
            pad = GLOW_SPAN - i * step
            self.rings.append(
                self.canvas.create_oval(
                    CX - LED_R - pad, CY - LED_R - pad,
                    CX + LED_R + pad, CY + LED_R + pad,
                    outline="", fill="#111",
                )
            )

        self.led = self.canvas.create_oval(
            CX - LED_R, CY - LED_R, CX + LED_R, CY + LED_R,
            outline="#222", fill="#111",
        )

        # ── Controls ──────────────────────────────────────────────────────────
        self.mode = 0
        self.mode_btn = tk.Button(
            root, text=self._mode_label(),
            font=("Helvetica", 13, "bold"),
            command=self.next_mode,
            bg="#222", fg="white", relief="flat",
            activebackground="#333", activeforeground="white",
            padx=12, pady=8,
        )
        self.mode_btn.pack(fill=tk.X, padx=16, pady=(6, 0))
        root.bind("<space>", lambda _: self.next_mode())

        self.info_lbl = tk.Label(
            root, text="", font=("Courier", 11), bg="#111", fg="#777"
        )
        self.info_lbl.pack(pady=(6, 0))

        self.beat_lbl = tk.Label(
            root, text="", font=("Helvetica", 12, "bold"), bg="#111", fg="#ff4455"
        )
        self.beat_lbl.pack()

        # ── HSV state (mirrors globals in HSV_music.ino) ──────────────────────
        self.hue       = 0.0
        self.smooth    = 0.0
        self.avg       = 0.0
        self.pulse_val = 0.0

        self._tick()

    # ── Mode cycling ──────────────────────────────────────────────────────────

    def _mode_label(self):
        return f"Mode {self.mode}: {MODE_NAMES[self.mode]}   (Space / click to cycle)"

    def next_mode(self):
        self.mode = (self.mode + 1) % len(MODE_NAMES)
        self.mode_btn.config(text=self._mode_label())

    # ── Main update loop ──────────────────────────────────────────────────────

    def _tick(self):
        with _lock:
            raw = _raw_amp

        if raw < NOISE_FLOOR:
            raw = 0.0

        # EMA smoothing — same coefficients as HSV_music.ino
        self.smooth = 0.4 * raw  + 0.6 * self.smooth
        self.avg    = 0.05 * raw + 0.95 * self.avg
        beat = (raw > self.avg * 1.5) and (raw > NOISE_FLOOR * 2)

        m = self.mode

        if m == 0:    # BEAT_HUE — hue drifts; beats kick it forward
            step     = (0.5 + (raw * 25.0 if beat else 0.0)) / 360.0
            self.hue = (self.hue + step) % 1.0
            v        = 0.05 if raw == 0 else max(0.1,  min(1.0, 0.1  + self.smooth * 0.9))
            h, s     = self.hue, 1.0

        elif m == 1:  # SPECTRUM — amplitude maps to hue (blue=quiet, red=loud)
            self.hue = (240.0 - self.smooth * 240.0) / 360.0
            v        = 0.05 if raw == 0 else max(0.15, min(1.0, 0.15 + self.smooth * 0.85))
            h, s     = self.hue, 1.0

        elif m == 2:  # PULSE — brightness snaps on beat then decays
            self.hue = (self.hue + 0.3 / 360.0) % 1.0
            if beat:
                self.pulse_val = 1.0
            self.pulse_val *= 0.85   # ~300 ms decay at 20 Hz
            h, s, v  = self.hue, 1.0, max(0.05, self.pulse_val)

        else:         # AMBIENT — slow pastel cycle; gentle brightness
            self.hue = (self.hue + 0.15 / 360.0) % 1.0
            v        = max(0.08, min(0.53, 0.08 + self.smooth * 0.45))
            h, s     = self.hue, 0.75

        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        self._paint(r, g, b, v)

        self.info_lbl.config(
            text=f"H {h*360:5.1f}°  S {s:.2f}  V {v:.2f}  amp {raw:.3f}"
        )
        self.beat_lbl.config(text="● BEAT" if beat else "")

        self.root.after(50, self._tick)   # 20 Hz — matches Arduino loop rate

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _paint(self, r: float, g: float, b: float, brightness: float):
        led_hex = "#{:02x}{:02x}{:02x}".format(
            int(r * 255), int(g * 255), int(b * 255)
        )
        self.canvas.itemconfig(self.led, fill=led_hex)

        # Halo: rings[0]=outermost/faintest, rings[-1]=innermost/brightest
        n = len(self.rings)
        for i, ring in enumerate(self.rings):
            f = brightness * (i + 1) / (n + 2) * 0.75
            glow_hex = "#{:02x}{:02x}{:02x}".format(
                min(255, int(r * f * 255)),
                min(255, int(g * f * 255)),
                min(255, int(b * f * 255)),
            )
            self.canvas.itemconfig(ring, fill=glow_hex)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    device = find_monitor()
    if device is None:
        print("No monitor source found — falling back to default mic input.")
        print("Tip: open pavucontrol → Recording tab → set source to")
        print("     'Monitor of <your output device>'.")
    else:
        print(f"Capturing: {sd.query_devices(device)['name']}")

    with sd.InputStream(
        device=device,
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        callback=audio_callback,
        dtype="float32",
    ):
        root = tk.Tk()
        Emulator(root)
        root.mainloop()
