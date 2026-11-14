# Integration guide

Marswalk 3D is a self-contained module. It can be embedded in the separate 2D Marswalk
Mission Planner, or in any other JavaScript application, without that application
depending on this folder's UI.

## 1. Which files do what

| File | Responsibility | Needed when embedding? |
| --- | --- | --- |
| `js/mars3d.js` | **Public API** (`Mars3D.init`, `goToJezero`, …) and the event bus | ✅ entry point |
| `js/sdk.js` | Loads the ArcGIS SDK **once** and resolves its modules | ✅ |
| `js/mars-scene.js` | Builds the `Map` + `SceneView` and the layer registry; no DOM/UI code | ✅ |
| `js/map-layers.js` | Global Mars services, Mars tiling scheme, layer descriptions | ✅ |
| `js/terrain-loader.js` | Local CTX/HiRISE elevation and imagery layers, manifest loading, footprint tests | ✅ |
| `js/jezero-region.js` | Jezero constants (IAU), marker, rim circle, coverage outlines | ✅ |
| `js/camera-controls.js` | Global ↔ Jezero flights, go-to-location, zoom, reset, auto-spin | ✅ |
| `js/location-selection.js` | Click → coordinates and elevation from each covering DEM | ✅ |
| `data/processed/` | Tiles + `manifest.json` | ✅ (serve statically) |
| `js/app.js`, `index.html`, `css/*` | The standalone page and its panels | ❌ host provides its own UI |

Rule of thumb: copy the whole `marswalk-3d/` folder (or mount it as a static
sub-path), then import `js/mars3d.js` from your app. Nothing in the module refers to
the other application's routes, database or backend.

## 2. Initialise inside an existing container

```html
<div id="mars3d" style="position:relative;width:100%;height:600px"></div>
<script type="module">
  import { Mars3D } from "/marswalk-3d/js/mars3d.js";

  await Mars3D.init(document.getElementById("mars3d"), {
    dataUrl: "/marswalk-3d/data/processed/", // where manifest.json + tiles are served
    autoSpin: false,                         // default true: slow rotation until interaction
    quality: "high",                         // "low" | "medium" | "high"
    // camera: { position: { x: 77.6, y: 10, z: 9e6, spatialReference: { wkid: 104971 } }, heading: 0, tilt: 0 },
    // sdkVersion: "5.1",
  });
</script>
```

* The container must have a size. The SceneView fills it.
* `dataUrl` defaults to `../data/processed/` relative to `mars3d.js`, so the default
  works whenever the folder is served as-is.
* `init` resolves after the view is ready. It rejects if the SDK cannot load. A missing
  manifest or an unreachable service does not reject; those are reported through the
  `"status"` and `"layers"` events.

## 3. API

| Method | Description |
| --- | --- |
| `Mars3D.init(container, options)` | Create the scene (one instance at a time) |
| `Mars3D.goToJezero({ duration })` | Animated flight: rotate to Jezero at orbit altitude, then descend to a tilted crater view |
| `Mars3D.goToGlobal({ duration })` | Pull back to the whole planet |
| `Mars3D.goToLocation(lat, lon, { altitude, tilt, heading, duration, select })` | Fly to a planetocentric lat / east-lon (0–360 accepted). By default it also selects the point and fires `"select"` |
| `Mars3D.setVisibleLayer(id, visible)` | Toggle a layer (ids below). Returns `false` for unknown ids |
| `Mars3D.setBasemap("mdim" \| "colordem")` | Colour imagery or colour shaded relief |
| `Mars3D.getLayers()` | `[{ id, kind, title, detail, scope, bounds, footprint, visible, failed }]` |
| `Mars3D.zoomIn()`, `zoomOut()`, `resetCamera()` | Navigation helpers |
| `Mars3D.getRegion()` | `"global"` or `"jezero"` |
| `Mars3D.getView()` | The underlying `SceneView` (advanced use) |
| `Mars3D.on(event, cb)` | Subscribe; returns an unsubscribe function |
| `Mars3D.destroy()` | Tear everything down (see §5) |

Layer ids: `global-imagery`, `global-colordem`, `global-elevation`, `ctx-dem`,
`ctx-ortho`, `hirise-dtm`, `hirise-imagery`, `jezero-marker`, `jezero-rim`,
`footprints`.

Events:

| Event | Payload |
| --- | --- |
| `status` | `{ state: "loading" \| "ready" \| "warning" \| "error", message }` |
| `region` | `{ region: "global" \| "jezero" }` |
| `camera` | `{ latitude, longitude, altitude, heading, tilt, centerLatitude, centerLongitude }` |
| `select` | `{ latitude, longitude, elevations: [{ id, label, value }] }` (value in m rel. to areoid, `null` if unavailable) |
| `layers` | `{ id, visible, failed? }` |

## 4. Passing a location or camera from the 2D app

```js
// a point picked in the 2D map (planetocentric lat, east-positive lon)
await Mars3D.goToLocation(18.4447, 77.4508, { altitude: 8000, tilt: 60 });

// or restore an exact camera
Mars3D.getView().goTo({
  position: { x: 77.45, y: 18.30, z: 12000, spatialReference: { wkid: 104971 } },
  heading: 0, tilt: 65,
});

// keep the 2D map in sync with the 3D view
const off = Mars3D.on("camera", (c) => my2DMap.setCenter(c.centerLatitude, c.centerLongitude));
```

If your 2D map uses **west-positive** longitudes (some older Mars products do), convert
with `lonEast = 360 - lonWest`, then normalise to −180…180. The 3D module always uses
planetocentric, east-positive coordinates on the Mars 2000 sphere (WKID 104971).

## 5. Switching between 2D and 3D

Two common patterns:

**A. Keep both alive, toggle visibility.** This gives instant switching but holds GPU
memory.

```js
function show3D(on) {
  document.getElementById("map2d").hidden = on;
  document.getElementById("mars3d").hidden = !on;
}
```

The SceneView keeps rendering only while visible. A hidden container costs memory but
no frames.

**B. Create on demand, destroy on leave.** This is the recommended pattern for
low-memory devices.

```js
async function enter3D(lat, lon) {
  await Mars3D.init(container, { autoSpin: false });
  if (lat != null) await Mars3D.goToLocation(lat, lon);
}
function leave3D() {
  const c = Mars3D.getView()?.camera; // keep it if you want to restore later
  Mars3D.destroy();                   // frees WebGL context, layers, listeners
  return c;
}
```

`destroy()` stops the spin animation, removes the event watchers and the click handler,
calls `view.destroy()` (which destroys the map and its layers), and empties the
container. It is safe to call twice. Event subscriptions made with `Mars3D.on` survive
`destroy()`; remove them with the function `on` returned if the host no longer needs
them.

## 6. Avoiding duplicate ArcGIS SDK instances

`js/sdk.js` checks for the global `$arcgis` before loading anything:

* If the host page already includes `<script type="module" src="https://js.arcgis.com/5.1/">`,
  that instance is reused.
* Otherwise the script and the dark theme CSS are injected exactly once, and repeated
  `init()` calls share one loading promise.

Constraints:

* Use **one SDK version per page**. If the 2D app uses the CDN at another 5.x version,
  pass `sdkVersion` so both agree, or load the SDK once in the host page.
* If the 2D app uses the **npm build** (`@arcgis/core` bundled with Vite or webpack),
  don't also load the CDN build. Replace `loadSdk()` in `js/sdk.js` with static imports,
  for example `import SceneView from "@arcgis/core/views/SceneView.js"`, and return the
  same keyed object. Nothing else needs to change, because the other modules only use
  the `esri` object they are given.
* The 2D app's own MapView can share the page. MapView and SceneView instances are
  independent, but each SceneView needs its own WebGL context, so avoid creating several
  at once.

## 7. Styling inside a host

The module adds no global CSS. The only visual dependency is the SDK theme stylesheet
(`esri/themes/dark/main.css`) for attribution and widgets. The standalone page's
`css/` files are optional. Reuse `panels.css` if you want the same look.
