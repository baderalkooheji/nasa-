---
document_id: "DOC-SW-006"
title: "NASA Space Weather Architecture for Human Mars Exploration & DONKI Thresholds"
source_url: "https://ntrs.nasa.gov/citations/20210014389"
organization: "NASA Goddard Space Flight Center / CCMC"
mission: "Multi-Mission Space Weather Operations"
instrument: "CCMC DONKI (Database Of Notifications, Knowledge, Information)"
publication_date: "2021-08-12"
document_type: "NASA Technical Memorandum"
scientific_topic: "space_weather_donki"
relevant_dataset: "CCMC DONKI CME, Flare, and SEP Notification Streams"
geographic_coverage: "Interplanetary Inner Heliosphere (Earth to Mars)"
reliability_category: "nasa_official"
---

# Space Weather Hazards: Solar Energetic Particles, Flares, and Shelter Rules

## 1. Solar Radiation Hazards to Surface Exploration
During Solar Particle Events (SPEs) triggered by fast Coronal Mass Ejections (CMEs) and X-class solar flares, flux of high-energy protons (>10 MeV to >100 MeV) can increase by several orders of magnitude within hours.
- Unshielded astronauts during an active EVA face acute radiation syndromes (ARS) or exceeding NASA career dose limits if caught outside base habitat storm shelters during peak flux.

## 2. DONKI Event Categories & Hazard Triggers
NASA CCMC DONKI tracks:
1. **Solar Flares (FLR):** Classified as C (minor), M (moderate), and X (severe). Flares $\ge M5.0$ or any $X$-class flare indicate severe magnetic reconnection and probable particle acceleration.
2. **Coronal Mass Ejections (CME):** Tracked by angular width, speed (km/s), and propagation direction. Interplanetary shocks accelerate local proton populations.
3. **Solar Energetic Particle (SEP) Events:** Direct in-situ particle flux measurements. Standard threshold: Proton flux exceeding 10 pfu (particle flux units, protons/(cm²·s·sr)) at energies $\ge 10\text{ MeV}$.

## 3. Mission Planner Alert Rules
- **"Caution: shelter-ready":** Triggered when an active SEP event exceeds 10 pfu at $\ge 10\text{ MeV}$, or an X-class / fast Earth-Mars directed CME occurred within the 7-day lookback window without confirmed decay. Astronauts must remain within immediate walking distance of pressurized habitat or terrain storm shelters.
- **"Nominal":** Triggered when background quiescent solar activity prevails, solar flare activity is below M5.0, and no SEP alerts are active.
