# Setup guide

## 1. Run the viewer

The app is plain HTML/CSS/JS. The ArcGIS Maps SDK 5.1 loads from the Esri CDN, so
there is no build step and no `npm install`.
It must be served over HTTP. Opening `index.html` from the file system does not work,
because ES modules and the tile requests need an origin.

```bash
cd marswalk-3d
```

```bash
python -m http.server 8080 --bind 127.0.0.1
```

Then open <http://127.0.0.1:8080/>. Any static server works, for example the VS Code
"Live Server" extension, `npx serve`, nginx or IIS.

Requirements: a WebGL2-capable browser (current Chrome, Edge, Firefox or Safari) and
internet access to `js.arcgis.com` and `astro.arcgis.com`.

If `data/processed/manifest.json` is missing, the globe still works with global data
only. The status bar then shows "Jezero terrain not prepared", and the Jezero layer
toggles are disabled.

## 2. (Re)build the Jezero terrain

The processed tiles in `data/processed` are generated from the original USGS/JPL
GeoTIFFs. You only need this step to regenerate them.

### 2.1 Python environment

```bash
python -m pip install -r scripts/prepare-terrain/requirements.txt
```

`rasterio` ships its own GDAL, so no separate GDAL or QGIS install is needed. QGIS is
handy for inspecting the inputs and `data/processed/footprints.geojson`.

### 2.2 Download the source rasters (~9 GB)

Windows PowerShell:

```bash
powershell -ExecutionPolicy Bypass -File scripts/prepare-terrain/download_data.ps1
```

Or manually, into `data/raw/ctx` and `data/raw/hirise`:

| File | Size |
| --- | --- |
| [M20_JezeroCrater_CTXDEM_20m.tif](https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif) | 86 MB |
| [M20_JezeroCrater_CTXortho_mosaic_5m.tif](https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXortho_mosaic_5m.tif) | 343 MB |
| [JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif](https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif) | 1.8 GB |
| [JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif](https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif) | 6.9 GB |

The originals are never modified. Intermediate rasters go to `data/processed/_work/`.

### 2.3 Generate tiles

```bash
cd scripts/prepare-terrain
```

```bash
python prepare_terrain.py --ctx-dem ../../data/raw/ctx/M20_JezeroCrater_CTXDEM_20m.tif --ctx-ortho ../../data/raw/ctx/M20_JezeroCrater_CTXortho_mosaic_5m.tif --hirise-dtm ../../data/raw/hirise/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif --hirise-ortho ../../data/raw/hirise/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif --out ../../data/processed
```

Typical run time on a laptop is about 15–25 minutes, and peak memory stays under 2 GB.
The script processes in blocks. Useful options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--only ctx-dem,ctx-ortho,hirise-dtm` | all | rebuild only some datasets (the manifest is merged) |
| `--ctx-dem-levels` | `6-11` | L11 ≈ 20 m elevation grid |
| `--ctx-ortho-levels` | `7-12` | L12 ≈ 5 m imagery |
| `--hirise-dtm-levels` | `11-14` | L14 ≈ 2.5 m elevation grid (L15 ≈ 1.3 m) |
| `--hirise-decimate` | `2` | average HiRISE to 2 m before tiling (1 = native 1 m) |
| `--ctx-feather-m` / `--hirise-feather-m` | `2000` / `300` | width of the edge blend into the surrounding DEM |
| `--hirise-ortho PATH` | — | controlled USGS HiRISE orthomosaic. Without it the app falls back to the Esri HiRISE mosaic |
| `--hirise-ortho-levels` | `12-15` | L15 ≈ 0.64 m imagery (L16 ≈ 0.32 m) |

The script prints each dataset's size, resolution, bounds and the feathering
statistics, so you can sanity-check a run from its log.

### 2.4 Verify in QGIS (optional)

* Open `data/processed/_work/ctx_dem_feathered.tif` and the original DEM, then compare
  them with the Raster Calculator. They are identical except in the outer 2 km.
* Open `data/processed/footprints.geojson` over the originals. Its CRS is lon/lat on
  the Mars 2000 sphere.

## 3. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Blank page, console says "Cannot use import statement outside a module" | The SDK 5.x script tag must be `type="module"` |
| Globe loads but no Jezero detail | `data/processed/manifest.json` is missing, or the page isn't served from the project root |
| Status shows "… unavailable" | An Esri service is unreachable. That layer is skipped and the rest keeps working |
| Very slow tiles | The local server is busy (for example while `prepare_terrain.py` runs), or you're on a slow connection |
