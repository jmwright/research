import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const BASE_R = 5.0;         // ring radius at minimum moisture
const R_SCALE = 3.0;        // how much moisture expands the radius
const H = 1.3;              // vertical spacing between day rings
const AVG_BINS = 64;

let rows = [];              // parsed CSV, in file order
/* Orthographic by default: the stacked rings are a comparison of radii up the
   drift axis, and that comparison is only valid without foreshortening. */
const DEFAULT_PROJECTION = "orthographic";   // "perspective" | "orthographic"

let state = {
  cycleMode: "watering",    // "watering" | "clock"
  cycleHours: 24,
  minRise: 60,
  frame: "absolute",        // "absolute" | "relative"
  phase: "normalized",      // "normalized" | "elapsed"
  colorBy: "moisture", day: 0, showAvg: false, dimOthers: false,
  projection: DEFAULT_PROJECTION,
};
let built = null;
let waterings = [];
let backwardSteps = 0;
let ringGroup, avgGroup;
let colorRange = null;   // the band color is stretched over; see quantileRange()

// ---- scene ----
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d141b);
const FOV = 55, NEAR = 0.1, FAR = 2000;
const aspect = () => window.innerWidth / window.innerHeight;
// Half the world-space height the orthographic frustum covers, before zoom.
let orthoHalfHeight = BASE_R + R_SCALE + 8;

function makeCamera(kind) {
  if (kind === "orthographic") {
    const a = aspect();
    return new THREE.OrthographicCamera(-orthoHalfHeight * a, orthoHalfHeight * a,
                                        orthoHalfHeight, -orthoHalfHeight, NEAR, FAR);
  }
  return new THREE.PerspectiveCamera(FOV, aspect(), NEAR, FAR);
}

let camera = makeCamera(DEFAULT_PROJECTION);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
document.body.appendChild(renderer.domElement);
let controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

// The half-height a perspective frustum covers at a given orbit distance.
const halfHeightAt = (dist) => dist * Math.tan((FOV / 2) * Math.PI / 180);

function applyOrthoFrustum() {
  const a = aspect();
  camera.left = -orthoHalfHeight * a;
  camera.right = orthoHalfHeight * a;
  camera.top = orthoHalfHeight;
  camera.bottom = -orthoHalfHeight;
  camera.updateProjectionMatrix();
}

/* Orthographic is the honest projection for reading the drift axis. Under
   perspective a ring's drawn size depends on its distance from the eye, so
   rings low in the stack are foreshortened against rings high in it and a
   radius difference up the axis cannot be trusted -- it is part moisture, part
   camera. Orthographic drops that term: equal radius draws equal, so drift over
   time is readable straight down the stack. Perspective stays the default
   because it gives the better sense of the 3D shape while orbiting.

   The swap preserves the view rather than resetting it: the eye direction and
   orbit target carry over, and the frustum is sized so the framing at the
   target is unchanged, so toggling shows the same scene under two projections
   instead of jumping somewhere new. */
function setProjection(kind) {
  if (kind === state.projection) return;
  const target = controls.target.clone();
  const pos = camera.position.clone();
  const up = camera.up.clone();
  const dir = pos.clone().sub(target);
  let next;

  if (kind === "orthographic") {
    orthoHalfHeight = halfHeightAt(dir.length());
    next = makeCamera(kind);
    next.position.copy(pos);
  } else {
    // Undo any orthographic zoom, then stand far enough back to cover the same
    // half-height through the perspective frustum.
    const covered = orthoHalfHeight / (camera.zoom || 1);
    const d = covered / Math.tan((FOV / 2) * Math.PI / 180);
    next = makeCamera(kind);
    next.position.copy(target).add(dir.normalize().multiplyScalar(d));
  }

  next.up.copy(up);
  next.lookAt(target);
  camera = next;
  state.projection = kind;
  if (camera.isOrthographicCamera) applyOrthoFrustum();
  else camera.updateProjectionMatrix();

  // OrbitControls binds its camera at construction, so it is rebuilt rather
  // than mutated; the target is carried across so the orbit centre holds.
  controls.dispose();
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.copy(target);
  controls.update();
}

/* Dry soil reads desert-sunset terracotta, wet soil reads blue, bridged by a
   cool slate -- the only honest path between two hues, since any other route
   from 50 deg to 258 deg goes through green. Chroma bottoms out at .05 rather
   than neutral so the bridge reads as a color, not as mud. Lightness rises
   .535 -> .684, wide enough to separate the stops but not so wide that the
   wettest samples -- the noisiest ones, and the drainage phase the shelf
   calculation discards -- become the brightest thing on screen. Every stop
   clears 3:1 on the #0d141b background (3.4:1 at the dry end, 6.5:1 at the wet).

   NB: this ramp is only half the discriminability story. norm() spreads it
   linearly over built.value, which is a raw [min, max]; on the real log that
   puts 75% of samples inside 13-15% of the ramp, which no choice of stops can
   fix. That is a normalization question, not a palette one, and still open. */
const SOIL_STOPS = [[0.655,0.322,0.090], [0.655,0.388,0.149], [0.616,0.514,0.388],
                    [0.467,0.569,0.675], [0.357,0.592,0.859], [0.349,0.600,0.973]];
// Temperature keeps its own cool->warm ramp; hot must not come out blue.
const TEMP_STOPS = [[0.03,0.20,0.55], [0.13,0.60,0.75], [0.30,0.70,0.40],
                    [0.95,0.80,0.25], [0.80,0.25,0.20]];

function colormap(x, stops) {
  x = Math.max(0, Math.min(1, x));
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

/* What color encodes: always the reading's distance from its OWN cycle's
   shelf, whatever frame the geometry is drawn in. Raw counts carry the
   between-cycle shelf step -- 123 counts across this log, against a color band
   only ~200 wide -- which swamps the within-cycle dry-down and makes color read
   as the stacking axis instead of as progression around the ring. cycleShelf
   exists for exactly this; the relative frame already subtracts it for the
   geometry, and this does the same for color unconditionally. Points keep raw
   `moisture` alongside the framed `value`, so this needs no frame branch: it is
   the same number in both. Falls back to the framed value on the rare cycle
   with no shelf. */
function colorValue(ring, p) {
  if (state.colorBy === "temp") return p.temp;
  if (p.moisture == null) return p.value;
  return ring.shelf != null ? p.moisture - ring.shelf : p.value;
}

function colorValues(rings) {
  const xs = [];
  for (const ring of rings) {
    for (const p of ring.points) {
      const v = colorValue(ring, p);
      if (v != null) xs.push(v);
    }
  }
  return xs;
}

/* The band to stretch the ramp over, as quantiles of the values actually
   colored. A raw [min, max] is the wrong instrument here for the same reason
   cycleShelf ignores the drainage phase: the pour transient is a different
   regime, ~200 counts above the shelf but 1.3% of samples, and it was taking a
   fifth of the ramp while 75% of the readings crowded into 15% of it. norm()
   may return outside [0,1] for the clipped tails; colormap() clamps. */
function quantileRange(xs, loQ, hiQ) {
  if (!xs.length) return [0, 1];
  const s = xs.slice().sort((a, b) => a - b);
  const at = (q) => s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))];
  const lo = at(loQ), hi = at(hiQ);
  return hi > lo ? [lo, hi] : [s[0], s[s.length - 1]];
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
  // Temperature is near-uniform across its own range -- no transient to crowd
  // it -- so it keeps the full span; clipping it would just bin its top decile.
  colorRange = state.colorBy === "temp"
    ? built.temp
    : quantileRange(colorValues(built.rings), 0.02, 0.90);

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
        const v = colorValue(ring, p);
        const [x, y, z] = pointXYZ(p.phase, p.value, idx, n);
        pos.push(x, y, z);
        const c = colormap(norm(v, colorRange),
                           state.colorBy === "temp" ? TEMP_STOPS : SOIL_STOPS);
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
    "color: " + (state.colorBy === "temp"
      ? colorRange[0].toFixed(1) + " – " + colorRange[1].toFixed(1) + " °C (full)"
      : colorRange[0].toFixed(0) + " – " + colorRange[1].toFixed(0) +
        " counts vs shelf (p2–p90; wetter clips)"),
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
  const prj = document.getElementById("projection");
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
  prj.addEventListener("change", () => setProjection(prj.value));
  for (const v of ["top", "front", "iso"]) {
    document.getElementById("view" + v[0].toUpperCase() + v.slice(1))
      .addEventListener("click", () => snapTo(v));
  }
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
  if (camera.isOrthographicCamera) {
    // Fit whichever is taller on screen: the ring spread or the whole column.
    orthoHalfHeight = Math.max(BASE_R + R_SCALE + 2, h / 2 + 2);
    camera.zoom = 1;
    applyOrthoFrustum();
  }
  controls.update();
}

/* Axis-aligned views. Orbiting to exactly down-axis by hand is not really
   possible, and "nearly down-axis" is the one case that misleads: a ring is
   then drawn as a thin ellipse and its radius is read short. These snap
   exactly, with the orbit distance preserved so the zoom level survives.

   TOP is the one the drift axis wants -- rings superimposed, so radius drift
   from cycle to cycle reads directly as concentric spacing. Looking straight
   down +Y is degenerate for a camera whose up is also +Y, so the direction is
   tilted by 1e-4, which at this orbit distance is a fifth of a pixel. Nudging
   the direction keeps up = +Y and leaves OrbitControls' orbit behaviour
   untouched, which changing the up vector would not. */
const VIEW_DIRS = {
  top:   new THREE.Vector3(0, 1, 1e-4),
  front: new THREE.Vector3(0, 0, 1),
  iso:   new THREE.Vector3(0.7, 0.36, 1),   // the direction frameCamera() opens on
};

function snapTo(view) {
  const dir = VIEW_DIRS[view] || VIEW_DIRS.iso;
  const target = controls.target.clone();
  const dist = camera.position.distanceTo(target) || 20;
  camera.position.copy(target).add(dir.clone().normalize().multiplyScalar(dist));
  camera.up.set(0, 1, 0);
  camera.lookAt(target);
  controls.update();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

window.addEventListener("resize", () => {
  if (camera.isOrthographicCamera) {
    applyOrthoFrustum();
  } else {
    camera.aspect = aspect();
    camera.updateProjectionMatrix();
  }
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
