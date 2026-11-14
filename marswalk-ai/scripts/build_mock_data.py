"""Build the standalone mission dataset from the original Marswalk data files.

Reads (never modifies):
  <marswalk>/data/stops.json          habitat + stops on real Perseverance positions
  <marswalk>/data/M20_waypoints.json  NASA/JPL-Caltech MMGIS rover localisations

Writes:
  backend/marswalk_ai/data/mission.json
  backend/marswalk_ai/data/rover_waypoints.json

Usage:  python scripts/build_mock_data.py [path-to-marswalk-root]
Default root: the parent folder of marswalk-ai/.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_ROOT = HERE.parent
OUT = AI_ROOT / "backend" / "marswalk_ai" / "data"
WAYPOINTS_URL = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json"


def fix(s):
    # data/stops.json contains UTF-8 "·" that was decoded as latin-1 ("Â·")
    return s.replace("Â·", "·") if isinstance(s, str) else s


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else AI_ROOT.parent
    stops = json.loads((root / "data" / "stops.json").read_text(encoding="utf-8"))
    wps = json.loads((root / "data" / "M20_waypoints.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    aliases = {
        "H": ["habitat", "base", "home", "camp", "start", "sample depot", "three forks"],
        "1": ["delta", "the delta", "delta front", "hawksbill gap", "stop 1", "first stop"],
        "2": ["upper delta-front ridge", "upper delta front ridge", "ridge", "stop 2", "second stop"],
        "3": ["crater floor", "crater floor east of the depot", "stop 3", "third stop"],
    }

    def poi(src, kind):
        pid = "habitat" if src["id"] == "H" else f"stop-{src['id']}"
        return {
            "id": pid,
            "name": fix(src["name"]),
            "kind": kind,
            "lat": src["lat"],
            "lon": src["lon"],
            "aliases": aliases.get(src["id"], []),
            "reason": fix(src.get("reason")),
            "rover_sol": src.get("rover_sol"),
            "rover_elev_geoid_m": src.get("rover_elev_geoid_m"),
            "source": src.get("source"),
            "provenance": "Position = real Perseverance end-of-drive localisation (NASA/JPL-Caltech MMGIS). "
                          "Name and reason = Marswalk starter set, not yet confirmed by the science team.",
        }

    pois = [poi(stops["habitat"], "habitat")] + [poi(s, "science_stop") for s in stops["stops"]]
    first = wps["features"][0]["properties"]
    pois.append({
        "id": "landing",
        "name": "Octavia E. Butler Landing (Perseverance landing site)",
        "kind": "landmark",
        "lat": first["lat"], "lon": first["lon"],
        "aliases": ["landing site", "landing", "octavia e. butler landing", "octavia butler"],
        "reason": f"Perseverance localisation at sol {first['sol']}.",
        "rover_sol": first["sol"], "rover_elev_geoid_m": first["elev_geoid"],
        "source": WAYPOINTS_URL,
        "provenance": "NASA/JPL-Caltech MMGIS M20_waypoints.json (first localisation).",
    })
    pois.append({
        "id": "jezero-centre",
        "name": "Jezero Crater centre (IAU)",
        "kind": "landmark",
        "lat": 18.4082, "lon": 77.6873,
        "aliases": ["crater centre", "crater center", "jezero centre", "jezero center", "centre of jezero", "center of jezero"],
        "reason": "Nominal crater centre; the crater is 47.52 km across.",
        "source": "IAU Gazetteer of Planetary Nomenclature, feature 14300",
        "provenance": "IAU Gazetteer (as used by Marswalk 3D, js/jezero-region.js).",
    })

    mission = {
        "mission_id": "EVA-01",
        "name": "EVA-01 · Jezero delta",
        "status": "planning",
        "status_source": "MOCK - the original Marswalk app has no mission-status model (see MARSWALK_TECHNICAL_REFERENCE.md §10)",
        "coordinate_system": "Mars planetocentric latitude, east-positive longitude (IAU2000:49900 sphere, R = 3 396 190 m)",
        "bounds": [[18.2110274, 77.1604655], [18.7211945, 77.6991776]],
        "current_position_poi": "habitat",
        "current_position_source": "MOCK - astronaut position assumed at the habitat",
        "planned_stops": [p["id"] for p in pois if p["kind"] == "science_stop"],
        "pois": pois,
        "built_from": [str((root / "data" / "stops.json").name), str((root / "data" / "M20_waypoints.json").name)],
    }
    (OUT / "mission.json").write_text(json.dumps(mission, indent=2, ensure_ascii=False), encoding="utf-8")

    reduced = [{
        "sol": f["properties"]["sol"],
        "lat": round(f["properties"]["lat"], 7),
        "lon": round(f["properties"]["lon"], 7),
        "elev_geoid_m": f["properties"]["elev_geoid"],
        "dist_total_m": f["properties"].get("dist_total_m"),
    } for f in wps["features"]]
    (OUT / "rover_waypoints.json").write_text(json.dumps({
        "source": WAYPOINTS_URL,
        "credit": "NASA/JPL-Caltech",
        "note": "Historical end-of-drive localisations. Snapshot copied from the Marswalk data folder; not live.",
        "count": len(reduced),
        "waypoints": reduced,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT / 'mission.json'} ({len(pois)} POIs) and rover_waypoints.json ({len(reduced)} points)")


if __name__ == "__main__":
    main()
