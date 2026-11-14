/* =====================================================================
   Marswalk: NASA data configuration for the 2D map
   ---------------------------------------------------------------------
   Every layer below is a real NASA product, served as map tiles by
   NASA Solar System Treks (Mars Trek, JPL) through its public WMTS API.
   All endpoints were checked against their WMTSCapabilities.xml files.

   COORDINATES
   - Mars Trek "EQ" tiles are equirectangular, planetocentric latitude,
     EAST-positive longitude, on the IAU2000:49900 Mars sphere
     (radius 3 396 190 m).
   - The tile grid starts at (-180°, 90°) with 2 × 1 tiles at zoom 0,
     which is exactly Leaflet's L.CRS.EPSG4326 grid, so lon/lat in the
     app are real Mars coordinates and line up with the rover data.
   - Perseverance waypoints/traverse from mars.nasa.gov (MMGIS) use the
     same planetocentric, east-positive lon/lat.
   ===================================================================== */

const TREK = "https://trek.nasa.gov/tiles/Mars/EQ";
const trekUrl = (layer, ext) => `${TREK}/${layer}/1.0.0/default/default028mm/{z}/{y}/{x}.${ext}`;

const NASA = {
  // Perseverance landing site (Octavia E. Butler Landing), sol 13 localisation
  // from NASA's M20_waypoints.json: 18.44463°N, 77.45089°E.
  landing: { lat: 18.444627, lon: 77.450886 },

  // Default view: western delta front of Jezero crater (Three Forks area)
  startView: { lat: 18.4545, lon: 77.4040, zoom: 15 },

  // Region the planner is about (Mars 2020 TRN CTX mosaic footprint)
  jezeroBounds: [[18.2110274, 77.1604655], [18.7211945, 77.6991776]],

  /* Base imagery, drawn bottom → top. Each one only requests tiles
     inside its own bounds and stops at its native resolution. */
  basemaps: [
    {
      id: "viking",
      name: "Viking colour mosaic (global, 232 m/px)",
      url: trekUrl("Mars_Viking_MDIM21_ClrMosaic_global_232m", "jpg"),
      maxNativeZoom: 7,
      bounds: [[-90, -180], [90, 180]],
      default_on: true,
      mission: "Viking Orbiter 1 & 2 · MDIM 2.1",
      resolution_m: 232,
      source: "NASA Mars Trek (USGS MDIM 2.1 colour mosaic)",
      info: "https://trek.nasa.gov/mars/",
      limitations: "Context only. Far too coarse for one walk.",
    },
    {
      id: "ctx_regional",
      name: "CTX Jezero region (block-adjusted, ~6 m/px)",
      url: trekUrl("Jezero_CTX_BlockAdj_dd", "png"),
      maxNativeZoom: 12,
      bounds: [[15.9058484, 75.1537692], [19.4453003, 78.8717121]],
      default_on: true,
      mission: "Mars Reconnaissance Orbiter · CTX",
      resolution_m: 6,
      source: "NASA Mars Trek (MRO CTX, NASA/JPL/MSSS)",
      info: "https://trek.nasa.gov/mars/",
      limitations: "Served to zoom 12 (~10 m/px on screen).",
    },
    {
      id: "ctx_trn",
      name: "CTX orthomosaic for Mars 2020 TRN (6 m/px)",
      url: trekUrl("JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0", "png"),
      maxNativeZoom: 13,
      bounds: [[18.2110274, 77.1604655], [18.7211945, 77.6991776]],
      default_on: true,
      mission: "Mars Reconnaissance Orbiter · CTX (Mars 2020 Terrain Relative Navigation map)",
      resolution_m: 6,
      source: "USGS Astrogeology / NASA Mars Trek: JEZ_ctx_B_soc_008_orthoMosaic_6m",
      info: "https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_context_camera_orthorectified_image_mosaic",
      limitations: "6 m/px: boulders under ~12 m are not visible.",
    },
    {
      id: "hirise",
      name: "HiRISE orthomosaic, walk zone (25 cm/px)",
      url: trekUrl("JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd", "png"),
      maxNativeZoom: 17,
      bounds: [[18.3067994, 77.2229331], [18.669315, 77.583964]],
      default_on: true,
      mission: "Mars Reconnaissance Orbiter · HiRISE (Mars 2020 TRN)",
      resolution_m: 0.25,
      source: "USGS Astrogeology / NASA Mars Trek: JEZ_hirise_soc_006_orthoMosaic_25cm",
      info: "https://trek.nasa.gov/tiles/Mars/EQ/JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first_dd/1.0.0/WMTSCapabilities.xml",
      limitations: "Covers only 77.22–77.58°E, 18.31–18.67°N (the walk zone). Greyscale.",
    },
  ],

  /* Optional science overlays (off by default, with opacity) */
  overlays: [
    {
      id: "mola",
      name: "MOLA shaded elevation (463 m/px)",
      url: trekUrl("Mars_MGS_MOLA_ClrShade_merge_global_463m", "jpg"),
      maxNativeZoom: 7,
      bounds: [[-90, -180], [90, 180]],
      opacity: 0.5,
      mission: "Mars Global Surveyor · MOLA",
      resolution_m: 463,
      source: "NASA Mars Trek (MGS MOLA colour hillshade)",
      info: "https://trek.nasa.gov/mars/",
      limitations: "Colour shows relative height only; read numbers from the DEM via the backend.",
    },
    {
      id: "themis",
      name: "THEMIS daytime infrared (100 m/px)",
      url: trekUrl("THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018", "png"),
      maxNativeZoom: 9,
      bounds: [[-65.0006576, -180], [65.0007535, 180]],
      opacity: 0.6,
      mission: "Mars Odyssey · THEMIS",
      resolution_m: 100,
      source: "NASA Mars Trek (ASU THEMIS Day IR controlled mosaic v2)",
      info: "https://trek.nasa.gov/mars/",
      limitations: "Brightness = daytime surface temperature context, not a temperature value.",
    },
  ],

  /* Perseverance rover data (NASA/JPL-Caltech, MMGIS).
     The app tries the local copy first (fast, works offline), then NASA live. */
  rover: {
    waypoints: ["data/M20_waypoints.json", "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json"],
    traverse:  ["data/M20_traverse.json",  "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json"],
    // A map click within this distance of a rover localisation shows the
    // rover-measured elevation when the DEM backend is offline.
    nearWaypoint_m: 40,
  },

  stopsFile: "data/stops.json",
};
