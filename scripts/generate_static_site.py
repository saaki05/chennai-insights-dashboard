"""
Static-snapshot generator for the GitHub Pages deployment.

GitHub Pages can only serve static files — there's no persistent process to
run `backend.main`'s live WebSocket loop. Instead, this script runs one
simulator tick + the same DBSCAN clustering / forecasting modules the live
app uses, and writes the result as JSON under docs/data/. A GitHub Actions
cron job (.github/workflows/update-snapshot.yml) runs this on a schedule and
commits the output, so the published site updates periodically even with
zero server to host.

Run manually with: python scripts/generate_static_site.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.clustering import HotspotParams, detect_hotspots
from backend.forecasting import RoadHistory
from backend.simulator import TrafficSimulator
from config.chennai_network import BOUNDS, CENTER, ROADS, ZONES

DATA_DIR = ROOT / "docs" / "data"
HISTORY_MAX_POINTS_PER_ROAD = 150  # ~ 37 hours of history at a 15-min cadence
FORECAST_HORIZONS_S = [900, 3600, 10800]  # 15 min / 1 hr / 3 hr — matched to the update cadence


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return default
    return default


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    now_s = time.time()

    prior_snapshot = _load_json(DATA_DIR / "snapshot.json", {})
    tick = int(prior_snapshot.get("tick", 0)) + 1

    sim = TrafficSimulator()
    pings = sim.tick(now)
    result = detect_hotspots(pings, HotspotParams())
    hotspots, labels, diagnostics = result["hotspots"], result["labels"], result["diagnostics"]

    # -- aggregate per-road congestion for this tick --------------------
    by_road: dict[str, list[dict]] = {}
    for p in pings:
        by_road.setdefault(p["road_id"], []).append(p)

    history = _load_json(DATA_DIR / "history.json", {})
    for road_id, pts in by_road.items():
        mean_c = sum(p["congestion"] for p in pts) / len(pts)
        mean_s = sum(p["speed_kmph"] for p in pts) / len(pts)
        entry = {"ts": now_s, "mean_congestion": round(mean_c, 3), "mean_speed_kmph": round(mean_s, 1)}
        series = history.setdefault(road_id, [])
        series.append(entry)
        del series[:-HISTORY_MAX_POINTS_PER_ROAD]

    # -- forecast each road from the (now-updated) history ---------------
    road_history = RoadHistory()
    for road_id, series in history.items():
        for point in series:
            road_history.record(road_id, point["ts"], point["mean_congestion"])

    forecasts = {}
    for road_id in history:
        fc = road_history.forecast(road_id, FORECAST_HORIZONS_S)
        if fc:
            forecasts[road_id] = fc

    avg_congestion = round(sum(p["congestion"] for p in pings) / len(pings), 3) if pings else None
    max_congestion = round(max((p["congestion"] for p in pings), default=0), 3)

    meta = {
        "zones": ZONES,
        "roads": ROADS,
        "bounds": BOUNDS,
        "center": CENTER,
        "update_interval_minutes": 15,
        "mode": "static-github-pages",
    }
    snapshot = {
        "pings": pings,
        "labels": labels,
        "hotspots": hotspots,
        "diagnostics": diagnostics,
        "incidents": sim.active_incident_payload(),
        "rain_active": False,
        "tick": tick,
        "avg_congestion": avg_congestion,
        "max_congestion": max_congestion,
        "generated_at": now.isoformat(),
        "generated_at_epoch": now_s,
    }

    (DATA_DIR / "meta.json").write_text(json.dumps(meta))
    (DATA_DIR / "snapshot.json").write_text(json.dumps(snapshot))
    (DATA_DIR / "history.json").write_text(json.dumps(history))
    (DATA_DIR / "forecast.json").write_text(json.dumps(forecasts))

    print(f"[generate_static_site] tick={tick} pings={len(pings)} hotspots={len(hotspots)} "
          f"avg_congestion={avg_congestion} generated_at={now.isoformat()}")


if __name__ == "__main__":
    main()
