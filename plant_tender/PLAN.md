# Self-Tending Watering Agent

A small agent that keeps a potted herb in a good moisture band by **learning
its plant through its own actions**, rather than following a fixed schedule.
It's the difference between a thermostat and something that figures things out:
it observes, models how its own watering changes the soil, predicts, acts,
checks whether reality matched the prediction, and adapts when it didn't.

The learning is a streaming estimator small enough to run entirely on an
ESP8266 and remember what it learned across reboots.

## What it learns (all from its own observations, in raw sensor units)

1. **The drying curve.** Soil dries faster when wet, slower when dry —
   exponential decay toward a floor, `dM/dt = -k*(M - M_floor)`. In rate-vs-
   level form that's a straight line, fit online with **recursive least
   squares (RLS)** — a fixed six-number state, a few multiply-adds per reading,
   no stored history. Ideal for a microcontroller.
2. **The watering response.** How much one dose raises moisture (units per mL),
   tracked as a slow exponential average, so the agent can size a dose to hit a
   target instead of guessing.

## What makes it *adapt* (not just fit once)

- **Forgetting factor (lambda).** A living system is non-stationary — a growing
  plant drinks faster, a hot week dries quicker. RLS fades old data so the model
  tracks the moving target.
- **Surprise-driven learning rate.** The agent compares each prediction to the
  next reading. When residuals grow (a heatwave, a clogged line), it *lowers
  lambda to forget faster*; when things are calm, it raises it to trust
  accumulated knowledge. It tunes its own learning rate by how wrong it's been —
  and that same surprise signal is the anomaly flag.

## Files

| File | Role |
|------|------|
| `tender.py` | The portable learning core. **Runs unchanged on laptop and board.** No hardware/sim imports. |
| `sim_host.py` | Host-only: a synthetic drying plant + plot, to watch the core learn before any hardware. |
| `main_device.py` | ESP8266/MicroPython wiring template: real STEMMA read, pump, flash persistence, clock. |

## Try it now (no hardware)

```bash
pip install matplotlib
python sim_host.py      # writes tender_sim.png
```

The plot shows the agent tracking the plant's hidden drying rate through an
injected heatwave: the moisture sawtooth tightening as it dries faster, the
learned `k` following the hidden truth, the prediction error spiking at the
onset, and lambda dropping as it forgets faster to catch up.

## Moving to the ESP8266

1. Flash MicroPython (ESP8266 build; **not** CircuitPython — unsupported here).
2. Copy `tender.py` and `main_device.py` to the board.
3. Adjust pins, `SEESAW_ADDR`, and `PUMP_ML_PER_S` (calibrate the pump with a
   measuring cup) at the top of `main_device.py`.
4. **Leave `cfg["actuate"] = False`.** The pump stays off; the agent only prints
   what it *would* do. Watch its recommendations for a week.
5. Connect the pump and set `actuate = True` only once you trust its calls.

The learned model (six-ish numbers) is saved to flash occasionally — not every
reading, to spare flash wear — and restored on boot, so power loss doesn't erase
what it learned.

## Tuning knobs (all in `DEFAULT_CFG` in `tender.py`)

- `target_low` / `target_high` — the moisture band to keep the plant in.
- `dt_learn` — hours between drying-rate observations.
- `lam_max` / `lam_min` / `surprise_hi` — how hard it trusts vs. forgets.
- `dose_min` / `dose_max` — hard safety clamps so a bad estimate can't flood or
  starve the plant.

## Honest limitations (visible in the sim, worth knowing)

- **The learned `k` lags the truth.** Any forgetting-based tracker trails a
  moving target; faster tracking (lower lambda) means noisier estimates. That
  trade-off is real — the defaults favor stability.
- **The floor is weakly observed.** The agent keeps moisture *above* the water
  threshold, so it rarely sees the plant dry near its floor and must extrapolate
  it — expect the floor estimate to be rough. (You can only learn what you let
  the system experience — a genuine lesson, not just a caveat.)
- **Cold start.** With no saved model it needs a few dry-down cycles before the
  curve is trustworthy — another reason to run in recommend-only mode first.
- **Capacitive sensors are noisy and drift.** Readings are smoothed before rates
  are computed; work in raw units and don't bother calibrating to %-moisture.

## Where it can grow

Vision as a second, richer sense (your stereo rig) so "a good state" becomes
more than one number — tracking growth and leaf state over time. And the
active-experimentation seed already hinted at in the code: deliberately varying
early doses to learn the watering response faster is curiosity-driven probing —
the agent choosing actions to reduce its own uncertainty.
