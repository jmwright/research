import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const BASE_R = 5.0;         // ring radius at minimum moisture
const R_SCALE = 3.0;        // how much moisture expands the radius
const H = 1.3;              // vertical spacing between day rings
const GAP = 0.06;           // phase gap above which we break the line (a data gap)
const AVG_BINS = 64;

let rows = [];              // parsed CSV
let state = { cycleHours: 24, colorBy: "moisture", day: 0, showAvg: false, dimOthers: false };
let built = null;
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

function pointXYZ(phase, moisture, cycleIdx, nCycles) {
  const theta = phase * Math.PI * 2;
  const r = BASE_R + norm(moisture, built.moisture) * R_SCALE;
  const y = (cycleIdx - (nCycles - 1) / 2) * H;   // center the stack on origin
  return [r * Math.cos(theta), y, r * Math.sin(theta)];
}

function rebuild() {
  if (ringGroup) scene.remove(ringGroup);
  if (avgGroup) scene.remove(avgGroup);
  ringGroup = new THREE.Group();
  avgGroup = new THREE.Group();

  built = RingLib.buildRings(rows, state.cycleHours * 3600);
  const n = built.rings.length;
  const colorRange = state.colorBy === "temp" ? built.temp : built.moisture;

  built.rings.forEach((ring, idx) => {
    const highlighted = idx === state.day;
    const opacity = state.dimOthers && !highlighted ? 0.08 : (highlighted ? 1.0 : 0.7);
    const lw = highlighted ? 2 : 1;
    for (const seg of RingLib.segments(ring.points, GAP)) {
      if (seg.length < 2) continue;
      const pos = [], col = [];
      for (const p of seg) {
        const v = state.colorBy === "temp" ? p.temp : p.moisture;
        const [x, y, z] = pointXYZ(p.phase, p.moisture, idx, n);
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

  // "typical day" average ring, drawn just below the stack in white
  if (state.showAvg && n > 0) {
    const avg = RingLib.averageRing(built.rings, AVG_BINS);
    const pos = [];
    const yBase = (-(n - 1) / 2 - 1.5) * H;
    for (const p of avg.concat([avg[0]])) {
      const theta = p.phase * Math.PI * 2;
      const r = BASE_R + norm(p.moisture, built.moisture) * R_SCALE;
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

function updateReadout() {
  const n = built.rings.length;
  const day = built.rings[state.day];
  const el = document.getElementById("readout");
  let when = "-";
  if (day && day.points.length) {
    when = new Date(day.points[0].t * 1000).toISOString().slice(0, 10);
  }
  el.innerHTML =
    "cycles: " + n + "<br>" +
    "moisture: " + built.moisture[0].toFixed(0) + " – " + built.moisture[1].toFixed(0) + "<br>" +
    "temp: " + built.temp[0].toFixed(1) + " – " + built.temp[1].toFixed(1) + " °C<br>" +
    "highlighted day " + state.day + " (" + when + ")";
}

// ---- controls wiring ----
function bind() {
  const cyc = document.getElementById("cycleHours");
  const col = document.getElementById("colorBy");
  const sld = document.getElementById("daySlider");
  const avg = document.getElementById("showAvg");
  const dim = document.getElementById("dimOthers");
  cyc.addEventListener("change", () => { state.cycleHours = Math.max(0.5, Number(cyc.value)); refreshSlider(); rebuild(); });
  col.addEventListener("change", () => { state.colorBy = col.value; rebuild(); });
  sld.addEventListener("input", () => { state.day = Number(sld.value); rebuild(); });
  avg.addEventListener("change", () => { state.showAvg = avg.checked; rebuild(); });
  dim.addEventListener("change", () => { state.dimOthers = dim.checked; rebuild(); });
}

function refreshSlider() {
  const tmp = RingLib.buildRings(rows, state.cycleHours * 3600);
  const sld = document.getElementById("daySlider");
  sld.max = Math.max(0, tmp.rings.length - 1);
  if (state.day > sld.max) { state.day = sld.max; sld.value = sld.max; }
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
  bind();
  refreshSlider();
  rebuild();
  frameCamera();
  animate();
}

main();
