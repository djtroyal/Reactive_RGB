/*
 * Music-Reactive HSV LED — Multi-Mode
 *
 * Four display modes toggled by a momentary switch on BTN_PIN (pin 4).
 * Wire the switch between pin 4 and GND; the internal pull-up does the rest.
 *
 *   Mode 0  BEAT_HUE  — hue auto-drifts; kicks forward on detected beats
 *   Mode 1  SPECTRUM  — amplitude maps to hue (blue=quiet → red=loud)
 *   Mode 2  PULSE     — brightness spikes on each beat then decays; hue drifts
 *   Mode 3  AMBIENT   — slow pastel hue cycle; gentle brightness envelope
 *
 * Pins: R=9, G=10, B=11, Mic=A2, Button=4
 */

// --- Pin configuration ---
const uint8_t RED_PIN   = 9;
const uint8_t GREEN_PIN = 10;
const uint8_t BLUE_PIN  = 11;
const uint8_t MIC_PIN   = A2;
const uint8_t BTN_PIN   = 4;

// true for common-anode LED (inverts PWM output)
const bool COMMON_ANODE = false;

// Per-channel calibration — adjust to balance your specific LED's brightness
const float RED_CAL   = 1.0f;
const float GREEN_CAL = 0.9f;
const float BLUE_CAL  = 1.0f;

// --- Audio parameters ---
const uint16_t SAMPLE_MS   = 50;   // Sampling window for peak-to-peak amplitude
const uint16_t NOISE_FLOOR = 20;   // ADC counts below which signal is silence

// --- Mode ---
const uint8_t NUM_MODES = 4;
uint8_t mode = 0;

// --- Button debounce ---
bool     btnLast   = HIGH;
uint32_t btnTime   = 0;
const uint16_t DEBOUNCE_MS = 200;  // Longer than the 50ms loop period

// --- Shared state ---
float hue       = 0.0f;   // Current hue in degrees (0–360)
float smoothAmp = 0.0f;   // Fast EMA — drives brightness
float avgAmp    = 0.0f;   // Slow EMA — beat detection baseline
float pulseVal  = 0.0f;   // PULSE mode: decaying brightness envelope

// Returns peak-to-peak amplitude (0–1023) sampled over SAMPLE_MS milliseconds
uint16_t sampleAmplitude() {
  uint16_t hi = 0, lo = 1023;
  unsigned long t = millis();
  while (millis() - t < SAMPLE_MS) {
    uint16_t v = analogRead(MIC_PIN);
    if (v > hi) hi = v;
    if (v < lo) lo = v;
  }
  return hi - lo;
}

// Converts HSV to RGB and writes to LED pins.  h: 0–360, s/v: 0–1
void setHSV(float h, float s, float v) {
  float hf = fmod(h, 360.0f) / 60.0f;
  int   i  = (int)hf;
  float f  = hf - i;
  float p  = v * (1.0f - s);
  float q  = v * (1.0f - s * f);
  float t  = v * (1.0f - s * (1.0f - f));

  float r, g, b;
  switch (i) {
    case 0:  r = v; g = t; b = p; break;
    case 1:  r = q; g = v; b = p; break;
    case 2:  r = p; g = v; b = t; break;
    case 3:  r = p; g = q; b = v; break;
    case 4:  r = t; g = p; b = v; break;
    default: r = v; g = p; b = q; break;
  }

  uint8_t rv = constrain((int)(RED_CAL   * r * 255), 0, 255);
  uint8_t gv = constrain((int)(GREEN_CAL * g * 255), 0, 255);
  uint8_t bv = constrain((int)(BLUE_CAL  * b * 255), 0, 255);

  if (COMMON_ANODE) { rv = 255 - rv; gv = 255 - gv; bv = 255 - bv; }

  analogWrite(RED_PIN,   rv);
  analogWrite(GREEN_PIN, gv);
  analogWrite(BLUE_PIN,  bv);
}

// Advances mode on each falling edge with debounce
void checkButton() {
  bool btnNow = digitalRead(BTN_PIN);
  if (btnNow == LOW && btnLast == HIGH && (millis() - btnTime) > DEBOUNCE_MS) {
    mode = (mode + 1) % NUM_MODES;
    btnTime = millis();
  }
  btnLast = btnNow;
}

void setup() {
  pinMode(RED_PIN,   OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN,  OUTPUT);
  pinMode(BTN_PIN,   INPUT_PULLUP);
}

void loop() {
  checkButton();

  uint16_t raw = sampleAmplitude();
  if (raw < NOISE_FLOOR) raw = 0;

  // Fast EMA for stable brightness (α=0.4, ~75 ms time constant)
  smoothAmp = 0.4f * raw + 0.6f * smoothAmp;

  // Slow EMA for beat detection baseline (~1.3 s time constant at 20 Hz)
  avgAmp = 0.05f * raw + 0.95f * avgAmp;

  // Beat: instantaneous amplitude spikes well above the running average
  bool beat = (raw > avgAmp * 1.5f) && (raw > NOISE_FLOOR * 2);

  float value;

  switch (mode) {

    case 0:
      // BEAT_HUE — hue drifts slowly at rest; detected beats kick it forward
      hue   = fmod(hue + 0.5f + (beat ? (raw / 1023.0f) * 25.0f : 0.0f), 360.0f);
      value = (raw == 0) ? 0.05f : constrain(0.1f + (smoothAmp / 1023.0f) * 0.9f, 0.1f, 1.0f);
      setHSV(hue, 1.0f, value);
      break;

    case 1:
      // SPECTRUM — amplitude maps to hue position: blue (quiet) → red (loud)
      hue   = 240.0f - constrain((smoothAmp / 1023.0f) * 240.0f, 0.0f, 240.0f);
      value = (raw == 0) ? 0.05f : constrain(0.15f + (smoothAmp / 1023.0f) * 0.85f, 0.15f, 1.0f);
      setHSV(hue, 1.0f, value);
      break;

    case 2:
      // PULSE — on each beat brightness snaps to 1.0 then decays exponentially;
      // hue drifts slowly so the colour changes between beats
      hue = fmod(hue + 0.3f, 360.0f);
      if (beat) pulseVal = 1.0f;
      pulseVal *= 0.85f;  // ~300 ms decay time constant at 20 Hz
      setHSV(hue, 1.0f, max(0.05f, pulseVal));
      break;

    case 3:
      // AMBIENT — very slow pastel hue cycle; brightness responds gently to volume
      hue   = fmod(hue + 0.15f, 360.0f);
      value = constrain(0.08f + (smoothAmp / 1023.0f) * 0.45f, 0.08f, 0.53f);
      setHSV(hue, 0.75f, value);  // Reduced saturation gives softer pastel tones
      break;
  }
}
