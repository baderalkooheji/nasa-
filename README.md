# Marswalk 3D

A standalone, interactive **3D model of Mars** with real terrain for **Jezero Crater**.
It is built with the ArcGIS Maps SDK for JavaScript 5.1 and plain HTML/CSS/ES modules,
and is one component of the Marswalk Mission Planner (NASA Space Apps). It is kept
independent of the 2D application so it can be integrated later.

* **Global Mars:** Viking MDIM 2.1 colour imagery (or MOLA/HRSC colour relief) on the MOLA/HRSC 200 m DEM, in GCS_Mars_2000_Sphere (WKID 104971)
* **Jezero Crater:** the JPL Mars 2020 **CTX DEM (20 m)** as true 3D terrain, textured with the **CTX orthomosaic (5 m)**
* **Close-up:** the USGS Mars 2020 TRN **HiRISE DTM** (1 m source, tiled to ~2.5 m) and the matching **controlled HiRISE orthomosaic** (25 cm source, tiled to ~0.64 m), only inside their verified footprint
* An animated **global → Jezero** flight and back, layer toggles, click-to-inspect coordinates and elevation, collapsible panels
* **No vertical exaggeration, no synthetic data.** See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)

## Quick start

```bash
cd marswalk-3d
```

```bash
python -m http.server 8080 --bind 127.0.0.1
```

Open <http://127.0.0.1:8080/>. Any static web server works; you need internet access
for the Esri SDK and the global Mars services. Full instructions, including how to
rebuild the Jezero tiles from the original GeoTIFFs, are in
[docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md).

## Using it

| Action | How |
| --- | --- |
| Rotate / tilt / zoom | Left-drag / right-drag / mouse wheel. The `+` `−` buttons zoom; ⟲ resets the camera |
| Fly to the crater | **Explore Jezero** (or "Jezero Crater" in the top bar) |
| Back to the planet | **Back to Global Mars** (or "Global Mars") |
| Inspect a point | Click the surface. The right panel shows lat/lon and the elevation from every DEM that covers the point |
| Layers | Left panel: basemap, global elevation, CTX terrain / ortho, HiRISE terrain / imagery, marker, coverage outlines |

## Project layout

```text
marswalk-3d/
├── index.html                 standalone page
├── css/                       main / layout / panels / globe styles
├── js/
│   ├── mars3d.js              public API: Mars3D.init / goToJezero / goToGlobal / goToLocation / setVisibleLayer / destroy
│   ├── sdk.js                 loads the ArcGIS SDK once (re-uses an existing instance)
│   ├── mars-scene.js          Map + SceneView + layer registry (no UI)
│   ├── map-layers.js          global Esri Mars services, tiling scheme
│   ├── terrain-loader.js      local CTX/HiRISE elevation + imagery layers
│   ├── jezero-region.js       Jezero (IAU) marker, rim circle, coverage outlines
│   ├── camera-controls.js     flights, zoom, reset, auto-spin
│   ├── location-selection.js  click → coordinates & elevations
│   └── app.js                 wires the standalone UI to Mars3D
├── assets/                    logo, favicon
├── data/
│   ├── raw/{ctx,hirise}/      original GeoTIFFs (not committed; see setup guide)
│   └── processed/             manifest.json, footprints.geojson, terrain/ and imagery/ tiles
├── scripts/prepare-terrain/   prepare_terrain.py (GDAL via rasterio), download_data.ps1, requirements.txt
└── docs/                      DATA_SOURCES.md, SETUP_GUIDE.md, INTEGRATION_GUIDE.md
```

## Embedding

```js
import { Mars3D } from "./marswalk-3d/js/mars3d.js";
await Mars3D.init(document.getElementById("mars3d"));
Mars3D.goToJezero();
```

See [docs/INTEGRATION_GUIDE.md](docs/INTEGRATION_GUIDE.md) for the full API, events,
2D↔3D switching, cleanup and how to avoid loading the SDK twice.

## Credits

The data products are by NASA / JPL-Caltech, MSSS, the University of Arizona (HiRISE),
the USGS Astrogeology Science Center, ESA / DLR / FU Berlin (HRSC) and Esri (hosted
Mars services). The interface design takes cues from Esri's
[Explore Mars](https://github.com/Esri/explore-mars) (Apache-2.0), whose public source
was used as a reference for the Mars spatial reference and service setup.
