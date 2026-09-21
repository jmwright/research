# Soil Logger + Stacked-Cycle-Ring Visualizer

Three small pieces, end to end:

1. **device/** — NodeMCU Amica (ESP8266 / MicroPython) firmware that samples the
   Adafruit STEMMA (Seesaw) soil sensor's **moisture and temperature**, logs
   timestamped CSV to flash, and serves the whole log over TCP.
2. **client/** — a Python downloader that pulls the log to your laptop as CSV.
3. **viz/** — a Node.js + Three.js visualizer that wraps the series into
   **stacked cycle rings**: phase goes *around* each ring, cycle number goes
   *up* the stack. Moisture is the ring radius; color is moisture or
   temperature. This is pure visualization — no prediction — so you can *see*
   whether a cycle exists, how much it drifts, and how noisy it is, before
   modeling anything.

   A cycle can be sliced two ways. **Watering spike to watering spike** (the
   default) finds the pours in the signal and makes each ring one complete
   dry-down, which is the physically meaningful unit. **Fixed clock length** is
   the original time-of-day wrapping, still there for finding periodicity.

A synthetic `sample_data/soil_sample.csv` (10 days, daily rhythm + drift + two
sensor gaps) is included so the client and visualizer run with no hardware.

## Data format

```
unix_time,moisture,temp_c
1700000000,1485.7,25.93
```
`unix_time` is standard Unix seconds UTC. Phase-of-day is `unix_time % 86400`,
so the visualizer needs no timezone info to wrap by day.

---

## 1. Device (MicroPython)

1. Flash MicroPython to the Amica (ESP8266 build).
2. `cp device/config_example.py device/config.py`, fill in WiFi + port.
3. Copy `device/main.py` and `device/config.py` to the board.
4. Wire the STEMMA sensor to I²C (defaults: SCL=D1/GPIO5, SDA=D2/GPIO4).
5. Reset. It syncs the clock over NTP, then logs every `SAMPLE_INTERVAL_S`
   (default 5 min) and serves the log on `TCP_PORT` (default 8266). The board's
   IP prints on boot.

Notes: NTP sync is what makes timestamps real (needed for phase). If it fails,
timestamps are boot-relative — check the serial console. Logging every few
minutes is gentle on flash; if you sample much faster, watch filesystem size.

## 2. Downloader (Python)

```
python client/download.py --host <device-ip> --port 8266 --out soil_log.csv
```
The device sends its entire log and closes; the client overwrites the local
CSV each time (the device holds the full record, so downloads are idempotent).

## 3. Visualizer (Node.js + Three.js)

```
cd viz
npm install
node server.js ../path/to/soil_log.csv     # omit path to use the sample data
# open http://localhost:3000
```

Controls:

- **cycle boundary** — *watering spike to spike* slices the record at the pours,
  so each ring is one dry-down; *fixed clock length* wraps by the clock instead.
- **watering = rise over (counts)** — how big a step counts as a pour. The
  default 60 sits far above the ~17-count per-sample noise and far below the
  150–250-count jump a real watering produces.
- **cycle length (hours)** (clock mode) — re-wraps the data live; set it wrong
  and the rings smear, set it to the true period and they snap into alignment.
  This is how you *discover* a period.
- **reference frame** — *absolute counts* plots the raw reading. *Relative to
  each cycle's shelf* subtracts the settled level that each cycle reaches after
  drainage, so cycles whose shelves sit at different absolute levels can be
  compared by shape. **This matters more than it sounds**: in the real log the
  post-drainage shelf moved 718 → 761 counts between two consecutive cycles, so
  in the absolute frame the later ring is simply fatter and you cannot see that
  the two dry-downs are otherwise the same. In the relative frame they overlay.
- **phase axis** (watering mode) — *fraction of cycle* makes every ring close,
  comparing shape regardless of length. *Hours since watering* puts every cycle
  on one shared time axis, so a short cycle visibly falls short of closing and
  you can compare *when* things happen (when drainage ends, when the shelf
  starts).
- **color by** moisture/temp, **highlight cycle**, **typical cycle ring**
  (per-phase average as a white reference loop), **isolate highlighted cycle**.

Drag to orbit, scroll to zoom.

What to look for: a stable rhythm makes every ring alike (a smooth tube); drift
(a plant growing thirstier) makes the rings change as you climb; data gaps show
as breaks in a ring rather than false lines across missing time. Switching
between the two reference frames tells you whether cycles differ in *level* or
in *shape* — a difference that vanishes in the relative frame was only ever an
offset.

### A note on ordering

The parser keeps rows in **file order and never sorts them by timestamp**. The
firmware schedules samples on a monotonic tick counter and only labels them with
an NTP-stepped wall clock, so file order is acquisition order and the timestamps
are the unreliable channel — the real log carries several NTP resyncs, including
backward steps. Sorting would reorder genuine samples to satisfy a corrupt
label, and it would do it right where the watering transient is. The panel
reports the backward-step count instead of hiding it.

---

## What was tested, and what wasn't

Verified here (`cd viz && npm test` — 29 assertions, no framework): the
downloader round-trips a log over TCP byte-for-byte; the server serves the page,
`rings.js`, `app.js` and the CSV. For `viz/public/rings.js` the tests cover
file-order preservation and backward-step counting, the noise estimator, spike
detection (position of the boundary, the refractory window, the `minRise` gate,
and a noiseless signal), watering-to-watering slicing with partial cycles at
each end, shelf estimation, both reference frames, both phase axes, phase
staying in range across a backward clock step, gap splitting in seconds
(including that a freshly-opened cycle still draws), the average ring,
and empty input. When `soil_log.csv` is present the suite also asserts against
the real record: that detection finds exactly the two waterings in the log book
at the right samples, and that the recovered cycle length and shelves match the
offline analysis. The device firmware is syntax-checked but needs the real board
+ sensor to run. The Three.js
**3D rendering itself was not run headlessly** (no browser/WebGL in the build
environment) — the data pipeline feeding it is tested, but eyeball the first
render and adjust `BASE_R` / `R_SCALE` / `H` at the top of `app.js` to taste.

The visualizer loads Three.js from a CDN (jsdelivr), so the page needs internet
the first time; vendor it locally if you want fully offline use.
