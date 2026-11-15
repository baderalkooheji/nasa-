/**
 * app.js — standalone Marswalk 3D page: wires the HTML UI to the Mars3D API.
 * Everything 3D lives behind js/mars3d.js; this file only touches the DOM.
 */

import { Mars3D } from "./mars3d.js";
import { covers, inBounds } from "./terrain-loader.js";

const $ = (id) => document.getElementById(id);

const ui = {
  app: $("app"),
  status: $("status"),
  statusText: $("status-text"),
  veil: $("loading-veil"),
  veilText: $("loading-text"),
  navGlobal: $("nav-global"),
  navJezero: $("nav-jezero"),
  explore: $("btn-explore"),
  back: $("btn-back"),
  region: $("info-region"),
  regionSub: $("info-region-sub"),
  lat: $("info-lat"),
  lon: $("info-lon"),
  elev: $("info-elev"),
  sources: $("info-sources"),
  alt: $("info-alt"),
  ht: $("info-ht"),
};

const fmtDeg = (v, pos, neg) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;
const fmtLon = (v) => `${v.toFixed(4)}° E`; // Mars: planetocentric, east-positive
const fmtM = (v) => (v == null ? "—" : `${Math.round(v).toLocaleString("en-US")} m`);
const fmtAlt = (m) => (m >= 10_000 ? `${(m / 1000).toLocaleString("en-US", { maximumFractionDigits: 0 })} km` : `${Math.round(m).toLocaleString("en-US")} m`);

// ------------------------------------------------------------------ status
function setStatus(state, message) {
  ui.status.classList.toggle("is-ready", state === "ready");
  ui.status.classList.toggle("is-loading", state === "loading");
  ui.status.classList.toggle("is-error", state === "error" || state === "warning");
  ui.statusText.textContent = message;
}

let warning = null;
Mars3D.on("status", ({ state, message }) => {
  if (state === "warning") warning = message;
  if (state === "ready" && warning) return setStatus("warning", warning);
  setStatus(state, message);
  if (!ui.veil.classList.contains("is-hidden")) ui.veilText.textContent = message;
});

// ------------------------------------------------------------------ region
function setRegion(region) {
  const jez = region === "jezero";
  ui.navGlobal.classList.toggle("is-active", !jez);
  ui.navJezero.classList.toggle("is-active", jez);
  ui.explore.hidden = jez;
  ui.back.hidden = !jez;
  ui.region.textContent = jez ? "Jezero Crater" : "Global Mars";
  ui.regionSub.textContent = jez
    ? `IAU centre 18.408° N, 77.687° E · ${Mars3D.JEZERO.diameterKm.toFixed(1)} km diameter`
    : "Click anywhere on the surface to inspect it.";
}
Mars3D.on("region", ({ region }) => setRegion(region));

// ------------------------------------------------------------------ sources
let lastCam = null;
function renderSources() {
  if (!Mars3D.isInitialized()) return;
  const layers = Mars3D.getLayers().filter((l) => l.kind !== "overlay" && l.visible && !l.failed);
  const lon = lastCam?.centerLongitude, lat = lastCam?.centerLatitude, alt = lastCam?.altitude ?? Infinity;
  const active = layers.filter((l) => {
    if (l.scope !== "local") return true;
    if (lon == null || !covers(l, lon, lat)) return false;
    if (l.id.startsWith("hirise")) return alt < 60_000;
    return alt < 1_500_000;
  });
  ui.sources.replaceChildren(
    ...active.map((l) => {
      const li = document.createElement("li");
      if (l.scope === "local") li.className = "is-local";
      const b = document.createElement("b");
      b.textContent = l.title || l.id;
      const s = document.createElement("span");
      s.textContent = l.detail || "";
      li.append(b, s);
      return li;
    }),
  );
}

let sourcesTimer = 0;
Mars3D.on("camera", (c) => {
  lastCam = c;
  ui.alt.textContent = fmtAlt(c.altitude);
  ui.ht.textContent = `${Math.round(c.heading)}° / ${Math.round(c.tilt)}°`;
  clearTimeout(sourcesTimer);
  sourcesTimer = setTimeout(renderSources, 150);
});
Mars3D.on("layers", ({ id, failed }) => {
  if (failed) document.querySelector(`[data-layer="${id}"]`)?.closest(".layer-row")?.classList.add("is-unavailable");
  renderSources();
});

// ------------------------------------------------------------------ selection
Mars3D.on("select", ({ latitude, longitude, elevations }) => {
  ui.lat.textContent = fmtDeg(latitude, "N", "S");
  ui.lon.textContent = fmtLon(longitude);
  const rows = [];
  for (const e of elevations) {
    const dt = document.createElement("dt");
    dt.textContent = shortLabel(e.id);
    dt.title = e.label || e.id;
    const dd = document.createElement("dd");
    dd.textContent = fmtM(e.value);
    rows.push(dt, dd);
  }
  if (!rows.length) {
    const dt = document.createElement("dt"); dt.textContent = "No elevation layer visible";
    const dd = document.createElement("dd"); dd.textContent = "—";
    rows.push(dt, dd);
  }
  ui.elev.replaceChildren(...rows);
  const inJezero = inBounds(Mars3D.JEZERO.studyArea, longitude, latitude);
  ui.region.textContent = inJezero ? "Jezero study area" : "Mars surface";
  ui.regionSub.textContent = inJezero ? "Inside the CTX DEM / orthomosaic coverage" : "Outside the local Jezero datasets";
});

function shortLabel(id) {
  return { "hirise-dtm": "HiRISE DTM", "ctx-dem": "CTX DEM 20 m", "global-elevation": "MOLA/HRSC 200 m" }[id] || id;
}

// ------------------------------------------------------------------ controls
function wireControls() {
  const fly = (fn) => async () => {
    ui.explore.disabled = ui.back.disabled = true;
    try { await fn(); } finally { ui.explore.disabled = ui.back.disabled = false; }
  };
  ui.explore.addEventListener("click", fly(() => Mars3D.goToJezero()));
  ui.navJezero.addEventListener("click", fly(() => Mars3D.goToJezero()));
  ui.back.addEventListener("click", fly(() => Mars3D.goToGlobal()));
  ui.navGlobal.addEventListener("click", fly(() => Mars3D.goToGlobal()));
  $("btn-zoom-in").addEventListener("click", () => Mars3D.zoomIn());
  $("btn-zoom-out").addEventListener("click", () => Mars3D.zoomOut());
  $("btn-reset").addEventListener("click", () => Mars3D.resetCamera());

  document.querySelectorAll("input[name=basemap]").forEach((r) =>
    r.addEventListener("change", () => r.checked && Mars3D.setBasemap(r.value)),
  );

  // HiRISE imagery comes either from the controlled USGS ortho (if processed) or Esri's mosaic.
  const hiriseImg = Mars3D.getLayers().find((l) => l.id === "hirise-imagery");
  const hiriseLabel = document.querySelector('input[data-layer="hirise-imagery"]')?.closest(".layer-row")?.querySelector("small");
  if (hiriseImg && hiriseLabel) {
    hiriseLabel.textContent = hiriseImg.controlled ? "USGS controlled ortho · 25 cm source" : "Esri mosaic · clipped to DTM coverage";
  }

  document.querySelectorAll("input[data-layer]").forEach((cb) => {
    const id = cb.dataset.layer;
    const ids = id === "jezero-marker" ? ["jezero-marker", "jezero-rim"] : [id];
    const known = Mars3D.getLayers().some((l) => l.id === id);
    if (!known) {
      cb.checked = false;
      cb.disabled = true;
      cb.closest(".layer-row")?.classList.add("is-unavailable");
      return;
    }
    cb.addEventListener("change", () => ids.forEach((i) => Mars3D.setVisibleLayer(i, cb.checked)));
  });

  document.querySelectorAll(".panel-toggle").forEach((btn) =>
    btn.addEventListener("click", () => {
      const panel = $(btn.dataset.target);
      const collapsed = panel.classList.toggle("is-collapsed");
      btn.setAttribute("aria-expanded", String(!collapsed));
      if (panel.classList.contains("panel-right")) ui.app.classList.toggle("right-collapsed", collapsed);
    }),
  );

  // Start with panels collapsed on narrow screens so the globe stays visible.
  if (window.matchMedia("(max-width: 760px)").matches) {
    // only those still open: something else (e.g. a route journey) may already have folded them
    document.querySelectorAll(".panel-toggle").forEach((b) => { if (!$(b.dataset.target)?.classList.contains("is-collapsed")) b.click(); });
  }
}

// ------------------------------------------------------------------ boot
(async function main() {
  try {
    await Mars3D.init("viewDiv", { dataUrl: "data/processed/" });
    wireControls();
    setRegion("global");
    renderSources();
    ui.veil.classList.add("is-hidden");
  } catch (err) {
    console.error(err);
    setStatus("error", "Failed to start");
    ui.veilText.textContent = `Could not start the 3D scene: ${err?.message || err}`;
    ui.veil.querySelector(".spinner")?.remove();
  }
})();
