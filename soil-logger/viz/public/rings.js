/* rings.js -- pure logic for wrapping a flat time series into stacked cycle
   rings. No browser or Three.js dependency, so it can be unit-tested in Node
   and reused in the page. UMD wrapper exports it both ways.

   Two things a reader should know up front:

   1. Rows are kept in FILE ORDER, never sorted by timestamp. The firmware
      schedules samples on a monotonic tick counter and only labels them with an
      NTP-stepped wall clock, so file order is acquisition order and the labels
      are the unreliable channel. Sorting would reorder real samples to satisfy
      a corrupt label -- and around a watering, where the interesting transient
      is, that is exactly where it would do damage. countBackwardSteps() reports
      how many labels went backwards so the page can show it rather than hide
      it.

   2. A "cycle" can be defined two ways: by the clock (fixed length, the
      original behaviour) or by watering events detected in the signal, running
      spike to spike. The second is the physically meaningful one for a
      dry-down: each ring is one complete watering-to-watering episode. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RingLib = factory();
}(typeof self !== "undefined" ? self : this, function () {

  // ---------- small helpers ----------

  function median(xs) {
    const v = xs.filter((x) => x != null).slice().sort((a, b) => a - b);
    if (!v.length) return null;
    const h = v.length >> 1;
    return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2;
  }

  function parseCSV(text) {
    const lines = text.trim().split(/\r?\n/);
    const rows = [];
    for (let i = 1; i < lines.length; i++) {            // skip header
      const p = lines[i].split(",");
      const t = Number(p[0]);
      if (isNaN(t)) continue;
      rows.push({
        t: t,
        moisture: p[1] === "" || p[1] == null ? null : Number(p[1]),
        temp: p[2] === "" || p[2] == null ? null : Number(p[2]),
      });
    }
    return rows;                                        // file order, unsorted
  }

  // How many timestamps step backwards from the one before. These are NTP
  // resyncs, not reordered data; the page reports the count as a health figure.
  function countBackwardSteps(rows) {
    let n = 0;
    for (let i = 1; i < rows.length; i++) if (rows[i].t <= rows[i - 1].t) n++;
    return n;
  }

  // Typical sample spacing, robust to the resync jumps.
  function medianInterval(rows) {
    const d = [];
    for (let i = 1; i < rows.length; i++) {
      const dt = rows[i].t - rows[i - 1].t;
      if (dt > 0) d.push(dt);
    }
    return median(d) || 300;
  }

  /* Robust per-sample noise, from the MAD of consecutive differences. Using
     differences rather than the spread of the values themselves removes the
     slow drying trend, so this measures the instrument and not the pot. The
     /sqrt(2) undoes the variance doubling from differencing. On the real log
     this returns ~17 counts. */
  function noiseEstimate(rows) {
    const d = [];
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].moisture != null && rows[i - 1].moisture != null) {
        d.push(Math.abs(rows[i].moisture - rows[i - 1].moisture));
      }
    }
    const m = median(d);
    return m == null ? 0 : 1.4826 * m / Math.SQRT2;
  }

  // ---------- watering detection ----------

  /* Local sample cadence, as a step function over the record.

     The detector's windows are specified in SECONDS but applied over INDICES,
     and turning one into the other needs a samples-per-window figure. A single
     global median would be wrong the moment the record holds more than one
     cadence -- and this one does: a firmware timebase fault stretched the
     interval from 300 s to 460 s between 2026-09-15 and 2026-09-17. Measuring
     in blocks tracks a regime change while staying robust to any one bad
     timestamp.

     Note what this does NOT do: indexing stays in file order throughout.
     Timestamps only ever set a window's SIZE, never its order, so an NTP step
     still cannot reorder a sample. */
  function localIntervals(rows, block) {
    const B = block || 100;
    const out = new Array(rows.length);
    const fallback = medianInterval(rows);
    for (let s = 0; s < rows.length; s += B) {
      const e = Math.min(rows.length, s + B);
      const d = [];
      for (let i = Math.max(1, s); i < e; i++) {
        const dt = rows[i].t - rows[i - 1].t;
        if (dt > 0) d.push(dt);
      }
      const m = median(d) || fallback;
      for (let i = s; i < e; i++) out[i] = m;
    }
    return out;
  }

  /* Per-sample noise measured on the samples immediately BEFORE index i.

     noiseEstimate() over the whole record returns ~17.8 counts, but this
     probe's noise scales with wetness, so that figure is an average of
     regimes rather than a description of any one of them. On the shelf ahead
     of the 2026-09-14 pour the swings run +-60. A threshold built from the
     global number sits *under* that local ceiling, an ordinary excursion
     clears it, and the onset scan latches on 75 minutes early. Measuring the
     noise where the scan actually looks is what removes that failure. */
  function localNoise(rows, i, nSamples) {
    const d = [];
    for (let k = Math.max(1, i - nSamples); k < i; k++) {
      if (rows[k].moisture != null && rows[k - 1].moisture != null) {
        d.push(Math.abs(rows[k].moisture - rows[k - 1].moisture));
      }
    }
    const m = median(d);
    return m == null ? 0 : 1.4826 * m / Math.SQRT2;
  }

  /* A watering is a step up far larger than the noise: the pour floods the
     probe zone with free water and the reading jumps 150-250 counts within a
     couple of samples, against a per-sample SD of ~17. We compare a short
     backward median to a short forward median rather than raw samples, so a
     single spiky reading cannot trigger it.

     Every window below is given in SECONDS. They used to be given in samples,
     which quietly made the detector a function of the logging cadence: the
     onset scan reached back 2*win samples, so it searched one hour of record
     at a 5 minute cadence and three hours at a 15 minute one, and the boundary
     it returned moved by up to 3.3 h across cadences on the same pour. Seconds
     make the search span a fixed amount of pot behaviour instead.

     opts.minRise      counts the forward median must exceed the backward one
     opts.minJump      counts a SINGLE sample may rise to qualify on its own
     opts.jumpK        ...and how many local noise SDs that jump must clear
     opts.winS         seconds each side of the candidate for the two medians
     opts.scanBackS    how far back the onset scan may reach
     opts.confirmS     how long a rise must hold to count as the onset
     opts.noiseWinS    span used for the local noise estimate
     opts.refractoryS  seconds to suppress further detections after one fires,
                       so the noisy drainage shoulder cannot re-trigger
     opts.noiseK       how many robust SDs above the pre-watering level a sample
                       must sit to count as part of the rise
     opts.noise        override the measured per-sample noise
     opts.minRiseFrac  floor for the same threshold, as a fraction of the step,
                       so a noiseless signal still gets a usable boundary

     Two gates, because a pour does not always look the same. Early waterings
     channelled past a hydrophobic mix and left free water bridging the probe
     for hours, which a median step detects easily. As the mix wetted through,
     the pour started being absorbed on contact: by 2026-09-17 the excursion was
     ONE sample and the median step had fallen to +63 against a threshold of 60.
     Extrapolated, what survives is the bare shelf step of ~23 counts, which is
     inside the noise -- the detector would simply stop finding waterings.

     So a large single-sample rise qualifies on its own. The threshold is
     measured, not guessed: across 1,779 non-pour rises in the real log the
     largest is 82 counts (99.9th percentile 79), while the pours jump 158, 164
     and 179. 100 sits above the noise ceiling with room and below the smallest
     real jump with more. A jump that big is ~10x the local noise SD, so it does
     not need the sustain test that protects the median path -- and must not use
     it, since these pours no longer sustain.

     Returns the index of the first sample that is genuinely rising, which is
     the cycle boundary. */
  function detectWaterings(rows, opts) {
    const o = Object.assign({
      minRise: 60,
      minJump: 100,             // measured: non-pour rises top out at 82
      jumpK: 5,
      winS: 1800,               // 30 min -- 6 samples at the 5 min cadence
      minWin: 4,                // ...but never fewer samples than this
      scanBackS: 3600,
      confirmS: 1800,
      noiseWinS: 4 * 3600,
      refractoryS: 12 * 3600,
      noiseK: 2.5,
      minRiseFrac: 0.25,
    }, opts || {});
    const dtl = localIntervals(rows);
    const nFor = (i, secs) => Math.max(2, Math.round(secs / dtl[i]));
    /* The two medians need a time span AND enough samples to be a median at
       all. Asking only for seconds gives ~2 samples at a 15 minute cadence,
       and a 2-sample median is just a mean of two noisy readings -- which
       manufactures steps that clear minRise and reports pours that never
       happened. Floor the count; below that the window grows in time again,
       which is the honest trade when samples are simply sparse. */
    const winFor = (i) => Math.max(o.minWin, nFor(i, o.winS));
    const out = [];
    let blockUntil = -Infinity;

    for (let i = 0; i < rows.length; i++) {
      const w = winFor(i);
      if (i < w || i >= rows.length - w) continue;
      if (rows[i].t < blockUntil) continue;
      const pre = [], post = [];
      for (let k = i - w; k < i; k++) if (rows[k].moisture != null) pre.push(rows[k].moisture);
      for (let k = i; k < i + w; k++) if (rows[k].moisture != null) post.push(rows[k].moisture);
      if (pre.length < 2 || post.length < 2) continue;
      const a = median(pre), b = median(post);

      /* The biggest single-sample rise inside the forward window, and where it
         happened. For an absorbed-on-contact pour this IS the pour. */
      let jump = 0, jumpAt = -1;
      for (let k = i; k < i + w && k + 1 < rows.length; k++) {
        if (rows[k].moisture == null || rows[k + 1].moisture == null) continue;
        const d = rows[k + 1].moisture - rows[k].moisture;
        if (d > jump) { jump = d; jumpAt = k + 1; }
      }
      const byStep = (b - a) >= o.minRise;
      if (!byStep && jump < o.minJump) continue;      // cheap gate, no medians

      /* Find the first sample of the rise. The detection index sits at the
         START of the forward window, which is before the pour, so scan forward
         for the onset rather than trusting i. Anchoring that scan to the bare
         pre-watering median does not work: per-sample noise is right-skewed, so
         isolated pre-watering samples sit above the median and would be taken
         for the onset. Anchor it at noiseK LOCAL robust SDs above the
         pre-watering level, floored at a fraction of the detected step so that
         a noiseless signal still gets a usable boundary. */
      const noise = o.noise != null ? o.noise : localNoise(rows, i, nFor(i, o.noiseWinS));
      // Confirm the jump against local noise too, so a noisy stretch cannot
      // clear the absolute floor on chatter alone.
      const byJump = jump >= Math.max(o.minJump, o.jumpK * noise);
      if (!byStep && !byJump) continue;
      const riseLevel = a + Math.max(o.noiseK * noise, o.minRiseFrac * (b - a));

      /* Clearing the threshold once is not enough. A pour floods the probe and
         STAYS up; a noise excursion falls straight back the next sample. So a
         candidate onset must also hold its level over confirmS. This is what
         separates the real 2026-09-14 pour at 10:11 (745 -> 924, and it stays)
         from the excursion at 08:56 (725 -> 786 -> 731), which the old
         single-sample test accepted as the boundary. */
      /* A qualifying jump names its own onset: the sample that jumped is the
         first sample of the pour, with no scanning needed. Prefer it -- it is
         both more direct and more robust than hunting a threshold crossing. */
      if (byJump) {
        out.push({
          index: jumpAt, t: rows[jumpAt].t, pre: a, post: b,
          rise: b - a, jump: jump, riseLevel: riseLevel, noise: noise, byJump: true,
        });
        blockUntil = rows[i].t + o.refractoryS;
        continue;
      }

      const cn = nFor(i, o.confirmS);
      const from = Math.max(1, i - nFor(i, o.scanBackS));
      const limit = Math.min(rows.length - 1, i + w);
      let onset = -1, firstOver = -1;
      for (let j = from; j < limit; j++) {
        if (rows[j].moisture == null || rows[j].moisture < riseLevel) continue;
        if (firstOver < 0) firstOver = j;
        const fwd = [];
        for (let k = j; k < Math.min(rows.length, j + cn); k++) {
          if (rows[k].moisture != null) fwd.push(rows[k].moisture);
        }
        const fm = median(fwd);
        if (fm != null && fm >= riseLevel) { onset = j; break; }
      }
      // Nothing held: fall back to the first sample over the line, then to the
      // detection index, so a boundary is always reported for a real step.
      if (onset < 0) onset = firstOver >= 0 ? firstOver : i;

      out.push({
        index: onset, t: rows[onset].t, pre: a, post: b,
        rise: b - a, jump: jump, riseLevel: riseLevel, noise: noise, byJump: false,
      });
      blockUntil = rows[i].t + o.refractoryS;
    }
    return out;
  }

  // ---------- cycles ----------

  /* Slice rows into watering-to-watering cycles in file order. Whatever
     precedes the first watering becomes a leading partial cycle (for this rig,
     the insertion cycle -- calibration, not training data) and whatever follows
     the last watering is the running partial cycle. Both are flagged
     `complete: false` so the page can label them. */
  function buildCyclesFromWaterings(rows, waterings) {
    const bounds = [0].concat(waterings.map((w) => w.index)).concat([rows.length]);
    const cycles = [];
    for (let k = 0; k < bounds.length - 1; k++) {
      const pts = rows.slice(bounds[k], bounds[k + 1]).filter((r) => r.moisture != null);
      if (pts.length < 2) continue;
      const openedByWatering = k > 0;
      const closedByWatering = k < bounds.length - 2;
      cycles.push({
        points: pts,
        startT: pts[0].t,
        endT: pts[pts.length - 1].t,
        openedByWatering: openedByWatering,
        closedByWatering: closedByWatering,
        complete: openedByWatering && closedByWatering,
        watering: openedByWatering ? waterings[k - 1] : null,
      });
    }
    return cycles;
  }

  // The original fixed-length wrapping, expressed as cycles so both modes feed
  // the same renderer.
  function buildCyclesFromClock(rows, cycleSeconds) {
    if (!rows.length) return [];
    const t0 = rows[0].t;
    const byCycle = new Map();
    for (const r of rows) {
      if (r.moisture == null) continue;
      const k = Math.floor((r.t - t0) / cycleSeconds);
      if (!byCycle.has(k)) byCycle.set(k, []);
      byCycle.get(k).push(r);
    }
    return [...byCycle.entries()].sort((a, b) => a[0] - b[0])
      .filter(([, pts]) => pts.length >= 2)     // a 1-point ring cannot be drawn
      .map(([, pts]) => ({
      points: pts,
      startT: pts[0].t,
      endT: pts[pts.length - 1].t,
      openedByWatering: false,
      closedByWatering: false,
      complete: true,
      watering: null,
      clockCycle: true,
    }));
  }

  /* The cycle's settled level -- the shelf the reading sits on once free water
     has drained out of the probe zone. Taken as the median of the last
     `tailFrac` of the cycle by TIME (not by sample count, so a gap cannot skew
     it). Drainage occupies roughly the first quarter of a cycle here, so the
     default 0.6 clears it comfortably while still averaging enough samples to
     beat the ~18-count per-sample noise. Returns null if too little is left. */
  function cycleShelf(cycle, tailFrac) {
    const f = tailFrac == null ? 0.6 : tailFrac;
    const span = cycle.endT - cycle.startT;
    if (!(span > 0)) return median(cycle.points.map((p) => p.moisture));
    const from = cycle.endT - span * f;
    const tail = cycle.points.filter((p) => p.t >= from).map((p) => p.moisture);
    return tail.length >= 3 ? median(tail) : median(cycle.points.map((p) => p.moisture));
  }

  // ---------- rings ----------

  /* Turn cycles into rings ready to draw.

     opts.phase  "normalized" -- phase is the fraction through that cycle, so
                 every ring closes and you compare SHAPE regardless of length.
                 "elapsed"    -- phase is time since the cycle started divided
                 by the longest cycle, so every ring shares one time axis and a
                 short cycle visibly falls short of closing. Use this to compare
                 when things happen (drainage end, shelf onset).
     opts.frame  "absolute" -- plot raw counts.
                 "relative" -- plot counts relative to that cycle's own shelf,
                 which is the only way to compare cycles whose shelves sit at
                 different levels. Reference-frame switch.
     opts.shelfTailFrac  passed to cycleShelf. */
  function buildRings(cycles, opts) {
    const o = Object.assign({ phase: "normalized", frame: "absolute", shelfTailFrac: 0.6 }, opts || {});
    const empty = { rings: [], value: [0, 1], temp: [0, 1], spanSeconds: 0 };
    if (!cycles.length) return empty;

    const longest = Math.max.apply(null, cycles.map((c) => c.endT - c.startT)) || 1;
    let vMin = Infinity, vMax = -Infinity, cMin = Infinity, cMax = -Infinity;

    const rings = cycles.map((cycle, idx) => {
      const shelf = cycleShelf(cycle, o.shelfTailFrac);
      const denom = o.phase === "elapsed" ? longest : Math.max(1, cycle.endT - cycle.startT);
      const points = cycle.points.map((p) => {
        const value = o.frame === "relative" && shelf != null ? p.moisture - shelf : p.moisture;
        if (value < vMin) vMin = value;
        if (value > vMax) vMax = value;
        if (p.temp != null) { if (p.temp < cMin) cMin = p.temp; if (p.temp > cMax) cMax = p.temp; }
        return {
          t: p.t,
          phase: Math.max(0, Math.min(1, (p.t - cycle.startT) / denom)),
          elapsedH: (p.t - cycle.startT) / 3600,
          moisture: p.moisture,
          temp: p.temp,
          value: value,
        };
      });
      points.sort((a, b) => a.phase - b.phase);     // draw order only
      return {
        cycle: idx,
        points: points,
        shelf: shelf,
        startT: cycle.startT,
        endT: cycle.endT,
        lengthH: (cycle.endT - cycle.startT) / 3600,
        complete: cycle.complete,
        openedByWatering: cycle.openedByWatering,
        closedByWatering: cycle.closedByWatering,
        watering: cycle.watering,
      };
    });

    return {
      rings: rings,
      value: [vMin === Infinity ? 0 : vMin, vMax === -Infinity ? 1 : vMax],
      temp: [cMin === Infinity ? 0 : cMin, cMax === -Infinity ? 1 : cMax],
      spanSeconds: longest,
    };
  }

  /* Break a ring where the record has a genuine hole, so we never draw a line
     across missing time.

     The threshold is in SECONDS and is compared against timestamps. It used to
     be given in PHASE units, which quietly made it a function of cycle length:
     normalized phase is time over the cycle's OWN span, so one fixed number
     meant 6.3 h of tolerance on the 105 h cycle and 3.0 min on a 51-minute
     running cycle -- shorter than the 5 min sample interval. Every consecutive
     pair then read as a gap, the ring came apart into one-point segments, and
     since a line needs two points the renderer dropped the ring entirely. The
     cycle this hits is always the newest one, which is the cycle holding the
     pour that was just made: the event you most want to see was the one event
     guaranteed to be invisible. Same fault as the detector's sample-counted
     windows -- a threshold in the wrong units becomes a function of something
     it should not depend on -- and the same fix: seconds.

     The default adapts to the ring's own cadence instead of being tuned to one
     of them, so the 300 s / 460 s regime change in this record needs no
     re-tuning. The largest real gap in the log is 1596 s, so the floor splits
     nothing that is merely jitter while still cutting a genuine outage. */
  const GAP_FLOOR_S = 1800;       // 30 min -- above the 1596 s worst real gap
  const GAP_K = 4;                // ...or 4 sample intervals, whichever is more

  function segments(points, maxGapS) {
    const g = maxGapS != null ? maxGapS
      : Math.max(GAP_FLOOR_S, GAP_K * (medianInterval(points) || 300));
    const segs = [];
    let cur = [];
    for (let i = 0; i < points.length; i++) {
      if (i > 0 && points[i].t - points[i - 1].t > g) {
        if (cur.length) segs.push(cur);
        cur = [];
      }
      cur.push(points[i]);
    }
    if (cur.length) segs.push(cur);
    return segs;
  }

  // The "typical cycle": mean per phase bin across every ring, in whatever
  // frame the rings were built in.
  function averageRing(rings, bins) {
    const sV = new Array(bins).fill(0), nV = new Array(bins).fill(0);
    const sC = new Array(bins).fill(0), nC = new Array(bins).fill(0);
    for (const ring of rings) for (const p of ring.points) {
      const b = Math.min(bins - 1, Math.max(0, Math.floor(p.phase * bins)));
      if (p.value != null) { sV[b] += p.value; nV[b]++; }
      if (p.temp != null) { sC[b] += p.temp; nC[b]++; }
    }
    const out = [];
    for (let b = 0; b < bins; b++) {
      out.push({
        phase: (b + 0.5) / bins,
        value: nV[b] ? sV[b] / nV[b] : null,
        temp: nC[b] ? sC[b] / nC[b] : null,
      });
    }
    return out;
  }

  return {
    parseCSV: parseCSV,
    countBackwardSteps: countBackwardSteps,
    medianInterval: medianInterval,
    noiseEstimate: noiseEstimate,
    localIntervals: localIntervals,
    localNoise: localNoise,
    median: median,
    detectWaterings: detectWaterings,
    buildCyclesFromWaterings: buildCyclesFromWaterings,
    buildCyclesFromClock: buildCyclesFromClock,
    cycleShelf: cycleShelf,
    buildRings: buildRings,
    segments: segments,
    averageRing: averageRing,
  };
}));
