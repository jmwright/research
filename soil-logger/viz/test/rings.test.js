/* Headless tests for the ring/cycle logic. Run with: npm test
   No framework, no dependencies -- node test/rings.test.js exits non-zero on
   the first failure. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const R = require("../public/rings.js");

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log("  ok   " + name); }
  catch (e) { console.error("  FAIL " + name + "\n       " + e.message); process.exitCode = 1; }
}

function csv(rows) {
  return "unix_time,moisture,temp_c\n" +
    rows.map((r) => r.join(",")).join("\n") + "\n";
}

/* A synthetic pot: `cycles` dry-downs, each opened by a watering spike. Cycles
   must be longer than detectWaterings' refractory window (12 h) or the detector
   will correctly swallow the second spike, so perCycle is 200 samples = 16.7 h
   at the 5-minute cadence in most tests below. */
function synth(cycles, perCycle, shelf, spike, step) {
  const rows = [];
  let t = 1700000000;
  // perCycle may be a number (uniform) or an array (one length per cycle)
  const lengths = Array.isArray(perCycle)
    ? perCycle
    : new Array(cycles).fill(perCycle);
  for (let c = 0; c < lengths.length; c++) {
    const base = shelf + c * (step || 0);
    const n = lengths[c];
    for (let i = 0; i < n; i++) {
      // drainage for the first fifth, then a flat shelf drifting down slightly
      const m = i < n / 5 ? base + spike * (1 - i / (n / 5)) : base - i * 0.05;
      rows.push([t, Math.round(m), 22 + (i % 7) * 0.1]);
      t += 300;
    }
  }
  return rows;
}

console.log("parsing / clock handling");

test("parseCSV keeps file order and does not sort by timestamp", () => {
  // second row's label steps backwards, as an NTP resync does
  const text = csv([[1000, 700, 22], [1300, 705, 22], [1150, 710, 22], [1600, 715, 22]]);
  const rows = R.parseCSV(text);
  assert.deepStrictEqual(rows.map((r) => r.t), [1000, 1300, 1150, 1600],
    "rows were reordered; file order is acquisition order and must be preserved");
});

test("countBackwardSteps finds the resyncs", () => {
  const rows = R.parseCSV(csv([[1000, 700, 22], [1300, 705, 22], [1150, 710, 22], [1600, 715, 22]]));
  assert.strictEqual(R.countBackwardSteps(rows), 1);
});

test("parseCSV tolerates blank moisture and temp fields", () => {
  const rows = R.parseCSV("unix_time,moisture,temp_c\n1000,,\n1300,705,22\n");
  assert.strictEqual(rows[0].moisture, null);
  assert.strictEqual(rows[0].temp, null);
  assert.strictEqual(rows[1].moisture, 705);
});

test("noiseEstimate recovers a known noise level", () => {
  const rows = [];
  let t = 0, seed = 1;
  for (let i = 0; i < 2000; i++) {
    seed = (seed * 1103515245 + 12345) % 2147483648;      // deterministic
    rows.push({ t: t += 300, moisture: 700 + ((seed / 2147483648) - 0.5) * 60, temp: 22 });
  }
  const n = R.noiseEstimate(rows);
  assert.ok(n > 10 && n < 25, "expected ~17 for uniform +/-30, got " + n.toFixed(1));
});

console.log("watering detection");

test("detects one spike per cycle, at the first rising sample", () => {
  const rows = R.parseCSV(csv(synth(3, 200, 700, 200)));
  const w = R.detectWaterings(rows);
  assert.strictEqual(w.length, 2, "expected 2 interior waterings, got " + w.length);
  // cycles are 200 samples long, so boundaries belong at indices 200 and 400
  assert.deepStrictEqual(w.map((x) => x.index), [200, 400]);
});

test("the refractory window collapses one pour into one detection", () => {
  // Without a refractory window the long drainage shoulder keeps re-qualifying,
  // reporting the single watering several times over.
  const rows = R.parseCSV(csv(synth(2, 200, 700, 200)));
  const loose = R.detectWaterings(rows, { refractoryS: 0 });
  const normal = R.detectWaterings(rows);
  assert.ok(loose.length > 1, "expected the unguarded detector to double-count");
  assert.strictEqual(normal.length, 1, "the refractory window should leave exactly one");
});

test("a refractory window longer than the record leaves only the first pour", () => {
  const rows = R.parseCSV(csv(synth(3, 200, 700, 200)));
  assert.strictEqual(R.detectWaterings(rows).length, 2);
  assert.strictEqual(R.detectWaterings(rows, { refractoryS: 365 * 86400 }).length, 1);
});

test("the rise threshold survives a noiseless signal", () => {
  // With no noise the SD term vanishes; without the step-fraction floor the
  // scan latches onto the gently declining shelf and reports the boundary
  // a dozen samples early.
  const rows = R.parseCSV(csv(synth(2, 200, 700, 200)));
  assert.strictEqual(R.noiseEstimate(rows) < 1, true, "this fixture should be effectively noiseless");
  assert.deepStrictEqual(R.detectWaterings(rows).map((x) => x.index), [200]);
});

test("a noise excursion before the pour does not become the boundary", () => {
  /* A shelf that twitches once, well above the noise threshold, and falls
     straight back -- then a real pour later. The old single-sample test took
     the first reading over the line and put the boundary on the twitch. A pour
     floods the probe and STAYS up, so the onset has to hold its level. */
  const rows = [];
  let t = 1700000000;
  for (let i = 0; i < 120; i++) {
    let m = 700 + (i % 2 ? 5 : -5);      // +-5 counts of deterministic chatter
    // Inside the onset scan's reach (it sweeps back an hour from the step),
    // or the detector never gets the chance to mistake it for the boundary.
    if (i === 62) m = 790;               // one-sample excursion, +90
    if (i >= 70) m = 900 - (i - 70) * 0.2;  // the pour, and it stays
    rows.push([t, Math.round(m), 22]);
    t += 300;
  }
  const w = R.detectWaterings(R.parseCSV(csv(rows)));
  assert.strictEqual(w.length, 1, "expected one watering, got " + w.length);
  assert.strictEqual(w[0].index, 70,
    "boundary should sit on the sustained pour at 70, not the excursion at 62; got " + w[0].index);
});

test("an absorbed-on-contact pour is caught by the jump gate", () => {
  /* What the real pours turned into. Early waterings left free water on the
     probe for hours and moved a median; by 2026-09-17 the excursion was ONE
     sample (732 -> 896) decaying inside half an hour to a shelf only ~23 counts
     above the old one. The median step sees almost nothing, so without a
     single-sample gate the detector stops finding waterings altogether. */
  const rows = [];
  let t = 1700000000;
  for (let i = 0; i < 160; i++) {
    let m = 700 + (i % 2 ? 6 : -6);
    if (i === 80) m = 862;                      // the pour: +156 in one sample
    else if (i > 80) m = 722 + (i % 2 ? 6 : -6);  // settles 22 counts higher
    rows.push([t, Math.round(m), 22]);
    t += 300;
  }
  const w = R.detectWaterings(R.parseCSV(csv(rows)));
  assert.strictEqual(w.length, 1, "expected the pour to be found, got " + w.length);
  assert.strictEqual(w[0].index, 80, "onset should be the jumping sample; got " + w[0].index);
  assert.strictEqual(w[0].byJump, true, "should have qualified on the jump, not the median step");
});

test("the jump gate stays above the measured non-pour ceiling", () => {
  /* Across 1,779 non-pour single-sample rises in the real log the largest is
     82 counts. The gate is 100, so the worst real excursion must not fire it. */
  const rows = [];
  let t = 1700000000;
  for (let i = 0; i < 160; i++) {
    let m = 700 + (i % 2 ? 6 : -6);
    if (i === 80) m = 788;                      // +82, the worst ever observed
    rows.push([t, Math.round(m), 22]);
    t += 300;
  }
  assert.strictEqual(R.detectWaterings(R.parseCSV(csv(rows))).length, 0,
    "an 82-count excursion is noise, not a pour");
});

test("a flat series with no watering yields no cycles boundaries", () => {
  const rows = R.parseCSV(csv(synth(1, 400, 700, 0)));
  assert.strictEqual(R.detectWaterings(rows).length, 0);
});

test("minRise gates detection", () => {
  const rows = R.parseCSV(csv(synth(2, 200, 700, 80)));
  assert.strictEqual(R.detectWaterings(rows, { minRise: 60 }).length, 1);
  assert.strictEqual(R.detectWaterings(rows, { minRise: 300 }).length, 0);
});

console.log("cycles and frames");

test("cycles run watering to watering, with partials at each end flagged", () => {
  const rows = R.parseCSV(csv(synth(3, 200, 700, 200)));
  const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
  assert.strictEqual(cycles.length, 3);
  assert.strictEqual(cycles[0].complete, false, "leading partial should not be complete");
  assert.strictEqual(cycles[1].complete, true, "middle cycle should be complete");
  assert.strictEqual(cycles[2].complete, false, "running cycle should not be complete");
  assert.strictEqual(cycles[0].openedByWatering, false);
  assert.strictEqual(cycles[2].openedByWatering, true);
});

test("every sample lands in exactly one cycle", () => {
  const rows = R.parseCSV(csv(synth(3, 200, 700, 200)));
  const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
  const total = cycles.reduce((s, c) => s + c.points.length, 0);
  assert.strictEqual(total, rows.length);
});

test("cycleShelf ignores the drainage phase", () => {
  const rows = R.parseCSV(csv(synth(1, 200, 700, 300)));
  const cycles = R.buildCyclesFromClock(rows, 1e9);            // one big cycle
  const shelf = R.cycleShelf(cycles[0]);
  assert.ok(Math.abs(shelf - 690) < 15,
    "shelf should sit near the settled level, not be dragged up by the spike; got " + shelf);
});

test("relative frame subtracts each cycle's own shelf", () => {
  // three cycles whose shelves step up 40 counts each time
  const rows = R.parseCSV(csv(synth(4, 200, 700, 200, 40)));
  const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
  const abs = R.buildRings(cycles, { frame: "absolute" });
  const rel = R.buildRings(cycles, { frame: "relative" });
  const shelves = abs.rings.map((r) => r.shelf);
  assert.ok(shelves[2] - shelves[1] > 30, "synthetic shelves should step up");
  // in the relative frame the complete cycles should overlay each other
  const tailMean = (ring) => {
    const pts = ring.points.filter((p) => p.phase > 0.5);
    return pts.reduce((s, p) => s + p.value, 0) / pts.length;
  };
  const t1 = tailMean(rel.rings[1]), t2 = tailMean(rel.rings[2]);
  assert.ok(Math.abs(t1 - t2) < 5,
    "relative frame should align the shelves, got " + t1.toFixed(1) + " vs " + t2.toFixed(1));
  const a1 = tailMean(abs.rings[1]), a2 = tailMean(abs.rings[2]);
  assert.ok(Math.abs(a1 - a2) > 30, "absolute frame should keep them apart");
});

test("normalized phase closes every ring; elapsed phase does not", () => {
  // uneven cycles, so "shortest" and "longest" actually differ
  const rows = R.parseCSV(csv(synth(3, [200, 400, 250], 700, 200)));
  const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
  const norm = R.buildRings(cycles, { phase: "normalized" });
  const elap = R.buildRings(cycles, { phase: "elapsed" });
  for (const r of norm.rings) {
    assert.ok(r.points[r.points.length - 1].phase > 0.98, "normalized rings should reach phase 1");
  }
  const shortest = elap.rings.reduce((a, b) => (a.lengthH < b.lengthH ? a : b));
  assert.ok(shortest.points[shortest.points.length - 1].phase < 0.99,
    "a short cycle should fall short of closing on the shared elapsed axis");
});

test("phases stay inside [0,1] even with backward clock steps", () => {
  const base = synth(2, 200, 700, 200);
  base[150][0] -= 900;                                        // shove one label backwards
  const rows = R.parseCSV(csv(base));
  const built = R.buildRings(R.buildCyclesFromWaterings(rows, R.detectWaterings(rows)), {});
  for (const ring of built.rings) for (const p of ring.points) {
    assert.ok(p.phase >= 0 && p.phase <= 1, "phase out of range: " + p.phase);
  }
});

test("segments split on gaps rather than drawing across them", () => {
  // Spacing is in SECONDS now, so the threshold is too: a 110 min hole in an
  // otherwise 5 min record.
  const t0 = 1788000000;
  const pts = [0, 300, 600, 7200, 7500].map((d) => ({ t: t0 + d, phase: d / 7500 }));
  assert.strictEqual(R.segments(pts, 1800).length, 2, "the hole should split the ring");
  assert.strictEqual(R.segments(pts, 7200).length, 1, "a tolerance above it should not");
});

/* The regression this replaced a phase threshold to fix. Normalized phase
   divides by the cycle's OWN span, so a fixed phase gap is a time gap that
   shrinks with the cycle: on the 51-minute cycle opened by the 2026-09-20 pour
   it came to 3.0 min, under the 5 min cadence. Every pair read as a gap, every
   segment came out one point long, and app.js skips segments shorter than two
   -- so the ring holding the newest watering drew nothing at all while the
   readout panel still reported it. Clock mode divided by 86400 and was fine,
   which is exactly how the bug presented: visible in one frame, absent in the
   other. */
test("a freshly-opened cycle still draws as one segment", () => {
  const t0 = 1788000000;
  const pts = [];
  for (let i = 0; i < 11; i++) pts.push({ t: t0 + i * 300, phase: i / 10 });
  const segs = R.segments(pts);
  assert.strictEqual(segs.length, 1, "51 minutes at a 5 min cadence is not a gap");
  assert.ok(segs[0].length >= 2, "a one-point segment cannot be drawn");
});

// The same ring, at the slower cadence the timebase fault produced, must also
// survive -- which is why the default is a multiple of the ring's own spacing
// and not one tuned number.
test("the gap default follows the cadence", () => {
  const t0 = 1788000000;
  const slow = [];
  for (let i = 0; i < 11; i++) slow.push({ t: t0 + i * 460, phase: i / 10 });
  assert.strictEqual(R.segments(slow).length, 1, "460 s spacing is not a gap either");

  const hourly = [];
  for (let i = 0; i < 11; i++) hourly.push({ t: t0 + i * 3600, phase: i / 10 });
  assert.strictEqual(R.segments(hourly).length, 1,
    "an hourly log should not be shattered by a 30 min floor");
});

test("averageRing bins in the active frame", () => {
  const rows = R.parseCSV(csv(synth(3, 200, 700, 200, 40)));
  const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
  const rel = R.buildRings(cycles, { frame: "relative" });
  const avg = R.averageRing(rel.rings, 16);
  assert.strictEqual(avg.length, 16);
  const tail = avg.filter((b) => b.phase > 0.5 && b.value != null);
  assert.ok(Math.abs(tail.reduce((s, b) => s + b.value, 0) / tail.length) < 10,
    "relative-frame average should sit near zero on the shelf");
});

test("empty and single-row inputs do not throw", () => {
  assert.deepStrictEqual(R.buildRings([], {}).rings, []);
  const one = R.parseCSV("unix_time,moisture,temp_c\n1000,700,22\n");
  assert.deepStrictEqual(R.buildCyclesFromWaterings(one, []), [],
    "a single sample is not a cycle");
  assert.strictEqual(R.buildCyclesFromClock(one, 3600).length, 0,
    "clock mode should drop 1-point cycles too, since they cannot be drawn");
  assert.strictEqual(R.buildRings(R.buildCyclesFromClock(one, 3600), {}).rings.length, 0);
});

// ---- the real log, if it is present ----
const REAL = path.join(__dirname, "..", "..", "soil_log.csv");
if (fs.existsSync(REAL)) {
  console.log("real log (" + REAL + ")");
  const rows = R.parseCSV(fs.readFileSync(REAL, "utf8"));

  /* The board is still logging, so anything asserted about "the whole record"
     rots the moment it is watered again. These bound themselves to the span
     the log book covers and ignore what comes after. */
  const BOOK_END = Date.parse("2026-09-16T00:00:00Z") / 1000;

  test("finds every watering in the log book span and nothing else", () => {
    // Detection runs on the FULL record -- including the stretched-cadence
    // stretch -- and only the assertion is bounded.
    const w = R.detectWaterings(rows).filter((x) => x.t < BOOK_END);
    assert.strictEqual(w.length, 3, "expected 3 waterings, got " + w.length);
    const when = w.map((x) => new Date(x.t * 1000).toISOString().slice(0, 16));
    /* log book: 2026-09-06 12:13; 2026-09-10 between 21:28:33 and 21:33:32;
       2026-09-14 14:11, the +179 count pour. The last of those is the one the
       sample-windowed detector put at 12:56, 75 minutes early, on a noise
       excursion that fell straight back. */
    assert.deepStrictEqual(when,
      ["2026-09-06T12:13", "2026-09-10T21:33", "2026-09-14T14:11"]);
  });

  test("boundaries survive a change of logging cadence", () => {
    /* This is the regression that matters. A firmware timebase fault stretched
       the sample interval from 300 s to 460 s for two days in September 2026,
       and the detector's windows used to be counted in SAMPLES -- so the onset
       scan swept one hour of record at the fast cadence and three at a slow
       one, and the 09-14 boundary moved 3.3 h depending on how often the board
       happened to be logging. Thin the record out and every boundary must hold
       to within a sample interval. */
    const thin = (dt) => {
      const out = []; let edge = rows[0].t;
      for (const r of rows) if (r.t >= edge) { out.push(r); edge = r.t + dt; }
      return out;
    };
    const base = R.detectWaterings(rows).filter((x) => x.t < BOOK_END).map((x) => x.t);
    for (const dt of [600, 900, 1200]) {
      const rs = thin(dt);
      const w = R.detectWaterings(rs).filter((x) => x.t < BOOK_END);
      assert.strictEqual(w.length, base.length,
        "at " + dt + "s cadence: expected " + base.length + " waterings, got " + w.length);
      const gaps = [];
      for (let i = 1; i < rs.length; i++) if (rs[i].t > rs[i - 1].t) gaps.push(rs[i].t - rs[i - 1].t);
      const tol = R.median(gaps) * 1.5;
      w.forEach((x, k) => {
        assert.ok(Math.abs(x.t - base[k]) <= tol,
          "at " + dt + "s cadence, boundary " + k + " moved " +
          Math.round(Math.abs(x.t - base[k]) / 60) + " min (tolerance " +
          Math.round(tol / 60) + " min)");
      });
    }
  });

  test("cycle lengths and shelves match the offline analysis", () => {
    const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
    // The trailing partial splits again with every new pour, so assert on the
    // closed cycles rather than on the total count.
    assert.ok(cycles.length >= 4, "expected at least 4 cycles, got " + cycles.length);
    const len = (c) => (c.endT - c.startT) / 3600;
    assert.ok(Math.abs(len(cycles[1]) - 105.3) < 0.5, "cycle 2 should run ~105 h");
    /* Block-bootstrapped medians from the Python analysis were 718 and 761.
       The first still measures 717. The second now measures 755: when 761 was
       computed that cycle was still running, and it has since closed at the
       09-14 pour, so the estimate covers more of its own drying tail. The
       shelf is not a constant of the pot -- that is the point of the relative
       frame -- so the figure moving with the cycle's extent is expected. */
    assert.ok(Math.abs(R.cycleShelf(cycles[1]) - 717) < 6, "cycle 2 shelf ~717");
    assert.ok(Math.abs(R.cycleShelf(cycles[2]) - 755) < 6, "cycle 3 shelf ~755");
  });

  test("the relative frame collapses the 38-count shelf step", () => {
    const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
    const rel = R.buildRings(cycles, { frame: "relative" });
    const tail = (r) => {
      const p = r.points.filter((x) => x.phase > 0.5);
      return p.reduce((s, x) => s + x.value, 0) / p.length;
    };
    assert.ok(Math.abs(tail(rel.rings[1]) - tail(rel.rings[2])) < 8,
      "cycle 2 and cycle 3 tails should overlay once each is measured against its own shelf");
  });

  /* End-to-end guard for the gap-units bug, in the shape the renderer sees it.
     The trailing ring is minutes old right after a pour, which is precisely
     when someone goes looking for it, so "every ring draws" has to hold in
     both phase modes and not just for the long closed ones. */
  test("every ring draws in both phase modes, including the running one", () => {
    const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
    for (const phase of ["normalized", "elapsed"]) {
      const built = R.buildRings(cycles, { phase: phase });
      built.rings.forEach((ring, i) => {
        const drawable = R.segments(ring.points).filter((seg) => seg.length >= 2);
        assert.ok(drawable.length > 0,
          phase + " phase: ring " + i + " (" + ring.lengthH.toFixed(2) +
          " h, " + ring.points.length + " points) contributed no drawable segment");
      });
    }
  });
}

console.log("\n" + passed + " passed" + (process.exitCode ? ", with failures above" : ""));
