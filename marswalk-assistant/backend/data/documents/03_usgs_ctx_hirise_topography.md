---
document_id: "DOC-TOPO-003"
title: "USGS Planetary Topography Specifications: CTX and HiRISE Digital Elevation Models"
source_url: "https://astrogeology.usgs.gov/search/map/Mars/Mars2020/JEZERO_CTX_DEM_20M"
organization: "USGS Astrogeology Science Center / NASA PDS"
mission: "Mars Reconnaissance Orbiter / Mars 2020"
instrument: "CTX / HiRISE"
publication_date: "2020-05-15"
document_type: "PDS Dataset Specification"
scientific_topic: "terrain_traversability"
relevant_dataset: "Mars 2020 Science Investigation CTX DEM Mosaic / TRN HiRISE DTM"
geographic_coverage: "Jezero Crater"
reliability_category: "nasa_official"
---

# USGS Topographic Data Guidelines: Resolution, Errors, and Slope Derivation

## 1. CTX DEM Mosaic Characteristics
- **Spatial Resolution:** ~20.0 meters per pixel.
- **Vertical Accuracy:** Approximately 2 to 4 meters root-mean-square error (RMSE) depending on stereo parallax geometry.
- **Coordinate Reference System:** Mars 2020 Equirectangular (Planetocentric latitude, center longitude 0.0°, IAU 2000 Mars spheroid with radius 3,396,190 meters).
- **Scientific Limitation:** 20m pixel spacing mathematically smooths high-frequency topographic variations. Micro-topography, meter-scale rock distributions, and sharp 1-meter escarpments are averaged into the cell slope value.

## 2. HiRISE DTM Mosaic Characteristics
- **Spatial Resolution:** ~1.0 to 2.0 meters per pixel.
- **Vertical Accuracy:** Approximately 0.2 to 0.5 meters RMSE.
- **Footprint:** Restricted to the Perseverance landing ellipse and primary delta exploration corridor.
- **Operational Rule:** Where HiRISE elevation data exists, it must supersede CTX data for fine-scale route cost calculation and boulder field obstacle avoidance.

## 3. Operational Slope Hazard Thresholds
- **0° – 5° (Nominal / Flat):** Smooth regolith or basaltic pavement. Minimum metabolic and mechanical traversal cost.
- **5° – 10° (Moderate):** Gentle inclines, small ripple flanks. Minor walking speed degradation.
- **10° – 15° (Caution):** Steep slopes. Requires zig-zag path planning; suit joint articulation begins to limit metabolic efficiency.
- **> 15° (Critical Slope Limit):** Impassable operational threshold for nominal Martian EVA planning. Risk of down-slope scree sliding, spacesuit falls, and thermal control overload.
