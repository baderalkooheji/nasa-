# Marswalk 2D map: NASA data sources

All map coordinates are **Mars planetocentric latitude, east-positive longitude** (IAU2000:49900, sphere radius 3 396 190 m). NASA Mars Trek "EQ" tiles and the Perseverance MMGIS files both use this system, so they line up with no conversion.

## Imagery (NASA Mars Trek WMTS tiles, loaded live)

| Layer | Trek product ID | Native res. | Coverage |
|---|---|---|---|
| Viking colour mosaic | `Mars_Viking_MDIM21_ClrMosaic_global_232m` | 232 m/px | global |
| CTX Jezero region | `Jezero_CTX_BlockAdj_dd` | ~6 m/px (served to z12) | 75.15–78.87°E, 15.91–19.45°N |
| CTX orthomosaic, Mars 2020 TRN | `JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0` | 6 m/px | 77.16–77.70°E, 18.21–18.72°N |
| HiRISE orthomosaic, Mars 2020 TRN | `JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd` | 25 cm/px | 77.22–77.58°E, 18.31–18.67°N |
| MOLA shaded elevation (overlay) | `Mars_MGS_MOLA_ClrShade_merge_global_463m` | 463 m/px | global |
| THEMIS day IR (overlay) | `THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018` | 100 m/px | 65°S–65°N |

Tile URL pattern: `https://trek.nasa.gov/tiles/Mars/EQ/{ID}/1.0.0/default/default028mm/{z}/{row}/{col}.{png|jpg}`
Capabilities: `https://trek.nasa.gov/tiles/Mars/EQ/{ID}/1.0.0/WMTSCapabilities.xml`

## Perseverance rover (NASA/JPL-Caltech)

- Traverse: https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json
- Waypoints (sol, lon, lat, elevation above areoid): https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json

Run `python tools/fetch_nasa_data.py` to save local copies in `data/` for an offline demo. The app reads the local copies first, then mars.nasa.gov.

## Still needed from the backend (USGS downloads)

Elevation and slope numbers must come from the DTMs, not the images:
- Mars 2020 TRN CTX DTM (20 m): `JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m`
- Mars 2020 TRN HiRISE DTM (1 m): `JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m`

Serve `/api/layers` bounds as `[[south, west], [north, east]]` in the same lon/lat. The map refuses overlays whose bounds are not on Jezero.

## Honesty notes

- The HiRISE mosaic was taken before landing, so it shows no rover tracks.
- The Viking basemap is coarse context and may sit tens to hundreds of metres off the TRN-controlled CTX/HiRISE mosaics. Use CTX/HiRISE for the walk.
- `data/stops.json` puts the stops on real rover positions (sols 477, 632, 658, 700). The names and reasons are a starter set for Data & Science to confirm.
