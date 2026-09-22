# Electrical Conductivity Sensor

A DIY electrical conductivity (EC) probe for local waterways, built to find out
whether a home-made two-electrode cell can produce defensible water-quality
measurements. Open-source, aimed at screening for agricultural runoff and
industrial discharge in Indiana rivers.

**Status: nothing built, nothing bought.** This file is the design record. It
supersedes the original firmware handoff brief, which it absorbs and corrects in
two places (noted inline).

---

## The goal is the constraint

This is a **handheld spot-sampler, not a deployed logger**. The purpose is to
prove the design does real science *before* committing to long deployments.

That has a consequence worth stating plainly, because it keeps getting
re-derived and set aside: **battery life, deep sleep, solar, gateways and
low-power radio are all out of scope.** An afternoon of runtime is the entire
power requirement. Don't design for months.

It also rules out buying the problem away. A commercial front end (Atlas
Scientific EZO-EC and similar) would produce good numbers immediately, but it
would prove *Atlas's* design works and sidestep the question being asked. The
design under test **is** the cell.

A commercial unit still has a place — as a **reference instrument rather than
the instrument**. Even a cheap handheld EC pen gives an independent ruler to
check against, and that is most of what validation means here.

## What EC can and cannot tell you

EC is **non-specific**. It measures total dissolved ionic content and cannot
distinguish agricultural runoff from industrial discharge from road salt from
natural carbonate. Indiana's glacial-till carbonate geology produces high
baselines on its own.

So EC is a **screening instrument**: it says *where* to look and *when*
something changed. It does not identify what changed. The scientific value comes
from the survey design — transects and time series — not from the number itself.

---

## Measurement principle

### Why AC excitation is mandatory

DC across electrodes in water causes electrolysis (bubbles, corrosion, plating)
and **electrode polarization**: ions accumulate at the electrode surfaces and
form a double-layer capacitance, so apparent cell resistance drifts upward for
as long as the probe is energised. Reversing polarity fast enough prevents net
ion accumulation and net electrolysis.

The excitation must therefore be **bipolar, symmetric and DC-free**. A design
that applies DC fails in the worst possible way: it produces stable-looking
numbers that are wrong in a direction depending on how long you have been
standing there.

### Antiphase two-pin drive

A bare MCU pin swings 0 to Vcc and cannot go negative. The workaround is to
drive the cell **across two digital pins in antiphase** — pin A high / pin B
low, then swap. Across the series combination this gives a symmetric ±Vcc square
wave with no negative supply and no coupling capacitor. Net DC is ~0 by
construction.

Residual DC comes only from the small mismatch between an output driver's high-
and low-side impedance. Negligible for spot sampling; would matter over a long
deployment.

- **Waveform:** square. A clean sine buys nothing for EC.
- **Frequency:** ~1–2 kHz for DIY two-electrode cells. Higher EC favours the
  higher end, because lower cell resistance makes the double-layer impedance a
  larger fraction of what you measure.
- **Symmetry:** equal time and amplitude each polarity.

### Ratiometric sensing

A series sense resistor sits in the cell circuit; the ADC measures the voltage
across it. Because both the excitation and the ADC reference scale with Vcc,
computing from the **ratio** cancels supply variation. Keep the ADC reference on
Vcc — do not switch to an internal bandgap reference, which would break this.

Note the common-mode consequence of the antiphase drive: on alternate
half-cycles the sense node sits near ground and near Vcc respectively, so one
polarity reads `V` and the other reads `Vcc − V`. Taking the magnitude across
both half-cycles is what reconciles them.

### Polarity averaging does double duty

Averaging magnitudes across both polarities cancels offset — and it also
cancels something the original brief did not mention. **Two stainless screws are
never quite the same alloy**, so there is a galvanic DC offset sitting across
the cell, tens to hundreds of millivolts, that would otherwise add straight into
the reading. Polarity averaging removes it for free. Use two identical screws
from the same batch anyway.

---

## The biggest risk is mechanical, not electronic

**An open two-electrode cell has no defined field geometry, so it has no stable
cell constant.** Current between two bare screws spreads into the whole
surrounding volume. A beaker's walls confine the field one way; open river water
lets it spread; nearby sediment, the probe body, or an operator's hand change it
again. A cell constant calibrated in a beaker silently does not apply in the
stream.

This is worse than noise because it is a **bias with no signature**. Nothing in
the data reveals it, and averaging more readings only increases confidence in a
wrong number.

**Fix: shroud the electrodes.** Mount the screws inside a short length of PVC or
acrylic tube, open at both ends so water flows through, electrodes recessed from
the ends. That fixes the field volume to the tube's geometry, making K a
property of the probe rather than of its surroundings — and making beaker
calibration transfer to the river.

This is the single most important change to the original design, and it costs a
couple of dollars of pipe.

## Relative beats absolute — design around differences

The field workbook has an **outfall transect sheet**, and that is the most
important fact about this project.

A transect is a *difference* measurement: upstream versus downstream of an
outfall, minutes apart, same probe, same water, same temperature.
**Cell-constant error very largely cancels in a difference.** If K is 15% off,
both readings are 15% off in the same direction and the *step* across the
outfall survives nearly intact.

So the scientific claim — "there is a discharge here" — rests on **relative
precision**, which a DIY cell can deliver, not on **absolute accuracy**, which
is the genuinely hard part.

This is the same shape as the result in `../soil-logger`: absolute counts died,
cycle-relative won. Design the field protocol around differences and treat
absolute µS/cm as the secondary number.

---

## Sampling

### Sample early in the half-cycle, not late

**This corrects the original handoff brief**, which said to sample just before
each polarity flip "so the value has settled." That is a DC instinct and it is
backwards here.

The cell is solution resistance in series with double-layer capacitance.
Immediately after a polarity flip the double layer is uncharged in the new
direction and the current reflects the solution resistance. As the half-cycle
proceeds the double layer charges and the current **decays** — so the end of the
half-cycle is the *most* polarized moment, not the most settled. Sampling there
maximises exactly the error that AC excitation exists to prevent.

Sample **early**: after the cable and stray transient has died, before the
double layer charges appreciably.

### Capture the half-cycle shape as a diagnostic

Do not assume where that point is — measure it. **Sample across the whole
half-cycle in v1 and log the shape.** The decay curve says directly whether
polarization is contaminating the reading:

- Flat across the half-cycle → sampling phase does not matter, the cell is clean.
- Visibly sagging → the double layer is charging appreciably; raise the
  frequency or sample earlier.

This is the same role the `spread` diagnostic plays in the soil firmware: cheap
instrumentation that tells you whether the primary measurement is trustworthy.

### Coherent averaging and 60 Hz

Average many excitation cycles coherently, with the polarity sign applied. This
gives sqrt(N) noise reduction and rejects DC offset.

**Choose the integration window as a whole number of mains periods (multiples of
1/60 s).** A probe on wires in a river picks up 60 Hz — from power lines, pumps,
and the pole transformer near the outfall you are most interested in. An
integer-cycle window nulls it for free.

Oversampling with genuine dither also buys back real resolution, which matters
given that neither candidate MCU has an excellent ADC (see below).

---

## Diagnostics to build in from the start

**Two-frequency check.** Measure the same water at ~1 kHz and ~4 kHz. If the
answers differ, you are measuring electrode polarization rather than bulk
conductivity; if they agree, you are clean. A few lines of firmware, and it can
be run **in the field on the actual water** — which matters, because
polarization depends on current density and behaves differently than in the
calibration beaker.

**Half-cycle decay shape.** As above.

**Sense-resistor range in the log.** If auto-ranging is implemented, record
which range was active. A range switch is a discontinuity in the instrument, and
an unrecorded one is a regime boundary you cannot find later.

---

## Temperature compensation

EC is strongly temperature dependent. Natural waters run **1.5–2.2 %/°C**, with
**1.91 %/°C** the standard KCl figure. Report **specific conductance at 25 °C**:

```
EC25 = EC_raw / (1 + 0.0191 * (T - 25))
```

For scale: the soil probe's tempco is ~0.15 %/°C in fractional terms. **EC's is
roughly thirteen times larger.** A 5 °C difference between two sites is a ~10%
difference in raw reading with no chemistry behind it. This is a dominant term,
not a correction.

**Sensor: DS18B20.** Digital, so no second ADC channel and no self-heating
concern; available in a sealed stainless probe for a few dollars; needs no
calibration of its own. **Mount it in the flow at the electrodes**, not on the
board, or you will compensate with the wrong temperature. Let it equilibrate
before reading — dropping a warm probe into cold water produces a reading that
drifts for a minute or two, and at 1.91 %/°C it drifts a lot.

---

## Range and component sizing

Indiana streams run roughly **300–800 µS/cm** at baseline (IDEM sampling shows
397 and 523 µS/cm at two Indian Creek sites), with winter chloride and runoff
events pushing well into the thousands. **Design for 100–5000 µS/cm.**

With a shrouded cell aimed at K ≈ 1.0 cm⁻¹, `R_cell = K / EC`:

| EC (µS/cm) | R_cell |
| --- | --- |
| 100 | 10 kΩ |
| 500 | 2 kΩ |
| 1000 | 1 kΩ |
| 5000 | 200 Ω |

A single **1.5 kΩ** sense resistor (geometric mean of the span) puts a 10-bit
ADC between ~133 and ~903 counts across the full range — good use of the scale.

Better: **two ranges, 470 Ω and 4.7 kΩ**, selected by driving one pin LOW and
leaving the other as a high-impedance input. Costs one pin, no parts, and
un-compresses both ends. Log which range was used.

---

## Platform

### The tradeoff is not actually forced

The apparent choice is timing determinism (Nano) versus wireless retrieval and
storage (ESP32). On the right silicon that tradeoff disappears, because **the
timing can live in hardware the radio cannot touch.**

### Options

| | Timing | ADC | Storage | Wireless |
| --- | --- | --- | --- | --- |
| **Nano alone** | software loop, deterministic only while nothing else runs | 10-bit, ~10 kSPS, clean | none | none |
| **Nano + ESP32 split** | fully isolated — best separation of concerns | as above | on host | on host |
| **Pico 2 W (RP2350)** | **PIO — hardware state machine, immune to the radio** | 12-bit, 500 kSPS, **noisy** | 4 MB flash / LittleFS | Wi-Fi + BLE |

### Recommendation: Pico 2 W

- **PIO generates the antiphase drive as a separate state machine**, cycle-exact
  regardless of what the CPU is doing. This is the decisive property: the CYW43
  wireless driver does blocking SPI transfers that can stall a CPU loop for
  milliseconds, which would wreck a software-timed excitation — and leaves PIO
  untouched.
- **Excitation and ADC share one clock domain**, so they are phase-locked by
  construction and never drift apart.
- **500 kSPS** gives ~250 samples per half-cycle at 1 kHz, so the half-cycle
  decay diagnostic comes essentially free and at high resolution. The Nano's
  ~10 kSPS gives about 5 — enough to detect sag, not to characterise it.
- **Built-in flash and Wi-Fi/BLE** solve storage and retrieval directly.
- **MicroPython** matches the existing stack, and the CSV-over-TCP pattern plus
  `client/download.py` from `../soil-logger` transfer with little change.
- 3.3 V rather than 5 V means slightly less excitation amplitude — cancelled by
  the ratiometric computation — and slightly less electrolysis. Net neutral to
  positive.
- ~$7.

### The honest caveat: the RP2350 ADC is not good

Do not assume the newer chip fixed this. Measured behaviour:

- **DNL jumps at multiples of 512** (1536, 2048, 2560). Better than RP2040, not
  eliminated.
- **No on-board reference** — ADC_AVDD comes from the SMPS 3.3 V through an R-C
  filter.
- **~60 LSB of noise at 12-bit in default SMPS mode.** Driving **GPIO23 high**
  forces the SMPS into PWM mode and brings this to **~20 LSB**. Do this; it is
  one line and a 3x improvement.

At ~20 LSB of 4096 that is ~0.5% per sample — comparable to or worse than the
AVR's clean 10-bit. **The Pico only wins once you oversample**, which its speed
makes easy: 50x averaging takes 20 LSB to ~3 LSB (~0.07%), and the noise
provides the dither that averages the DNL steps away.

**Escape hatch if resolution proves limiting:** an external SPI SAR ADC —
MCP3208 (12-bit, 100 kSPS, ~$3) or similar. 100 kSPS is still 100 samples per
half-cycle at 1 kHz. Note that the obvious choice, the ADS1115, is **ruled out**:
at 860 SPS it cannot sample inside the excitation waveform at all.

Do not buy this up front. Measure the noise first — the same way the soil probe's
per-level sigma was measured — and only then decide.

### Why not a Raspberry Pi Zero W

Considered 2026-09-22 (one was already on hand) and rejected:

- **No ADC.** No Raspberry Pi has analog inputs, so an external ADC is required
  before anything else works.
- **Linux is not real-time.** Userspace GPIO jitter runs tens to hundreds of
  microseconds and occasionally into milliseconds under scheduler preemption. A
  half-cycle at 1 kHz is 500 us, so the jitter is comparable to the entire
  window being sampled. The original Zero W is single-core, which makes this
  worse.
- pigpio DMA waveforms can generate a clean square wave (~1 us jitter), which
  rescues the excitation — but not the phase-locked *sampling*, which is the
  half of the problem that matters.
- **Unclean power-down corrupts the SD card.** A handheld instrument at a creek
  bank will have its power yanked.
- ~120-150 mA and a 20-30 s boot, against ~20 mA and instant start.

It remains useful as a **bench-side host** — calibration sessions, analysis on
device, a real web UI in AP mode — and as a future base station. Not as the
measurement engine.

### Sequencing: the mechanical question comes first

**The largest unresolved risk — whether shrouding stabilises K, and whether
beaker calibration transfers to open water — is entirely MCU-independent.** It
can be answered with the Nano already on hand, for no money: build the shroud,
calibrate in a beaker, then measure the same standard in containers of
different geometry and see whether K holds.

If K does not hold, no microcontroller rescues the design. If it does, the
result also says what resolution is actually needed, which may change the
platform answer. **Settle the cell first; buy silicon second.**

### If the Nano is preferred instead

The existing Nano experiments are not wasted either way: the analog front end —
electrodes, shroud, sense resistor, divider topology — is identical, and only
the MCU code changes. If the Nano stays, the working architecture is **Nano as a
dedicated EC front end, ESP32-S3 as host over serial**, which restores storage
and wireless without compromising timing. Costs: two boards, two firmwares, and
level shifting on the 5 V → 3.3 V line.

---

## Data schema

Log **raw and derived values as separate columns**. Never store only the
compensated number — if the temperature coefficient or the cell constant is
later found wrong, you must be able to recompute from raw.

```
unix_time, adc_raw, sense_range_ohms, r_cell_ohms, ec_raw_us_cm, temp_c, ec25_us_cm
```

Plus, per reading, the diagnostic fields: half-cycle sample count, within-burst
spread, and (when run) the two-frequency pair.

Match the existing field-log ODS workbook's outfall transect sheet where the
columns overlap — **that workbook has not been reviewed yet** (see open items).

---

## Field protocol

The protocol is what makes the result defensible; the hardware only makes it
possible.

- **Calibrate before *and* after each outing, and record both.** The drift
  between them bounds the day's uncertainty. Highest-value habit on this list.
- **Carry a check standard and measure it at the site**, not only on the bench.
- **Field blank** in distilled water, to catch contamination and carryover.
- **Replicates per site — report median and spread**, never a spot value.
- **Rinse between sites.** Carryover is real.
- **Sample upstream of where you are standing**, and avoid stirring sediment.
- **Let the probe thermally equilibrate** before reading.
- **Record position and time** for every reading. A measurement without both is
  not data.

### Calibration standards

KCl standards at **84, 1413 and 12880 µS/cm**. For freshwater streams, two-point
at 84 and 1413 covers the working range. Buying standards is the purchase that
converts a number into a measurement.

### The reference instrument, and the TDS trap

A cheap handheld pen is a legitimate independent ruler — **but only once it has
been calibrated against a standard.** Out of the box it is not.

Beware what these pens actually measure. A "TDS/EC/TEMP three-in-one" pen has
**one** sensor: it measures EC and multiplies by a conversion factor to display
TDS in ppm. **TDS is not an independent measurement.** Common scales are 0.5
(NaCl), 0.64 (TDS-4) and 0.7 ("442"), and the correct factor is salt-specific,
ranging roughly 0.5-0.8 depending on what is dissolved.

Consequences:

- Two pens can disagree by 40% on TDS while agreeing exactly on EC. The
  disagreement lives entirely in an assumed constant.
- **Always record uS/cm, never ppm.** USGS parameter `00095` is in uS/cm; stay
  in the same unit as the validation target. Comparing uS/cm against a ppm
  display means comparing a number to roughly half itself, which looks like a
  catastrophic calibration error and is not one.
- Cheap pens apply **automatic temperature compensation with an unstated
  coefficient**, often a fixed 2 %/degC to 25 degC, and some do not compensate
  at all. Compare a pen reading against `ec25`, never against raw EC — and only
  after confirming what it does.

Buying criteria: **displays uS/cm** (not ppm-only), **is calibratable** against
a standard, and **states its ATC behaviour**. Many cheap pens fail the first
two.

Buy the standards before the pen. They are what makes either instrument mean
anything, and the cell needs them regardless.

### Validation: measure next to a USGS gauge

USGS NWIS publishes real-time **specific conductance as parameter `00095`** at
gauged Indiana sites. Taking readings alongside a gauge that reports 00095 gives
an independent, traceable comparison against an instrument that is maintained
and calibrated by someone else.

**This is the most direct possible answer to "can real science be done with this
design," and it costs a drive.** The first proving outing should be built around
it. NWIS also gives the actual EC range of the target reaches for free, before a
single resistor is sized.

---

## Open decisions

- **The field-log ODS workbook** — not yet reviewed. Needed before the CSV
  schema is fixed.
- **Platform** — Pico 2 W recommended above; not decided.
- **Excitation frequency** — start ~1 kHz, tune empirically against the
  half-cycle decay diagnostic.
- **Cell geometry** — shroud dimensions, electrode spacing and recess depth, to
  land near K ≈ 1.0 cm⁻¹.
- **Auto-ranging** — one resistor or two.
- **Which USGS gauge** to target for the first validation outing.

## Upgrade path (not for v1)

- **Four-electrode cell** — drive with two, sense with two high-impedance
  probes. Far more robust to fouling and polarization, since the sense
  electrodes carry essentially no current. This is the real answer for any long
  deployment.
- **External SPI ADC** (MCP3208 class) if oversampling proves insufficient.
- **Op-amp with virtual ground or an external DAC** for a true bipolar analog
  drive, if a cleaner waveform is ever needed.
- **Biofouling** becomes a first-order problem within days of immersion. It does
  not affect spot sampling at all, and it will dominate any deployment.

## References

- [USGS TWRI 6.3 — Specific Electrical Conductance](https://pubs.usgs.gov/twri/twri9a6/twri9a63/twri9a63.pdf)
- [USGS Indiana real-time water data (NWIS)](https://waterdata.usgs.gov/in/nwis/rt)
- [Temperature compensation for conductivity](https://www.aqion.de/site/112)
- [IDEM Indian Creek sampling results](https://www.in.gov/idem/nps/files/tmdl_indian_creek_monroe_q4_sampling_results.pdf)
- [Raspberry Pi Pico 2 datasheet](https://datasheets.raspberrypi.com/pico/pico-2-datasheet.pdf) — ADC supply, SMPS mode pin
- [RP2350 ADC INL/DNL discussion](https://github.com/earlephilhower/arduino-pico/issues/2534)
