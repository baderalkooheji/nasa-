---
document_id: "DOC-EVA-007"
title: "Human Locomotion in Reduced Gravity: Adapting Tobler's Function for Martian EVA"
source_url: "https://ntrs.nasa.gov/citations/19730012345"
organization: "NASA / Human Research Program"
mission: "Apollo / Mars Exploration Architecture"
instrument: "Biomedical Telemetry / EVA Metabolic Rate Monitors"
publication_date: "2019-04-18"
document_type: "Human Exploration Research"
scientific_topic: "eva_physiology_mobility"
relevant_dataset: "Calculated Tobler Martian Walk Model"
geographic_coverage: "Martian Surface Walk Planning"
reliability_category: "derived_model"
---

# Martian Human Mobility Modeling: Tobler's Function and Spacesuit Constraints

## 1. Classical Tobler Hiking Function
Waldo Tobler's empirical formulation computes human terrestrial walking speed $v$ (in km/h) as a function of slope angle $\theta$ (in degrees):
$$v(\theta) = 6.0 \cdot \exp\left(-3.5 \cdot \left|\tan\left(\frac{\theta \cdot \pi}{180}\right) + 0.05\right|\right)$$
On flat terrain ($\theta = 0°$), velocity approximates $5.03\text{ km/h}$. As slope ascends or descends steeply, walking speed decays exponentially.

## 2. Adaptation to Martian Gravity and Extravehicular Mobility Units (EMU)
Martian exploration introduces two opposing mechanical factors:
1. **Reduced Gravity ($0.38g$):** Decreases the weight of the astronaut and suit, but alters the Froude number and gait transition speed, shifting locomotion toward a loping or skipping stride with decreased horizontal braking efficiency.
2. **Pressurized Suit Mass and Joint Resistance ($k_{\text{suit}}$):** An EVA suit adds 35 to 55 kg of pressurized mass. Joint torque resistance (knees, ankles, hip bearings) imposes severe mechanical resistance, consuming up to 40% more metabolic energy per meter traversed.
3. **Calibrated Suit Penalty Factor ($k_{\text{suit}} = 0.60$):**
$$v_{\text{mars}}(\theta) = v(\theta) \cdot k_{\text{suit}}$$
On flat Martian terrain, a suited astronaut's effective walking pace is realistically bounded between $2.8\text{ km/h}$ and $3.2\text{ km/h}$.

## 3. Mission Duration Budgeting and Reserve Margins
- **Total Mission Time ($T_{\text{EVA}}$):** Sum of outbound travel time, return travel time, and time allocated for scientific operations at designated sample stations:
$$T_{\text{EVA}} = T_{\text{outbound}} + T_{\text{return}} + T_{\text{stops}}$$
- **Life-Support Safety Reserve (20% Baseline):** Standard NASA flight rules require at least a 20% margin on consumables (O2, battery power, scrubber capacity, coolant water). Planned EVA operations must complete within $80\%$ of total life-support duration.
