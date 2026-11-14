# Data sources

Every dataset shown in Marswalk 3D is a published scientific product. Nothing is
synthesised. This file lists what each layer is, where it comes from, how it was
checked and what processing was applied.

## Coordinate system

| Item | Value |
| --- | --- |
| Spatial reference | **GCS_Mars_2000_Sphere, WKID 104971** |
| Body radius | 3 396 190 m (sphere) |
| Longitude convention | planetocentric, **east-positive**, −180…180 |
| Vertical reference | Mars areoid (MOLA geoid), metres |
| Tiling scheme | 512 × 512 px, origin (−180, 90), level-0 resolution 0.3515625°/px, halving per level |

All three Esri global services report WKID 104971 with the 3 396 190 m sphere and
the tiling scheme above. They were checked with `?f=json` on 2026-11-14. The
local Jezero tiles are cut in exactly the same scheme, so they stack on the global
layers without resampling.

The USGS/JPL rasters are **equirectangular projections of the same sphere**:

* CTX DEM and orthomosaic: `standard_parallel_1 = 18.4663°`, `central_meridian = 0`
* HiRISE DTM: `standard_parallel_1 = 0°`, `central_meridian = 0`

For these projections `lon = x / (R·cos φts·π/180)` and `lat = y / (R·π/180)`.
`prepare_terrain.py` checks GDAL's bounds against this formula. They agree to better
than 1e-6°.

## Global layers (streamed from astro.arcgis.com)

| Layer id | Service | Content | Resolution |
| --- | --- | --- | --- |
| `global-imagery` | [OnMars/MDIM](https://astro.arcgis.com/arcgis/rest/services/OnMars/MDIM/MapServer) | Viking MDIM 2.1 colour mosaic | ~232 m/px |
| `global-colordem` | [OnMars/MColorDEM](https://astro.arcgis.com/arcgis/rest/services/OnMars/MColorDEM/MapServer) | MOLA/HRSC colourised shaded relief | — |
| `global-elevation` | [OnMars/MDEM200M](https://astro.arcgis.com/arcgis/rest/services/OnMars/MDEM200M/ImageServer) | MOLA/HRSC blended DEM (F32, LERC tiles, range −8210…21241 m) | 200 m (finest cached level ≈163 m) |
| `hirise-imagery` (fallback only) | [OnMars/HiRISE](https://astro.arcgis.com/arcgis/rest/services/OnMars/HiRISE/MapServer) | HiRISE red-band uncontrolled equatorial mosaic (UA, released through 2018) | ≤1 m |

Credits: USGS Astrogeology Science Center, NASA, JPL, ESA, DLR, University of
Arizona, Esri.

**About HiRISE imagery.** The app uses the **controlled USGS orthomosaic** (see
[HiRISE orthomosaic](#hirise-orthomosaic--hirise-imagery) below) whenever it is present in
`manifest.json`, as it is in this project. Only if that dataset has not been processed
does the app fall back to Esri's HiRISE mosaic. Esri marks that mosaic **beta** and
**uncontrolled**: its images are placed by spacecraft pointing only and can be offset by
metres to tens of metres from the terrain. In that fallback case it is clipped to the
HiRISE DTM footprint and shown only from tile level 12 (≈5 m/px).

## Jezero layers (processed locally, served from `data/processed`)

### CTX DEM — `ctx-dem`

* **Product:** Mars 2020 Science Investigation CTX DEM mosaic (JPL), 20 m/px
* **Page:** <https://astrogeology.usgs.gov/search/map/mars_2020_science_investigation_ctx_dem_mosaic>
* **File:** `ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXDEM_20m.tif` (86 MB, float32)
* **Verified metadata:** 4456 × 5067 px, 20.000 m, nodata −3.4028e38.
  Bounds lon **76.99744 … 78.58256 °E**, lat **17.58052 … 19.29019 °N**.
  This matches the requested study area (76.99–78.58 °E, 17.58–19.29 °N).
  Valid data covers 99.9 % of the rectangle; only the first row is empty.
* **Elevation range (tiles):** about −3 504 m (crater floor / Isidis side) to −476 m (west plains)

### CTX orthomosaic — `ctx-ortho`

* **Product:** Mars 2020 Science Investigation CTX orthomosaic (JPL), 5 m/px
* **File:** `ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXortho_mosaic_5m.tif` (343 MB, uint8, single band)
* **Verified metadata:** 17766 × 20234 px, 5.002 m. Same projection parameters as the DEM.
  Bounds lon 77.00085 … 78.58147 °E, lat 17.58139 … 19.28896 °N.
* **Alignment with the DEM:** identical projection. Both grids are resampled into the
  same geographic tiling, so pixel registration is exact. The ortho footprint is
  slightly smaller than the DEM footprint, inset by 52–192 m depending on the side.
* **No-data:** the file has no declared no-data value. Pixels equal to 0 are gaps:
  the next value used is 3, and zeros make up about 2 % of the file, mostly the outer
  collar plus small clusters on steep, shadowed escarpments. Outside the mosaic's
  footprint polygon (the collar), zeros are transparent. Inside it, they are drawn black,
  as recorded, rather than letting the differently lit global mosaic show through.
* The ortho is **grayscale**, as published. It is not colourised.

### HiRISE DTM — `hirise-dtm`

* **Product:** Mars Sample Return / Mars 2020 Terrain Relative Navigation HiRISE DTM mosaic (USGS), 1 m/px
* **Page:** <https://astrogeology.usgs.gov/search/map/mars-sample-return-terrain-relative-navigation-hirise-dtm-mosaic>
* **File:** `mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif` (1.8 GB, float32)
* **Verified metadata:** 21400 × 21488 px, 1.000 m. Bounds lon **77.22293 … 77.58396 °E**,
  lat **18.30680 … 18.66932 °N**, which is the crater floor and delta.
  Valid data covers about 66 % of that rectangle. The real, irregular footprint is stored
  as a polygon and used for clipping and coverage checks.
* **Tiled resolution:** averaged to 2 m, then tiled with ~2.5 m grid spacing at the finest level (L14).
  Use `--hirise-decimate 1 --hirise-dtm-levels 11-15` for ~1.3 m tiles. That produces
  about 4× as many tiles.

### HiRISE orthomosaic — `hirise-imagery`

* **Product:** Mars 2020 TRN HiRISE orthomosaic (USGS), 25 cm/px, controlled to the same
  solution as the HiRISE DTM
* **File:** `mosaic/mars2020_trn/HiRISE/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif`
  (6.85 GB, uint8, uncompressed strips; the downloaded size was checked against the server's)
* **Verified metadata:** 85600 × 85952 px, 0.25 m, nodata 0. Its CRS is identical to the
  HiRISE DTM's and its bounds match the DTM's exactly (0.00 m edge offset), so imagery and
  terrain are registered by construction.
* **Tiled resolution:** area-averaged to 512-px tiles at **~0.64 m/px** at the finest
  level (L15); 4 092 tiles, 224 MB. Use `--hirise-ortho-levels 12-16` for ~0.32 m, about
  4× the tiles. Coarser levels are 2×2 averages of the finer tiles.
* **Gaps:** as with CTX, zeros outside the mosaic footprint are transparent and zeros
  inside it are drawn black.

## Processing applied (and only this)

1. **Reprojection** from equirectangular metres to geographic degrees on the same sphere.
   This is a pure axis scaling; no datum change.
   DEMs use bilinear resampling at the finest level and averaging for coarser levels.
2. **Edge feathering** (DEMs only). Neighbouring datasets differ by up to tens of metres
   where they meet. Example: the CTX DEM vs the global DEM at 77.75 °E, 19.29 °N
   is −2152 m vs −2125 m. Without feathering these steps render as walls. In the outermost band of each DEM, values
   are blended with a smoothstep weight into the surrounding dataset:
   * CTX DEM → global MDEM200M over the outer **2 000 m**. 1.85 M of 22.6 M pixels were
     adjusted; the largest adjustment is **195 m**, right at the outer edge.
   * HiRISE DTM → CTX DEM over the outer **300 m**. The largest adjustment is **18 m**.

   Interior values are unchanged. The exact numbers are recorded under
   `datasets.*.feathering` in `manifest.json`.
3. **Edge fill.** Where a terrain tile extends beyond its dataset, the empty pixels are
   filled from the surrounding dataset (global DEM around CTX, CTX around HiRISE). The
   SDK's terrain mesh does not honour per-pixel no-data; empty pixels render as deep
   trenches. For the same reason, when the SDK asks a local layer for a tile it doesn't
   have (a coarser level, or outside the footprint), the browser serves the next dataset
   down instead: HiRISE → CTX → global, upsampling the finest ancestor tile if needed.
   Elevation queries in the app only report a dataset inside its verified footprint, so
   filled areas are never presented as HiRISE or CTX measurements.
4. **Global imagery depth.** MDIM and MColorDEM advertise 18 levels but are cached only
   to level 7 (~163 m/px), which was checked tile by tile. Deeper tiles are cut from the level-7
   tile in the browser instead of being requested from the service.
5. **Encoding.** Elevations are stored losslessly to 0.1 m as Terrain-RGB PNG.
   Imagery tiles are JPEG (quality 85), or PNG where a tile touches a data edge.

**No vertical exaggeration** is applied anywhere. The 3D view shows true relief. The
Jezero rim stands ~500–1 000 m above a 47.5 km-wide floor, so at crater scale the
relief looks gentle; that is accurate.

## Jezero reference values

| Item | Value | Source |
| --- | --- | --- |
| Centre | 18.4082 °N, 77.6873 °E | IAU Gazetteer of Planetary Nomenclature, feature 14300 |
| Diameter | 47.52 km | IAU Gazetteer |
| Study area | 76.997–78.583 °E, 17.581–19.290 °N | CTX DEM raster extent (verified) |

The orange circle on the globe is drawn from the IAU centre and diameter. It shows the
nominal crater size and is **not** a mapped rim trace.
