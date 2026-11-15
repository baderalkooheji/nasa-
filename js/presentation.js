/* Presentation Mode: the active 2D map or 3D globe fills the screen for demonstrations.
 *
 * One state, a CSS class on <body> ("presenting"), see css/presentation.css. Nothing is rebuilt or re-created:
 * the Leaflet map, the ArcGIS SceneView, routes, layers, selection, the journey and the AI assistant keep running.
 * The browser Fullscreen API is used when allowed; when it is not (or it is refused), the same layout is applied
 * inside the window. Exit with the button, or Escape (in browser full screen, Escape leaves full screen, which
 * also leaves Presentation Mode).
 * Classic script, loaded after js/map.js (uses its global `map`).
 */
(function () {
  "use strict";
  const btn = document.getElementById("btnPresent");
  if (!btn) return;
  const label = btn.querySelector(".present-label");
  const root = document.documentElement;
  const canFullscreen = !!(root.requestFullscreen && document.fullscreenEnabled !== false);
  let usingFullscreen = false;

  const isOn = () => document.body.classList.contains("presenting");

  /** Let the 2D map and the 3D scene adopt the new size without moving the map or the camera. */
  function relayout() {
    const fit = () => {
      if (typeof map !== "undefined" && map && map.invalidateSize) map.invalidateSize({ pan: true, animate: false });   // pan: keep the centre
      window.dispatchEvent(new Event("resize"));          // ArcGIS also watches its container; this covers other listeners
    };
    requestAnimationFrame(fit);
    setTimeout(fit, 250);                                  // after the browser's full-screen transition
  }

  function apply(on) {
    // keep the 2D map's CENTRE and zoom across the size change, so the route stays where it was on screen
    const hasMap = typeof map !== "undefined" && map && map.getCenter;
    const c = hasMap ? map.getCenter() : null, z = hasMap ? map.getZoom() : null;
    document.body.classList.toggle("presenting", on);
    if (hasMap) requestAnimationFrame(() => { map.invalidateSize({ pan: false, animate: false }); map.setView(c, z, { animate: false }); });
    btn.setAttribute("aria-pressed", String(on));
    label.textContent = on ? "Exit Presentation Mode" : "Presentation Mode";
    btn.title = on ? "Leave presentation mode (Esc)" : "Full-screen view of the active 2D map or 3D globe (Esc to exit)";
    relayout();
  }

  function enter() {
    if (isOn()) return;
    // Layout first, so the mode works even when the browser refuses or never answers the full-screen request
    // (embedded browsers, iframes, kiosk policies). Browser full screen is then an extra on top of it.
    apply(true);
    usingFullscreen = false;
    if (canFullscreen && !document.fullscreenElement) {
      try {
        const p = root.requestFullscreen({ navigationUI: "hide" });   // still inside the click: allowed by the browser
        if (p && p.then) p.then(() => { usingFullscreen = true; relayout(); }, () => { /* refused: in-window layout stays */ });
      } catch { /* not allowed here */ }
    }
  }

  function exit() {
    if (!isOn()) return;
    usingFullscreen = false;                               // set first: the fullscreenchange that follows must not re-apply
    apply(false);
    if (document.fullscreenElement) { try { document.exitFullscreen().catch(() => {}); } catch { /* already left */ } }
  }

  btn.addEventListener("click", () => (isOn() ? exit() : enter()));

  // Escape in browser full screen exits full screen (the page never sees the key): follow it.
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && usingFullscreen && isOn()) { usingFullscreen = false; apply(false); }
    else relayout();
  });
  // Escape without browser full screen. An open AI assistant panel takes the first Escape (it closes itself).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !isOn() || document.fullscreenElement) return;
    if (document.querySelector(".mwai-panel:not([hidden])")) return;
    exit();
  });
  // Switching 2D ↔ 3D while presenting: the newly shown view gets the full area too.
  window.addEventListener("marswalk:view", () => { if (isOn()) relayout(); });

  window.MarswalkPresentation = { enter, exit, toggle: () => (isOn() ? exit() : enter()), get active() { return isOn(); } };
})();
