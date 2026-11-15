---
document_id: "DOC-ENV-004"
title: "The Mars Environmental Dynamics Analyzer (MEDA) Instrument Suite on Mars 2020"
source_url: "https://doi.org/10.1007/s11214-021-00816-9"
organization: "NASA / Centro de Astrobiología (CAB) / JPL"
mission: "Mars 2020"
instrument: "MEDA (ATS, PS, TIRS, WS, RDS, HS)"
publication_date: "2021-06-25"
document_type: "Instrument Calibration Document"
scientific_topic: "atmospheric_meda"
relevant_dataset: "Mars 2020 MEDA Calibrated Sensor Records"
geographic_coverage: "Perseverance Local Chassis Environment"
reliability_category: "nasa_official"
---

# MEDA Atmospheric Dynamics: Baseline Ranges, Local Variations, and Limitations

## 1. Sensor Capabilities and Measured Variables
MEDA measures:
- **Surface Atmospheric Pressure (PS):** Barometric pressure range 650 to 850 Pa (~6.5 to 8.5 mbar) at Jezero crater floor elevation (~ -2500m relative to MOLA datum). Accuracy ± 1 Pa.
- **Air Temperature (ATS):** Measured at 0.85m and 1.45m above the surface. Diurnal swings range from -85°C (late nocturnal minimum) to -15°C (early afternoon peak).
- **Wind Speed and Direction (WS):** Thermal film sensors measuring 0 to 40 m/s with ±0.5 m/s precision.
- **Ground Temperature (TIRS):** Radiative surface temperature characterizing local regolith thermal inertia.

## 2. Atmospheric Density & Spacesuit Convective Heat Loss
At Martian surface pressures (~750 Pa), atmospheric density is ~0.015 kg/m³ (less than 1% of terrestrial sea-level density). Convective heat loss is minimal compared to terrestrial environments; conductive heat transfer through suit soles into freezing Martian regolith and radiative cooling into space dominate the suit thermal budget.

## 3. Crucial Provenance & Generalization Limitations
- **Perseverance Local Environment:** MEDA sensors are physically attached to the rover mast and body. Readings reflect the microclimate of Perseverance's exact position.
- **Crater-Wide Extrapolation Prohibited:** Pressure decreases with altitude by roughly 1 Pa per 10m of elevation gain. Ascending the Jezero delta or rim (+500m to +1000m) drops ambient pressure significantly.
- **Temporal Stationarity:** Measurements represent historical snapshots recorded on specific Sols. They must not be presented as live forecasts across Jezero crater.
