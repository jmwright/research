import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const BASE_R = 5.0;         // ring radius at minimum moisture
const R_SCALE = 3.0;        // how much moisture expands the radius
const H = 1.3;              // vertical spacing between day rings
const AVG_BINS = 64;

let rows = [];              // parsed CSV, in file order
let state = {
  cycleMode: "watering",    // "watering" | "clock"
  cycleHours: 24,
  minRise: 60,
  frame: "absolute",        // "absolute" | "relative"
  phase: "normalized",      // "normalized" | "elapsed"
  colorBy: "moisture", day: 0, showAvg: false, dimOthers: false,
};
let built = null;
let waterings = [];
let backwardSteps = 0;
let ringGroup, avgGroup;

// ---- scene ----
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d141b);
const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 1000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function colormap(x) {
  // clamp + simple blue->cyan->green->yellow->red ramp
  x = Math.max(0, Math.min(1, x));
  const stops = [[0.03,0.20,0.55], [0.13,0.60,0.75], [0.30,0.70,0.40],
                 [0.95,0.80,0.25], [0.80,0.25,0.20]];
  const s = x * (stops.length - 1);
  const i = Math.floor(s), f = s - i;
  const a = stops[i], b = stops[Math.min(stops.length - 1, i + 1)];
  return new THREE.Color(a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f);
}

function norm(v, range) {
  if (v == null) return 0;
  const [lo, hi] = range;
  return hi > lo ? (v - lo) / (hi - lo) : 0.5;
}

function pointXYZ(phase, value, cycleIdx, nCycles) {
  const theta = phase * Math.PI * 2;
  const r = BASE_R + norm(value, built.value) * R_SCALE;
  const y = (cycleIdx - (nCycles - 1) / 2) * H;   // center the stack on origin
  return [r * Math.cos(theta), y, r * Math.sin(theta)];
}

/* Slice the series into cycles under the current mode, then wrap them into
   rings under the current reference frame. Kept separate from rebuild() so the
   slider can ask how many cycles there are without touching the scene. */
function computeRings() {
  const cycles = state.cycleMode === "watering"
    ? RingLib.buildCyclesFromWaterings(rows, waterings)
    : RingLib.buildCyclesFromClock(rows, state.cycleHours * 3600);
  return RingLib.buildRings(cycles, { phase: state.phase, frame: state.frame });
}

function rebuild() {
  if (ringGroup) scene.remove(ringGroup);
  if (avgGroup) scene.remove(avgGroup);
  ringGroup = new THREE.Group();
  avgGroup = new THREE.Group();

  built = computeRings();
  const n = built.rings.length;
  const colorRange = state.colorBy === "temp" ? built.temp : built.value;

  built.rings.forEach((ring, idx) => {
    const highlighted = idx === state.day;
    const opacity = state.dimOthers && !highlighted ? 0.08 : (highlighted ? 1.0 : 0.7);
    const lw = highlighted ? 2 : 1;
    // No threshold passed: segments() sizes the gap from this ring's own
    // cadence. A fixed one here could not, and shattered short rings.
    for (const seg of RingLib.segments(ring.points)) {
      if (seg.length < 2) continue;
      const pos = [], col = [];
      for (const p of seg) {
        const v = state.colorBy === "temp" ? p.temp : p.value;
        const [x, y, z] = pointXYZ(p.phase, p.value, idx, n);
        pos.push(x, y, z);
        const c = colormap(norm(v, colorRange));
        col.push(c.r, c.g, c.b);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
      const m = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true,
        opacity: opacity, linewidth: lw });
      ringGroup.add(new THREE.Line(g, m));
    }
  });

  // "typical cycle" average ring, drawn just below the stack in white
  if (state.showAvg && n > 0) {
    const avg = RingLib.averageRing(built.rings, AVG_BINS);
    const pos = [];
    const yBase = (-(n - 1) / 2 - 1.5) * H;
    for (const p of avg.concat([avg[0]])) {
      const theta = p.phase * Math.PI * 2;
      const r = BASE_R + norm(p.value, built.value) * R_SCALE;
      pos.push(r * Math.cos(theta), yBase, r * Math.sin(theta));
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    avgGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0xffffff })));
  }

  // reference geometry: vertical axis + a phase-0 (cycle start) marker plane line
  const axisG = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(0, (-(n - 1) / 2 - 2) * H, 0),
    new THREE.Vector3(0, ((n - 1) / 2 + 1) * H, 0)]);
  ringGroup.add(new THREE.Line(axisG, new THREE.LineBasicMaterial({ color: 0x293643 })));

  scene.add(ringGroup);
  scene.add(avgGroup);
  updateReadout();
}

function fmt(t) { return new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "); }

function updateReadout() {
  const n = built.rings.length;
  const ring = built.rings[state.day];
  const el = document.getElementById("readout");
  const unit = state.frame === "relative" ? " counts vs shelf" : " counts";
  const lines = [
    "cycles: " + n + (state.cycleMode === "watering" ? " (" + waterings.length + " waterings found)" : ""),
    "radius: " + built.value[0].toFixed(0) + " – " + built.value[1].toFixed(0) + unit,
    "temp: " + built.temp[0].toFixed(1) + " – " + built.temp[1].toFixed(1) + " °C",
  ];
  if (ring) {
    lines.push("<br>cycle " + state.day + " — " + fmt(ring.startT));
    lines.push("length " + ring.lengthH.toFixed(1) + " h" +
      (ring.complete ? "" : ring.openedByWatering ? " (running)" : " (before first watering)"));
    if (ring.shelf != null) lines.push("shelf " + ring.shelf.toFixed(1) + " counts");
    if (ring.watering) {
      lines.push("opened by a +" + ring.watering.rise.toFixed(0) + " count rise");
    }
  }
  if (backwardSteps) {
    lines.push("<br><span style=\"color:#f2a93b\">" + backwardSteps +
      " backward clock step" + (backwardSteps === 1 ? "" : "s") +
      " in the log (NTP resync; rows kept in file order, not sorted)</span>");
  }
  el.innerHTML = lines.join("<br>");
}

// ---- controls wiring ----
function syncPanel() {
  // Cycle length only means anything in clock mode; the rise threshold and the
  // phase-axis choice only mean anything in watering mode.
  document.getElementById("rowCycleHours").hidden = state.cycleMode !== "clock";
  document.getElementById("rowMinRise").hidden = state.cycleMode !== "watering";
  document.getElementById("rowPhase").hidden = state.cycleMode !== "watering";
}

function redetect() {
  waterings = RingLib.detectWaterings(rows, { minRise: state.minRise });
}

function bind() {
  const mode = document.getElementById("cycleMode");
  const cyc = document.getElementById("cycleHours");
  const rise = document.getElementById("minRise");
  const frm = document.getElementById("frame");
  const phs = document.getElementById("phase");
  const col = document.getElementById("colorBy");
  const sld = document.getElementById("daySlider");
  const avg = document.getElementById("showAvg");
  const dim = document.getElementById("dimOthers");
  mode.addEventListener("change", () => {
    state.cycleMode = mode.value; syncPanel(); refreshSlider(); rebuild();
  });
  cyc.addEventListener("change", () => {
    state.cycleHours = Math.max(0.5, Number(cyc.value)); refreshSlider(); rebuild();
  });
  rise.addEventListener("change", () => {
    state.minRise = Math.max(5, Number(rise.value)); redetect(); refreshSlider(); rebuild();
  });
  frm.addEventListener("change", () => { state.frame = frm.value; rebuild(); });
  phs.addEventListener("change", () => { state.phase = phs.value; rebuild(); });
  col.addEventListener("change", () => { state.colorBy = col.value; rebuild(); });
  sld.addEventListener("input", () => { state.day = Number(sld.value); rebuild(); });
  avg.addEventListener("change", () => { state.showAvg = avg.checked; rebuild(); });
  dim.addEventListener("change", () => { state.dimOthers = dim.checked; rebuild(); });
}

function refreshSlider() {
  const sld = document.getElementById("daySlider");
  sld.max = Math.max(0, computeRings().rings.length - 1);
  if (state.day > Number(sld.max)) { state.day = Number(sld.max); sld.value = sld.max; }
}

function frameCamera() {
  const n = built.rings.length;
  const h = n * H;
  camera.position.set(BASE_R + R_SCALE + 6, h * 0.4 + 4, BASE_R + R_SCALE + 12);
  controls.target.set(0, 0, 0);
  controls.update();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

async function main() {
  const text = await (await fetch("data.csv")).text();
  rows = RingLib.parseCSV(text);
  backwardSteps = RingLib.countBackwardSteps(rows);
  redetect();
  // Open on the most recent cycle -- the one still running is what you want to
  // look at first.
  bind();
  syncPanel();
  refreshSlider();
  state.day = Number(document.getElementById("daySlider").max);
  document.getElementById("daySlider").value = state.day;
  rebuild();
  frameCamera();
  animate();
}

main();
