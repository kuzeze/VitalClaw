"""Local monitoring console for VitalClaw — Digital Twin panel."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
import webbrowser

from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
import uvicorn

from vitalclaw.service import (
    build_latest_features,
    check_alerts,
    dashboard_snapshot,
    record_context_event,
    sync_remote_data,
)

_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

_ASSETS_DIR = Path(__file__).parent / "assets"


def run_ui_server(
    *,
    project_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 3000,
    open_browser: bool = True,
) -> None:
    """Run the local monitoring console."""
    app = build_ui_app(project_root=project_root)
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def build_ui_app(*, project_root: Path | None = None) -> Starlette:
    """Create the Starlette UI application."""

    async def homepage(request) -> HTMLResponse:
        snapshot = dashboard_snapshot(project_root=project_root)
        flash = request.query_params.get("flash")
        return HTMLResponse(_render_twin_panel(snapshot, flash), headers=_NO_STORE)

    async def refresh(request) -> RedirectResponse:
        sync_remote_data(project_root=project_root)
        build_latest_features(project_root=project_root)
        check_alerts(project_root=project_root)
        return RedirectResponse("/?flash=Refreshed", status_code=303)

    async def add_context(request) -> RedirectResponse:
        form: FormData = await request.form()
        event_type = str(form.get("event_type") or "").strip()
        note = str(form.get("note") or "").strip() or f"Recorded from monitoring console: {event_type}"
        effective_date = str(form.get("effective_date") or "").strip() or None
        record_context_event(
            project_root=project_root,
            event_type=event_type,
            note=note,
            effective_date=effective_date,
        )
        return RedirectResponse("/?flash=Context+saved", status_code=303)

    async def api_snapshot(request) -> JSONResponse:
        return JSONResponse(dashboard_snapshot(project_root=project_root), headers=_NO_STORE)

    async def serve_glb(request) -> Response:
        glb = _ASSETS_DIR / "Project.glb"
        if not glb.exists():
            return Response("Model asset missing", status_code=404)
        return FileResponse(
            glb,
            media_type="model/gltf-binary",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    return Starlette(
        debug=False,
        routes=[
            Route("/", homepage),
            Route("/refresh", refresh, methods=["POST"]),
            Route("/context", add_context, methods=["POST"]),
            Route("/api/snapshot", api_snapshot, methods=["GET"]),
            Route("/assets/Project.glb", serve_glb, methods=["GET"]),
        ],
    )


# ---------- snapshot → twin panel mapping ----------

_STATE_BY_TONE = {"good": "steady", "warn": "fatigued", "alert": "alert"}

# Health value per tone, 0–100 slider units.
_HEALTH_BY_TONE = {"good": 78, "warn": 45, "alert": 20}


def _derive_state(snapshot: dict) -> str:
    return _STATE_BY_TONE.get(snapshot.get("status", {}).get("tone", "good"), "steady")


def _worst_tone(metrics_by_id: dict, metric_ids: list[str]) -> str:
    tones = [metrics_by_id[m]["tone"] for m in metric_ids if m in metrics_by_id]
    if "alert" in tones:
        return "alert"
    if "warn" in tones:
        return "warn"
    return "good"


def _derive_health_per_region(snapshot: dict) -> dict[str, int]:
    metrics = {m["metric"]: m for m in snapshot.get("metrics", [])}
    return {
        "head": _HEALTH_BY_TONE[_worst_tone(metrics, ["sleep_duration_hours"])],
        "chest": _HEALTH_BY_TONE[_worst_tone(metrics, ["resting_heart_rate", "heart_rate_variability_sdnn"])],
        "abdomen": _HEALTH_BY_TONE[_worst_tone(metrics, ["wrist_temperature_celsius"])],
        "legs": _HEALTH_BY_TONE[_worst_tone(metrics, ["respiratory_rate"])],
    }


def _stream_clock(snapshot: dict) -> str:
    raw = snapshot.get("last_sync_at")
    if not raw or " " not in raw:
        return "streaming"
    return f"streaming · {raw.split(' ', 1)[1][:5]}"


# ---------- template ----------


def _render_twin_panel(snapshot: dict, flash: str | None) -> str:
    health = _derive_health_per_region(snapshot)
    state = _derive_state(snapshot)
    overall = round(sum(health.values()) / 4)
    defaults = {
        "state": state,
        "hHead": health["head"],
        "hChest": health["chest"],
        "hAbdomen": health["abdomen"],
        "hLegs": health["legs"],
        "overall": overall,
        "density": 40000,
        "size": 180,
        "spin": 30,
    }
    defaults_json = json.dumps(defaults).replace("<", "\\u003c")
    clock = _stream_clock(snapshot)
    flash_block = f'<div class="flash-banner">{escape(flash)}</div>' if flash else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VitalClaw — Digital Twin</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@300;400;500;600&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap" rel="stylesheet">
<script type="importmap">
{{"imports":{{
  "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/examples/jsm/loaders/GLTFLoader":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/GLTFLoader.js"
}}}}
</script>
<style>
  :root {{
    --ink:#1a1e27; --ink-dim:#69707f; --ink-mute:#8f95a1;
    --line:rgba(25,30,39,.08); --line-strong:rgba(25,30,39,.14);
    --primary:#6FE3F0;
  }}
  *{{box-sizing:border-box}}
  html,body{{margin:0;padding:0;background:#ffffff;color:var(--ink);font-family:'Inter Tight',system-ui,sans-serif;-webkit-font-smoothing:antialiased;min-height:100vh}}
  .panel{{position:relative;min-height:100vh;background:radial-gradient(1200px 900px at 50% 40%, #f6fbff 0%, #ffffff 60%, #ffffff 100%);display:flex;flex-direction:column;overflow:hidden}}
  .panel-head{{display:flex;align-items:center;justify-content:space-between;padding:22px 28px 14px}}
  .panel-head .t{{font-size:12px;color:var(--ink-dim);font-family:'JetBrains Mono',monospace;letter-spacing:.08em;text-transform:uppercase}}
  .panel-head .r{{display:flex;gap:6px;align-items:center;color:var(--ink-mute);font-size:11px;font-family:'JetBrains Mono',monospace}}
  .liveDot{{width:6px;height:6px;border-radius:50%;background:#5FC79A;box-shadow:0 0 12px #5FC79A;animation:pulseDot 2.4s ease-in-out infinite}}
  @keyframes pulseDot{{0%,100%{{opacity:.9}}50%{{opacity:.35}}}}
  .stage{{flex:1;position:relative;margin:0 22px;border-radius:20px;overflow:hidden;background:radial-gradient(72% 72% at 50% 48%, #ffffff 0%, #f3f7fb 100%);border:1px solid rgba(25,30,39,.08);min-height:600px;cursor:grab}}
  .stage.dragging{{cursor:grabbing}}
  .stage::after{{content:"";position:absolute;inset:0;background:radial-gradient(70% 60% at 50% 50%, transparent 58%, rgba(103,117,140,.08) 100%);pointer-events:none}}
  .twin{{position:absolute;inset:0;width:100%;height:100%}}
  .concept-chip{{position:absolute;left:18px;bottom:14px;z-index:5;font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-mute);display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.86);backdrop-filter:blur(8px);padding:6px 10px;border-radius:999px;border:1px solid var(--line)}}
  .concept-chip b{{color:var(--ink);font-weight:500}}
  .loading{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ink-mute);letter-spacing:.1em;text-transform:uppercase;z-index:4}}
  .loading.hidden{{display:none}}
  .readout{{padding:16px 28px 10px;display:grid;grid-template-columns:repeat(4,1fr);gap:20px;max-width:760px;width:100%;margin:0 auto}}
  .reg{{display:flex;flex-direction:column;gap:6px}}
  .reg .lbl{{font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-mute);display:flex;align-items:center;gap:6px}}
  .reg .lbl .sw{{width:7px;height:7px;border-radius:2px}}
  .reg .val{{font-size:13px;color:var(--ink);letter-spacing:-.01em}}
  .reg .val small{{color:var(--ink-dim);font-family:'JetBrains Mono',monospace;font-size:10px;margin-left:4px}}
  .statebar-wrap{{padding:10px 28px 28px;max-width:760px;width:100%;margin:0 auto}}
  .statebar{{height:3px;width:100%;border-radius:999px;overflow:hidden;position:relative;background:linear-gradient(90deg,#d24c4c 0%,#E87B5A 25%,#F4C96B 52%,#9bcf6f 75%,#5FC79A 100%)}}
  .statebar::after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(255,255,255,.55) 0%,rgba(255,255,255,0) var(--state,72%),rgba(255,255,255,.55) calc(var(--state,72%) + .5%),rgba(255,255,255,.55) 100%)}}
  .statebar-axis{{display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--ink-mute);margin-top:8px;letter-spacing:.08em;text-transform:uppercase}}
  .flash-banner{{position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:60;font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink);background:rgba(255,255,255,.92);border:1px solid var(--line-strong);padding:8px 14px;border-radius:999px;backdrop-filter:blur(14px)}}
  .rotate-hint{{position:absolute;right:18px;bottom:14px;z-index:5;font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-mute);background:rgba(255,255,255,.86);padding:6px 10px;border-radius:999px;border:1px solid var(--line)}}
</style>
</head>
<body>
{flash_block}
<section class="panel">
  <div class="panel-head">
    <div class="t">Digital Twin · Live</div>
    <div class="r"><span class="liveDot"></span> {escape(clock)}</div>
  </div>
  <div class="stage" id="stage">
    <canvas id="twin" class="twin" data-engine="three.js r160"></canvas>
    <div class="loading" id="loading">Loading model…</div>
    <div class="concept-chip">Model · <b id="conceptName">Particle Twin</b></div>
    <div class="rotate-hint">Auto-rotate on · drag to rotate</div>
  </div>
  <div class="readout">
    <div class="reg" data-reg="head"><span class="lbl"><span class="sw" id="swHead"></span>Head</span><span class="val" id="lblHead">—<small>sleep · cognition</small></span></div>
    <div class="reg" data-reg="chest"><span class="lbl"><span class="sw" id="swChest"></span>Chest</span><span class="val" id="lblChest">—<small>heart · autonomic</small></span></div>
    <div class="reg" data-reg="abdomen"><span class="lbl"><span class="sw" id="swAbdomen"></span>Abdomen</span><span class="val" id="lblAbdomen">—<small>metabolism</small></span></div>
    <div class="reg" data-reg="legs"><span class="lbl"><span class="sw" id="swLegs"></span>Legs</span><span class="val" id="lblLegs">—<small>training load</small></span></div>
  </div>
  <div class="statebar-wrap">
    <div class="statebar" id="statebar" style="--state: {overall}%;"></div>
    <div class="statebar-axis"><span>Red</span><span>Yellow</span><span>Green</span></div>
  </div>
</section>

<script id="twin-defaults" type="application/json">{defaults_json}</script>
<script>
  window.TWEAK_DEFAULTS = JSON.parse(document.getElementById('twin-defaults').textContent);
</script>

<script type="module">
import * as THREE from 'three';
import {{ GLTFLoader }} from 'three/examples/jsm/loaders/GLTFLoader';

const stageEl = document.getElementById('stage');
const canvas = document.getElementById('twin');
const loadingEl = document.getElementById('loading');

const cfg = {{
  state: 'steady',
  health: {{ head: 0.82, chest: 0.55, abdomen: 0.70, legs: 0.28 }},
  overall: 0.59, density: 40000, size: 180, spin: 30,
}};
window.__twin = {{ cfg }};

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 1000);
camera.position.set(0, 0, 45);
const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' }});
renderer.setClearColor(0xffffff, 0);
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

function resize() {{
  const r = stageEl.getBoundingClientRect();
  const w = Math.max(320, Math.floor(r.width));
  const h = Math.max(320, Math.floor(r.height));
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}}
resize();
window.addEventListener('resize', resize);
if (window.ResizeObserver) {{ try {{ new ResizeObserver(resize).observe(stageEl); }} catch (e) {{}} }}

// ----- Sampling -----
function sampleSurfacePoints(meshes, N) {{
  const tris = [];
  let totalArea = 0;
  const vA = new THREE.Vector3(), vB = new THREE.Vector3(), vC = new THREE.Vector3();
  meshes.forEach(mesh => {{
    const g = mesh.geometry;
    if (!g || !g.attributes.position) return;
    mesh.updateWorldMatrix(true, false);
    const mw = mesh.matrixWorld;
    const pos = g.attributes.position;
    const idx = g.index;
    const triCount = idx ? (idx.count / 3) : (pos.count / 3);
    for (let i = 0; i < triCount; i++) {{
      let a, b, c;
      if (idx) {{ a = idx.getX(i * 3); b = idx.getX(i * 3 + 1); c = idx.getX(i * 3 + 2); }}
      else {{ a = i * 3; b = i * 3 + 1; c = i * 3 + 2; }}
      vA.fromBufferAttribute(pos, a).applyMatrix4(mw);
      vB.fromBufferAttribute(pos, b).applyMatrix4(mw);
      vC.fromBufferAttribute(pos, c).applyMatrix4(mw);
      const ab = new THREE.Vector3().subVectors(vB, vA);
      const ac = new THREE.Vector3().subVectors(vC, vA);
      const area = new THREE.Vector3().crossVectors(ab, ac).length() * 0.5;
      if (!isFinite(area) || area <= 0) continue;
      tris.push({{ A: vA.clone(), B: vB.clone(), C: vC.clone(), area }});
      totalArea += area;
    }}
  }});
  const cum = new Float32Array(tris.length);
  let acc = 0;
  for (let i = 0; i < tris.length; i++) {{ acc += tris[i].area; cum[i] = acc / totalArea; }}
  function pickTri() {{
    const r = Math.random();
    let lo = 0, hi = cum.length - 1;
    while (lo < hi) {{ const m = (lo + hi) >> 1; if (cum[m] < r) lo = m + 1; else hi = m; }}
    return tris[lo];
  }}
  const points = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {{
    const t = pickTri();
    let u = Math.random(), v = Math.random();
    if (u + v > 1) {{ u = 1 - u; v = 1 - v; }}
    const w = 1 - u - v;
    points[i * 3]     = t.A.x * w + t.B.x * u + t.C.x * v;
    points[i * 3 + 1] = t.A.y * w + t.B.y * u + t.C.y * v;
    points[i * 3 + 2] = t.A.z * w + t.B.z * u + t.C.z * v;
  }}
  return {{ points, bounds: computeBounds(points) }};
}}

function computeBounds(pts) {{
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i < pts.length; i += 3) {{
    const x = pts[i], y = pts[i + 1], z = pts[i + 2];
    if (x < min.x) min.x = x; if (y < min.y) min.y = y; if (z < min.z) min.z = z;
    if (x > max.x) max.x = x; if (y > max.y) max.y = y; if (z > max.z) max.z = z;
  }}
  return {{
    min, max,
    size: new THREE.Vector3().subVectors(max, min),
    center: new THREE.Vector3().addVectors(min, max).multiplyScalar(0.5),
  }};
}}

// ----- Shader material -----
const vert = /*glsl*/`
  attribute vec3 color;
  attribute vec3 aRand;
  uniform float uTime;
  uniform float uSize;
  uniform float uBreath;
  uniform float uJitter;
  varying vec3 vColor;
  varying float vEdge;
  void main() {{
    vec3 p = position;
    p += aRand * uJitter;
    p *= (1.0 + uBreath);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    float d = -mv.z;
    gl_PointSize = uSize / max(d, 1.0);
    vColor = color;
    vEdge = clamp(length(p) * 0.05, 0.0, 1.0);
  }}
`;
const frag = /*glsl*/`
  precision mediump float;
  varying vec3 vColor;
  varying float vEdge;
  void main() {{
    vec2 d = gl_PointCoord - 0.5;
    float r = length(d);
    if (r > 0.5) discard;
    float a = pow(1.0 - r * 2.0, 1.85);
    vec3 ink = vec3(0.12, 0.15, 0.20);
    vec3 c = mix(vColor, ink, 0.18);
    gl_FragColor = vec4(c, a * 0.92);
  }}
`;

const uniforms = {{
  uTime: {{ value: 0 }}, uSize: {{ value: cfg.size }}, uBreath: {{ value: 0 }}, uJitter: {{ value: 0.02 }},
}};
const pointsMat = new THREE.ShaderMaterial({{
  vertexShader: vert, fragmentShader: frag, uniforms,
  transparent: true, depthWrite: false, blending: THREE.NormalBlending,
}});

let pointsObj = null;
const modelCenter = new THREE.Vector3();
let modelSize = 1;
let autoRotation = 0;
let dragRotationX = 0;
let dragRotationY = 0;
let dragging = false;
let dragStartX = 0;
let dragStartY = 0;
let dragBaseX = 0;
let dragBaseY = 0;

// Anatomical seed-based region classifier.
// Seeds live in normalized body space (nx ∈ [-1,1] relative to half-width, ny ∈ [0,1] feet→head).
// Each seed is tagged with a group; each particle takes the group of its nearest seed (Y weighted 2.2× X).
// Result: organic body-following boundaries instead of horizontal slices. Arms hanging at hip level stay chest.
const REGION_KEYS = ['legs', 'abdomen', 'chest', 'head'];
const BODY_SEEDS = [
  // head + neck
  [ 0.00, 0.96, 'head'],
  [ 0.00, 0.88, 'head'],
  [-0.05, 0.83, 'head'],
  [ 0.05, 0.83, 'head'],
  // shoulders + upper chest
  [-0.35, 0.79, 'chest'],
  [ 0.35, 0.79, 'chest'],
  [ 0.00, 0.77, 'chest'],
  // mid chest
  [-0.15, 0.70, 'chest'],
  [ 0.15, 0.70, 'chest'],
  [ 0.00, 0.68, 'chest'],
  // upper arms
  [-0.45, 0.68, 'chest'],
  [ 0.45, 0.68, 'chest'],
  [-0.55, 0.58, 'chest'],
  [ 0.55, 0.58, 'chest'],
  // forearms + hands (hang to hip level, still chest)
  [-0.60, 0.48, 'chest'],
  [ 0.60, 0.48, 'chest'],
  [-0.65, 0.40, 'chest'],
  [ 0.65, 0.40, 'chest'],
  // upper abdomen
  [-0.12, 0.60, 'abdomen'],
  [ 0.12, 0.60, 'abdomen'],
  [ 0.00, 0.57, 'abdomen'],
  // lower abdomen + hips
  [-0.15, 0.50, 'abdomen'],
  [ 0.15, 0.50, 'abdomen'],
  [ 0.00, 0.48, 'abdomen'],
  // upper thighs
  [-0.14, 0.42, 'legs'],
  [ 0.14, 0.42, 'legs'],
  // mid thighs
  [-0.14, 0.33, 'legs'],
  [ 0.14, 0.33, 'legs'],
  // knees
  [-0.13, 0.24, 'legs'],
  [ 0.13, 0.24, 'legs'],
  // calves
  [-0.12, 0.15, 'legs'],
  [ 0.12, 0.15, 'legs'],
  // feet
  [-0.12, 0.04, 'legs'],
  [ 0.12, 0.04, 'legs'],
];
function classifyToGroup(nx, ny) {{
  let bestI = 0, bestD = Infinity;
  for (let i = 0; i < BODY_SEEDS.length; i++) {{
    const s = BODY_SEEDS[i];
    const dx = (nx - s[0]);
    const dy = (ny - s[1]) * 2.2;
    const d = dx * dx + dy * dy;
    if (d < bestD) {{ bestD = d; bestI = i; }}
  }}
  return REGION_KEYS.indexOf(BODY_SEEDS[bestI][2]);
}}

// Red → amber → vibrant-green ramp. High end pivots past yellow straight to cyan-green for a clear "healthy" signal.
const RAMP = [
  {{ t: 0.00, c: [0.74, 0.24, 0.28] }},  // muted red
  {{ t: 0.35, c: [0.82, 0.44, 0.22] }},  // warm orange
  {{ t: 0.55, c: [0.82, 0.65, 0.20] }},  // amber
  {{ t: 0.72, c: [0.28, 0.66, 0.49] }},  // soft green
  {{ t: 0.88, c: [0.08, 0.61, 0.48] }},  // teal green
  {{ t: 1.00, c: [0.06, 0.54, 0.42] }},  // deep teal
];
function healthColor(h, out) {{
  h = Math.max(0, Math.min(1, h));
  for (let i = 0; i < RAMP.length - 1; i++) {{
    const a = RAMP[i], b = RAMP[i + 1];
    if (h <= b.t) {{
      const k = (h - a.t) / (b.t - a.t || 1);
      out[0] = a.c[0] + (b.c[0] - a.c[0]) * k;
      out[1] = a.c[1] + (b.c[1] - a.c[1]) * k;
      out[2] = a.c[2] + (b.c[2] - a.c[2]) * k;
      return out;
    }}
  }}
  const last = RAMP[RAMP.length - 1].c;
  out[0] = last[0]; out[1] = last[1]; out[2] = last[2];
  return out;
}}
function healthHex(h) {{
  const c = [0, 0, 0]; healthColor(h, c);
  const to = v => ('0' + Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16)).slice(-2);
  return '#' + to(c[0]) + to(c[1]) + to(c[2]);
}}
window.__twin_healthHex = healthHex;

// per-particle region index + brightness jitter, cached per geometry rebuild
let regionIndexArr = null;
let brightJitterArr = null;

function rebuildColors() {{
  if (!pointsObj || !regionIndexArr) return;
  const geom = pointsObj.geometry;
  const col = geom.attributes.color;
  const hVals = [cfg.health.legs, cfg.health.abdomen, cfg.health.chest, cfg.health.head];
  const regColors = hVals.map(h => {{ const c = [0, 0, 0]; healthColor(h, c); return c; }});
  const N = col.count;
  for (let i = 0; i < N; i++) {{
    const r = regionIndexArr[i];
    const base = regColors[r];
    const j = brightJitterArr[i];
    col.setXYZ(i, base[0] * j, base[1] * j, base[2] * j);
  }}
  col.needsUpdate = true;
}}

async function loadModel() {{
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync('/assets/Project.glb');
  const meshes = [];
  gltf.scene.traverse(o => {{ if (o.isMesh) meshes.push(o); }});
  if (!meshes.length) throw new Error('No meshes in model');
  return meshes;
}}

function buildPoints(sampled) {{
  if (pointsObj) {{ scene.remove(pointsObj); pointsObj.geometry.dispose(); }}
  const N = sampled.points.length / 3;
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(sampled.points, 3));
  const colors = new Float32Array(N * 3);
  geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  const rands = new Float32Array(N * 3);
  for (let i = 0; i < N * 3; i++) rands[i] = (Math.random() - 0.5) * 0.1;
  geom.setAttribute('aRand', new THREE.BufferAttribute(rands, 3));

  const s = sampled.bounds.size;
  const rawMax = Math.max(s.x, s.y, s.z) || 1;
  const fitScale = 16 / rawMax;
  const cx = sampled.bounds.center.x, cy = sampled.bounds.center.y, cz = sampled.bounds.center.z;
  const posArr = geom.attributes.position.array;
  for (let i = 0; i < posArr.length; i += 3) {{
    posArr[i]     = (posArr[i]     - cx) * fitScale;
    posArr[i + 1] = (posArr[i + 1] - cy) * fitScale;
    posArr[i + 2] = (posArr[i + 2] - cz) * fitScale;
  }}
  geom.attributes.position.needsUpdate = true;
  geom.computeBoundingSphere();

  pointsObj = new THREE.Points(geom, pointsMat);
  modelCenter.set(0, 0, 0);
  modelSize = rawMax * fitScale;

  regionIndexArr = new Uint8Array(N);
  brightJitterArr = new Float32Array(N);
  let minY = Infinity, maxY = -Infinity, maxAbsX = 0;
  for (let i = 0; i < N; i++) {{
    const x = posArr[i * 3], y = posArr[i * 3 + 1];
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    const ax = Math.abs(x); if (ax > maxAbsX) maxAbsX = ax;
  }}
  const span = (maxY - minY) || 1;
  const halfW = maxAbsX || 1;
  for (let i = 0; i < N; i++) {{
    const nx = posArr[i * 3] / halfW;
    const ny = (posArr[i * 3 + 1] - minY) / span;
    regionIndexArr[i] = classifyToGroup(nx, ny);
    brightJitterArr[i] = 0.82 + Math.random() * 0.36;
  }}

  scene.add(pointsObj);
  rebuildColors();
}}

let rawMeshes = null;

async function init() {{
  try {{
    rawMeshes = await loadModel();
    const sampled = sampleSurfacePoints(rawMeshes, cfg.density);
    buildPoints(sampled);
    loadingEl.classList.add('hidden');
  }} catch (e) {{
    loadingEl.textContent = 'Model load failed: ' + e.message;
    console.error(e);
  }}
}}
init();

function rebuildPoints() {{
  if (!rawMeshes) return;
  const sampled = sampleSurfacePoints(rawMeshes, cfg.density);
  buildPoints(sampled);
}}
window.__twin.rebuildParticles = rebuildPoints;

stageEl.addEventListener('pointerdown', (event) => {{
  dragging = true;
  dragStartX = event.clientX;
  dragStartY = event.clientY;
  dragBaseX = dragRotationX;
  dragBaseY = dragRotationY;
  stageEl.classList.add('dragging');
  stageEl.setPointerCapture?.(event.pointerId);
}});

stageEl.addEventListener('pointermove', (event) => {{
  if (!dragging) return;
  const dx = event.clientX - dragStartX;
  const dy = event.clientY - dragStartY;
  dragRotationY = dragBaseY + dx * 0.008;
  dragRotationX = THREE.MathUtils.clamp(dragBaseX + dy * 0.006, -0.65, 0.65);
}});

function endDrag(event) {{
  dragging = false;
  stageEl.classList.remove('dragging');
  if (event && stageEl.hasPointerCapture?.(event.pointerId)) {{
    stageEl.releasePointerCapture(event.pointerId);
  }}
}}

stageEl.addEventListener('pointerup', endDrag);
stageEl.addEventListener('pointerleave', endDrag);
stageEl.addEventListener('pointercancel', endDrag);

// ----- State modulations (motion) -----
function stateMod(s) {{
  switch (s) {{
    case 'stressed': return {{ breathAmp: .012, breathRate: 1.7, jitter: 0.06, spinMul: 1.4 }};
    case 'fatigued': return {{ breathAmp: .020, breathRate: .55, jitter: 0.015, spinMul: 0.5 }};
    case 'alert':    return {{ breathAmp: .010, breathRate: 1.2, jitter: 0.025, spinMul: 1.2 }};
    default:         return {{ breathAmp: .016, breathRate: 1.0, jitter: 0.02, spinMul: 1.0 }};
  }}
}}

const t0 = performance.now();
function frame() {{
  requestAnimationFrame(frame);
  const t = (performance.now() - t0) * 0.001;
  const m = stateMod(cfg.state);
  uniforms.uTime.value = t;
  uniforms.uSize.value = cfg.size;
  uniforms.uBreath.value = Math.sin(t * m.breathRate) * m.breathAmp;
  uniforms.uJitter.value = m.jitter;
  if (pointsObj) {{
    const spin = (cfg.spin / 100) * 0.25 * m.spinMul;
    autoRotation += spin * 0.016;
    pointsObj.rotation.y = autoRotation + dragRotationY;
    pointsObj.rotation.x = Math.sin(t * 0.2) * 0.08 + dragRotationX;
  }}
  renderer.render(scene, camera);
}}
frame();

window.__twin.refreshColors = rebuildColors;
</script>

<script>
/* Tweaks controller + readout */
(() => {{
  const d = window.TWEAK_DEFAULTS;
  const statebar = document.getElementById('statebar');
  function labelFor(h) {{
    if (h >= 0.75) return 'Optimal';
    if (h >= 0.55) return 'Healthy';
    if (h >= 0.40) return 'Moderate';
    if (h >= 0.25) return 'Strained';
    return 'At risk';
  }}
  function overallFrom(c) {{ return Math.round(((c.head + c.chest + c.abdomen + c.legs) / 4) * 100); }}

  function applyHealthToUI() {{
    const cfg = window.__twin.cfg, hx = window.__twin_healthHex;
    const map = {{
      head:    ['swHead',    'lblHead'],
      chest:   ['swChest',   'lblChest'],
      abdomen: ['swAbdomen', 'lblAbdomen'],
      legs:    ['swLegs',    'lblLegs'],
    }};
    for (const k in map) {{
      const [swId, lblId] = map[k];
      const v = cfg.health[k];
      document.getElementById(swId).style.background = hx(v);
      const lbl = document.getElementById(lblId);
      const small = lbl.querySelector('small');
      if (lbl.firstChild && lbl.firstChild.nodeType === 3) {{
        lbl.firstChild.nodeValue = labelFor(v);
      }} else {{
        lbl.insertBefore(document.createTextNode(labelFor(v)), small);
      }}
    }}
    const ov = overallFrom(cfg.health);
    cfg.overall = ov / 100;
    statebar.style.setProperty('--state', ov + '%');
  }}

  function waitTwin(cb) {{ if (window.__twin && window.__twin.cfg) cb(); else setTimeout(() => waitTwin(cb), 50); }}
  waitTwin(() => {{
    const cfg = window.__twin.cfg;
    cfg.state = d.state;
    cfg.health = {{
      head: d.hHead / 100, chest: d.hChest / 100,
      abdomen: d.hAbdomen / 100, legs: d.hLegs / 100,
    }};
    cfg.density = d.density; cfg.size = d.size; cfg.spin = d.spin;
    applyHealthToUI();
  }});
}})();
</script>
</body>
</html>"""
