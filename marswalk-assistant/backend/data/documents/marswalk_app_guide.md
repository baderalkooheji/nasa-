---
document_id: "APP-GUIDE-01"
title: "Marswalk Mission Planner: user guide"
source_url: "https://github.com/baderalkooheji/nasa-"
organization: "Marswalk project team"
document_type: "Application documentation"
scientific_topic: "marswalk_app"
reliability_category: "project_documentation"
---

# Marswalk Mission Planner user guide

## What is Marswalk?
Marswalk plans simulated astronaut walks (EVAs) in Jezero Crater, Mars, the landing site of NASA's Perseverance rover. It combines a 2D
map (NASA Mars Trek imagery) with a 3D globe, plans walking routes on NASA Mars 2020 elevation models (HiRISE DTM 1 m, CTX DEM 20 m),
estimates the EVA time budget, and includes this AI assistant. Coordinates are planetocentric latitude and east-positive longitude;
elevations are metres relative to the Mars areoid.

## How to switch between the 2D map and the 3D globe
Use the "2D Map" and "3D Globe" buttons at the top of the page. Routes, the selected point and a running journey are shared by both
views. In 3D, "Global Mars" and "Jezero Crater" (or "Explore Jezero") fly the camera between the whole planet and the crater.

## Can I get the terrain data analysis in 3D? (3D globe analysis)
Yes. Switch to "3D Globe" (or ask the assistant "open the 3D globe"). In 3D, click any point on the surface: the "Details" panel on
the right shows its latitude/longitude, the elevation from every elevation model that covers the point (HiRISE DTM, CTX DEM, global
MOLA/HRSC), the active data sources and the camera altitude, heading and tilt. The terrain is shown without vertical exaggeration. A
point clicked on the globe is also selected in the 2D planner, so you can then ask the assistant "analyse the terrain at the selected
point" for elevation, relief, slope statistics and the difficulty class within a radius. Routes A and B are draped on the 3D terrain,
and the route journey has an Immersive ground-level camera that follows the rover over the real terrain. The "Layers" panel on the
left switches the basemap, global elevation, CTX terrain and orthomosaic, HiRISE terrain and imagery, the Jezero marker and the data
coverage outlines.

## How to select a location and see its elevation
Click anywhere on the 2D map (or the 3D globe). The "Selected location" panel shows the latitude/longitude, the DEM elevation and the
calculated slope at that point. Hovering the 2D map shows a live x / y / z readout (longitude, latitude, elevation).

## How to plan a route (Route tab)
Open the Route tab. "Auto-route around hazards" plans Route B from the habitat through the planned stops, avoiding slopes above the
limit set by the "Max walkable slope" slider (5-25°, project default 15°). To draw your own Route A, press "Draw route by hand" and
click points on the map (drag a point to move it, right-click to delete it), then press "Finish route"; it is analysed on the DEM.
"Clear both routes" removes them. The Layers tab also lists the mission stops and has "Reset map view". The legend colours route sections: amber between 10° and the
limit, orange above the limit.

## How to read the elevation profile and compare routes
Below the 2D map, the elevation profile shows how the ground rises and falls along the route, coloured by slope; hovering it marks the
spot on the map. "Compare routes" lists distance, climbing and slopes of Route A and Route B side by side. The EVA budget bar shows the
walk out, science stops, walk back and the suit reserve.

## How to start the animated route journey
In the Route tab, under "Route journey", choose Route A or Route B and press "Start journey". A rover marker moves along the real route.
The journey panel shows progress, distance travelled and remaining, elevation and position, with Pause/Resume, Stop, Restart and a speed
selector (2 to 200 m/s). In 3D the camera follows the rover at ground level (Immersive), from above (Elevated) or shows the whole route
(Overview). The panel can be dragged by its top row and minimised with the ▾ button; the Space key pauses.

## How to use Presentation Mode
Press "Presentation Mode" at the top of the page to make the active 2D map or 3D globe fill the screen for a demonstration. Sidebars
and panels are hidden; a small bar keeps the 2D/3D switch and "Exit Presentation Mode". Escape also exits. The map position, routes and
3D camera are kept.

## How to save places to the mission log
The mission log lives inside this AI assistant: open it with the "☰ Log" button in the assistant's header. Select a point on the map,
then press "Save selected location", or ask the assistant "save this location to the log". Entries keep the name, coordinates, DEM
elevation and slope, and the current route as context. Search the log and click an entry to fly the map back to it.

## Map layers (Layers tab)
NASA base imagery: Viking colour mosaic (global, 232 m/px), CTX Jezero mosaics (about 6 m/px) and the HiRISE orthomosaic of the walk
zone (25 cm/px). Data layers: MOLA shaded elevation and THEMIS infrared, plus slope classes and hillshade computed from the DEM. Each
layer's "Info" lists its mission, instrument, resolution and limits.

## Environment tab and space weather
The top bar shows NASA DONKI space weather (solar flares, solar energetic particles, coronal mass ejections). The radiation reference
is 0.21 mGy/day measured at Gale crater by Curiosity RAD, used as context for Jezero; it is not a Jezero measurement. MEDA weather is
shown only when a calibrated MEDA dataset is installed.

## Data sources and limits
Elevation: Mars 2020 TRN HiRISE DTM (1 m) and CTX DEM (20 m) from NASA/JPL/USGS. Rover positions: NASA/JPL MMGIS M20_waypoints
(historical localisations, not live). Slopes are calculated on the DEM (Horn 3x3) and do not resolve boulders or small ledges. Slope
limits, caution thresholds, the suit time budget and the walking model are project-defined, not NASA standards. No route is guaranteed
safe; results are preliminary terrain assessments.
