/*
 * Music-Reactive HSV LED
 *
 * Generates an HSV color output driven by music/audio from a microphone.
 *
 *   Hue   — auto-cycles slowly at rest; jumps on detected beats
 *   Sat   — fixed at 1.0 for vivid, fully-saturated colors
 *   Value — mapped from audio amplitude (louder = brighter)
 *
 * Pins: R=9, G=10, B=11, Mic=A2
 */

// --- Pin configuration ---
const uint8_t RED_PIN   = 9;
const uint8_t GREEN_PIN = 10;
const uint8_t BLUE_PIN  = 11;
const uint8_t MIC_PIN   = A2;

// true for common-anode LED (inverts PWM output)
const bool COMMON_ANODE = false;

// Per-channel calibration — adjust to balance your specific LED's brightness
const float RED_CAL   = 1.0f;
const float GREEN_CAL = 0.9f;
const float BLUE_CAL  = 1.0f;

// --- Audio parameters ---
const uint16_t SAMPLE_MS   = 50;  // Sampling window for peak-to-peak amplitude
const uint16_t NOISE_FLOOR = 20;  // ADC counts below which signal is treated as silence

// --- State ---
float hue       = 0.0f;  // Current hue in degrees (0–360)
float smoothAmp = 0.0f;  // EMA-smoothed amplitude, drives brightness
float avgAmp    = 0.0f;  // Long-running average, used for beat detection

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

void setup() {
  pinMode(RED_PIN,   OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN,  OUTPUT);
}

void loop() {
  uint16_t raw = sampleAmplitude();
  if (raw < NOISE_FLOOR) raw = 0;

  // Fast EMA smooths amplitude for stable brightness (α=0.4, ~75ms time constant)
  smoothAmp = 0.4f * raw + 0.6f * smoothAmp;

  // Slow EMA tracks the ambient level for beat detection (~1.3 s time constant at 20 Hz)
  avgAmp = 0.05f * raw + 0.95f * avgAmp;

  // A beat is a transient that spikes well above the running average
  bool beat = (raw > avgAmp * 1.5f) && (raw > NOISE_FLOOR * 2);

  // Hue drifts slowly always; a beat kicks it proportionally to loudness
  float step = 0.5f + (beat ? (raw / 1023.0f) * 25.0f : 0.0f);
  hue = fmod(hue + step, 360.0f);

  // Value: faint idle glow in silence; amplitude scales brightness up to 1.0
  float value = (raw == 0)
    ? 0.05f
    : constrain(0.1f + (smoothAmp / 1023.0f) * 0.9f, 0.1f, 1.0f);

  setHSV(hue, 1.0f, value);
}
