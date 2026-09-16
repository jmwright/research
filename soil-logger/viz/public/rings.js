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

  /* A watering is a step up far larger than the noise: the pour floods the
     probe zone with free water and the reading jumps 150-250 counts within a
     couple of samples, against a per-sample SD of ~17. We compare a short
     backward median to a short forward median rather than raw samples, so a
     single spiky reading cannot trigger it.

     opts.minRise      counts the forward median must exceed the backward one
     opts.win          samples on each side of the candidate (~30 min at 5 min)
     opts.refractoryS  seconds to suppress further detections after one fires,
                       so the noisy drainage shoulder cannot re-trigger
     opts.noiseK       how many robust SDs above the pre-watering level a sample
                       must sit to count as part of the rise. 2.5 clears the
                       observed pre-watering noise (peaks ~0.6 SD over median)
                       with room to spare while still catching the first sample
                       of the pour; 3.0 was half a count too high and missed it
     opts.noise        override the measured per-sample noise
     opts.minRiseFrac  floor for the same threshold, as a fraction of the step,
                       so a noiseless signal still gets a usable boundary

     Returns the index of the first sample that is genuinely rising, which is
     the cycle boundary. */
  function detectWaterings(rows, opts) {
    const o = Object.assign({ minRise: 60, win: 6, refractoryS: 12 * 3600, noiseK: 2.5, minRiseFrac: 0.25 }, opts || {});
    const noise = o.noise != null ? o.noise : noiseEstimate(rows);
    const out = [];
    let blockUntil = -Infinity;
    for (let i = o.win; i < rows.length - o.win; i++) {
      if (rows[i].t < blockUntil) continue;
      const pre = [], post = [];
      for (let k = i - o.win; k < i; k++) if (rows[k].moisture != null) pre.push(rows[k].moisture);
      for (let k = i; k < i + o.win; k++) if (rows[k].moisture != null) post.push(rows[k].moisture);
      if (pre.length < 2 || post.length < 2) continue;
      const a = median(pre), b = median(post);
      if (b - a < o.minRise) continue;

      /* Find the first sample of the rise. The detection index sits at the
         START of the forward window, which is before the pour, so scan forward
         for the onset rather than trusting i. Anchoring that scan to the bare
         pre-watering median does not work: per-sample noise is ~17 counts and
         right-skewed, so isolated pre-watering samples sit above the median and
         would be mistaken for the onset, dragging the boundary minutes early.
         Anchor it at noiseK robust SDs above the pre-watering level -- high
         enough that noise cannot reach it, low enough that the first genuine
         sample of the pour clears it. On the real log this lands both
         boundaries on the exact samples recorded in the log book.

         Floor it at a fraction of the detected step as well. On a very quiet
         signal the noise term goes to zero, the threshold collapses onto the
         pre-watering median, and since the shelf is gently declining every
         earlier sample sits above it -- so the scan would run backwards to the
         start of its window and put the boundary before the pour. */
      const riseLevel = a + Math.max(o.noiseK * noise, o.minRiseFrac * (b - a));
      let j = Math.max(1, i - 2 * o.win);
      const limit = Math.min(rows.length - 1, i + o.win);
      while (j < limit && !(rows[j].moisture != null && rows[j].moisture >= riseLevel)) j++;
      out.push({ index: j, t: rows[j].t, pre: a, post: b, rise: b - a, riseLevel: riseLevel });
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

  // Break a ring where the phase gap is large (a data gap), so we never draw a
  // fake line across missing time.
  function segments(points, maxGap) {
    const segs = [];
    let cur = [];
    for (let i = 0; i < points.length; i++) {
      if (i > 0 && points[i].phase - points[i - 1].phase > maxGap) {
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
