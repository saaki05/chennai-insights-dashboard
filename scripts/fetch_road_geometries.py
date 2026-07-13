"""
One-time fetch of real road-following geometry for each of the 28 Chennai
road segments in config/chennai_network.py.

Previously the simulator interpolated a *straight line* between each pair of
junction coordinates — visually nothing like a real road, and a big part of
why the map looked "mild" next to Google Maps' traffic layer (which draws
colored lines that actually hug the street). This script queries OSRM's
free public routing API (no key, no account — https://project-osrm.org,
their public demo server) once per road to get the real driving-route
polyline between the two junctions, and caches it to
config/road_geometries.json so it's fetched once, not on every run.

Run manually with: python scripts/fetch_road_geometries.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.chennai_network import ROADS, ZONES_BY_ID

OUT_PATH = ROOT / "config" / "road_geometries.json"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"


def fetch_route(lat1, lon1, lat2, lon2) -> list[list[float]] | None:
    url = OSRM_URL.format(lon1=lon1, lat1=lat1, lon2=lon2, lat2=lat2)
    req = urllib.request.Request(url, headers={"User-Agent": "chennai-insights-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  ! request failed: {exc!r}")
        return None

    if data.get("code") != "Ok" or not data.get("routes"):
        print(f"  ! no route: {data.get('code')}")
        return None

    coords = data["routes"][0]["geometry"]["coordinates"]  # [lon, lat] pairs
    return [[lat, lon] for lon, lat in coords]


def main():
    existing = {}
    if OUT_PATH.exists():
        existing = json.loads(OUT_PATH.read_text())

    geometries = dict(existing)
    for road in ROADS:
        if road["id"] in geometries:
            print(f"skip {road['id']} ({road['name']}) — already cached")
            continue

        a, b = ZONES_BY_ID[road["from"]], ZONES_BY_ID[road["to"]]
        print(f"fetching {road['id']} ({road['name']}): {road['from']} -> {road['to']}")
        route = fetch_route(a["lat"], a["lon"], b["lat"], b["lon"])
        if route:
            geometries[road["id"]] = route
            print(f"  ok, {len(route)} points")
        else:
            print(f"  falling back to straight line for {road['id']}")
            geometries[road["id"]] = [[a["lat"], a["lon"]], [b["lat"], b["lon"]]]

        OUT_PATH.write_text(json.dumps(geometries))
        time.sleep(1.1)  # be polite to the free public demo server

    print(f"\nSaved {len(geometries)} road geometries to {OUT_PATH}")


if __name__ == "__main__":
    main()
