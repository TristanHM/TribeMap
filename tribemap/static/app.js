"use strict";
// Tribemap browser view. Draws what /api/map and /api/plan return; all geometry and movement maths come
// from the server (SVG units: hex radius 1, y down). The plan editor sends unsaved routes to
// /api/plan/{turn}/preview, so the browser never re-implements the simulator.

const $ = (id) => document.getElementById(id);
// Browser-only version (branch web-static): no Others' info or People I know (it holds only the player's own
// reports). Their code stays, switched off, so merges from main stay simple.
const OMITTED = new Set(["others", "people"]);
const LAYERS = [
  ["terrain", "Terrain"], ["icons", "Terrain icons"], ["rivers", "Rivers & fords"], ["labels", "Hex labels"],
  ["resources", "Resources"], ["units", "Our units"], ["people", "People I know"],
  ["scouting", "Only glimpsed"], ["fog", "Fog (age)"], ["others", "Others' info"], ["sheets", "Map sheets"],
  ["world", "World grid"],
  ["plan", "Plan"], ["old", "Old map style"],
].filter(([k]) => !OMITTED.has(k));
const OFF_BY_DEFAULT = new Set(["old"]);   // a whole different look: only when asked for
const KNOWLEDGE = {
  full: ["full", "Fully scouted", "Terrain and resources known: our scouts entered it, or our unit stood in it."],
  terrain: ["terrain", "Only glimpsed: needs scouting", "Resources never checked (glimpsed from next door or passed through)."],
  reported: ["unverified", "Needs scouting to verify", "Only other players' word so far."],
  none: ["", "Never seen", "Nothing known yet."],
};
const DARK = new Set(["LCM", "LJM", "JG", "JH", "O", "UW", "LVM", "UM", "CH", "DH"]);
const PALETTE = ["#1f3a93", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085", "#2c3e50", "#b7950b"];
const UNV = "#e67e22";
const SPECIAL = "#c08a00";   // gold: special hexes
const DRAFT = "#e0115f";
const HEX = [0, 60, 120, 180, 240, 300].map((a) => [Math.cos(a * Math.PI / 180), -Math.sin(a * Math.PI / 180)]);
const HOW_LABEL = { scouted: "scouted", glimpsed: "glimpsed from next door", passed: "passed through by a tribe",
                    manual: "our correction", mapped: "from a map file", reported: "unverified", guess: "guess" };
const RESULT = { ok: "", mp_block: "out of MP: glimpses this hex", river_block: "river, no known ford",
                 impassable: "impassable" };

// URL parameters (?plan=906-06&asof=906-05&hex=QB 1016) override what this browser remembers.
const URLP = new URLSearchParams(location.search);
// ?snapshot=1: the page is being photographed for Save (see web.page_snapshot): no sidebar, buttons or pop-ups
if (URLP.get("snapshot")) document.body.classList.add("snapshot");
const state = {
  meta: null, map: null, plan: null, byId: {}, terrain: {},
  asOf: URLP.get("asof") ?? recall("asOf", ""), planTurn: URLP.get("plan") ?? recall("planTurn", ""),
  planVariant: URLP.get("variant") ?? recall("planVariant", "main"),   // which of the turn's plans
  layers: new Set(recall("layers", LAYERS.map(([k]) => k).filter((k) => !OFF_BY_DEFAULT.has(k))).concat(newLayers())
    .filter((k) => !OMITTED.has(k))),
  sel: URLP.get("hex"), icons: new Set(), detail: null, vb: null, hiRoute: null,
  edit: null,   // {draft: {...}, replace: [unit, kind, party] | null, next: [], picking: false, error: ""}
  selected: recall("selected", null),   // the one tribe (with its elements) or element shown at full strength
  hist: { on: false, from: recall("histFrom", ""), to: recall("histTo", ""), tracks: [] },   // off each time the page opens
};

// ---------- helpers ----------
function newLayers() {
  // Layers added since this browser saved its choices start switched on.
  const seen = recall("layersKnown", null);
  store("layersKnown", LAYERS.map(([k]) => k));
  if (seen === null) return recall("layers", null) ? ["scouting", "sheets"] : [];   // saved before layer tracking existed
  return LAYERS.map(([k]) => k).filter((k) => !seen.includes(k) && !OFF_BY_DEFAULT.has(k));
}
function store(k, v) { try { localStorage.setItem("tribemap." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
function recall(k, d) {
  try { const v = localStorage.getItem("tribemap." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; }
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function poly(x, y, r = 1) { return HEX.map(([dx, dy]) => `${(x + dx * r).toFixed(3)},${(y + dy * r).toFixed(3)}`).join(" "); }
function pts(list, j = 0) { return list.map(([x, y]) => `${(x + j).toFixed(3)},${(y + j).toFixed(3)}`).join(" "); }
function nice(n) { return /^[A-Z0-9 ]+$/.test(n) && /[A-Z]/.test(n) ? n.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : n; }
function terrainName(code) { return (state.terrain[code] || {}).name || code; }
function colour(code) { return (state.terrain[code] || {}).colour || "#dddddd"; }
function text(x, y, s, size, attrs = "") {
  return `<text x="${(+x).toFixed(3)}" y="${(+y).toFixed(3)}" font-size="${size}" text-anchor="middle" ${attrs}>${esc(s)}</text>`;
}
async function download(res, fallback) {
  // a file the server sent (Content-Disposition names it) into the browser's downloads; returns its name
  if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || res.statusText);
  const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(await res.blob()), a = document.createElement("a");
  a.href = url;
  a.download = name ? name[1] : fallback;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return a.download;
}
async function api(url, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  if (init.method === "DELETE") init.headers = { "X-Tribemap": "1" };
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
function q(asOf) { return asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""; }
// query string for plan calls: the chosen variant (if not main) and the as-of turn
function pq() {
  const p = new URLSearchParams();
  if (state.planVariant && state.planVariant !== "main") p.set("variant", state.planVariant);
  if (state.asOf) p.set("as_of", state.asOf);
  const s = p.toString();
  return s ? "?" + s : "";
}
function planLabel(turn, variant) { return variant && variant !== "main" ? `${turn} · ${variant}` : turn; }
function setPlan(turn, variant) {
  state.planTurn = turn || "";
  state.planVariant = variant || "main";
  store("planTurn", state.planTurn);
  store("planVariant", state.planVariant);
}
function nextTurn(t) {
  if (!t) return "";
  let [y, m] = t.split("-").map(Number);
  m += 1;
  if (m > 12) { y += 1; m = 1; }
  return `${y}-${String(m).padStart(2, "0")}`;
}

// ---------- loading ----------
async function loadAll(keepView) {
  try {
    state.meta = await api("/api/meta");
    state.terrain = Object.fromEntries(state.meta.terrain.map((t) => [t.code, t]));
    $("projectOpen").hidden = !state.meta.project_open;
    $("guideLink").hidden = !state.meta.project_open;   // the guide is published with the browser-only version
    $("zsave").hidden = !!state.meta.project_open;   // the picture is taken by the desktop server (web.page_snapshot)
    backupNote();
    buildIcons(state.meta.icons || {});
    buildWorld(state.meta.world);
    fillSelects();
    fillHistory();
    await Promise.all([refreshMap(), loadHistory()]);
    state.plan = state.planTurn ? await api(`/api/plan/${state.planTurn}` + pq()).catch(() => null) : null;
    if (!keepView || !state.vb) fit();
    if (URLP.get("vb") && !state.vbFromUrl) {   // a saved view (Save's snapshot uses this)
      state.vbFromUrl = true;
      const [x, y, w, h] = URLP.get("vb").split(",").map(Number);
      if ([x, y, w, h].every(Number.isFinite) && w > 0 && h > 0) setVB({ x, y, w, h });
    }
    if (state.edit) await preview();
    drawAll();
    renderPlan();
    renderEditor();
    renderUnits();
    renderLegend();
    if (state.sel) select(state.sel, false);
    const known = state.map.hexes.filter((h) => h.terrain).length;
    $("status").textContent = state.meta.latest_turn
      ? `Our latest report ${state.meta.latest_turn} · ${known} hexes known` + (state.asOf ? ` · showing as of ${state.asOf}` : "")
      : "No reports imported yet: use Import or Folder to add your turn reports";
  } catch (e) {
    $("status").textContent = "Could not load: " + e.message;
  }
}

function fillSelects() {
  const m = state.meta;
  $("clan").textContent = m.clan ? "Clan " + m.clan : "";   // none until the first report
  if (state.asOf && !m.turns.includes(state.asOf)) state.asOf = "";
  // every saved plan (turn + variant); a plan created in the browser has no routes yet, so keep it listed
  const plans = (m.plans || []).map((p) => [p.turn, p.variant]);
  if (state.planTurn && !plans.some(([t, v]) => t === state.planTurn && v === state.planVariant)) {
    plans.push([state.planTurn, state.planVariant]);
  }
  $("asof").innerHTML = `<option value="">Latest (everything)</option>` +
    [...m.turns].reverse().map((t) => `<option ${t === state.asOf ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("plan").innerHTML = `<option value="">None</option>` +
    plans.map(([t, v]) => `<option value="${esc(t)}|${esc(v)}" ${t === state.planTurn && v === state.planVariant ? "selected" : ""}>
      ${esc(planLabel(t, v))}</option>`).join("") +
    `<option value="__new">+ New plan…</option>`;
}

// ---------- world grid ----------
// The whole game grid as a backdrop: a repeating hex pattern (one tile, aligned with the real hexes: even
// columns' centres at x = 0 mod 3, y = 0 mod √3; odd columns' at x = 1.5, y = √3/2) clipped to each sheet,
// with sheet outlines and names. Only our known hexes are drawn individually on top.
function buildWorld(world) {
  state.world = world;
  const r3 = Math.sqrt(3);
  const tile = [[0, 0], [3, 0], [0, r3], [3, r3], [1.5, r3 / 2]]
    .map(([x, y]) => `<polygon points="${poly(x, y)}"/>`).join("");
  let defs = document.getElementById("hexgrid");
  if (!defs) {
    defs = document.createElementNS("http://www.w3.org/2000/svg", "pattern");
    defs.id = "hexgrid";
    svg.querySelector("defs:not(#iconDefs)").appendChild(defs);
  }
  defs.setAttribute("patternUnits", "userSpaceOnUse");
  defs.setAttribute("width", "3");
  defs.setAttribute("height", String(r3));
  defs.innerHTML = `<g fill="none" stroke="#d3d0c5" stroke-width="0.04">${tile}</g>`;   // over the sheet's own tint
}

// Light tints for the climate bands (tribenet.wiki/mapping/climate), behind the world grid.
const CLIMATE_TINT = { Cold: "#e4e9f0", Cool: "#e6eef3", Temp: "#eef2e2", SubTrop: "#f4f0d8", Tropical: "#f6e7d2" };
const NO_CLIMATE = "#f1efe9";

function drawWorld() {
  const W = state.world;
  let s = "";
  if (W && state.layers.has("world")) {
    for (const sh of W.sheets) {
      const w = sh.x1 - sh.x0, h = sh.y1 - sh.y0;
      s += `<rect x="${sh.x0}" y="${sh.y0}" width="${w}" height="${h}" fill="${CLIMATE_TINT[sh.climate] || NO_CLIMATE}"/>`;
      s += `<rect class="worldpat" x="${sh.x0}" y="${sh.y0}" width="${w}" height="${h}" fill="url(#hexgrid)"
              stroke="#a9a598" stroke-width="0.25"/>`;
      s += text((sh.x0 + sh.x1) / 2, (sh.y0 + sh.y1) / 2 + 3, sh.name, 9, `class="sheetname"`);
    }
    // each row's climate band, once, just left of the grid
    const left = Math.min(...W.sheets.map((sh) => sh.x0)), rows = {};
    for (const sh of W.sheets) rows[sh.name[0]] ||= sh;
    for (const sh of Object.values(rows)) {
      if (sh.climate) {
        s += `<text x="${left - 2}" y="${((sh.y0 + sh.y1) / 2 + 2.5).toFixed(3)}" font-size="7" text-anchor="end"
                class="sheetname">${esc(sh.climate)}</text>`;
      }
    }
  }
  $("gWorld").innerHTML = s;
}

// The world grid appears two mouse-wheel notches out from the "Fit" view of our map (each notch is 1.2x, so
// one notch = 1.2x stays clean and two = 1.44x shows it), and hides again closer in.
function seesPastMap() {
  return !!state.fitW && state.vb && state.vb.w > state.fitW * 1.3;
}

function fitWorld() {
  const b = state.world && state.world.box;
  if (!b) return;
  const pad = 10, labels = 40;   // room on the left for the climate band names
  setVB({ x: b[0] - pad - labels, y: b[1] - pad, w: b[2] - b[0] + 2 * pad + labels, h: b[3] - b[1] + 2 * pad });
}

// ---------- terrain icons ----------
// Shapes come from the server (tribemap/icons.py), shared with PNG maps. Each terrain gets one <symbol>;
// "ink" parts take the <use>'s color, so one symbol works on light and dark terrain.
function buildIcons(icons) {
  // shapes carry their own colours; "ink" means the <use>'s color (dark on light terrain, light on dark)
  state.icons = new Set(Object.keys(icons));
  const paint = (c) => (c === "ink" ? "currentColor" : c === "snow" ? "#ffffff" : c || "none");
  let defs = "";
  for (const [code, shapes] of Object.entries(icons)) {
    let g = "";
    for (const sh of shapes) {
      const st = `stroke="${paint(sh.stroke ?? "ink")}" stroke-width="${sh.w ?? 0.022}"`;
      const pts = sh.pts ? sh.pts.map((p) => p.join(",")).join(" ") : "";
      if (sh.k === "line") g += `<polyline points="${pts}" fill="none" ${st}/>`;
      else if (sh.k === "poly") g += `<polygon points="${pts}" fill="${paint(sh.fill)}" ${st}/>`;
      else if (sh.k === "circle") g += `<circle cx="${sh.c[0]}" cy="${sh.c[1]}" r="${sh.r}" fill="${paint(sh.fill)}" ${st}/>`;
    }
    defs += `<symbol id="ic-${esc(code)}" overflow="visible"><g stroke-linecap="round" stroke-linejoin="round">${g}</g></symbol>`;
  }
  // the old map style's ink symbols (black line art; "paper" fills knock out what's behind)
  const sym = (id, body) => `<symbol id="ink-${id}" overflow="visible"><g fill="none" stroke="#17110a" stroke-width="0.03"
      stroke-linecap="round" stroke-linejoin="round">${body}</g></symbol>`;
  // several hand-drawn variants of each, so a cluster never repeats one shape (light from the upper left)
  const P = "#e9cf8a", K = "#17110a";
  // mountains: drawn procedurally (6 shapes). A jagged silhouette filled with paper, a ridge line down from the
  // summit, the shaded (right) face hatched with strokes running down from the ridge (dense near the top, thinning
  // towards the base), a dark wedge under the summit, and a stroke or two on the lit side.
  for (let v = 0; v < MOUNTAIN_SHAPES; v++) defs += sym(v ? `peak${v + 1}` : "peak", peakShape(seeded(`peak${v}`)));
  // the same six shapes with snow caps (low snowy, high snowy, Alps)
  for (let v = 0; v < MOUNTAIN_SHAPES; v++) defs += sym(v ? `speak${v + 1}` : "speak", peakShape(seeded(`peak${v}`), true));
  // a volcano: a broad cone cut off at the top, its crater, the shaded side hatched, smoke rising
  defs += sym("volcano", `<path d="M-0.36,0.18 L-0.1,-0.3 L0.1,-0.3 L0.36,0.18 L0.2,0.24 L-0.2,0.24 Z" fill="${P}" stroke="none"/>
      <path d="M-0.36,0.18 L-0.1,-0.3 M0.1,-0.3 L0.36,0.18" stroke-width="0.03"/>
      <ellipse cx="0" cy="-0.3" rx="0.1" ry="0.03" fill="${K}" stroke-width="0.02"/>
      <path d="M0.14,-0.22 L0.18,-0.12 M0.18,-0.15 L0.24,-0.03 M0.22,-0.06 L0.29,0.07 M0.26,0.02 L0.31,0.13 M0.08,-0.2 L0.1,-0.1"
        stroke-width="0.018"/>
      <path d="M-0.03,-0.36 Q-0.1,-0.42 -0.04,-0.48 Q0.02,-0.54 -0.02,-0.6 M0.03,-0.36 Q0.1,-0.44 0.05,-0.5 Q0,-0.56 0.06,-0.64"
        stroke-width="0.02"/>
      <circle cx="-0.05" cy="-0.66" r="0.035" fill="${P}" stroke-width="0.018"/><circle cx="0.05" cy="-0.71" r="0.045" fill="${P}" stroke-width="0.018"/>`);
  // palms: a curving trunk with a ring of drooping fronds
  const palm = (lean, h) => {
    const top = [lean, -h];
    const fr = [[-0.13, 0.05], [-0.09, -0.05], [0.0, -0.08], [0.09, -0.05], [0.13, 0.05]]
      .map(([dx, dy]) => `M${top[0].toFixed(3)},${top[1].toFixed(3)} Q${(top[0] + dx * 0.6).toFixed(3)},${(top[1] + dy - 0.04).toFixed(3)}
        ${(top[0] + dx).toFixed(3)},${(top[1] + dy + 0.04).toFixed(3)}`).join(" ");
    return `<path d="M0,0.12 Q${(lean * 0.2).toFixed(3)},${(-h * 0.45).toFixed(3)} ${top[0].toFixed(3)},${top[1].toFixed(3)}"
        stroke-width="0.03"/><path d="${fr}" stroke-width="0.025"/><circle cx="${top[0].toFixed(3)}" cy="${(top[1] + 0.015).toFixed(3)}"
        r="0.018" fill="${K}" stroke="none"/>`;
  };
  defs += sym("palm", palm(0.05, 0.22));
  defs += sym("palm2", palm(-0.04, 0.25));
  defs += sym("palm3", palm(0.08, 0.19));
  // boulders: an angular block, its right face dark
  const rock = (pts, face) => `<path d="M${pts}Z" fill="${P}" stroke-width="0.022"/><path d="M${face}Z" fill="${K}" stroke="none"/>`;
  defs += sym("rock", rock("-0.11,0.05 L-0.08,-0.04 L-0.02,-0.09 L0.06,-0.07 L0.11,0.05 ", "0.02,-0.08 L0.05,0.05 L0.11,0.05 L0.06,-0.07 "));
  defs += sym("rock2", rock("-0.09,0.05 L-0.06,-0.06 L0.03,-0.08 L0.09,0.05 ", "0.03,-0.08 L0.03,0.05 L0.09,0.05 ")
      + rock("0.07,0.06 L0.09,0.01 L0.14,0.0 L0.16,0.06 ", "0.12,0.0 L0.13,0.06 L0.16,0.06 "));
  defs += sym("rock3", rock("-0.07,0.05 L-0.04,-0.05 L0.04,-0.06 L0.08,0.05 ", "0.02,-0.06 L0.03,0.05 L0.08,0.05 L0.04,-0.06 "));
  // snowy hills: no hatching on top, a ragged snowline across the shoulders, shading only below it
  const snowHill = (d, line, shade) => `<path d="${d} Z" fill="${P}" stroke="none"/><path d="${d}"/>
      <path d="${line}" stroke-width="0.016"/><path d="${shade}" stroke-width="0.018"/>`;
  defs += sym("snowhill", snowHill("M-0.28,0.06 Q-0.12,-0.18 0.04,-0.17 Q0.2,-0.14 0.3,0.06",
      "M-0.18,-0.06 L-0.12,-0.03 L-0.06,-0.07 L0.02,-0.04 L0.08,-0.08 L0.15,-0.04 L0.21,-0.06",
      "M0.17,-0.01 L0.19,0.04 M0.22,0 L0.24,0.05"));
  defs += sym("snowhill2", snowHill("M-0.2,0.05 Q0,-0.16 0.2,0.05",
      "M-0.12,-0.03 L-0.07,0 L-0.02,-0.04 L0.04,-0.01 L0.1,-0.04 L0.14,-0.02",
      "M0.1,0.01 L0.12,0.05"));
  // small stones, cracked ground, ice floes
  defs += sym("pebble", `<ellipse cx="-0.05" cy="0.02" rx="0.025" ry="0.015" fill="${K}"/><ellipse cx="0.04" cy="0.03" rx="0.018"
      ry="0.012" fill="${K}"/><ellipse cx="0.0" cy="-0.01" rx="0.012" ry="0.008" fill="${K}"/>`);
  defs += sym("pebble2", `<ellipse cx="-0.02" cy="0.02" rx="0.022" ry="0.014" fill="${K}"/><ellipse cx="0.06" cy="0.0" rx="0.014"
      ry="0.009" fill="${K}"/>`);
  defs += sym("crack", `<path d="M-0.16,0.02 L-0.08,-0.02 L-0.02,0.03 L0.06,-0.01 L0.15,0.03 M-0.02,0.03 L0.0,0.07 M0.06,-0.01
      L0.09,-0.05" stroke-width="0.018"/>`);
  defs += sym("crack2", `<path d="M-0.12,-0.02 L-0.04,0.03 L0.03,-0.01 L0.12,0.04 M-0.04,0.03 L-0.07,0.07" stroke-width="0.018"/>`);
  defs += sym("ice", `<path d="M-0.24,0.04 L-0.18,-0.05 L-0.04,-0.07 L0.1,-0.05 L0.23,-0.02 L0.2,0.06 L0.02,0.08 Z" fill="#f4ead2"
      stroke-width="0.022"/><path d="M-0.06,-0.06 L-0.02,0.0 L-0.07,0.07 M0.1,-0.04 L0.08,0.02" stroke-width="0.014"/>
      <path d="M-0.2,0.07 L0.18,0.09" stroke-width="0.012"/>`);
  // an ice block standing up out of the ice sheet: jagged top, dark right face
  defs += sym("serac", `<path d="M-0.1,0.03 L-0.08,-0.06 L-0.03,-0.13 L0.02,-0.08 L0.06,-0.14 L0.1,0.03 Z" fill="#f4ead2"
      stroke-width="0.022"/><path d="M0.02,-0.08 L0.06,-0.14 L0.1,0.03 L0.04,0.03 Z" fill="${K}" stroke="none"/>
      <path d="M-0.06,-0.02 L-0.02,-0.05" stroke-width="0.012"/>`);
  defs += sym("serac2", `<path d="M-0.07,0.03 L-0.05,-0.08 L0.01,-0.11 L0.07,0.03 Z" fill="#f4ead2" stroke-width="0.022"/>
      <path d="M0.01,-0.11 L0.07,0.03 L0.02,0.03 Z" fill="${K}" stroke="none"/>`);
  // sand dunes: a long windward slope up to a sharp crest, then a short steep lee face in shade (cross-hatched)
  const sdune = (w, h, flip) => {
    const m = flip ? -1 : 1, f = (x, y) => `${(x * m).toFixed(3)},${y.toFixed(3)}`;
    const crest = [w * 0.3, -h], foot = [w * 0.55, 0.03], back = [w * 0.12, 0.03];
    const outline = `M${f(-w, 0.03)} Q${f(-w * 0.25, -h * 0.95)} ${f(...crest)} L${f(...foot)}`;
    // a few grains on the lower slope and along the foot, never near the crest
    // (only on the sunlit side: none on or beside the shaded lee face)
    const dots = [[-0.7, 0.01], [-0.45, 0.02], [-0.2, 0.0], [-0.55, -0.15], [-0.3, -0.25], [-0.85, 0.05]]
      .map(([x, y]) => `<circle cx="${(x * w * m).toFixed(3)}" cy="${(y * h + 0.01).toFixed(3)}" r="0.007" fill="${K}" stroke="none"/>`).join("");
    // paper fill under the whole dune, and a skirt of paper a little below its foot, so a dune in front hides the
    // lines of the one behind (none run out underneath it)
    const skirt = `M${f(-w, 0.03)} L${f(w * 0.55, 0.03)} L${f(w * 0.45, 0.09)} L${f(-w * 0.9, 0.09)} Z`;
    return `<path d="${skirt}" fill="${P}" stroke="none"/><path d="${outline} Z" fill="${P}" stroke="none"/>
      <path d="M${f(...crest)} L${f(...foot)} L${f(...back)} Z" fill="url(#xhatch)" stroke="none"/>
      <path d="${outline}" stroke-width="0.024"/>${dots}`;
  };
  defs += sym("sdune", sdune(0.26, 0.09, false));
  defs += sym("sdune2", sdune(0.2, 0.07, false));   // all dunes face one way (the wind)
  defs += sym("sdune3", sdune(0.16, 0.06, false));
  defs += sym("stipple", [[-0.06, 0.01], [-0.02, 0.03], [0.03, 0.0], [0.07, 0.025], [0.0, -0.02], [-0.08, 0.035]]
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="0.008" fill="${K}" stroke="none"/>`).join(""));
  defs += sym("stipple2", [[-0.05, 0.0], [0.01, 0.02], [0.06, -0.01], [-0.01, -0.02]]
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="0.008" fill="${K}" stroke="none"/>`).join(""));
  defs += sym("ice2", `<path d="M-0.16,0.03 L-0.1,-0.05 L0.06,-0.06 L0.17,0.0 L0.12,0.06 L-0.08,0.07 Z" fill="#f4ead2"
      stroke-width="0.022"/><path d="M0.0,-0.05 L0.03,0.02 L-0.01,0.06" stroke-width="0.014"/>`);
  defs += sym("conifer", `<path d="M0,0.09 L0,0.15" stroke-width="0.03"/>
      <path d="M0,-0.26 L0.07,-0.13 L0.035,-0.13 L0.1,-0.02 L0.05,-0.02 L0.12,0.09 L-0.12,0.09 L-0.05,-0.02 L-0.1,-0.02
               L-0.035,-0.13 L-0.07,-0.13 Z" fill="${P}" stroke-width="0.025"/>
      <path d="M0,-0.26 L0.07,-0.13 L0.035,-0.13 L0.1,-0.02 L0.05,-0.02 L0.12,0.09 L0,0.09 Z" fill="${K}" stroke="none"/>`);
  defs += sym("conifer2", `<path d="M0,0.08 L0,0.14" stroke-width="0.03"/>
      <path d="M0,-0.24 L0.06,-0.06 L0.03,-0.06 L0.08,0.08 L-0.08,0.08 L-0.03,-0.06 L-0.06,-0.06 Z" fill="${P}" stroke-width="0.025"/>
      <path d="M0,-0.24 L0.06,-0.06 L0.03,-0.06 L0.08,0.08 L0,0.08 Z" fill="${K}" stroke="none"/>`);
  defs += sym("conifer3", `<path d="M0,0.07 L0,0.12" stroke-width="0.03"/>
      <path d="M0,-0.18 L0.09,0.07 L-0.09,0.07 Z" fill="${K}" stroke-width="0.02"/>`);
  const crown = (d) => `<path d="M0,0.13 L0,0.03" stroke-width="0.035"/><path d="${d}" fill="${P}"/>
      <path d="M0.03,0.02 Q0.1,0 0.09,-0.07" stroke-width="0.02"/>`;
  defs += sym("tree", crown("M-0.09,0.04 Q-0.14,-0.03 -0.08,-0.08 Q-0.07,-0.15 0,-0.15 Q0.07,-0.15 0.08,-0.08 Q0.14,-0.03 0.09,0.04 Z"));
  defs += sym("tree2", crown("M-0.07,0.03 Q-0.11,-0.05 -0.07,-0.12 Q-0.05,-0.2 0,-0.2 Q0.06,-0.2 0.07,-0.12 Q0.11,-0.05 0.07,0.03 Z"));
  defs += sym("tree3", crown("M-0.12,0.03 Q-0.15,-0.04 -0.09,-0.07 Q-0.08,-0.13 -0.02,-0.12 Q0.03,-0.16 0.08,-0.11 Q0.15,-0.09 0.13,-0.02 Q0.15,0.03 0.1,0.04 Z"));
  defs += sym("hill", `<path d="M-0.28,0.06 Q-0.12,-0.18 0.04,-0.17 Q0.2,-0.14 0.3,0.06 Z" fill="${P}" stroke="none"/>
      <path d="M-0.28,0.06 Q-0.12,-0.18 0.04,-0.17 Q0.2,-0.14 0.3,0.06"/>
      <path d="M0.12,-0.1 L0.15,-0.01 M0.18,-0.06 L0.21,0.02 M0.23,-0.02 L0.25,0.04" stroke-width="0.02"/>`);
  defs += sym("hill2", `<path d="M-0.3,0.06 Q-0.2,-0.1 -0.08,-0.08 Q0,-0.18 0.12,-0.14 Q0.24,-0.1 0.3,0.06 Z" fill="${P}" stroke="none"/>
      <path d="M-0.3,0.06 Q-0.2,-0.1 -0.08,-0.08 Q0,-0.18 0.12,-0.14 Q0.24,-0.1 0.3,0.06"/>
      <path d="M0.16,-0.08 L0.18,0 M0.22,-0.04 L0.24,0.03 M-0.12,-0.04 L-0.1,0.02" stroke-width="0.02"/>`);
  defs += sym("hill3", `<path d="M-0.18,0.05 Q0,-0.14 0.18,0.05 Z" fill="${P}" stroke="none"/><path d="M-0.18,0.05 Q0,-0.14 0.18,0.05"/><path d="M0.07,-0.04 L0.1,0.03 M0.12,-0.01 L0.14,0.04"
      stroke-width="0.02"/>`);
  // brush: little cloud-puff shrubs (bumpy top, flat bottom, dark shadow underneath), two or three to a clump
  const puff = (x, y, k) => {
    const g = (a, b) => `${(x + a * k).toFixed(3)},${(y + b * k).toFixed(3)}`;
    const crown = `M${g(-0.1, 0.03)} Q${g(-0.13, -0.02)} ${g(-0.07, -0.045)} Q${g(-0.06, -0.1)} ${g(-0.01, -0.085)}
        Q${g(0.03, -0.115)} ${g(0.07, -0.065)} Q${g(0.125, -0.055)} ${g(0.1, 0.03)} Z`;
    // paper, then cross-hatching (the "xhatch" pattern), a lit patch on the upper left left bare, then the outline
    return `<path d="${crown}" fill="${P}" stroke="none"/><path d="${crown}" fill="url(#xhatch)" stroke="none"/>
      <ellipse cx="${(x - 0.04 * k).toFixed(3)}" cy="${(y - 0.06 * k).toFixed(3)}" rx="${(0.035 * k).toFixed(3)}"
        ry="${(0.022 * k).toFixed(3)}" fill="${P}" stroke="none"/>
      <path d="${crown}" stroke-width="0.02"/>
      <path d="M${g(-0.1, 0.03)} Q${g(0, 0.0)} ${g(0.1, 0.03)} Q${g(0, 0.075)} ${g(-0.1, 0.03)} Z" fill="${K}" stroke="none"/>`;
  };
  defs += sym("bush", puff(-0.07, 0.02, 0.8) + puff(0.06, 0.03, 0.65) + puff(0, 0.05, 0.9));
  defs += sym("bush2", puff(-0.05, 0.02, 0.85) + puff(0.06, 0.04, 0.75));
  defs += sym("bush3", puff(0.0, 0.03, 1.0) + puff(0.1, 0.05, 0.6));
  // where a hill meets water: a paper band hides its foot, with a wavy line and a shorter one below
  defs += sym("waterline", `<path d="M-0.34,0.02 L0.34,0.02 L0.34,0.12 L-0.34,0.12 Z" fill="${P}" stroke="none"/>
      <path d="M-0.3,0.03 Q-0.22,0 -0.15,0.03 Q-0.07,0.06 0,0.03 Q0.07,0 0.15,0.03 Q0.22,0.06 0.3,0.03" stroke-width="0.022"/>
      <path d="M-0.16,0.085 Q-0.08,0.06 0,0.085 Q0.08,0.11 0.16,0.085" stroke-width="0.018"/>`);
  defs += sym("grass", `<path d="M-0.08,0.05 L-0.1,-0.03 M-0.03,0.05 L-0.03,-0.07 M0.02,0.05 L0.04,-0.05 M0.07,0.05 L0.1,-0.02"
      stroke-width="0.022"/>`);
  defs += sym("grass2", `<path d="M-0.05,0.05 Q-0.06,-0.01 -0.09,-0.05 M0,0.05 L0,-0.08 M0.05,0.05 Q0.06,-0.01 0.09,-0.05"
      stroke-width="0.022"/>`);
  defs += sym("grass3", `<path d="M-0.03,0.05 L-0.05,-0.04 M0.02,0.05 L0.03,-0.06" stroke-width="0.022"/>`);
  defs += sym("swamp", `<path d="M-0.18,0.05 L0.18,0.05 M-0.1,0.11 L0.1,0.11 M-0.06,0.05 L-0.08,-0.07 M0,0.05 L0,-0.1
      M0.06,0.05 L0.08,-0.06" stroke-width="0.022"/>`);
  defs += sym("swamp2", `<path d="M-0.15,0.04 L0.12,0.04 M-0.05,0.1 L0.15,0.1 M0.02,0.04 L0,-0.09 M0.06,0.04 L0.08,-0.05"
      stroke-width="0.022"/><ellipse cx="0" cy="-0.1" rx="0.012" ry="0.03" fill="${K}"/>`);
  defs += sym("wave", `<path d="M-0.2,0 Q-0.15,-0.06 -0.1,0 Q-0.05,0.06 0,0 Q0.05,-0.06 0.1,0 Q0.15,0.06 0.2,0" stroke-width="0.022"/>`);
  defs += sym("wave2", `<path d="M-0.12,0 Q-0.06,-0.06 0,0 Q0.06,0.06 0.12,0" stroke-width="0.022"/>`);
  defs += sym("dune", `<path d="M-0.2,0.05 Q0,-0.1 0.2,0.05" stroke-width="0.022"/><circle cx="-0.05" cy="0.1" r="0.012" fill="${K}"/>
      <circle cx="0.08" cy="0.12" r="0.012" fill="${K}"/>`);
  defs += sym("dune2", `<path d="M-0.15,0.05 Q-0.02,-0.08 0.12,0.03" stroke-width="0.022"/><circle cx="0.02" cy="0.09" r="0.012" fill="${K}"/>`);
  $("iconDefs").innerHTML = defs;
}

// ---------- drawing ----------
// ---------- old map style ----------
// A hand-inked map for showing: black line art on golden parchment (peaks shaded black on one side, forests as
// dense clusters of little trees, hills as arcs, sparse grass), black rivers, red lettering, a red-and-black compass
// and a checkered frame. Unknown ground is bare parchment. None of the working detail is shown (labels, codes,
// units, plans, fog, scouting marks, notes, sheets).
// Each terrain is a recipe of [symbol, how many, size]; symbols are scattered in the hex and drawn back to front.
const INK = {
  mountain: [["peak", 4, 1.55]], conifer: [["conifer", 9, 1.5]], forest: [["tree", 9, 1.5]],
  hill: [["hill", 4, 1.5]], conhill: [["hill", 2, 1.6], ["conifer", 6, 1.35]], dechill: [["hill", 2, 1.6], ["tree", 6, 1.35]],
  brushhill: [["hill", 3, 1.55], ["bush", 6, 1.2]], brush: [["bush", 10, 1.3]], plain: [["grass", 8, 0.95]],
  swamp: [["swamp", 2, 1.4]], water: [["wave", 5, 0.9]], desert: [["dune", 3, 1.5]],   // water: a few small waves
  grassland: [["grass", 13, 1.1]], brushflat: [["bush", 5, 1.15], ["grass", 5, 0.9]],
  jungle: [["palm", 8, 1.5]], junglehill: [["hill", 2, 1.6], ["palm", 5, 1.3]], wetforest: [["tree", 6, 1.4], ["swamp", 2, 1.2]],
  tundra: [["grass", 4, 0.8], ["pebble", 4, 1.1]], arid: [["crack", 2, 1.3], ["grass", 3, 0.8]],
  colddesert: [["crack", 2, 1.3], ["pebble", 4, 1.1]], rockhill: [["hill", 2, 1.6], ["rock", 7, 0.6]],
  snowhill: [["snowhill", 4, 1.5]], aridhill: [["hill", 3, 1.5], ["pebble", 3, 1.1]], ice: [["serac", 2, 1.2]],
  sand: [["sdune", 4, 1.8]],   // the dunes carry their own grains (lower slope and foot)
};
const INK_VARIANTS = { peak: 3, conifer: 3, tree: 3, hill: 3, bush: 3, grass: 3, swamp: 2, wave: 2, dune: 2,
                       palm: 3, rock: 3, snowhill: 2, pebble: 2, crack: 2, ice: 2, serac: 2, sdune: 3, stipple: 2 };
// no shading side, so they can face either way
const INK_MIRROR = new Set(["bush", "grass", "swamp", "wave", "dune", "palm", "pebble", "crack", "ice", "stipple"]);
// what grows at the foot of a mountain range, by terrain (drawn in front of the peaks)
const MOUNTAIN_FOOT = { LCM: ["conifer"], LJM: ["palm"], UM: ["tree", "conifer"], LAM: ["pebble"] };
const MOUNTAIN = /M$|^ALPS$/;
const MOUNTAIN_SHAPES = 6;
function peakShape(rnd, snow) {
  const P = "#e9cf8a", K = "#17110a", base = 0.18;
  const w = 0.34 + 0.12 * rnd(), ht = 0.34 + 0.16 * rnd(), sx = (rnd() - 0.5) * 0.12;
  const lerp = (a, b, t) => a + (b - a) * t;
  // silhouette: left base -> shoulder(s) -> summit -> shoulder(s) -> right base
  const left = [[-w, base]], right = [];
  for (const t of [0.35, 0.68]) {
    left.push([lerp(-w, sx, t) + (rnd() - 0.5) * 0.06, lerp(base, -ht, t) + (rnd() < 0.5 ? 0.05 * rnd() : -0.03 * rnd())]);
  }
  for (const t of [0.32, 0.66]) {
    right.push([lerp(sx, w, t) + (rnd() - 0.5) * 0.06, lerp(-ht, base, t) + (rnd() < 0.5 ? -0.05 * rnd() : 0.03 * rnd())]);
  }
  const outline = [...left, [sx, -ht], ...right, [w, base]];
  // ridge: from the summit down towards the base, wandering a little right of centre
  const rb = sx + 0.06 + 0.06 * rnd();
  const ridge = [[sx, -ht], [lerp(sx, rb, 0.35) + 0.03 * rnd(), lerp(-ht, base, 0.35)],
                 [lerp(sx, rb, 0.7) - 0.02 * rnd(), lerp(-ht, base, 0.7)], [rb, base]];
  const xAt = (line, y) => {   // x where a top-to-bottom polyline crosses height y
    for (let i = 0; i < line.length - 1; i++) {
      const [a, b] = [line[i], line[i + 1]];
      if ((y - a[1]) * (y - b[1]) <= 0 && a[1] !== b[1]) return a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]);
    }
    return line[line.length - 1][0];
  };
  const rightEdge = [[sx, -ht], ...right, [w, base]];
  const f = (p) => `${p[0].toFixed(3)},${p[1].toFixed(3)}`;
  let hatch = "";
  const n = 7 + Math.floor(rnd() * 4);
  for (let k = 0; k < n; k++) {
    const y = -ht + 0.05 + (base + ht - 0.07) * (k / n), t = (y + ht) / (base + ht);
    if (t > 0.55 && rnd() < t - 0.35) continue;   // thinner towards the base
    const x0 = xAt(ridge, y) + 0.012, x1 = xAt(rightEdge, y) - 0.01;
    if (x1 - x0 < 0.03) continue;
    const len = (x1 - x0) * (0.95 - 0.45 * t);
    hatch += `M${f([x0, y])} L${f([x0 + len, y + len * 0.55])} `;
  }
  const wedge = [[sx, -ht], [lerp(sx, ridge[1][0], 0.6) + 0.035, lerp(-ht, ridge[1][1], 0.6)], ridge[1].map((v, i) => v - (i ? 0 : 0.0))];
  // no outline along the base: the paper fill runs on below it (fading into the ground), so the peaks in front
  // cover the feet of the ones behind and a range reads as one mass rather than separate triangles
  const skirt = `M${outline.map(f).join(" L")} L${f([w * 0.55, base + 0.06])} L${f([-w * 0.55, base + 0.06])} Z`;
  // snow: a pale cap over the top third, with a ragged snowline (it hides the hatching and wedge under it)
  let cap = "";
  if (snow) {
    const yl = -ht + (base + ht) * 0.38;
    const lx = xAt([[sx, -ht], ...left.slice().reverse()], yl), rx = xAt(rightEdge, yl);
    const teeth = [];
    for (let k = 1; k < 6; k++) teeth.push([lerp(rx, lx, k / 6), yl + (k % 2 ? -0.05 : 0.02) * (0.6 + 0.6 * rnd())]);
    const shape = [[sx, -ht], ...right.filter((q) => q[1] < yl), [rx, yl], ...teeth, [lx, yl],
                   ...left.filter((q) => q[1] < yl)];   // round from the summit, down the right, back up the left
    cap = `<path d="M${shape.map(f).join(" L")} Z" fill="#f4ead2" stroke-width="0.018"/>`;
  }
  return `<path d="${skirt}" fill="${P}" stroke="none"/>
    <path d="M${outline.map(f).join(" L")}" stroke-width="0.03"/>
    <path d="M${f(wedge[0])} L${f(wedge[1])} L${f(wedge[2])} Z" fill="${K}" stroke-width="0.015"/>
    <path d="M${ridge.slice(0, 3).map(f).join(" L")}" stroke-width="0.025"/>
    <path d="${hatch}" stroke-width="0.018"/>${cap}`;
}
// Mountains as ranges: peaks fill each mountain hex and spill over its edges, a ridge peak sits between every two
// neighbouring mountain hexes so a range reads as one chain, peaks are biggest in the heart of a range, and small
// foothills fringe it where it meets lower ground.
function mountainRanges() {
  const out = [], key = (x, y) => `${x.toFixed(2)},${y.toFixed(2)}`, at = {};
  for (const h of state.map.hexes) {
    const [t, unv] = shownTerrain(h);
    if (t) at[key(h.x, h.y)] = { h, mtn: MOUNTAIN.test(t), t };
  }
  const r3 = Math.sqrt(3), dirs = [30, 90, 150, 210, 270, 330].map((a) => [r3 * Math.cos(a * Math.PI / 180), -r3 * Math.sin(a * Math.PI / 180)]);
  let snowy = false;   // the hex being drawn: snow-capped peaks
  const peak = (x, y, sc, rnd) => {
    const v = Math.floor(rnd() * MOUNTAIN_SHAPES), id = (snowy ? "speak" : "peak") + (v ? v + 1 : "");
    out.push({ x, y, id, sx: sc, sy: sc });
  };
  for (const { h, mtn, t } of Object.values(at)) {
    if (!mtn) continue;
    const rnd = seeded(h.id + "m");
    const nbrs = dirs.map(([dx, dy]) => [dx, dy, at[key(h.x + dx, h.y + dy)]]);
    const inner = nbrs.filter(([, , n]) => n && n.mtn).length;   // 0-6: how deep in the range this hex is
    // high snowy mountains and the Alps stand taller; snowy ones are capped; a volcano gets its smoking cone
    const big = (1.7 + 0.15 * inner) * ({ HSM: 1.15, ALPS: 1.3 }[t] || 1);
    snowy = ["LSM", "HSM", "ALPS"].includes(t);
    // the hex's own peaks: one big central one, smaller ones around it, some reaching over the edges
    if (t === "LVM") out.push({ x: h.x + (rnd() - 0.5) * 0.2, y: h.y + 0.05, id: "volcano", sx: big, sy: big });
    else peak(h.x + (rnd() - 0.5) * 0.3, h.y + (rnd() - 0.5) * 0.2, big * (0.95 + 0.25 * rnd()), rnd);
    const n = 4 + Math.floor(rnd() * 3);
    for (let k = 0; k < n; k++) {
      const a = rnd() * 2 * Math.PI, r = 0.35 + 0.55 * rnd();
      peak(h.x + Math.cos(a) * r, h.y + Math.sin(a) * r * 0.75, big * (0.5 + 0.3 * rnd()), rnd);
    }
    for (const [dx, dy, nb] of nbrs) {
      if (nb && nb.mtn && (dx > 0 || (dx === 0 && dy > 0))) {
        // a ridge peak between two mountain hexes (each pair once), so the range runs on unbroken
        for (const f of [0.35, 0.65]) {   // two ridge peaks along the join
          peak(h.x + dx * f + (rnd() - 0.5) * 0.25, h.y + dy * f + (rnd() - 0.5) * 0.2, big * (0.75 + 0.35 * rnd()), rnd);
        }
      } else if (nb && !nb.mtn) {
        // foothills on the lower ground just beside the range
        for (let k = 0; k < 1 + Math.floor(rnd() * 2); k++) {
          const f = 0.62 + 0.2 * rnd(), side = (rnd() - 0.5) * 0.9;
          const x = h.x + dx * f - dy * side * 0.35, y = h.y + dy * f + dx * side * 0.35;
          const v = Math.floor(rnd() * 3), id = v ? `hill${v + 1}` : "hill";
          const sc = 0.9 + 0.5 * rnd();
          out.push({ x, y, id, sx: sc, sy: sc });
        }
      }
    }
  }
  return out;
}
// Two neighbouring hexes of the same terrain share a cluster of its symbols across their border, so several hexes
// of forest (or hills, brush, grass...) read as one stretch of country rather than separate clumps. Mountains do
// the same in mountainRanges; swamps and water are drawn as single regions.
function joiners() {
  const out = [], key = (x, y) => `${x.toFixed(2)},${y.toFixed(2)}`, at = {};
  for (const h of state.map.hexes) {
    const [t] = shownTerrain(h);
    if (t && !MOUNTAIN.test(t) && !["SW", "L", "O", "UW", "PI"].includes(t)) at[key(h.x, h.y)] = { h, recipe: inkRecipe(t) };
  }
  const r3 = Math.sqrt(3), dirs = [30, 90, 150, 210, 270, 330].map((a) => [r3 * Math.cos(a * Math.PI / 180), -r3 * Math.sin(a * Math.PI / 180)]);
  for (const { h, recipe } of Object.values(at)) {
    for (const [dx, dy] of dirs) {
      if (!(dx > 0.01 || (Math.abs(dx) < 0.01 && dy > 0))) continue;   // each pair once
      const nb = at[key(h.x + dx, h.y + dy)];
      if (!nb || nb.recipe !== recipe) continue;
      const rnd = seeded(h.id + "|" + nb.h.id), mx = h.x + dx / 2, my = h.y + dy / 2;
      const len = Math.hypot(dx, dy), ax = -dy / len, ay = dx / len;   // along the shared border
      for (const [sym, base, size] of recipe) {
        const n = Math.max(1, Math.round(base * 0.45));
        for (let k = 0; k < n; k++) {
          if (sym === "grass" && rnd() < 0.25) continue;
          const along = (rnd() - 0.5) * 0.9, across = (rnd() - 0.5) * 0.35;
          const x = mx + ax * along + (dx / len) * across, y = my + ay * along + (dy / len) * across;
          const sc = size * (0.8 + 0.45 * rnd()), v = Math.floor(rnd() * INK_VARIANTS[sym]);
          out.push({ x, y, id: v ? `${sym}${v + 1}` : sym, sx: sc * (INK_MIRROR.has(sym) && rnd() < 0.5 ? -1 : 1), sy: sc });
        }
      }
    }
  }
  return out;
}
// A tree or shrub drawn behind a hill (further up the page, overlapping it) is lifted so its base sits above the
// hill's foot: it stands on the far slope and the hill hides its trunk, instead of the trunk poking out underneath.
function tuckBehindHills(items) {
  const kind = (o) => o.id.replace(/\d+$/, "");
  const hills = items.filter((o) => kind(o) === "hill");
  for (const o of items) {
    if (!["tree", "conifer", "bush", "palm"].includes(kind(o))) continue;
    const down = INK_EXTENT[kind(o)][2] * o.sy;
    for (const h of hills) {
      if (o.y > h.y || Math.abs(o.x - h.x) > INK_EXTENT.hill[0] * Math.abs(h.sx) * 0.85) continue;
      const foot = h.y + 0.04 * h.sy;               // a little above the hill's base line
      if (o.y + down > foot) o.y = foot - down;     // still behind it (smaller y), so drawn before it
    }
  }
}

// A hill or peak standing in a swamp or lake gets a short wavy waterline across its foot.
function waterlines(items) {
  const wet = state.map.hexes.filter((h) => { const [t, unv] = shownTerrain(h); return t && ["SW", "L", "O", "UW"].includes(t); });
  if (!wet.length) return;
  for (const o of items) {
    const kind = o.id.replace(/\d+$/, "");
    if (!["hill", "peak", "speak", "snowhill", "volcano"].includes(kind)) continue;
    if (!wet.some((h) => Math.hypot(h.x - o.x, h.y - o.y) < 0.85)) continue;
    o.water = true;
  }
}

// How far each symbol reaches from its anchor, before scaling: [half-width, above, below].
const INK_EXTENT = { peak: [0.42, 0.48, 0.2], conifer: [0.12, 0.26, 0.15], tree: [0.15, 0.21, 0.13], hill: [0.3, 0.19, 0.07],
                     bush: [0.14, 0.07, 0.07], grass: [0.1, 0.08, 0.05], swamp: [0.18, 0.11, 0.12], dune: [0.2, 0.1, 0.13],
                     speak: [0.42, 0.48, 0.2], volcano: [0.36, 0.62, 0.2], palm: [0.17, 0.28, 0.15], rock: [0.12, 0.1, 0.06],
                     snowhill: [0.3, 0.19, 0.07], pebble: [0.09, 0.03, 0.04], crack: [0.16, 0.05, 0.06], ice: [0.24, 0.08, 0.09],
                     serac: [0.12, 0.16, 0.04], sdune: [0.26, 0.1, 0.06], stipple: [0.1, 0.04, 0.04] };
// The old map takes artistic licence: a symbol that would cover a river is nudged away from it (up to about half a
// hex), or left out if it still would, so the river always shows.
function clearOfRivers(items) {
  const segs = state.map.sides.filter((e) => (e.verified || state.layers.has("others")) && e.kinds.some((k) => k === "river" || k === "ford"));
  if (!segs.length) return items;
  const pts = [];   // the river as drawn (riverRuns + smoothPath), sampled every few hundredths of a hex
  for (const p of riverRuns(segs)) {
    const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const line = (a, b) => { for (let t = 0; t <= 1; t += 0.1) pts.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); };
    if (p.length < 3) { line(p[0], p[p.length - 1]); continue; }
    line(p[0], mid(p[0], p[1]));
    for (let i = 1; i < p.length - 1; i++) {
      const a = mid(p[i - 1], p[i]), c = p[i], b = mid(p[i], p[i + 1]);
      for (let t = 0; t <= 1; t += 0.1) {
        const u = 1 - t;
        pts.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
      }
    }
    line(mid(p[p.length - 2], p[p.length - 1]), p[p.length - 1]);
  }
  const margin = 0.07;
  const hit = (o) => {   // the nearest river point inside the symbol's box (plus a margin), if any
    const [w, up, down] = INK_EXTENT[o.id.replace(/\d+$/, "")] || [0.2, 0.2, 0.1];
    const x0 = o.x - w * Math.abs(o.sx) - margin, x1 = o.x + w * Math.abs(o.sx) + margin;
    const y0 = o.y - up * o.sy - margin, y1 = o.y + down * o.sy + margin;
    let best = null, bd = Infinity;
    for (const p of pts) {
      if (p[0] < x0 || p[0] > x1 || p[1] < y0 || p[1] > y1) continue;
      const d = Math.hypot(p[0] - o.x, p[1] - o.y);
      if (d < bd) { bd = d; best = p; }
    }
    return best;
  };
  const out = [];
  for (const o of items) {
    let p = hit(o), steps = 0;
    while (p && steps < 8) {
      // step away from the river, from the symbol's middle (a peak's middle is well above its anchor)
      const [w, up, down] = INK_EXTENT[o.id.replace(/\d+$/, "")] || [0.2, 0.2, 0.1];
      const cy = o.y + (down - up) * o.sy / 2;
      let dx = o.x - p[0], dy = cy - p[1];
      const len = Math.hypot(dx, dy) || 1;
      o.x += (dx / len) * 0.07;
      o.y += (dy / len) * 0.07;
      p = hit(o);
      steps++;
    }
    if (!p) out.push(o);
  }
  return out;
}

function inkRecipe(code) {
  if (/M$|^ALPS$/.test(code)) return INK.mountain;
  if (["L", "O", "UW"].includes(code)) return INK.water;
  const own = { C: "conifer", D: "forest", DF: "forest", JG: "jungle", UJS: "wetforest", CH: "conhill", DH: "dechill",
                JH: "junglehill", BH: "brushhill", RH: "rockhill", SH: "snowhill", AH: "aridhill", BR: "brush",
                BF: "brushflat", SW: "swamp", DE: "sand", AR: "arid", UL: "colddesert", TU: "tundra", PI: "ice",
                PPR: "grassland" }[code];
  if (own) return INK[own];
  if (/H$|^GHP$/.test(code)) return INK.hill;
  return INK.plain;
}
function seeded(id) {   // a small repeatable random source per hex, so the clumps don't jump about between redraws
  let h = 2166136261;
  for (const ch of id) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => ((h = Math.imul(h ^ (h >>> 15), 2246822507) ^ Math.imul(h ^ (h >>> 13), 3266489909)) >>> 0) / 4294967296;
}
function oldMap() { return state.layers.has("old"); }

// The old map's parchment sheet: a torn edge all round (a wandering tear line with small nicks), cut to the map
// area's size. Rebuilt when the window size changes.
function tearParchment() {
  const box = $("mapWrap").getBoundingClientRect(), w = box.width, h = box.height, key = `${Math.round(w)}x${Math.round(h)}`;
  if (!w || !h || state.torn === key) return;
  state.torn = key;
  const rnd = seeded("parchment"), inset = 14, step = 6;
  // slow wander (value noise every ~60px) plus fine jitter, 3-17px in from the box edge
  const knots = Array.from({ length: Math.ceil(2 * (w + h) / 60) + 2 }, () => rnd());
  const depth = (d) => {
    const k = d / 60, i = Math.floor(k), f = k - i, a = knots[i % knots.length], b = knots[(i + 1) % knots.length];
    return inset - 11 + 14 * (a + (b - a) * (3 - 2 * f) * f * f) + 3 * rnd() + (rnd() < 0.03 ? 6 : 0);
  };
  // each side runs between the inset corners (not the box's own corners, which left slivers poking out)
  const pts = [], c = inset + 3;
  let d = 0;
  for (let x = c; x < w - c; x += step, d += step) pts.push([x, depth(d)]);
  for (let y = c; y < h - c; y += step, d += step) pts.push([w - depth(d), y]);
  for (let x = w - c; x > c; x -= step, d += step) pts.push([x, h - depth(d)]);
  for (let y = h - c; y > c; y -= step, d += step) pts.push([depth(d), y]);
  const clip = `polygon(${pts.map(([x, y]) => `${x.toFixed(1)}px ${y.toFixed(1)}px`).join(",")})`;
  $("parchmentSheet").style.clipPath = clip;
  svg.style.clipPath = clip;   // the drawing stops at the torn edge too: symbols go under it, not over the leather
}

// The old map's drawing fades away round the title and subtitle (a soft oval in screen space, where the title
// sits), so the lettering stays clear whatever the zoom.
function fadeUnderTitle() {
  const t = $("cartoucheBox").getBoundingClientRect(), w = $("mapWrap").getBoundingClientRect();
  if (!t.width) return;
  const cx = t.left - w.left + t.width / 2, cy = t.top - w.top + t.height / 2;
  // clear over the lettering plus a margin, then a long fade back to full strength
  const rx = (t.width / 2 + 45) / 0.5, ry = (t.height / 2 + 30) / 0.5;
  const m = `radial-gradient(ellipse ${Math.round(rx)}px ${Math.round(ry)}px at ${Math.round(cx)}px ${Math.round(cy)}px,
    transparent 0%, transparent 50%, rgba(0,0,0,.35) 68%, rgba(0,0,0,.8) 85%, #000 100%)`;
  svg.style.maskImage = m;
  svg.style.webkitMaskImage = m;
}
window.addEventListener("resize", () => { if (oldMap()) { tearParchment(); fadeUnderTitle(); } });
document.fonts.ready.then(() => { if (oldMap()) fadeUnderTitle(); });   // the title's size once its font is in

function drawAll() {
  svg.classList.toggle("old", oldMap());
  if (oldMap()) { tearParchment(); fadeUnderTitle(); }
  else if (state.torn) { svg.style.clipPath = ""; svg.style.maskImage = svg.style.webkitMaskImage = ""; state.torn = null; }
  if (!oldMap()) $("gOld").innerHTML = "";
  $("cartouche").textContent = state.meta && state.meta.clan ? `The Lands of Clan ${state.meta.clan}` : "";
  $("cartoucheSub").textContent = state.map ? `as known in ${state.map.now || ""}` : "";
  drawWorld();
  drawHexes(); drawSides(); drawSheets(); drawPlan(); drawEdit(); drawHistory(); drawUnits(); drawPeople(); drawSel();
  svg.classList.toggle("editing", !!state.edit);
}

function shownTerrain(h) {
  // Hexes known only from other players are hidden when the "Others' info" layer is off.
  const unverified = h.terrain && h.verified === false;
  if (unverified && !state.layers.has("others")) return [null, false];
  return [h.terrain, unverified];
}

function drawHexes() {
  if (oldMap()) { drawOldHexes(); return; }
  const L = state.layers;
  let hex = "", fog = "", txt = "";
  for (const h of state.map.hexes) {
    const [t, unv] = shownTerrain(h);
    const fill = t && L.has("terrain") ? colour(t) : "#ececec";
    hex += `<polygon class="hex" data-id="${h.id}" points="${poly(h.x, h.y)}" fill="${fill}" fill-opacity="${unv ? 0.5 : 1}"/>`;
    if (L.has("scouting") && h.knowledge === "terrain") {
      fog += `<polygon points="${poly(h.x, h.y)}" fill="url(#hatch)"/>`;
    }
    if (L.has("fog") && t && !unv) {
      if (h.fog > 0) {   // ground scouted a year or more ago (queries.fog_alpha)
        fog += `<polygon points="${poly(h.x, h.y)}" fill="#808080" fill-opacity="${h.fog}"/>`;
      }
    }
    if (unv) {
      fog += `<polygon points="${poly(h.x, h.y, 0.92)}" fill="none" stroke="${UNV}" stroke-width="0.06" stroke-dasharray="0.15 0.1"/>`;
    }
    // special hexes (towns, encounters, points of interest) and resource hexes (deposits): a gold double
    // border, with ★ (special) or ⚒ (resource) in the corner; dashed when only another player has reported it
    const ours = (h.specials || []).length + (h.resources || []).length;
    const theirs = L.has("others") ? (h.unverified_specials || []).length + (h.unverified_resources || []).length : 0;
    if (L.has("resources") && (ours || theirs)) {
      const dash = ours ? "" : `stroke-dasharray="0.16 0.1"`;
      const mark = (h.specials || []).length || (L.has("others") && (h.unverified_specials || []).length) ? "★" : "⚒";
      fog += `<polygon points="${poly(h.x, h.y, 0.94)}" fill="none" stroke="${SPECIAL}" stroke-width="0.09" ${dash}/>`;
      fog += `<polygon points="${poly(h.x, h.y, 0.8)}" fill="none" stroke="${SPECIAL}" stroke-width="0.04" ${dash}/>`;
      txt += text(h.x + 0.5, h.y - 0.36, mark, 0.3, `fill="${SPECIAL}" stroke="#fff" stroke-width="0.03" paint-order="stroke"`);
    }
    if (L.has("labels")) txt += text(h.x, h.y - 0.6, h.id, 0.19, `class="lbl" fill="#555"`);
    const icon = t && L.has("terrain") && L.has("icons") && state.icons.has(t);
    if (icon) {
      // dark ink on light terrain, light ink on dark terrain; fainter when unverified
      const ink = DARK.has(t) ? "#ffffff" : "#2b2b2b";
      txt += `<use href="#ic-${t}" x="${h.x}" y="${h.y}" color="${ink}" opacity="${unv ? 0.55 : 1}"/>`;
    }
    if (t && L.has("terrain")) {
      // with icons on, the code moves down to the bottom edge and shrinks
      txt += text(h.x, h.y + (icon ? 0.66 : 0.52), t + (unv ? "?" : ""), icon ? 0.19 : 0.26,
        `fill="${unv ? "#b85d12" : DARK.has(t) ? "#fff" : "#333"}" font-weight="${unv ? 700 : 400}"`);
    }
    if (L.has("resources")) {
      const notes = [...(h.notes || [])];
      if (L.has("others")) notes.push(...(h.unverified_notes || []).map((n) => n + "?"));
      notes.slice(0, 3).forEach((n, i) => {
        txt += text(h.x, h.y - 0.3 + i * 0.2, nice(n), 0.17,
          `font-weight="700" fill="${n.endsWith("?") ? "#b85d12" : "#5a2d0c"}"`);
      });
    }
  }
  $("gHex").innerHTML = hex;
  $("gFog").innerHTML = fog;
  $("gFog").setAttribute("pointer-events", "none");
  $("gText").innerHTML = txt;
}

function drawOldHexes() {
  // only ground we know gets symbols; unknown ground is bare parchment
  let hit = "", marks = "";
  const sites = [];   // where the red X marks go: symbols are cleared from under them
  const found = [];   // hexes with a special site or deposit
  const items = [], reeds = [];
  // waves stay well inside the water: clear of the shore and the ripple lines drawn inside it (lakeEdge)
  const wetAt = new Set(state.map.hexes.filter((h) => ["L", "O", "UW"].includes(shownTerrain(h)[0]))
    .map((h) => `${h.x.toFixed(2)},${h.y.toFixed(2)}`));
  const r3 = Math.sqrt(3), sides = [30, 90, 150, 210, 270, 330].map((a) => [Math.cos(a * Math.PI / 180), -Math.sin(a * Math.PI / 180)]);
  const offshore = (h, x, y, halfW) => sides.every(([ux, uy]) =>
    wetAt.has(`${(h.x + ux * r3).toFixed(2)},${(h.y + uy * r3).toFixed(2)}`)
    || (x - h.x) * ux + (y - h.y) * uy + Math.abs(ux) * halfW + Math.abs(uy) * 0.04 < r3 / 2 - 0.4);
  for (const h of state.map.hexes) {
    hit += `<polygon class="hex" data-id="${h.id}" points="${poly(h.x, h.y)}" fill="transparent" style="stroke:none"/>`;
    const [t, unv] = shownTerrain(h);
    if (!t) continue;   // others' ground is drawn too, when the Others' info layer is on
    const rnd = seeded(h.id);
    if (MOUNTAIN.test(t)) {   // the peaks are drawn below, as whole ranges; here only what grows at their foot
      for (const sym of MOUNTAIN_FOOT[t] || []) {
        for (let k = 0; k < 3; k++) {
          const v = Math.floor(rnd() * INK_VARIANTS[sym]), sc = 1.2 * (0.8 + 0.4 * rnd());
          items.push({ x: h.x + (rnd() - 0.5) * 1.1, y: h.y + 0.45 + 0.2 * rnd(), id: v ? `${sym}${v + 1}` : sym,
                       sx: sc * (INK_MIRROR.has(sym) && rnd() < 0.5 ? -1 : 1), sy: sc });
        }
      }
      continue;
    }
    if (t === "SW") {
      // reeds sit round the swamp's own core (the same one its outline is built from, in polys() below), small
      // enough to stay inside; they're also drawn through the swamp's mask, so none can show past its border
      const rw = seeded(h.id + "w"), cx = h.x + (rw() - 0.5) * 0.4, cy = h.y + (rw() - 0.5) * 0.3;
      const n = 2 + Math.floor(rnd() * 2);
      for (let k = 0; k < n; k++) {
        const a = (k / n) * 2 * Math.PI + rnd(), r = 0.12 + 0.12 * rnd();
        const v = Math.floor(rnd() * INK_VARIANTS.swamp), sc = 0.9 + 0.3 * rnd();
        reeds.push({ x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r * 0.7, id: v ? `swamp${v + 1}` : "swamp",
                     sx: sc * (rnd() < 0.5 ? -1 : 1), sy: sc });
      }
      continue;
    }
    for (const [sym, base, size] of inkRecipe(t)) {
      // a jittered grid over the hex, so clusters are dense but not regular; the count varies from hex to hex
      const n = Math.max(1, base + Math.floor(rnd() * 3) - 1);
      const cols = Math.ceil(Math.sqrt(n * 1.15));
      for (let k = 0; k < n; k++) {
        if (sym === "grass" && rnd() < 0.25) continue;   // open grassland: leave a few gaps
        const gx = (k % cols + 0.5) / cols - 0.5, gy = (Math.floor(k / cols) + 0.5) / Math.ceil(n / cols) - 0.5;
        const x = h.x + gx * 1.25 + (rnd() - 0.5) * 0.3, y = h.y + gy * 1.05 + (rnd() - 0.5) * 0.25;
        if (Math.hypot(x - h.x, (y - h.y) * 1.15) > 0.82) continue;
        const sc = size * (0.8 + 0.45 * rnd()) * (sym === "peak" ? 1.15 : 1);
        if (sym === "wave" && !offshore(h, x, y, 0.2 * sc)) continue;
        const v = Math.floor(rnd() * INK_VARIANTS[sym]), id = v ? `${sym}${v + 1}` : sym;
        const fx = INK_MIRROR.has(sym) && rnd() < 0.5 ? -1 : 1;
        items.push({ x, y, id, sx: sc * fx, sy: sc });
      }
    }
    const notes = [...(h.specials || []), ...(h.resources || []).map(nice)];
    if (notes.length) found.push({ h, note: notes.join(", ") });
  }
  items.push(...joiners());
  // special sites and deposits: a hand-drawn red X with a cursive note beside it, as if written on the map.
  // Neighbouring hexes with the same note (e.g. a salt flat across two hexes) share one mark, between them.
  const groups = [];
  for (const f of found) {
    const g = groups.find((gr) => gr.note === f.note && gr.hs.some((o) => Math.hypot(o.x - f.h.x, o.y - f.h.y) < 1.9));
    g ? g.hs.push(f.h) : groups.push({ note: f.note, hs: [f.h] });
  }
  state.oldSites = [];   // drawn by oldMapSites (redrawn on zoom, so they keep a readable size)
  for (const g of groups) {
    const rx = seeded(g.hs[0].id + "x");
    const x = g.hs.reduce((a, h) => a + h.x, 0) / g.hs.length - 0.15 + (rx() - 0.5) * 0.15;
    const y = g.hs.reduce((a, h) => a + h.y, 0) / g.hs.length + (rx() - 0.5) * 0.15;
    sites.push([x, y]);
    state.oldSites.push({ x, y, note: g.note, tilt: -4 - 6 * rx() });
  }
  items.push(...mountainRanges());
  // swamps and lakes: one rounded region each, with a natural shoreline (lakes with water lines inside, swamps
  // with marsh dashes); reeds are still scattered over the swamp as symbols
  const region = (codes) => state.map.hexes.filter((h) => { const [t, unv] = shownTerrain(h); return t && codes.includes(t); });
  // each hex plus a few random lobes, so the outline is irregular rather than a tidy rounded hex
  const polys = (hs, whole) => hs.map((h) => {
    const rnd = seeded(h.id + "w");
    const cx = h.x + (rnd() - 0.5) * 0.4, cy = h.y + (rnd() - 0.5) * 0.3;
    let g = `<polygon points="${poly(cx, cy, 0.55 + 0.25 * rnd())}"/>`;
    if (whole) g += `<polygon points="${poly(h.x, h.y, 1.0)}"/>`;   // seas and ice sheets: neighbours run together
    for (let k = 0; k < 4; k++) {   // lobes of different sizes, out to the hex's edge and a little beyond
      const a = rnd() * 2 * Math.PI, r = 0.4 + 0.5 * rnd();
      g += `<ellipse cx="${(h.x + Math.cos(a) * r).toFixed(3)}" cy="${(h.y + Math.sin(a) * r * 0.85).toFixed(3)}"
              rx="${(0.2 + 0.4 * rnd()).toFixed(3)}" ry="${(0.15 + 0.25 * rnd()).toFixed(3)}"
              transform="rotate(${Math.round(rnd() * 180)} ${(h.x + Math.cos(a) * r).toFixed(3)} ${(h.y + Math.sin(a) * r * 0.85).toFixed(3)})"/>`;
    }
    return g;
  }).join("");
  const sw = region(["SW"]), wet = region(["L", "O", "UW"]), ice = region(["PI"]);
  let regions = "";
  if (sw.length) {
    regions += `<defs><mask id="marshMask" maskUnits="userSpaceOnUse" x="-5000" y="-5000" width="10000" height="10000">
      <g filter="url(#blobFill)">${polys(sw)}</g></mask></defs>
      <rect x="-5000" y="-5000" width="10000" height="10000" fill="url(#marshPat)" mask="url(#marshMask)"/>
      <g filter="url(#blobEdge)">${polys(sw)}</g>`;
  }
  if (wet.length) regions += `<g filter="url(#lakeEdge)">${polys(wet, true)}</g>`;
  if (ice.length) {   // an ice sheet: pale, wind-ridged, with a ragged edge
    regions += `<defs><mask id="iceMask" maskUnits="userSpaceOnUse" x="-5000" y="-5000" width="10000" height="10000">
      <g filter="url(#blobFill)">${polys(ice, true)}</g></mask></defs>
      <rect x="-5000" y="-5000" width="10000" height="10000" fill="#f3e8cb" mask="url(#iceMask)"/>
      <rect x="-5000" y="-5000" width="10000" height="10000" fill="url(#icePat)" mask="url(#iceMask)"/>
      <g filter="url(#blobEdge)">${polys(ice, true)}</g>`;
  }
  // rivers stop at the shore of a swamp or lake rather than running across it (drawSides draws through this)
  regions += `<defs><mask id="riverMask" maskUnits="userSpaceOnUse" x="-5000" y="-5000" width="10000" height="10000">
    <rect x="-5000" y="-5000" width="10000" height="10000" fill="#fff"/>
    <g filter="url(#blobCut)">${polys(sw)}${polys(wet, true)}</g></mask></defs>`;
  const placed = clearOfRivers(items).filter((o) => !sites.some(([x, y]) => Math.hypot(o.x - x, o.y - y) < 0.3));
  waterlines(placed);
  tuckBehindHills(placed);
  placed.sort((a, b) => a.y - b.y);   // back to front: nearer symbols overlap the ones behind
  $("gOld").innerHTML = regions;
  $("gHex").innerHTML = hit;
  $("gFog").innerHTML = "";
  $("gText").innerHTML = placed.map((o) => `<use href="#ink-${o.id}" transform="translate(${o.x.toFixed(3)} ${o.y.toFixed(3)})
      scale(${o.sx.toFixed(3)} ${o.sy.toFixed(3)})"/>` + (o.water ? `<use href="#ink-waterline" transform="translate(${o.x.toFixed(3)}
      ${(o.y + 0.02).toFixed(3)}) scale(${Math.abs(o.sx).toFixed(3)} ${o.sy.toFixed(3)})"/>` : "")).join("") + marks;
  // the reeds go in the swamps' own layer, under their mask
  if (reeds.length) {
    $("gOld").insertAdjacentHTML("beforeend", `<g mask="url(#marshMask)">${reeds.sort((a, b) => a.y - b.y).map((o) =>
      `<use href="#ink-${o.id}" transform="translate(${o.x.toFixed(3)} ${o.y.toFixed(3)}) scale(${o.sx.toFixed(3)} ${o.sy.toFixed(3)})"/>`)
      .join("")}</g>`);
  }
}

// Join river hexside segments that share an end into runs of points (each run one stretch of river).
function riverRuns(segs) {
  const key = (p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`, ends = {};
  segs.forEach((e, i) => { for (const p of [e.a, e.b]) (ends[key(p)] ||= []).push(i); });
  const used = new Set(), runs = [];
  const other = (e, p) => (key(e.a) === key(p) ? e.b : e.a);
  for (let i = 0; i < segs.length; i++) {
    if (used.has(i)) continue;
    // start from a free end if this run has one, so a whole stretch comes out as one line
    let start = i, from = segs[i].a;
    for (let guard = 0, cur = i, at = segs[i].a; guard < segs.length; guard++) {
      const next = (ends[key(at)] || []).find((j) => j !== cur);
      if (next === undefined || next === i) break;
      at = other(segs[next], at); cur = next; start = next; from = at;
    }
    const pts = [from];
    for (let cur = start, at = from; cur !== undefined && !used.has(cur);) {
      used.add(cur);
      at = other(segs[cur], at);
      pts.push(at);
      cur = (ends[key(at)] || []).find((j) => !used.has(j));
    }
    runs.push(pts);
  }
  return runs;
}
// A curve through the midpoints of a run of points, bending at each corner (so hexside zigzags become a meander).
function smoothPath(p) {
  const f = (q) => `${q[0].toFixed(3)},${q[1].toFixed(3)}`, mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  if (p.length < 3) return `M${f(p[0])} L${f(p[p.length - 1])}`;
  let d = `M${f(p[0])} L${f(mid(p[0], p[1]))}`;
  for (let i = 1; i < p.length - 1; i++) d += ` Q${f(p[i])} ${f(mid(p[i], p[i + 1]))}`;
  return d + ` L${f(p[p.length - 1])}`;
}

function drawSides() {
  const L = state.layers;
  let s = "";
  if (oldMap()) {
    // rivers: the hexside segments chained into runs and drawn as smooth black ink curves
    const segs = state.map.sides.filter((e) => (e.verified || state.layers.has("others")) && e.kinds.some((k) => k === "river" || k === "ford"));
    for (const run of riverRuns(segs)) {
      const d = smoothPath(run);
      s += `<path d="${d}" fill="none" stroke="#17110a" stroke-width="0.1" stroke-linecap="round" stroke-linejoin="round"/>`;
    }
    s = `<g mask="url(#riverMask)"><g filter="url(#riverWobble)">${s}</g></g>`;
    $("gSides").innerHTML = s;
    return;
  }
  if (L.has("rivers")) {
    for (const e of state.map.sides) {
      if (!e.verified && !L.has("others")) continue;
      const [a, b] = [e.a, e.b];
      s += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#3b7dd8" stroke-linecap="round"
              stroke-width="${e.verified ? 0.13 : 0.09}" ${e.verified ? "" : `stroke-dasharray="0.2 0.12"`}/>`;
      if (e.kinds.includes("ford")) {
        const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
        s += `<rect x="${mx - 0.08}" y="${my - 0.08}" width="0.16" height="0.16" fill="#c49a5a" stroke="#333" stroke-width="0.02"/>`;
      }
    }
  }
  $("gSides").innerHTML = s;
}

function drawSheets() {
  // Map sheet borders (dashed, along the hexsides where two sheets meet) and faint sheet names.
  let names = "", lines = "";
  if (state.layers.has("sheets")) {
    // Names only matter where sheets meet; a single sheet is already named in every hex label.
    for (const s of state.map.sheets.length > 1 ? state.map.sheets : []) {
      const size = Math.min(3, 0.7 * Math.sqrt(s.hexes));
      names += text(s.x, s.y + size * 0.35, s.name, size.toFixed(2), `font-weight="800" fill="#000" fill-opacity="0.07"`);
    }
    for (const b of state.map.borders) {
      lines += `<line x1="${b.a[0]}" y1="${b.a[1]}" x2="${b.b[0]}" y2="${b.b[1]}" stroke="#222" stroke-opacity="0.75"
                  stroke-width="0.07" stroke-dasharray="0.22 0.13" stroke-linecap="round"/>`;
    }
  }
  $("gSheetNames").innerHTML = names;
  $("gBorders").innerHTML = lines;
}

// ---------- one selected tribe (or element) at a time ----------
// Exactly one tribe or element is selected; every other unit's scout routes are faded (the units and their moves
// always show in full). Selecting a tribe includes its
// elements. Route colours are given per tribe (party 1 = blue, 2 = red, ...), so only one tribe's routes
// are ever shown at full strength and 8 colours are enough.
const FADED = 0.3;   // opacity of scout routes of units that aren't selected
function tribeOf(u) { return u.slice(0, 4); }
function allUnits() {
  const s = new Set(state.map ? state.map.units.map((u) => u.unit) : []);
  if (state.map) state.map.paths.forEach((p) => s.add(p.unit));
  if (state.plan) state.plan.routes.forEach((r) => s.add(r.unit));
  return [...s].sort();
}
function selected() {
  const units = allUnits();
  if (state.selected && units.some((u) => u === state.selected || tribeOf(u) === state.selected)) return state.selected;
  return units.includes(state.meta && state.meta.clan) ? state.meta.clan : (units[0] ? tribeOf(units[0]) : null);
}
function isSelected(unit) {
  const sel = selected();
  if (!sel) return true;
  return sel.length === 4 ? tribeOf(unit) === sel : unit === sel;
}
function isFaded(unit) { return !isSelected(unit); }
function selectUnit(u) {
  state.selected = u;
  store("selected", u);
  drawAll(); renderPlan(); renderUnits();
  if (state.detail) renderDetail(state.detail);
}
function renderUnits() {
  const units = allUnits(), sel = selected();
  $("unitPicker").hidden = units.length < 2;
  const tribes = [...new Set(units.map(tribeOf))].sort();
  let h = `<div class="pickhead">Showing</div>`;
  for (const t of tribes) {
    for (const u of [t, ...units.filter((x) => x !== t && tribeOf(x) === t)]) {
      const element = u !== t, on = u === sel || (element && sel === t);
      const name = ((state.meta && state.meta.unit_names) || {})[u];
      h += `<button class="pick ${element ? "element" : ""} ${on ? "on" : ""}" data-select="${esc(u)}"
        title="${element ? "Show only this element" : "Show this tribe and its elements"}">${esc(u)}${name ? `<span class="pickname">${esc(name)}</span>` : ""}</button>`;
    }
  }
  $("unitPicker").innerHTML = h;
  if (sel) $("autoScout").textContent = `Auto-plan ${sel} scouts`;
}

// ---------- route lanes ----------
// Lines run through hex centres. Where several routes use the same hex-to-hex segment, each bows out into
// its own lane (a small gap apart) in the middle of the segment and comes back to the centre at both ends.
const LANE_GAP = 0.075;
function segKey(a, b) {
  const ka = `${a[0]},${a[1]}`, kb = `${b[0]},${b[1]}`;
  return ka < kb ? [ka + "|" + kb, false] : [kb + "|" + ka, true];   // [key, reversed]
}
function laneTable(pointLists) {
  const seg = {};
  pointLists.forEach((ps, ri) => {
    for (let i = 1; i < ps.length; i++) {
      const [k] = segKey(ps[i - 1], ps[i]);
      (seg[k] ||= []).includes(ri) || seg[k].push(ri);
    }
  });
  return seg;
}
function lanePoints(ps, ri, seg) {
  // -> polyline points: every hex centre, plus two lane points inside each shared segment
  const out = ps.length ? [ps[0]] : [];
  for (let i = 1; i < ps.length; i++) {
    const [a, b] = [ps[i - 1], ps[i]];
    const [k, rev] = segKey(a, b), users = seg[k], n = users.length;
    const off = (users.indexOf(ri) - (n - 1) / 2) * LANE_GAP;
    if (off) {
      // the normal comes from the segment's canonical direction so opposite-going routes agree on lanes
      const [p, q] = rev ? [b, a] : [a, b];
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1, nx = -(q[1] - p[1]) / len, ny = (q[0] - p[0]) / len;
      for (const t of [0.3, 0.7]) {
        out.push([a[0] + (b[0] - a[0]) * t + nx * off, a[1] + (b[1] - a[1]) * t + ny * off]);
      }
    }
    out.push(b);
  }
  return out;
}

function routeColour(r) {
  // The server picks colours (plans.assign_colours): nearby routes always differ, distant ones may share.
  return r.colour || (r.draft ? DRAFT : r.kind === "move" ? "#111111" : "#1f77b4");
}

const TO_SCOUT = "#7a7a7a";   // grey: turns unknown #ececec into a darker #c4c4c4

function planScouts() {
  // Hexes this plan's scout parties will enter that we don't fully know yet (incl. stale ones):
  // after the turn they'll be fully scouted. Tribe moves and glimpses only give terrain, so they don't count.
  const P = state.plan, out = new Set();
  if (!P) return out;
  const stale = new Set((P.coverage.by_status || {}).stale || []);
  for (const r of P.routes) {
    if (r.kind !== "scout" || !r.sim) continue;   // every tribe counts, selected or not
    for (const s of r.sim.steps) {
      const h = state.byId[s.hex];
      if (s.result === "ok" && (!h || h.knowledge !== "full" || stale.has(s.hex))) out.add(s.hex);
    }
  }
  return out;
}

function drawPlanFill() {
  let s = "";
  if (state.plan && (state.layers.has("plan") || state.edit)) {
    for (const id of planScouts()) {
      const h = state.byId[id];
      if (h) s += `<polygon points="${poly(h.x, h.y)}" fill="${TO_SCOUT}" fill-opacity="0.28"/>`;
    }
  }
  $("gPlanFill").innerHTML = s;
}

function drawPlan() {
  drawPlanFill();
  let s = "";
  const P = state.plan;
  if (P && (state.layers.has("plan") || state.edit)) {
    const unitOf = Object.fromEntries(P.routes.map((r) => [r.name, r]));
    for (const o of P.overlap) {
      // only an overlap between routes that are still shown
      const shown = o.routes.filter((n) => unitOf[n] && (!isFaded(unitOf[n].unit) || unitOf[n].draft));
      if (shown.length < 2) continue;
      s += `<polygon points="${poly(o.x, o.y, 0.86)}" fill="none" stroke="#8e44ad" stroke-width="0.07"/>`;
    }
    const focus = state.edit ? (r) => r.draft : state.hiRoute ? (r) => r.name === state.hiRoute : null;
    const labelsAt = {};   // stack the labels of routes that end in the same hex
    const scouts = P.routes.filter((r) => r.points && r.kind === "scout");
    const seg = laneTable(scouts.map((r) => r.points));
    for (const r of P.routes) {
      if (!r.points) continue;
      // only scout routes fade with the unit picker: tribes, elements and their moves always show in full
      const hi = focus && focus(r), faded = r.kind === "scout" && isFaded(r.unit) && !r.draft, dim = (focus && !hi) || faded;
      if (r.kind === "move") {
        // one line through the hexes, with a single arrowhead on the last step
        if (r.points.length > 1) {
          s += `<polyline points="${pts(r.points)}" fill="none" stroke="${r.draft ? DRAFT : "#111"}" stroke-linejoin="round"
                  stroke-width="${hi ? 0.18 : 0.12}" marker-end="url(#arrow)" opacity="${faded ? FADED : dim ? 0.3 : 1}"/>`;
        }
        if (r.draft) s += stopMark(r, r.points[r.points.length - 1], 0, DRAFT);
        continue;
      }
      const c = routeColour(r), line = lanePoints(r.points, scouts.indexOf(r), seg), last = line[line.length - 1];
      s += `<g opacity="${faded ? FADED : dim ? 0.3 : 1}">`;
      s += `<polyline points="${pts(line)}" fill="none" stroke="${c}" stroke-linejoin="round" stroke-width="${hi ? 0.13 : 0.065}"
/>`;
      s += `<circle cx="${last[0]}" cy="${last[1]}" r="0.08" fill="${c}"/>`;
      s += stopMark(r, last, 0, c);
      s += followArrows(r, last, 0, c);
      const n = (labelsAt[r.end] = (labelsAt[r.end] || 0) + 1) - 1;
      s += text(last[0], last[1] - 0.2 - n * 0.22, `${r.unit}/${r.party}${r.follow ? " " + r.follow : ""}`, 0.17,
        `fill="${c}" font-weight="700" stroke="#fff" stroke-width="0.06" paint-order="stroke"`);
      s += `</g>`;
    }
  }
  $("gPlan").innerHTML = s;
}

function followArrows(r, last, j, c) {
  // Which way a follow order (FRR, FCL, ...) should start: dashed arrow towards that neighbour.
  let s = "";
  const [x0, y0] = [last[0] + j, last[1] + j];
  for (const [tx, ty] of r.follow_points || []) {
    const x1 = x0 + (tx - x0) * 0.75, y1 = y0 + (ty - y0) * 0.75;
    const dx = x1 - x0, dy = y1 - y0, n = Math.hypot(dx, dy) || 1, ux = dx / n, uy = dy / n, k = 0.16;
    s += `<line x1="${x0}" y1="${y0}" x2="${x1 - ux * k}" y2="${y1 - uy * k}" stroke="${c}" stroke-width="0.07" stroke-dasharray="0.12 0.08"/>`;
    s += `<polygon points="${x1},${y1} ${x1 - ux * k - uy * k * 0.6},${y1 - uy * k + ux * k * 0.6} ${x1 - ux * k + uy * k * 0.6},${y1 - uy * k - ux * k * 0.6}" fill="${c}"/>`;
  }
  return s;
}

function stopMark(r, last, j, c) {
  if (!r.stop_point) return "";
  let s = "", [bx, by] = r.stop_point;
  if (r.stop_kind === "river_block") {
    bx = (last[0] + j + bx) / 2; by = (last[1] + j + by) / 2;
    s += text(bx, by + 0.1, "×", 0.4, `fill="${c}" font-weight="700"`);
  }
  return s + `<line x1="${last[0] + j}" y1="${last[1] + j}" x2="${bx}" y2="${by}" stroke="${c}" stroke-width="0.045" stroke-dasharray="0.07 0.07"/>`;
}

function drawEdit() {
  // While editing: MP left at each step of the draft, and what each possible next step would cost.
  let s = "";
  const E = state.edit, r = E && state.plan && state.plan.routes.find((x) => x.draft);
  if (r && r.sim) {
    const byHex = Object.fromEntries(state.map.hexes.map((h) => [h.id, h]));
    for (const st of r.sim.steps) {
      const h = byHex[st.hex];
      if (!h || st.result !== "ok") continue;
      s += `<circle cx="${h.x}" cy="${h.y}" r="0.2" fill="#fff" stroke="${DRAFT}" stroke-width="0.04"/>`;
      s += text(h.x, h.y + 0.07, st.mp_left, 0.19, `fill="${DRAFT}" font-weight="700"`);
    }
    if (!E.picking) {
      for (const n of E.next) {
        const ok = n.result === "ok";
        const label = n.result === "river_block" ? "river" : n.result === "impassable" ? "✕"
          : `${n.cost}${n.assumed ? "*" : ""}`;
        s += `<circle cx="${n.x}" cy="${n.y}" r="0.24" fill="${ok ? "#ffffff" : "#fde8e8"}" fill-opacity="0.9"
                stroke="${ok ? "#2d7a2d" : "#b03030"}" stroke-width="0.03" stroke-dasharray="0.05 0.04"/>`;
        s += text(n.x, n.y + 0.07, label, label.length > 3 ? 0.13 : 0.19,
          `fill="${ok ? "#2d7a2d" : "#b03030"}" font-weight="700"`);
      }
    }
  }
  $("gEdit").innerHTML = s;
  $("gEdit").setAttribute("pointer-events", "none");
}

// Group map items that would sit on top of each other on screen (within px pixels at the current zoom), so
// zoomed out they share one marker instead of piling up; close in they separate again.
function nearOnScreen(items, px) {
  const pxPerUnit = (svg.clientWidth || 1000) / (state.vb ? state.vb.w : 40), reach = px / pxPerUnit;
  const groups = [];
  for (const it of items) {
    const g = groups.find((gr) => Math.hypot(gr[0].x - it.x, gr[0].y - it.y) < Math.max(reach, 0.01));
    g ? g.push(it) : groups.push([it]);
  }
  return groups;
}

// Other clans' units where we last knew them, named from contacts.csv (or "Clan 0123" for clans we only sighted).
// a position from an earlier turn says when; older ones fade (see drawPeople)
function personLabel(p) {
  return (p.name ? `${p.name} · ${p.unit}` : `Clan ${p.clan} · ${p.unit}`) + (p.approx ? ` · somewhere in ${p.hex.slice(0, 2)}` : "")
    + (p.age > 0 ? ` · ${p.turn}` : "");
}
function personFade(ps) { return Math.min(...ps.map((p) => p.age ?? 99)) >= 2 ? 0.55 : 1; }   // 2+ turns old
// Zoomed out (hexes too small to read), every position is a small solid hex that keeps its size on screen:
// black for our units, blue for people we have a truce with, red for everyone else, each with a small name
// label. Close in, other people get a name box over their outlined hex. Dashed = only their word.
const US = "#111111", TRUCE = "#1f5fbf", OTHERS = "#c0201a", FAR_PX = 9;   // below 9 px per unit = zoomed out
function pxPerUnit() { return (svg.clientWidth || 1000) / (state.vb ? state.vb.w : 40); }
function personColour(p) { return p.truce ? TRUCE : OTHERS; }
function hexMarker(x, y, fill, ppu, dashed) {
  const r = 7 / ppu;
  return `<polygon points="${poly(x, y, r)}" fill="${fill}" stroke="#fff" stroke-width="${(1.6 / ppu).toFixed(4)}"
            ${dashed ? `stroke-dasharray="${(3 / ppu).toFixed(4)} ${(2 / ppu).toFixed(4)}"` : ""}/>`;
}
function farLabel(x, y, label, colour, ppu) {
  return text(x + 11 / ppu, y + 4 / ppu, label, (12 / ppu).toFixed(4),
    `text-anchor="start" fill="${colour}" font-weight="700" stroke="#fff" stroke-width="${(3 / ppu).toFixed(4)}" paint-order="stroke"`)
    .replace('text-anchor="middle" ', "");
}
// A name box inside a hex (close in): white text on the unit's colour, dashed when it's only their word.
function nameBox(x, y, label, colour, solid) {
  const w = label.length * 0.14 + 0.2;
  return `<g><rect x="${x - w / 2}" y="${y - 0.27}" width="${w}" height="0.3" rx="0.05" fill="${colour}"
            ${solid ? "" : `stroke="#fff" stroke-width="0.03" stroke-dasharray="0.06 0.04"`}/>` +
    text(x, y - 0.05, label, 0.2, `fill="#fff" font-weight="700"`) + `</g>`;
}
// Old map style: other tribes we know of get a red ring penned round where they may be, with "Tribe?" beside it in
// the map's red cursive. No names, so the picture can be shared. Kept between a readable and a modest size on
// screen (15-22 px lettering), however far in or out.
function oldMapRings() {
  const ppu = pxPerUnit();
  let s = "";
  for (const ps of nearOnScreen(state.map.people || [], 30)) {
    const p = ps[0], rr = seeded(p.hex + "ring"), big = p.approx ? 2.6 : 1;   // a sheet-only position: a wider ring
    // never smaller than ~40 px across on screen, never bigger than ~90 px (~220 px for a sheet-wide ring)
    const rx = Math.min(Math.max(0.95 * big, 20 / ppu), (big > 1 ? 110 : 45) / ppu), ry = rx * 0.84;
    const tilt = (rr() - 0.5) * 0.6, start = rr() * 2 * Math.PI, wob = rr() * 6, pts = [];
    for (let i = 0; i <= 44; i++) {   // once round and a little past where it started, wobbling as a hand does
      const t = start + (i / 40) * 2 * Math.PI, w = 1 + 0.06 * Math.sin(3 * t + wob) + (i > 40 ? 0.05 * (i - 40) : 0);
      const ex = Math.cos(t) * rx * w, ey = Math.sin(t) * ry * w;
      pts.push([p.x + ex * Math.cos(tilt) - ey * Math.sin(tilt), p.y + ex * Math.sin(tilt) + ey * Math.cos(tilt)]);
    }
    const fs = Math.min(Math.max(0.42, 15 / ppu), 22 / ppu), ty = p.y - ry * 0.85;
    // beside the ring on the right, or on the left if that would run off the map (past the torn edge)
    const left = state.vb && p.x + rx * 0.8 + fs * 3.4 + 40 / ppu > state.vb.x + state.vb.w;
    const tx = left ? p.x - rx * 0.8 : p.x + rx * 0.8, anchor = left ? "end" : "start";
    s += `<g class="site"><polyline points="${pts.map(([x, y]) => `${x.toFixed(3)},${y.toFixed(3)}`).join(" ")}" fill="none"
          stroke="#b01e16" stroke-width="${Math.max(0.05, 2 / ppu).toFixed(4)}" stroke-linecap="round" stroke-linejoin="round"/>
        <text x="${tx.toFixed(3)}" y="${ty.toFixed(3)}" text-anchor="${anchor}" transform="rotate(-6 ${tx.toFixed(3)} ${ty.toFixed(3)})" font-size="${fs.toFixed(4)}"
          fill="#9c1b14" style="stroke-width:${(fs * 0.22).toFixed(4)}">Tribe?</text></g>`;
  }
  return s;
}

// Old map style's site notes (a red X and a cursive note, e.g. Salt): the same size limits as the rings, so they
// read zoomed out and don't balloon zoomed in (lettering 13-22 px on screen).
function oldMapSites() {
  const ppu = pxPerUnit(), k = Math.min(Math.max(1, 13 / (0.3 * ppu)), 22 / (0.3 * ppu));
  return (state.oldSites || []).map((m) => `<g class="site" transform="translate(${m.x.toFixed(3)} ${m.y.toFixed(3)}) scale(${k.toFixed(3)})">
      <path d="M-0.14,-0.13 Q-0.01,0.0 0.15,0.13 M0.13,-0.15 Q0.02,-0.02 -0.13,0.14" fill="none" stroke="#b01e16"
            stroke-width="0.05" stroke-linecap="round"/>
      <text x="0.22" y="0.08" transform="rotate(${m.tilt.toFixed(1)})" font-size="0.3" fill="#9c1b14">${esc(m.note)}</text>
    </g>`).join("");
}

function drawPeople() {
  let s = "";
  if (oldMap()) s = oldMapSites() + (state.layers.has("people") ? oldMapRings() : "");
  if (state.layers.has("people") && !oldMap()) {
    const ppu = pxPerUnit(), all = state.map.people || [];
    for (const colour of [OTHERS, TRUCE]) {
      const mine = all.filter((p) => personColour(p) === colour);
      if (ppu < FAR_PX) {
        for (const p of mine) s += hexMarker(p.x, p.y, colour, ppu, false);   // too small to dash
        for (const ps of nearOnScreen(mine, 70)) {
          const label = ps.map((p) => (p.name || `Clan ${p.clan}`) + (p.approx ? ` (${p.hex.slice(0, 2)})` : "")).join(", ");
          s += farLabel(ps[0].x, ps[0].y, label, colour, ppu);
        }
        continue;
      }
      // close in: their hex outlined (dashed = their word) and a name box above it
      for (const p of mine) {
        s += `<polygon points="${poly(p.x, p.y, 0.94)}" fill="none" stroke="${colour}" stroke-width="0.08"
                ${p.verified ? "" : `stroke-dasharray="0.18 0.1"`}/>`;
      }
      if (!seesPastMap()) {   // close in (no world grid): the name inside their hex, like ours
        for (const ps of nearOnScreen(mine, 40)) {
          s += `<g opacity="${personFade(ps)}">` +
            nameBox(ps[0].x, ps[0].y, ps.map(personLabel).join(", "), colour, ps.some((q) => q.verified)) + `</g>`;
        }
        continue;
      }
      // the same name box as our own units (see drawUnits), in their colour, pointing down at the outlined hex
      const k = Math.max(1, 22 / (0.3 * ppu));
      for (const ps of nearOnScreen(mine, 60)) {
        const p = ps[0], seen = ps.some((q) => q.verified);
        const label = ps.map(personLabel).join(", "), w = label.length * 0.14 + 0.2;
        s += `<g opacity="${personFade(ps)}" transform="translate(${p.x.toFixed(3)} ${(p.y - 0.86).toFixed(3)}) scale(${k.toFixed(3)})">
          <path d="M-0.08,-0.06 L0,0.04 L0.08,-0.06 Z" fill="${colour}"/>
          <rect x="${-w / 2}" y="-0.36" width="${w}" height="0.3" rx="0.05" fill="${colour}" stroke="#fff" stroke-width="0.03"
                ${seen ? "" : `stroke-dasharray="0.06 0.04"`}/>`;
        s += text(0, -0.14, label, 0.2, `fill="#fff" font-weight="700"`) + `</g>`;
      }
    }
  }
  $("gPeople").innerHTML = s;
  $("gPeople").setAttribute("pointer-events", "none");
}

function drawUnits() {
  let s = "";
  if (state.layers.has("units")) {
    if (pxPerUnit() < FAR_PX && !oldMap()) {   // zoomed out: black hexes and a small label (see drawPeople)
      const ppu = pxPerUnit();
      for (const u of state.map.units) s += hexMarker(u.x, u.y, US, ppu, false);
      for (const g of nearOnScreen(state.map.units, 70)) s += farLabel(g[0].x, g[0].y, g.map((u) => u.unit).join(" + "), US, ppu);
      $("gUnits").innerHTML = s;
      $("gUnits").setAttribute("pointer-events", "none");
      return;
    }
    const here = nearOnScreen(state.map.units, 60).map((g) => ({ x: g[0].x, y: g[0].y, names: g.map((u) => u.unit) }));
    // the hex is outlined and the name box sits just above it, pointing down, so the hex itself stays visible;
    // normal size close in, and never smaller than about 22 px tall on screen
    const k = Math.max(1, 22 / (0.3 * pxPerUnit()));
    if (!seesPastMap()) {   // close in (no world grid): the hex outlined, the name inside it
      for (const u of state.map.units) {
        s += `<polygon points="${poly(u.x, u.y, 0.94)}" fill="none" stroke="${US}" stroke-width="0.08"/>`;
      }
      for (const g of here) s += nameBox(g.x, g.y, g.names.join(" + "), US, true);
      $("gUnits").innerHTML = s;
      $("gUnits").setAttribute("pointer-events", "none");
      return;
    }
    for (const u of state.map.units) {
      s += `<polygon points="${poly(u.x, u.y, 0.94)}" fill="none" stroke="${US}" stroke-width="0.08"/>`;
    }
    for (const g of here) {
      const label = g.names.join(" + "), w = label.length * 0.14 + 0.2;
      s += `<g transform="translate(${g.x.toFixed(3)} ${(g.y - 0.86).toFixed(3)}) scale(${k.toFixed(3)})">
        <path d="M-0.08,-0.06 L0,0.04 L0.08,-0.06 Z" fill="${US}"/>
        <rect x="${-w / 2}" y="-0.36" width="${w}" height="0.3" rx="0.05" fill="${US}" stroke="#fff" stroke-width="0.03"/>`;
      s += text(0, -0.14, label, 0.2, `fill="#fff" font-weight="700"`) + `</g>`;
    }
  }
  $("gUnits").innerHTML = s;
  $("gUnits").setAttribute("pointer-events", "none");
}

// ---------- history: where each unit has been over a range of turns ----------
// Two handles pick the first and last turn. Our units' tracks follow their moves hex by hex, in their tribe's
// colour (a dot where each turn ended, the turn written beside it close in); other clans' units join up the
// places they were seen (dashed where it was only a player's word), when People I know is on. Units not selected are faded, as for routes.
// The ends are kept as "" for the first and latest turn, so a range up to the latest grows when a new report comes in.
function histTurns() { return (state.meta && state.meta.turns) || []; }
function histRange() {
  const turns = histTurns(), H = state.hist;
  return [turns.includes(H.from) ? H.from : turns[0] || "", turns.includes(H.to) ? H.to : turns[turns.length - 1] || ""];
}
function fillHistory() {
  const turns = histTurns(), [from, to] = histRange();
  $("historyBox").hidden = turns.length < 2;
  for (const id of ["histFrom", "histTo"]) $(id).max = Math.max(0, turns.length - 1);
  $("histFrom").value = turns.indexOf(from);
  $("histTo").value = turns.indexOf(to);
  $("histOn").checked = state.hist.on;
  histLabel();
}
function histLabel() {
  const turns = histTurns(), [from, to] = histRange(), n = turns.indexOf(to) - turns.indexOf(from) + 1;
  $("histLabel").textContent = from === to ? `Turn ${from}` : `${from} to ${to} (${n} turns)`;
}
async function loadHistory() {
  const [from, to] = histRange();
  state.hist.tracks = state.hist.on && from
    ? (await api(`/api/history?first=${from}&last=${to}`).catch(() => ({ tracks: [] }))).tracks : [];
}
function drawHistory() {
  let s = "";
  if (state.hist.on && !oldMap()) {
    const ppu = pxPerUnit(), r = Math.max(0.12, 3.5 / ppu), close = ppu >= 30;
    const tribes = [...new Set(state.hist.tracks.filter((t) => t.own).map((t) => tribeOf(t.unit)))].sort();
    for (const t of state.hist.tracks) {
      if (!t.own && !state.layers.has("people")) continue;   // other clans: only with the People I know layer
      const c = t.own ? PALETTE[tribes.indexOf(tribeOf(t.unit)) % PALETTE.length] : OTHERS;
      const fade = t.own ? (isFaded(t.unit) ? FADED : 1) : 0.8;
      const line = t.turns.flatMap((x) => x.path);
      s += `<g opacity="${fade}"><title>${esc(t.unit + (t.name ? " (" + t.name + ")" : ""))}: ${esc(t.turns[0].turn)} to ${esc(t.turns[t.turns.length - 1].turn)}</title>`;
      if (line.length > 1) {
        s += `<polyline points="${pts(line)}" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" opacity="0.7"/>`;
        s += `<polyline points="${pts(line)}" fill="none" stroke="${c}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"
                ${t.own || t.turns.every((x) => x.verified) ? "" : `stroke-dasharray="6 4"`}/>`;
      }
      t.turns.forEach((x, i) => {
        const [px, py] = x.path[x.path.length - 1], last = i === t.turns.length - 1;
        s += `<circle cx="${px}" cy="${py}" r="${(last ? 1.5 : 1) * r}" fill="${i === 0 ? "#fff" : c}" stroke="${c}" stroke-width="1.5" vector-effect="non-scaling-stroke"><title>${esc(t.unit)} ${esc(x.turn)}: ${esc(x.hex)}</title></circle>`;
        if (close) s += text(px + 0.32, py + 0.42, x.turn, 0.22, `fill="${c}" font-weight="700" stroke="#fff" stroke-width="0.06" paint-order="stroke"`);
      });
      s += `</g>`;
    }
  }
  $("gHistory").innerHTML = s;
}
async function histChanged() {
  const H = state.hist;
  store("histFrom", H.from); store("histTo", H.to);
  histLabel();
  await loadHistory();
  drawHistory();
}

function drawSel() {
  // No selection outline for now (nothing acts on a selected hex yet). Clicking still fills the Hex panel.
  $("gSel").innerHTML = "";
}

// ---------- side panel ----------
function renderLayers() {
  $("layers").innerHTML = LAYERS.map(([k, name]) =>
    `<label><input type="checkbox" data-layer="${k}" ${state.layers.has(k) ? "checked" : ""}> ${name}</label>`).join("");
}

async function select(id, recentre) {
  state.sel = id;
  drawSel();
  if (recentre) centreOn(state.byId[id]);
  try {
    state.detail = await api(`/api/hex/${encodeURIComponent(id)}` + q(state.asOf));
    renderDetail(state.detail);
  } catch (e) {
    $("detail").textContent = e.message;
  }
}

function renderDetail(d) {
  const unv = d.terrain && d.verified === false;
  const [kcls, klabel, kwhy] = KNOWLEDGE[d.knowledge] || KNOWLEDGE.none;
  let h = `<div><b style="font-size:16px">${esc(d.hex)}</b> <span class="badge ${kcls}">${esc(klabel)}</span></div>`;
  h += `<div class="muted" style="font-size:12px">${esc(kwhy)}${d.terrain_how && !unv ? ` Terrain: ${esc(HOW_LABEL[d.terrain_how] || d.terrain_how)}.` : ""}</div>`;
  h += `<div style="margin-top:4px">${d.terrain ? `${esc(terrainName(d.terrain))} (${esc(d.terrain)})` : "Terrain unknown"}`;
  if (unv) h += ` <span class="muted">from ${esc(d.terrain_provider || "unknown")}, as of ${esc(d.terrain_as_of || "unknown turn")}</span>`;
  h += `</div><dl>`;
  h += `<dt>Last known</dt><dd>${esc(d.last_known || "—")}</dd>`;
  h += `<dt>We last saw it</dt><dd>${esc(d.last_seen || "never")}</dd>`;
  h += `<dt>Last scouted</dt><dd>${esc(d.last_scouted || "never")}</dd>`;
  if (d.first_seen) h += `<dt>First seen</dt><dd>${esc(d.first_seen)}</dd>`;
  h += `</dl>`;
  const chips = [...d.resources.map((r) => [nice(r), ""]), ...d.specials.map((r) => [r, ""]), ...d.items.map((r) => [r + " (item)", ""])];
  for (const u of d.unverified) if (["resource", "special"].includes(u.kind)) chips.push([nice(u.value) + "?", "unv"]);
  if (chips.length) h += `<div class="chips">${chips.map(([c, cls]) => `<span class="${cls}">${esc(c)}</span>`).join("")}</div>`;
  if (d.sightings.length) h += `<p>Units seen: ${d.sightings.map((s) => `${esc(s.unit)} (${esc(s.turn)})`).join(", ")}</p>`;
  const sides = Object.entries(d.hexsides);
  if (sides.length) h += `<p>Hexsides: ${sides.map(([k, v]) => `${k} ${v.join(", ")}`).join("; ")}</p>`;
  if (d.units_here.length) h += `<p>Our units here: <b>${d.units_here.map(esc).join(", ")}</b></p>`;
  if (d.conflicts.length) h += `<p class="warn">Conflict: our reports also say ${d.conflicts.map(esc).join(", ")}</p>`;
  if (d.unverified.length) {
    h += `<h2>From other players (unverified)</h2><ul class="unv-list">`;
    h += d.unverified.map((u) => `<li>${esc(u.kind)} <b>${esc(nice(u.value))}</b> from ${esc(u.provider || "unknown")}, as of
      ${esc(u.as_of || "unknown turn")}<br><span class="muted">received ${esc(u.received || "?")}${u.note ? " · " + esc(u.note) : ""}</span></li>`).join("");
    h += `</ul>`;
  }
  if (state.plan && state.layers.has("plan")) {
    const through = [];
    for (const r of state.plan.routes) {
      const step = r.sim && r.sim.steps.find((s) => s.hex === d.hex);
      if (step) through.push(`${esc(r.name)}${r.draft ? " (editing)" : ""}: step ${step.n}, ${step.result === "ok" ? `${step.mp_left} MP left` : esc(RESULT[step.result])}`);
    }
    if (through.length || d.knowledge !== "full") {
      // Only a scout party entering the hex gets resources; a tribe passing through or a glimpse gets terrain only.
      let gain = "Not reached by this plan.";
      const steps = state.plan.routes.flatMap((r) => (r.sim ? r.sim.steps.filter((s) => s.hex === d.hex).map((s) => [r, s]) : []));
      if (steps.some(([r, s]) => r.kind === "scout" && s.result === "ok")) gain = "<b>Scouted by this plan:</b> full details expected.";
      else if (steps.some(([r, s]) => s.result === "ok" || s.result === "mp_block" || s.result === "impassable")) {
        gain = "<b>Terrain only</b> from this plan (passed through or glimpsed): still needs scouting.";
      }
      if (d.knowledge === "full") gain = "";   // already fully known: nothing to gain or lose
      if (gain || through.length) h += `<h2>This hex in plan ${esc(state.plan.turn)}</h2><div>${gain}${through.length ? "<br>" + through.join("<br>") : ""}</div>`;
    }
  }
  if (d.history.length) {
    h += `<details><summary>History (${d.history.length})</summary><table><tr><th>Turn</th><th>Source</th><th>Fact</th><th>How</th><th>Conditions</th></tr>`;
    h += d.history.map((o) => `<tr><td>${esc(o.turn || "—")}</td><td>${esc(o.unit || (o.provider ? o.provider : o.source_kind))}
      ${o.party && o.party !== o.source_kind ? `<span class="muted">${esc(o.party)}</span>` : ""}</td>
      <td>${esc(o.kind)} ${esc(o.value)}</td><td>${esc(o.how)}</td>
      <td>${esc([o.season, o.weather].filter(Boolean).join(", ") || "—")}</td></tr>`).join("");
    h += `</table></details>`;
  }
  $("detail").innerHTML = h;
  $("detail").classList.remove("muted");
}

function renderPlan() {
  const P = state.plan;
  $("planBox").hidden = !P;
  if (!P) return;
  $("planTitle").textContent = planLabel(P.turn, P.variant);
  $("deletePlan").classList.remove("armed");
  $("deletePlan").textContent = "Delete plan";
  const c = P.coverage;
  let h = "";
  if (!P.routes.length) {
    h = `<p class="muted">No routes yet. Use + Unit move or + Scout party above.</p>`;
  } else {
    h = `<p>${c.entered} hexes entered: ` +
      Object.entries(c.counts).map(([k, v]) => `<b>${v}</b> ${esc(k.replace("_", " "))}`).join(", ") +
      `; <b>${c.new_glimpses.length}</b> new glimpses.</p>`;
    if (P.overlap.length) h += `<p><span style="color:#8e44ad">■</span> Scout overlap: ${P.overlap.map((o) => esc(o.id)).join(", ")}</p>`;
    const row = (r, label) => {
      // only notes worth reading: errors, rivers, impassable terrain, follow directions (running out of MP
      // is how every scout route ends, so it isn't repeated on each row)
      const flag = r.error ? `<div class="warn">${esc(r.error)}</div>`
        : (r.notable_stop ? `<div class="muted">${esc(r.notable_stop)}</div>` : "");
      const auto = r.kind === "move" ? `<button class="rowbtn" data-auto="${esc(r.unit)}" title="Auto-plan ${esc(r.unit)}'s scout parties from where this move ends (keeps routes already planned)">Auto</button>` : "";
      const edit = state.edit ? "" : `${auto}<button class="rowbtn" data-edit="${esc(r.name)}">Edit</button>
        <button class="rowbtn danger" data-del="${esc(r.name)}" title="Delete this route (click twice)">✕</button>`;
      // MP used: exact when every hex's cost is known, otherwise the known part as a minimum
      const mp = r.mp == null ? "—" : r.assumed
        ? `<span title="at least: the cost of hexes with unknown terrain isn't counted">≥${r.known_cost}/${r.mp}</span>`
        : `${r.mp_used}/${r.mp}`;
      const edge = `style="border-left:4px solid ${routeColour(r)}"`;   // same colour as the route on the map
      let t = `<tr class="route ${r.draft ? "draft" : ""} ${state.hiRoute === r.name ? "on" : ""} ${r.kind === "scout" && isFaded(r.unit) && !r.draft ? "faded" : ""}" data-route="${esc(r.name)}"><td ${edge}>${esc(label)}${r.draft ? " ✎" : ""}</td>
        <td>${esc(r.orders)}</td><td>${esc(r.end || "—")}</td><td>${mp}</td><td class="btns">${edit}</td></tr>`;
      if (flag) t += `<tr class="flag"><td colspan="5" ${edge}>${flag}</td></tr>`;
      return t;
    };
    const head = `<tr><th>Route</th><th>Orders</th><th>Ends</th><th title="MP used of the total; ≥ means at least (unknown terrain not counted)">MP</th><th></th></tr>`;
    // Unit moves (tribes and elements) first, then scouting grouped by tribe: an element's parties sit with
    // its parent tribe because they share that tribe's 8 scouting parties.
    const moves = P.routes.filter((r) => r.kind === "move");
    if (moves.length) {
      h += `<h3 class="group">Unit moves</h3><table>${head}${moves.map((r) => row(r, r.unit)).join("")}</table>`;
    }
    const tribeOf = (u) => u.slice(0, 4);
    const groups = {};
    for (const r of P.routes.filter((x) => x.kind === "scout")) (groups[tribeOf(r.unit)] ||= []).push(r);
    for (const tribe of Object.keys(groups).sort()) {
      const rs = groups[tribe].sort((a, b) => a.unit.localeCompare(b.unit) || a.party - b.party);
      const units = [...new Set(rs.map((r) => r.unit))];
      const over = rs.length > 8;
      const with_ = units.length > 1 || units[0] !== tribe ? ` <i class="muted">(${units.map(esc).join(", ")})</i>` : "";
      h += `<h3 class="group" data-tribe="${esc(tribe)}" title="Show ${esc(tribe)} and its elements"><b>${esc(tribe)} scouting${with_}</b>
        <span class="${over ? "warn" : "muted"}">${rs.length}/8 parties${over ? ": too many" : ""}</span>${state.edit ? ""
        : `<button class="rowbtn danger delall" data-delgroup="${esc(tribe)}" title="Delete all of ${esc(tribe)}'s scouting, elements included (click twice)">✕ All</button>`}</h3>`;
      h += `<table>${head}${rs.map((r) => row(r, r.unit === tribe ? `Party ${r.party}` : `${r.unit} party ${r.party}`)).join("")}</table>`;
    }
    if (!state.edit) {
      h += `<div class="row"><button id="exportPlan">Export paste-in</button><span id="exportMsg" class="muted"></span></div>`;
    }
  }
  $("planSummary").innerHTML = h;
}

function renderLegend() {
  // grouped: the terrain on the map now (as shown: others' hexes only with their layer on), then how much we know
  // of each hex, lines, marks, people, the world grid, and the plan's own symbols
  const present = [...new Set(state.map.hexes.map((h) => shownTerrain(h)[0]).filter(Boolean))].sort();
  const group = (title) => `<div class="lgroup">${title}</div>`;
  const sw = (fill, extra = "") => `<svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><rect x="1" y="1" width="16" height="12" fill="${fill}" stroke="#999" ${extra}/></svg>`;
  let h = present.length ? group("Terrain") : "";
  h += present.map((c) => {
    const t = state.terrain[c] || {};
    return `<div>${sw(colour(c))}<b>${esc(c)}</b> ${esc(t.name || "")}${t.mp ? ` <span class="muted">${t.mp} MP</span>` : ""}</div>`;
  }).join("");
  if (present.some((c) => (state.terrain[c] || {}).mp)) h += `<div class="muted lnote">MP is for fair weather. Light rain, snow or wind adds 1 MP a hex; heavy rain or snow adds 2.</div>`;
  h += group("Map Knowledge") + `<div>${sw("#ececec")}Unknown</div>`;
  h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><defs><pattern id="lh" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="#fff" stroke-width="1.5"/></pattern></defs><rect x="1" y="1" width="16" height="12" fill="#6b7a1e" stroke="#999"/><rect x="1" y="1" width="16" height="12" fill="url(#lh)"/></svg>Only glimpsed: terrain known, resources not checked</div>`;
  h += `<div>${sw("#c8ec8f")}Plain fill: fully scouted</div>`;
  h += `<div>${sw("#bbbbbb")}Greyer = scouted over a year ago</div>`;
  if (state.layers.has("others")) h += `<div>${sw("#f3d9bf", `stroke="${UNV}" stroke-dasharray="3 2" stroke-width="1.5"`)}Unverified (other players), marked ?</div>`;
  h += group("Lines and marks");
  h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><line x1="1" y1="7" x2="17" y2="7" stroke="#3b7dd8" stroke-width="3"/><rect x="6" y="4" width="6" height="6" fill="#c49a5a" stroke="#333"/></svg>River · ford</div>`;
  h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><line x1="1" y1="7" x2="17" y2="7" stroke="#222" stroke-width="2" stroke-dasharray="4 2.5"/></svg>Map sheet border</div>`;
  h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><rect x="1.5" y="1.5" width="15" height="11" fill="none" stroke="${SPECIAL}" stroke-width="2"/><rect x="4" y="4" width="10" height="6" fill="none" stroke="${SPECIAL}" stroke-width="1"/></svg>★ special (town, encounter…) · ⚒ resource; dashed = unverified</div>`;
  if (state.layers.has("people") && (state.map.people || []).length) {
    h += group("People");
    const box = (c) => `<svg width="16" height="12" style="vertical-align:-1px;margin-right:3px"><rect x="1" y="1" width="14" height="10" rx="2" fill="${c}"/></svg>`;
    h += `<div>${box(TRUCE)}truce ${box(OTHERS)}other clans ${box(US)}us; dashed = their word</div>`;
  }
  if (state.layers.has("world")) {
    h += group("World grid") + `<div>Climate: ` + Object.entries(CLIMATE_TINT).map(([k, c]) =>
      `<span style="white-space:nowrap">${sw(c)}${esc(k)}</span>`).join(" ") + `</div>`;
  }

  if (state.plan || state.edit) h += group("Plan");
  if (state.plan) {
    h += `<div>${sw("#c4c4c4")}Will be scouted by this plan</div>`;
    h += `<div>${sw("none", `stroke="#8e44ad" stroke-width="2"`)}Scout overlap (shared first steps not shown)</div>`;
    h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><line x1="1" y1="7" x2="12" y2="7" stroke="#333" stroke-width="2" stroke-dasharray="3 2"/><polygon points="17,7 11,3 11,11" fill="#333"/></svg>Follow order: expected first direction</div>`;
  }
  if (state.edit) {
    h += `<div><svg width="18" height="14" style="vertical-align:-2px;margin-right:6px"><circle cx="9" cy="7" r="6" fill="#fff" stroke="#2d7a2d" stroke-dasharray="2 1.5"/></svg>Next step: MP it costs (* = assumed); red = can't</div>`;
  }
  $("legend").innerHTML = h;
}

// Movement cost of a terrain code, for the hover text: from the rules, estimated, or not known yet.
function moveCost(code) {
  const t = state.terrain[code];
  if (!t) return "";
  // the planner moves land units only: water stops them, but ships cross it (Mandate 10.6-10.7)
  if (["O", "UW", "L"].includes(code)) return "By ship only (land units can't enter)";
  if (t.impassable) return "Impassable on land (except through a pass)";
  if (t.mp == null) return "Move: cost unknown";
  return t.mp_source === "estimate" ? `Move: ~${t.mp} MP (estimate)` : `Move: ${t.mp} MP`;   // rules or seen in reports
}

function tipText(h) {
  const [t, unv] = shownTerrain(h);
  const lines = [`${h.id} · ${t ? terrainName(t) + (unv ? " (unverified)" : "") : "unknown"}`];
  if (t) lines.push(moveCost(t));
  // Fully scouted is the norm, so only the exceptions are called out.
  const level = unv && !state.layers.has("others") ? "none" : h.knowledge;
  if (level === "terrain" || level === "reported") lines.push(KNOWLEDGE[level][1]);
  if (state.plan && planScouts().has(h.id)) lines.push(`Scouted by plan ${state.plan.turn}`);
  const sp = [...(h.specials || []), ...(state.layers.has("others") ? (h.unverified_specials || []).map((x) => x + " (unverified)") : [])];
  if (sp.length) lines.push(`★ Special: ${sp.join(", ")}`);
  const rs = [...(h.resources || []).map(nice), ...(state.layers.has("others") ? (h.unverified_resources || []).map((x) => nice(x) + " (unverified)") : [])];
  if (rs.length) lines.push(`⚒ Resource: ${rs.join(", ")}`);
  // Last known: the newest information from anyone; marked when it is only other players' word.
  const others = state.layers.has("others") && h.has_unverified && h.last_known !== h.last_seen;
  const when = state.layers.has("others") ? h.last_known : h.last_seen;
  if (when) lines.push(`Last known: ${when}${others ? " (unverified)" : ""}`);
  else if (others) lines.push("Last known: unknown turn (unverified)");
  if (state.layers.has("people")) {
    for (const p of (state.map.people || []).filter((q) => q.hex === h.id)) {
      const who = p.name ? `${p.name} (Clan ${p.clan}${p.relation ? ", " + p.relation : ""})` : `Clan ${p.clan}`;
      lines.push(`⚑ ${who}: ${p.unit} here as of ${p.turn || "an unknown turn"}${p.verified ? " (seen by our scouts)" : `, their word (${p.provider || "unverified"})`}`);
    }
  }
  if (state.edit && state.edit.picking) lines.push("Click to set the start here");
  else if (state.edit) {
    const r = state.plan && state.plan.routes.find((x) => x.draft);
    const hexes = r && r.sim ? r.sim.steps.map((s) => s.hex) : [];
    if (r && r.sim && hexes.length && h.id === r.sim.start) lines.push("Click to clear the route");
    else if (hexes.length && h.id === hexes[hexes.length - 1]) lines.push("Click to remove this step");
    else if (hexes.includes(h.id)) lines.push("Click to cut the route back to here");
  }
  return lines.join("\n");
}

// ---------- plan editor ----------
function planMsg(text, warn) {
  const el = $("planMsg");
  el.hidden = !text;
  el.className = "msg" + (warn ? " warn" : "");
  el.innerHTML = text || "";
}
function droppedText(n, unit) {
  return n ? ` Removed ${n} of ${esc(unit)}'s scout route${n === 1 ? "" : "s"}: they left from the old move's end.` : "";
}
function draftFromForm() {
  const kind = $("eKind").value;
  return {
    unit: $("eUnit").value.trim(), kind,
    party: kind === "scout" && $("eParty").value ? Number($("eParty").value) : null,
    orders: $("eOrders").value.toUpperCase().split(/[\s,]+/).filter(Boolean),
    start: $("eStart").value.trim() || null, mover: $("eMover").value || null,
    scouts: Number($("eScouts").value) || 2, horses: Number($("eHorses").value) || 0, mission: $("eMission").value,
  };
}

function usualMover(unit) {
  return ((state.meta && state.meta.unit_movers) || {})[unit] || "foot";
}

async function startEdit(draft, replace) {
  if (!state.planTurn) return;
  state.edit = { draft, replace, next: [], picking: false, error: "" };
  state.hiRoute = null;
  fillEditForm();
  $("editBox").hidden = false;
  $("editIdle").hidden = true;
  $("editForm").hidden = false;
  $("editBox").scrollIntoView({ block: "start", behavior: "smooth" });
  await refreshMap();   // wider margin to click into
  preview();
}

function fillEditForm() {
  const d = state.edit.draft;
  // the unit: one of ours from the list, or Other… to type one (e.g. a tribe or element made this turn)
  const units = unitChoices(), known = !d.unit || units.includes(d.unit);
  $("eUnitPick").innerHTML = units.map((u) => `<option ${u === d.unit ? "selected" : ""}>${esc(u)}</option>`).join("") +
    `<option value="__other" ${known ? "" : "selected"}>Other…</option>`;
  $("eUnit").value = d.unit || "";
  $("eUnit").hidden = known;
  $("eKind").value = d.kind;
  $("eParty").innerHTML = `<option value="">next free</option>` +
    [1, 2, 3, 4, 5, 6, 7, 8].map((p) => `<option ${p === d.party ? "selected" : ""}>${p}</option>`).join("");
  const usual = usualMover(d.unit);
  $("eUsualL").hidden = d.kind !== "move";
  $("eUsual").checked = usual === "mounted";
  const movers = d.kind === "move" ? [["", `${usual} (unit's usual)`], ["mounted", "mounted"], ["foot", "foot"]]
    : [["", "scout (default)"], ["scout", "scout"], ["mounted", "mounted"], ["foot", "foot"]];
  $("eMover").innerHTML = movers.map(([v, l]) => `<option value="${v}" ${v === (d.mover || "") ? "selected" : ""}>${l}</option>`).join("");
  $("eStart").value = d.start || "";
  $("eScouts").value = d.scouts ?? 2;
  $("eHorses").value = d.horses ?? 2;
  $("eMission").value = d.mission || "PATROL";
  $("eOrders").value = (Array.isArray(d.orders) ? d.orders : String(d.orders || "").split(",")).filter(Boolean).join(",");
  $("ePartyL").style.visibility = d.kind === "scout" ? "visible" : "hidden";
  $("eScoutRow").hidden = d.kind !== "scout";
  $("eDelete").hidden = !state.edit.replace;
  $("eDelete").classList.remove("armed");
  $("eDelete").textContent = "Delete";
}

let previewSeq = 0;
async function preview(extendTo) {
  const E = state.edit;
  if (!E) return;
  E.draft = draftFromForm();
  if (!E.draft.unit) return;   // Other… picked, nothing typed yet
  if (/^\d{4}/.test(E.draft.unit) && !isSelected(E.draft.unit)) {
    state.selected = tribeOf(E.draft.unit);   // the tribe being planned is always the one shown
    store("selected", state.selected);
    renderUnits();
  }
  const seq = ++previewSeq;
  try {
    const res = await api(`/api/plan/${state.planTurn}/preview` + pq(),
      { json: { draft: E.draft, replace: E.replace, extend_to: extendTo || null } });
    if (seq !== previewSeq || !state.edit) return;   // a newer preview is on its way
    state.plan = res;
    E.error = res.draft.error || "";
    E.next = res.draft.next;
    E.drops = res.draft.drops_scouts || 0;
    E.draft.orders = res.draft.orders.split(",").filter(Boolean);
    E.draft.party = E.draft.kind === "scout" ? res.draft.party : null;
    $("eOrders").value = E.draft.orders.join(",");
    E.partyShown = res.draft.party;
    // a route that reaches unknown country needs those hexes drawn
    const missing = res.routes.some((r) => r.sim && r.sim.steps.some((s) => !state.byId[s.hex]));
    if (missing) await refreshMap();
  } catch (e) {
    if (seq !== previewSeq) return;
    E.error = e.message;
  }
  drawAll();
  renderPlan();
  renderEditor();
  renderLegend();
}

async function refreshMap() {
  // While editing, draw a wider margin of unknown hexes (room to click into) plus the draft's own hexes.
  const params = new URLSearchParams();
  if (state.asOf) params.set("as_of", state.asOf);
  if (state.planTurn) params.set("plan", state.planTurn);
  if (state.planTurn && state.planVariant !== "main") params.set("variant", state.planVariant);
  if (state.edit) {
    params.set("pad", "5");
    const r = state.plan && state.plan.routes.find((x) => x.draft);
    if (r && r.sim) params.set("extra", [r.sim.start, ...r.sim.steps.map((s) => s.hex)].join(","));
  }
  state.map = await api("/api/map?" + params);
  state.byId = Object.fromEntries(state.map.hexes.map((h) => [h.id, h]));
}

function renderEditor() {
  const on = !!state.planTurn;
  $("editBox").hidden = !on;
  if (!on) return;
  $("editTurn").textContent = state.planTurn;
  $("editIdle").hidden = !!state.edit;
  $("editForm").hidden = !state.edit;
  if (!state.edit) return;
  const E = state.edit, r = state.plan && state.plan.routes.find((x) => x.draft);
  let st = "";
  if (E.error) st = `<span class="err">${esc(E.error)}</span>`;
  else if (r && r.sim) {
    const used = r.assumed ? `at least ${r.known_cost}` : `${r.mp_used}`;
    st = `<b>${esc(r.name)}</b> from ${esc(r.sim.start)} → <b>${esc(r.end)}</b>, ${used} of ${r.mp} MP used (${esc(r.mover)})`;
    if (r.notable_stop) st += `<br><span class="err">${esc(r.notable_stop)}</span>`;
    if (E.drops) st += `<br><span class="err">Saving will remove ${esc(E.draft.unit)}'s ${E.drops} scout route${E.drops === 1 ? "" : "s"} (they leave from where this move ends).</span>`;
    if (r.sim.unused_orders.length) st += `<br><span class="muted">never reached: ${esc(r.sim.unused_orders.join(","))}</span>`;
  }
  if (E.picking) st = `<b>Click a hex on the map to set the start.</b><br>` + st;
  $("eStatus").innerHTML = st;
  $("ePick").classList.toggle("on", E.picking);
  if (E.draft.kind === "scout" && E.partyShown && !$("eParty").value) {
    $("eParty").options[0].textContent = `next free (${E.partyShown})`;
  }
  let t = "";
  if (r && r.sim && r.sim.steps.length) {
    t = `<table><tr><th>#</th><th>Order</th><th>Hex</th><th>Terrain</th><th>MP</th><th>Left</th></tr>`;
    t += r.sim.steps.map((s) => `<tr><td>${s.n}</td><td>${esc(s.order)}</td><td>${esc(s.hex)}</td><td>${esc(s.terrain || "?")}</td>
      <td>${s.cost ?? "—"}${s.assumed ? "*" : ""}</td><td class="${s.result === "ok" ? "" : "stop"}">${s.result === "ok" ? s.mp_left : esc(RESULT[s.result])}</td></tr>`).join("");
    t += `</table>`;
  }
  $("eSteps").innerHTML = t;
}

function addOrder(o) {
  const E = state.edit;
  if (!E) return;
  const orders = $("eOrders").value.toUpperCase().split(/[\s,]+/).filter(Boolean);
  orders.push(o);
  $("eOrders").value = orders.join(",");
  preview();
}

async function saveEdit() {
  const E = state.edit;
  if (!E) return;
  E.draft = draftFromForm();
  try {
    const res = await api(`/api/plan/${state.planTurn}/route` + pq(), { json: { draft: E.draft, replace: E.replace } });
    state.edit = null;
    planMsg(res.dropped_scouts ? `Saved ${esc(E.draft.unit)}'s move.${droppedText(res.dropped_scouts, E.draft.unit)}` : "",
      !!res.dropped_scouts);
    await loadAll(true);
  } catch (e) {
    E.error = e.message;
    renderEditor();
  }
}

async function cancelEdit() {
  state.edit = null;
  await loadAll(true);
}

async function deleteEdit() {
  const E = state.edit, btn = $("eDelete");
  if (!E || !E.replace) return;
  if (!btn.classList.contains("armed")) {   // two clicks to delete, no browser dialogs
    btn.classList.add("armed");
    btn.textContent = "Click again to delete";
    return;
  }
  const [unit, kind, party] = E.replace;
  try {
    const res = await api(`/api/plan/${state.planTurn}/route?unit=${encodeURIComponent(unit)}&kind=${kind}` +
      (party ? `&party=${party}` : "") + `&variant=${encodeURIComponent(state.planVariant)}`, { method: "DELETE" });
    state.edit = null;
    planMsg(res.dropped_scouts ? `Deleted ${esc(unit)}'s move.${droppedText(res.dropped_scouts, unit)}` : "",
      !!res.dropped_scouts);
    await loadAll(true);
  } catch (e) {
    E.error = e.message;
    renderEditor();
  }
}

function unitChoices() {
  const units = new Set(state.map.units.map((u) => u.unit));
  if (state.plan) state.plan.routes.forEach((r) => units.add(r.unit));
  return [...units].sort();
}
function newDraftUnit() {
  // a new route is for the tribe or element selected in Showing; else the first unit
  const units = unitChoices(), sel = selected();
  return units.includes(sel) ? sel : units[0] || state.meta.clan;
}

// ---------- pan / zoom ----------
const svg = $("map");

function setVB(vb) {
  state.vb = vb;
  svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  svg.classList.toggle("near", vb.w < 30);
  if (state.map) { drawHistory(); drawUnits(); drawPeople(); }   // units, people and tracks keep their size on screen
  svg.classList.toggle("far", vb.w > 160);
  // crossing the zoom-out threshold ticks the World grid layer on; zooming back in ticks it off. In between it's
  // an ordinary layer, so it can be switched off while zoomed out.
  const wide = !!seesPastMap();
  if (wide !== state.wasWide) {
    state.wasWide = wide;
    wide ? state.layers.add("world") : state.layers.delete("world");
    const box = document.querySelector('input[data-layer="world"]');
    if (box) box.checked = wide;
    if (state.map) { drawWorld(); renderLegend(); }
  }
}

function fit() {
  const hs = state.map.hexes;
  if (!hs.length) { setVB({ x: -10, y: -10, w: 20, h: 20 }); return; }
  const xs = hs.map((h) => h.x), ys = hs.map((h) => h.y);
  const x0 = Math.min(...xs) - 1.2, x1 = Math.max(...xs) + 1.2, y0 = Math.min(...ys) - 1.2, y1 = Math.max(...ys) + 1.2;
  state.fitW = x1 - x0;   // the zoom the world grid compares against
  if (oldMap()) {
    // the old map only draws known ground (the unknown margin is bare parchment), so frame that, leaving room for
    // the torn edge, and above it for the title and compass
    const known = hs.filter((h) => { const [t, unv] = shownTerrain(h); return t; });
    if (known.length) {
      const kx = known.map((h) => h.x), ky = known.map((h) => h.y);
      const a0 = Math.min(...kx) - 1.6, a1 = Math.max(...kx) + 1.6, b0 = Math.min(...ky) - 3.2, b1 = Math.max(...ky) + 1.6;
      setVB({ x: a0, y: b0, w: a1 - a0, h: b1 - b0 });
      return;
    }
  }
  setVB({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
}

function toSvg(cx, cy) {
  const p = svg.createSVGPoint();
  p.x = cx; p.y = cy;
  return p.matrixTransform(svg.getScreenCTM().inverse());
}

function zoomAt(f, cx, cy) {
  const vb = state.vb, p = toSvg(cx, cy);
  // zoom out as far as the whole world (plus a margin), in as far as a few hexes
  const b = state.world && state.world.box, maxW = b ? (b[2] - b[0]) * 1.4 : 400;
  const w = Math.min(maxW, Math.max(3, vb.w * f));
  f = w / vb.w;
  setVB({ x: p.x - (p.x - vb.x) * f, y: p.y - (p.y - vb.y) * f, w, h: vb.h * f });
}

function centreOn(h) {
  if (!h) return;
  const w = Math.min(state.vb.w, 14), r = state.vb.h / state.vb.w;
  setVB({ x: h.x - w / 2, y: h.y - (w * r) / 2, w, h: w * r });
}

svg.addEventListener("wheel", (e) => {
  e.preventDefault();
  zoomAt(e.deltaY < 0 ? 1 / 1.2 : 1.2, e.clientX, e.clientY);
}, { passive: false });

let drag = null;
svg.addEventListener("pointerdown", (e) => {
  drag = { x: e.clientX, y: e.clientY, vb: { ...state.vb }, k: svg.getScreenCTM().a, moved: false, target: e.target };
  svg.setPointerCapture(e.pointerId);
});
svg.addEventListener("pointermove", (e) => {
  const tip = $("tip");
  if (drag) {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) { drag.moved = true; svg.classList.add("dragging"); tip.hidden = true; }
    if (drag.moved) setVB({ ...drag.vb, x: drag.vb.x - dx / drag.k, y: drag.vb.y - dy / drag.k });
    return;
  }
  const id = e.target.dataset && e.target.dataset.id;
  const h = id && state.byId[id];
  if (!h || oldMap()) { tip.hidden = true; return; }   // the old map is a picture: no hover details
  tip.textContent = tipText(h);
  const box = $("mapWrap").getBoundingClientRect();
  tip.style.left = `${e.clientX - box.left + 14}px`;
  tip.style.top = `${e.clientY - box.top + 14}px`;
  tip.hidden = false;
});
svg.addEventListener("pointerup", () => {
  if (drag && !drag.moved) {
    const id = drag.target.dataset && drag.target.dataset.id;
    if (id) hexClicked(id);
  }
  drag = null;
  svg.classList.remove("dragging");
});
svg.addEventListener("pointerleave", () => { $("tip").hidden = true; });

function hexClicked(id) {
  const E = state.edit;
  if (!E) { select(id, false); return; }
  if (E.picking) {
    E.picking = false;
    $("eStart").value = id;
    preview();
    return;
  }
  // a hex already on the route shortens it: its last hex drops that step, an earlier one cuts the route back to
  // there, the start clears it; any other hex extends the route to it
  const r = state.plan && state.plan.routes.find((x) => x.draft);
  const orders = $("eOrders").value.toUpperCase().split(/[\s,]+/).filter(Boolean);
  if (r && r.sim && orders.length) {
    const steps = r.sim.steps;
    let keep = -1;
    if (id === r.sim.start) keep = 0;
    else {
      const k = steps.map((s) => s.hex).lastIndexOf(id);
      if (k >= 0) keep = k + 1 >= orders.length ? k : k + 1;
    }
    if (keep >= 0) {
      $("eOrders").value = orders.slice(0, keep).join(",");
      preview();
      return;
    }
  }
  preview(id);   // extend the route to this hex
}

$("zin").onclick = () => { const b = svg.getBoundingClientRect(); zoomAt(1 / 1.4, b.left + b.width / 2, b.top + b.height / 2); };
$("zout").onclick = () => { const b = svg.getBoundingClientRect(); zoomAt(1.4, b.left + b.width / 2, b.top + b.height / 2); };
$("zfit").onclick = fit;
$("zworld").onclick = fitWorld;
$("zsave").onclick = async () => {
  // the server photographs the page with this browser's settings and view (web.page_snapshot), so the picture
  // matches what's on screen in either mode
  const b = $("zsave");
  b.disabled = true;
  b.textContent = "Saving…";
  try {
    const storage = {};
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k.startsWith("tribemap.")) storage[k] = localStorage.getItem(k);
      }
    } catch (e) { /* private mode: the defaults are used */ }
    storage["tribemap.layers"] = JSON.stringify([...state.layers]);
    storage["tribemap.selected"] = JSON.stringify(state.selected);
    const box = $("mapWrap").getBoundingClientRect(), vb = state.vb;
    const res = await fetch("/api/snapshot", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ storage, vb: [vb.x, vb.y, vb.w, vb.h], width: Math.round(box.width), height: Math.round(box.height),
                             params: { plan: state.planTurn, variant: state.planVariant, asof: state.asOf } }),
    });
    if (!res.ok) throw new Error(((await res.json().catch(() => ({}))).detail) || res.statusText);
    const url = URL.createObjectURL(await res.blob()), a = document.createElement("a");
    a.href = url;
    a.download = `tribemap-${state.asOf || state.map.now || "map"}${state.planTurn ? `-plan-${state.planTurn}` : ""}${oldMap() ? "-old" : ""}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) {
    $("status").textContent = `Couldn't save the map: ${e.message}`;
  } finally {
    b.disabled = false;
    b.textContent = "Save";
  }
};

// ---------- controls ----------
$("layers").addEventListener("change", (e) => {
  const k = e.target.dataset.layer;
  if (!k) return;
  e.target.checked ? state.layers.add(k) : state.layers.delete(k);
  store("layers", [...state.layers]);
  drawAll();
  if (k === "old") fit();   // the old map frames only known ground; normal mode frames the whole map again
  if (state.detail) renderDetail(state.detail);
});
// folding sections remember whether they were open (in this browser)
for (const d of document.querySelectorAll("details.fold")) {
  d.open = recall("fold." + d.id, true);
  d.addEventListener("toggle", () => store("fold." + d.id, d.open));
}
$("histOn").onchange = () => { state.hist.on = $("histOn").checked; histChanged(); };
for (const [id, other] of [["histFrom", "histTo"], ["histTo", "histFrom"]]) {
  $(id).oninput = () => {
    // the handles can't cross: dragging one past the other pushes it along
    const a = +$(id).value, b = +$(other).value;
    if (id === "histFrom" ? a > b : a < b) $(other).value = a;
    const turns = histTurns(), f = +$("histFrom").value, t = +$("histTo").value;
    state.hist.from = f === 0 ? "" : turns[f];
    state.hist.to = t === turns.length - 1 ? "" : turns[t];
    state.hist.on = $("histOn").checked = true;   // moving the range means wanting to see it
    histChanged();
  };
}
$("unitPicker").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-select]");
  if (b && !state.edit) selectUnit(b.dataset.select);   // while editing, the edited route's tribe stays selected
});
// the picker sits on the map: don't let its clicks pan or select hexes
$("unitPicker").addEventListener("pointerdown", (e) => e.stopPropagation());
$("asof").onchange = (e) => { state.asOf = e.target.value; store("asOf", state.asOf); loadAll(true); };
$("plan").onchange = (e) => {
  if (e.target.value === "__new") {
    // plans are always for the next turn: suggest it, with the next free name (main, B, C, ...)
    const turn = state.meta.latest_turn ? nextTurn(state.meta.latest_turn) : (state.planTurn || "");
    $("newPlanTurn").value = turn;
    $("newPlanName").value = freeName(turn);
    $("newPlanFrom").textContent = state.planTurn ? planLabel(state.planTurn, state.planVariant) : "";
    $("newPlanCopy").checked = !!state.planTurn && state.plan && state.plan.routes.length > 0;
    $("newPlanCopy").disabled = !state.planTurn;
    $("newPlanErr").textContent = "";
    $("newPlan").hidden = false;
    $("newPlanName").focus();
    return;
  }
  state.edit = null;
  planMsg("");
  const [t, v] = e.target.value ? e.target.value.split("|") : ["", "main"];
  setPlan(t, v);
  loadAll(true);
};
function freeName(turn) {
  const taken = new Set((state.meta.plans || []).filter((p) => p.turn === turn).map((p) => p.variant));
  if (state.planTurn === turn) taken.add(state.planVariant);
  if (!taken.has("main")) return "main";
  for (const c of "BCDEFGHIJKLMNOPQRSTUVWXYZ") if (!taken.has(c)) return c;
  return "";
}
$("newPlan").addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = $("newPlanTurn").value.trim();
  const [y, m] = t.split("-");
  if (!/^\d{3}-\d{1,2}$/.test(t) || Number(m) < 1 || Number(m) > 12) {
    $("newPlanErr").textContent = "Turn should look like 906-06 (month 1–12)";
    return;
  }
  const turn = `${y}-${m.padStart(2, "0")}`;
  const name = $("newPlanName").value.trim() || "main";
  const taken = (state.meta.plans || []).some((p) => p.turn === turn && p.variant === name) ||
    (state.planTurn === turn && state.planVariant === name);
  if (taken) {
    $("newPlanErr").textContent = `Plan ${planLabel(turn, name)} already exists: pick another name, or choose it in the list.`;
    return;
  }
  const copyFrom = $("newPlanCopy").checked && state.planTurn === turn ? state.planVariant : null;
  try {
    const res = await api(`/api/plan/${turn}/copy`, { json: { to: name, from: copyFrom } });
    setPlan(turn, res.variant);
    $("newPlan").hidden = true;
    state.edit = null;
    planMsg(res.copied
      ? `New plan ${esc(planLabel(turn, res.variant))}: a copy of ${esc(planLabel(turn, copyFrom))} (${res.copied} routes) to vary.`
      : `New plan ${esc(planLabel(turn, res.variant))}. Add unit moves first, then scouts (or Auto-plan scouts).`);
    loadAll(true);
  } catch (err) {
    $("newPlanErr").textContent = err.message;
  }
});
$("newPlanTurn").addEventListener("change", () => {
  // copying only makes sense within the same turn
  const t = $("newPlanTurn").value.trim();
  $("newPlanName").value = freeName(t);
  const same = state.planTurn && t === state.planTurn;
  $("newPlanCopy").disabled = !same;
  if (!same) $("newPlanCopy").checked = false;
});
$("deletePlan").onclick = async () => {
  const b = $("deletePlan");
  if (!b.classList.contains("armed")) { b.classList.add("armed"); b.textContent = "Click again to delete"; return; }
  try {
    await api(`/api/plan/${state.planTurn}?variant=${encodeURIComponent(state.planVariant)}`, { method: "DELETE" });
    planMsg(`Deleted plan ${esc(planLabel(state.planTurn, state.planVariant))}.`);
    const other = (state.meta.plans || []).find((p) => !(p.turn === state.planTurn && p.variant === state.planVariant));
    setPlan(other ? other.turn : "", other ? other.variant : "main");
    state.edit = null;
    loadAll(true);
  } catch (err) {
    planMsg(esc(err.message), true);
  }
};
$("newPlanCancel").onclick = () => { $("newPlan").hidden = true; fillSelects(); };
$("exportWxx").onclick = async () => {
  // a .wxx of what the map shows, for players using Clan Cartographer or Worldographer (web.wxx_export)
  const b = $("exportWxx");
  b.disabled = true;
  try {
    const res = await fetch("/api/export-wxx", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: state.asOf || null, layers: [...state.layers] }),
    });
    await download(res, "map.wxx");
  } catch (e) {
    $("status").textContent = `Couldn't export the map: ${e.message}`;
  } finally {
    b.disabled = false;
  }
};

// ---------- project files ----------
// Save data downloads everything as one .zip (web/project.py); Open data (browser-only version) replaces
// everything with one, after the old data is backed up.
function projectMsg(text, warn) {
  const el = $("projectMsg");
  el.hidden = !text;
  el.className = warn ? "warn" : "muted";
  el.innerHTML = text || "";
}
const STALE_DAYS = 7;   // the browser-only version nags for a Save data after this long
function backupNote() {
  // The browser-only version keeps everything in this browser alone (clearing site data loses it): say so, and
  // warn when it was never saved as a project, or not for a while. The desktop copy makes its own backups.
  const el = $("backupNote");
  el.hidden = !state.meta.project_open || !state.meta.latest_turn;
  if (el.hidden) return;
  const saved = recall("projectSaved", null);
  const nag = saved === null ? " This data has not been saved yet."
    : Date.now() - saved > STALE_DAYS * 864e5 ? " This data has not been saved recently." : "";
  el.className = nag ? "warn" : "muted";
  el.textContent = "Your map is kept only in this browser." + nag;
}
$("projectSave").onclick = async () => {
  try {
    const res = await fetch("/api/project");
    const saved = await download(res, "tribemap-project.zip");
    store("projectSaved", Date.now());
    backupNote();
    projectMsg(`Saved ${esc(saved)} to your downloads.`);
  } catch (e) {
    projectMsg(`Couldn't save the data: ${esc(e.message)}`, true);
  }
};
$("projectOpen").onclick = () => {
  if (!$("projectOpen").classList.contains("armed")) {
    // two clicks, no browser dialogs: this replaces everything
    $("projectOpen").classList.add("armed");
    $("projectOpen").textContent = "Replace everything?";
    return;
  }
  $("projectOpen").classList.remove("armed");
  $("projectOpen").textContent = "Open data…";
  $("projectFile").value = "";
  $("projectFile").click();
};
$("projectFile").onchange = async () => {
  const f = $("projectFile").files[0];
  if (!f) return;
  projectMsg(`Opening ${esc(f.name)}…`);
  try {
    const info = await api("/api/project", { json: { data: await readBase64(f) } });
    projectMsg(`Opened clan ${esc(info.clan)}'s data (latest turn ${esc(info.latest_turn || "none")}, saved ${esc(info.saved)}).`);
    setPlan("", "main");
    await loadAll(false);
  } catch (e) {
    projectMsg(esc(e.message), true);
  }
};

// ---------- importing turn reports ----------
// Pick one or several reports (or drop them on the page) and they're imported straight away, oldest turn first, so
// each new turn archives the plans as `tn import` does. A file whose name is taken by a different file is skipped,
// never overwritten. Then the summary: anything the parser couldn't place.
$("importBtn").onclick = () => { $("importFile").value = ""; $("importFile").click(); };
$("importDone").onclick = () => { $("importBox").hidden = true; };
$("importFile").onchange = () => importFiles([...$("importFile").files]);
// a whole folder (and the folders inside it): the turn reports (by name) and .wxx maps; orders, Tribe News, Word's
// ~$ lock files and the rest are left alone
const REPORT_NAME = /^(?!~\$)(.*results.*\.(docx|txt)|.*\.wxx)$/i;
$("importDirBtn").onclick = () => { $("importDir").value = ""; $("importDir").click(); };
$("importDir").onchange = () => {
  const all = [...$("importDir").files], files = all.filter((f) => REPORT_NAME.test(f.name));
  if (files.length) importFiles(files);
  else $("status").textContent = `No turn reports (files named …Results….docx) among the ${all.length} files in that folder`;
};
document.addEventListener("dragover", (e) => {
  if ([...e.dataTransfer.types].includes("Files")) e.preventDefault();
});
document.addEventListener("drop", (e) => {
  const files = [...e.dataTransfer.files].filter((f) => /\.(docx|txt|wxx)$/i.test(f.name));
  if (!files.length) return;
  e.preventDefault();
  importFiles(files);
});

function readBase64(f) {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || "");
    r.onerror = () => bad(r.error);
    r.readAsDataURL(f);
  });
}

let importing = false;
async function importFiles(files) {
  if (!files.length || importing) return;
  importing = true;
  $("importBox").hidden = false;
  $("importBox").scrollIntoView({ block: "start" });
  $("importDone").hidden = true;
  const info = $("importInfo");
  info.innerHTML = `<p class="muted">Reading ${files.length} file${files.length === 1 ? "" : "s"}…</p>`;
  // read each one first (nothing changes yet) to put them in turn order
  const ups = [], out = [];
  for (const f of files) {
    try {
      const up = { filename: f.name, data: await readBase64(f) };
      up.check = await api("/api/import", { json: { ...up, dry_run: true } });
      ups.push(up);
    } catch (e) {
      out.push(`<li><b>${esc(f.name)}</b>: <span class="warn">${esc(e.message)}</span></li>`);
    }
  }
  // reports oldest turn first, then maps (they have no turn)
  ups.sort((a, b) => (a.check.turn || "~").localeCompare(b.check.turn || "~", undefined, { numeric: true }));
  let done = 0;
  for (const up of ups) {
    info.innerHTML = `<p class="muted">Importing ${esc(up.filename)} (${++done} of ${ups.length})…</p>`;
    if (up.check.replaces_different_file) {
      out.push(`<li><b>${esc(up.filename)}</b>: <span class="warn">skipped: ${esc(up.check.saved_to)} already exists with
        different contents (rename the file to import it)</span></li>`);
      continue;
    }
    try {
      const d = await api("/api/import", { json: { filename: up.filename, data: up.data } });
      let h = d.skipped ? `already imported; nothing changed`
        : d.map ? `map: ${d.hexes} hexes, ${d.rivers} river sides` + (d.own ? "" : ", as other players' information")
        : `turn ${esc(d.turn)}, ${d.observations ?? 0} facts`;
      if (d.archived && d.archived.length) h += `; archived plans ${d.archived.map((a) => esc(a.plan)).join(", ")}`;
      if (d.next_plan) h += `; plan ${esc(d.next_plan)} ready to fill in`;
      if (d.unparsed && d.unparsed.length) {
        h += `<p class="warn">${d.unparsed.length} map line(s) TribeMap couldn't place on the map (anything named is kept as a special hex):</p>
          <ul>${d.unparsed.map((u) => `<li><span class="unp">line ${u.line}: ${esc(u.text)}</span><br><span class="muted">${esc(u.reason)}</span></li>`).join("")}</ul>`;
      }
      out.push(`<li><b>${esc(d.file)}</b>: ${h}</li>`);
    } catch (e) {
      out.push(`<li><b>${esc(up.filename)}</b>: <span class="warn">${esc(e.message)}</span></li>`);
    }
  }
  info.innerHTML = `<p><b>Import</b></p><ul class="imports">${out.join("")}</ul>`;
  $("importDone").hidden = false;
  importing = false;
  setPlan("", "main");   // after an import, show the map itself
  await loadAll(false);
}

$("planSummary").addEventListener("mouseover", (e) => {
  if (state.edit) return;
  const row = e.target.closest("tr.route");
  const name = row ? row.dataset.route : null;
  if (name !== state.hiRoute) { state.hiRoute = name; drawPlan(); }
});
$("planSummary").addEventListener("mouseleave", () => { if (!state.edit) { state.hiRoute = null; drawPlan(); } });
$("planSummary").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) {
    // clicking a route's row (or a tribe's heading) shows that tribe with its elements
    if (state.edit) return;
    const row = e.target.closest("tr.route"), head = e.target.closest("h3.group[data-tribe]");
    const r = row && state.plan.routes.find((x) => x.name === row.dataset.route);
    const tribe = r ? tribeOf(r.unit) : head ? head.dataset.tribe : null;
    if (tribe) selectUnit(tribe);
    return;
  }
  if (b.dataset.del || b.dataset.delgroup) {
    // two clicks to delete, no browser dialogs
    if (!b.classList.contains("armed")) {
      $("planSummary").querySelectorAll("button.armed").forEach((x) => {
        x.classList.remove("armed");
        x.textContent = x.dataset.delgroup ? "✕ All" : "✕";
      });
      b.classList.add("armed");
      b.textContent = b.dataset.delgroup ? "Delete all?" : "Delete?";
      return;
    }
  }
  if (b.dataset.delgroup) {
    // every scout party of the tribe and its elements
    const tribe = b.dataset.delgroup;
    const units = [...new Set(state.plan.routes.filter((r) => r.kind === "scout" && tribeOf(r.unit) === tribe).map((r) => r.unit))];
    try {
      let n = 0;
      for (const u of units) {
        const res = await api(`/api/plan/${state.planTurn}/route?unit=${encodeURIComponent(u)}&kind=scout` +
          `&variant=${encodeURIComponent(state.planVariant)}`, { method: "DELETE" });
        n += res.removed;
      }
      planMsg(`Deleted ${n} of ${esc(tribe)}'s scout route${n === 1 ? "" : "s"}.`);
      await loadAll(true);
    } catch (err) {
      planMsg(esc(err.message), true);
    }
    return;
  }
  if (b.dataset.del) {
    const r = state.plan.routes.find((x) => x.name === b.dataset.del);
    try {
      const res = await api(`/api/plan/${state.planTurn}/route?unit=${encodeURIComponent(r.unit)}&kind=${r.kind}` +
        (r.party ? `&party=${r.party}` : "") + `&variant=${encodeURIComponent(state.planVariant)}`, { method: "DELETE" });
      planMsg(`Deleted ${esc(r.name)}.${droppedText(res.dropped_scouts, r.unit)}`, !!res.dropped_scouts);
      await loadAll(true);
    } catch (err) {
      planMsg(esc(err.message), true);
    }
    return;
  }
  if (b.dataset.auto) {
    await autoPlan(b.dataset.auto, b);
    return;
  }
  if (b.dataset.edit) {
    const r = state.plan.routes.find((x) => x.name === b.dataset.edit);
    startEdit({ unit: r.unit, kind: r.kind, party: r.party, orders: r.orders.split(","), start: r.start_hex,
                mover: r.mover_set, scouts: r.scouts, horses: r.horses, mission: r.mission },
              [r.unit, r.kind, r.party]);
  } else if (b.id === "exportPlan") {
    try {
      if (state.meta.project_open) {   // the browser-only version: no turn folder, so it goes to the downloads
        const res = await fetch(`/api/plan/${state.planTurn}/paste-in` + pq());
        $("exportMsg").textContent = `Saved ${await download(res, "PlanPaste.xlsx")} to your downloads`;
      } else {
        const res = await api(`/api/plan/${state.planTurn}/export` + pq(), { json: {} });
        $("exportMsg").textContent = `Saved ${res.xlsx.split(/[\\/]/).slice(-2).join("\\")}`;
      }
    } catch (err) {
      $("exportMsg").textContent = err.message;
    }
  }
});

function autoShare(unit) {
  // how many of the tribe's free parties (8 shared by the tribe and its elements) this unit gets: an equal
  // share with the tribe's other moving units that have no scouts yet, so planning one doesn't starve the rest
  const tribe = tribeOf(unit), rs = state.plan.routes;
  const free = 8 - rs.filter((r) => r.kind === "scout" && tribeOf(r.unit) === tribe).length;
  const scouting = new Set(rs.filter((r) => r.kind === "scout").map((r) => r.unit));
  const waiting = new Set(rs.filter((r) => r.kind === "move" && tribeOf(r.unit) === tribe && !scouting.has(r.unit))
    .map((r) => r.unit));
  waiting.add(unit);
  return Math.max(1, Math.ceil(free / waiting.size));
}
async function autoPlan(unit, button) {
  if (button.dataset.auto) selectUnit(unit);
  button.disabled = true;
  planMsg(`Planning ${esc(unit)}'s scouts…`);
  try {
    const res = await api(`/api/plan/${state.planTurn}/autoscout` + pq(), { json: { unit, parties: autoShare(unit) } });
    planMsg(res.added.length
      ? `Auto-planned ${res.added.length} scout part${res.added.length === 1 ? "y" : "ies"} for ${esc(unit)}: ` +
        res.added.map((a) => `party ${a.party} ${esc(a.orders)}`).join("; ") +
        `. Unknown terrain is assumed to cost 3 MP, so check them and edit as needed.` +
        (res.look_ins.length ? ` Look-ins moved so they aren't wasted: ` +
          res.look_ins.map((f) => `${esc(f.unit)} party ${f.party} ${esc(f.old)} → ${esc(f.new)}`).join("; ") + "." : "")
      : `Nothing left worth scouting from ${esc(unit)}'s position.`);
    await loadAll(true);
  } catch (e) {
    planMsg(esc(e.message), true);
  } finally {
    button.disabled = false;
  }
}
$("autoScout").onclick = (e) => autoPlan(selected() || state.meta.clan, e.currentTarget);
$("goto").addEventListener("submit", async (e) => {
  // a hex label (QB 1016, qb1016) or one of our units: centre on it and show what's known
  e.preventDefault();
  let t = $("gotoText").value.trim().toUpperCase();
  const m = t.match(/^([A-Z]{2})\s*(\d{4})$/);
  if (m) t = `${m[1]} ${m[2]}`;
  const unit = state.map.units.find((u) => u.unit.toUpperCase() === t);
  const id = unit ? unit.hex : t;
  if (state.byId[id]) { select(id, true); return; }
  try {
    renderDetail(state.detail = await api(`/api/hex/${encodeURIComponent(id)}` + q(state.asOf)));
    $("status").textContent = `${state.detail.hex} is outside the drawn area (nothing known nearby).`;
  } catch (err) {
    $("status").textContent = err.message;
  }
});

$("newMove").onclick = () => startEdit({ unit: newDraftUnit(), kind: "move", orders: [] }, null);
$("newScout").onclick = () => startEdit({ unit: newDraftUnit(), kind: "scout", party: null, orders: [], scouts: 2,
                                          horses: 2, mission: "PATROL" }, null);
$("eUnitPick").addEventListener("input", () => {
  // runs before the form's own input handler (below), so that sees the unit already in #eUnit
  const other = $("eUnitPick").value === "__other";
  $("eUnit").hidden = !other;
  $("eUnit").value = other ? "" : $("eUnitPick").value;
  if (other) $("eUnit").focus();
});

// any change in the form re-simulates (typing is debounced)
let typing = null;
$("editForm").addEventListener("input", async (e) => {
  if (e.target.id === "eUsual") {
    // saved straight away for the unit (units.csv), not with the route
    const unit = $("eUnit").value.trim();
    try {
      const res = await api("/api/unit-mover", { json: { unit, mover: e.target.checked ? "mounted" : "foot" } });
      state.meta.unit_movers = res.unit_movers;
      planMsg(`${esc(unit)} ${e.target.checked ? "usually moves mounted" : "moves on foot"} from now on.`);
    } catch (err) {
      planMsg(esc(err.message), true);
    }
    state.edit.draft = draftFromForm();
    fillEditForm();
  }
  if ((e.target.id === "eUnit" || e.target.id === "eUnitPick") && $("eKind").value === "move") {
    // the unit's usual mover follows the unit chosen or typed (without refilling the form under the cursor)
    const usual = usualMover($("eUnit").value.trim());
    $("eUsual").checked = usual === "mounted";
    $("eMover").options[0].textContent = `${usual} (unit's usual)`;
  }
  if (e.target.id === "eKind") {
    state.edit.draft = draftFromForm();
    fillEditForm();
  }
  clearTimeout(typing);
  typing = setTimeout(() => preview(), e.target.tagName === "SELECT" ? 0 : 300);
});
$("editForm").addEventListener("submit", (e) => { e.preventDefault(); saveEdit(); });
document.querySelector(".pad").addEventListener("click", (e) => { if (e.target.dataset.dir) addOrder(e.target.dataset.dir); });
$("eSpecial").onchange = (e) => { if (e.target.value) { addOrder(e.target.value); e.target.value = ""; } };
$("eUndo").onclick = () => {
  const orders = $("eOrders").value.split(",").filter(Boolean);
  orders.pop();
  $("eOrders").value = orders.join(",");
  preview();
};
$("eClear").onclick = () => { $("eOrders").value = ""; preview(); };
$("ePick").onclick = () => { state.edit.picking = !state.edit.picking; drawAll(); renderEditor(); };
$("eCancel").onclick = cancelEdit;
$("eDelete").onclick = deleteEdit;

document.addEventListener("keydown", (e) => {
  const typingInField = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (e.key === "Escape") {
    if (state.edit && state.edit.picking) { state.edit.picking = false; renderEditor(); drawAll(); }
    else if (state.edit) cancelEdit();
    else { state.sel = null; drawSel(); }
  } else if (e.key === "Backspace" && state.edit && !typingInField) {
    e.preventDefault();
    $("eUndo").click();
  }
});

renderLayers();
loadAll(false).then(async () => {
  // Save's snapshot waits for this: drawn, fonts loaded, filters settled
  if (!URLP.get("snapshot")) return;
  await document.fonts.ready;
  setTimeout(() => { window.tribemapReady = true; }, 400);
});
