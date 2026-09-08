/* rings.js -- pure logic for wrapping a flat time series into stacked cycle
   rings. No browser or Three.js dependency, so it can be unit-tested in Node
   and reused in the page. UMD wrapper exports it both ways. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RingLib = factory();
}(typeof self !== "undefined" ? self : this, function () {

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
    rows.sort((a, b) => a.t - b.t);
    return rows;
  }

  // Wrap into cycles of length cycleSeconds (86400 = one day).
  // Phase is fraction through the cycle (0..1); cycle index stacks vertically.
  function buildRings(rows, cycleSeconds) {
    if (!rows.length) return { rings: [], moisture: [0, 1], temp: [0, 1] };
    const t0 = rows[0].t;
    const byCycle = new Map();
    let mMin = Infinity, mMax = -Infinity, cMin = Infinity, cMax = -Infinity;
    for (const r of rows) {
      const cycle = Math.floor((r.t - t0) / cycleSeconds);
      const phase = (((r.t % cycleSeconds) + cycleSeconds) % cycleSeconds) / cycleSeconds;
      if (!byCycle.has(cycle)) byCycle.set(cycle, []);
      byCycle.get(cycle).push({ phase: phase, moisture: r.moisture, temp: r.temp, t: r.t });
      if (r.moisture != null) { if (r.moisture < mMin) mMin = r.moisture; if (r.moisture > mMax) mMax = r.moisture; }
      if (r.temp != null) { if (r.temp < cMin) cMin = r.temp; if (r.temp > cMax) cMax = r.temp; }
    }
    const rings = [...byCycle.entries()].sort((a, b) => a[0] - b[0])
      .map(([cycle, pts]) => {
        pts.sort((a, b) => a.phase - b.phase);
        return { cycle: cycle, points: pts };
      });
    return {
      rings: rings,
      moisture: [mMin === Infinity ? 0 : mMin, mMax === -Infinity ? 1 : mMax],
      temp: [cMin === Infinity ? 0 : cMin, cMax === -Infinity ? 1 : cMax],
    };
  }

  // Break a ring into segments where the phase gap is large (a data gap), so we
  // never draw a fake line across missing time.
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

  // The "typical cycle": mean per phase bin across every ring.
  function averageRing(rings, bins) {
    const sM = new Array(bins).fill(0), nM = new Array(bins).fill(0);
    const sC = new Array(bins).fill(0), nC = new Array(bins).fill(0);
    for (const ring of rings) for (const p of ring.points) {
      const b = Math.min(bins - 1, Math.max(0, Math.floor(p.phase * bins)));
      if (p.moisture != null) { sM[b] += p.moisture; nM[b]++; }
      if (p.temp != null) { sC[b] += p.temp; nC[b]++; }
    }
    const out = [];
    for (let b = 0; b < bins; b++) {
      out.push({
        phase: (b + 0.5) / bins,
        moisture: nM[b] ? sM[b] / nM[b] : null,
        temp: nC[b] ? sC[b] / nC[b] : null,
      });
    }
    return out;
  }

  return { parseCSV: parseCSV, buildRings: buildRings, segments: segments, averageRing: averageRing };
}));
