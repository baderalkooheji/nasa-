"""Download NASA Perseverance rover data into nasa/data/ so the map works offline.

Run from the nasa/ folder:   python tools/fetch_nasa_data.py
Source: NASA/JPL-Caltech Mars 2020 MMGIS (same files the live map reads).
Coordinates are planetocentric latitude, east-positive longitude.
"""
import json
import pathlib
import urllib.request

BASE = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/"
FILES = ["M20_waypoints.json", "M20_traverse.json"]
OUT = pathlib.Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)

for name in FILES:
    url = BASE + name
    print("Downloading", url)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    data["source"] = url
    (OUT / name).write_text(json.dumps(data, separators=(",", ":")))
    n = len(data.get("features", []))
    print(f"  saved {OUT / name}  ({n} features)")

print("Done. Credit: NASA/JPL-Caltech.")
