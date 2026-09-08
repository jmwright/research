# Soil Logger + Stacked-Cycle-Ring Visualizer

Three small pieces, end to end:

1. **device/** — NodeMCU Amica (ESP8266 / MicroPython) firmware that samples the
   Adafruit STEMMA (Seesaw) soil sensor's **moisture and temperature**, logs
   timestamped CSV to flash, and serves the whole log over TCP.
2. **client/** — a Python downloader that pulls the log to your laptop as CSV.
3. **viz/** — a Node.js + Three.js visualizer that wraps the series into
   **stacked cycle rings**: phase (time of day) goes *around* each ring,
   day/cycle number goes *up* the stack. Moisture is the ring radius; color is
   moisture or temperature. This is pure visualization — no prediction — so you
   can *see* whether a daily cycle exists, how much it drifts over days, and how
   noisy it is, before modeling anything.

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

Controls: **cycle length (hours)** re-wraps the data live — set it wrong and the
rings smear; set it to the true period and they snap into alignment (this is how
you *discover* the period). **color by** moisture/temp. **highlight day** scrolls
through the stack. **typical day ring** overlays the per-phase average as a white
reference loop. **isolate highlighted day** dims the rest. Drag to orbit, scroll
to zoom.

What to look for: a stable rhythm makes every ring alike (a smooth tube); drift
(a plant growing thirstier) makes the rings change as you climb; data gaps show
as breaks in a ring rather than false lines across missing time.

---

## What was tested, and what wasn't

Verified here: the downloader round-trips a log over TCP byte-for-byte; the
wrapping logic (`viz/public/rings.js`) correctly bins the sample into per-day
rings, keeps phases in range, splits rings at the injected gaps, and computes the
average ring; the server serves the page, `rings.js`, and the CSV. The device
firmware is syntax-checked but needs the real board + sensor to run. The Three.js
**3D rendering itself was not run headlessly** (no browser/WebGL in the build
environment) — the data pipeline feeding it is tested, but eyeball the first
render and adjust `BASE_R` / `R_SCALE` / `H` at the top of `app.js` to taste.

The visualizer loads Three.js from a CDN (jsdelivr), so the page needs internet
the first time; vendor it locally if you want fully offline use.
