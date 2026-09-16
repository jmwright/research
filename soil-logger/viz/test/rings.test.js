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
  const pts = [{ phase: 0.1 }, { phase: 0.2 }, { phase: 0.8 }, { phase: 0.9 }];
  assert.strictEqual(R.segments(pts, 0.3).length, 2);
  assert.strictEqual(R.segments(pts, 0.9).length, 1);
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

  test("finds both recorded waterings and nothing else", () => {
    const w = R.detectWaterings(rows);
    assert.strictEqual(w.length, 2, "expected exactly 2 waterings, got " + w.length);
    const when = w.map((x) => new Date(x.t * 1000).toISOString().slice(0, 16));
    // log book: 2026-09-06 12:13, and 2026-09-10 between 21:28:33 and 21:33:32
    assert.strictEqual(when[0], "2026-09-06T12:13");
    assert.strictEqual(when[1], "2026-09-10T21:33");
  });

  test("cycle lengths and shelves match the offline analysis", () => {
    const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
    assert.strictEqual(cycles.length, 3);
    const len = (c) => (c.endT - c.startT) / 3600;
    assert.ok(Math.abs(len(cycles[1]) - 105.3) < 0.5, "cycle 2 should run ~105 h");
    // block-bootstrapped medians from the Python analysis: 718 and 761
    assert.ok(Math.abs(R.cycleShelf(cycles[1]) - 718) < 6, "cycle 2 shelf ~718");
    assert.ok(Math.abs(R.cycleShelf(cycles[2]) - 761) < 6, "cycle 3 shelf ~761");
  });

  test("the relative frame collapses the 42-count shelf step", () => {
    const cycles = R.buildCyclesFromWaterings(rows, R.detectWaterings(rows));
    const rel = R.buildRings(cycles, { frame: "relative" });
    const tail = (r) => {
      const p = r.points.filter((x) => x.phase > 0.5);
      return p.reduce((s, x) => s + x.value, 0) / p.length;
    };
    assert.ok(Math.abs(tail(rel.rings[1]) - tail(rel.rings[2])) < 8,
      "cycle 2 and cycle 3 tails should overlay once each is measured against its own shelf");
  });
}

console.log("\n" + passed + " passed" + (process.exitCode ? ", with failures above" : ""));
