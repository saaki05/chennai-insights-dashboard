"""
FastAPI backend for the Real-Time Chennai Insights Dashboard.

Runs a background loop that ticks the traffic simulator, clusters the
resulting pings into congestion hotspots (DBSCAN), persists aggregates to
SQLite, updates the rolling forecast model, and broadcasts each snapshot to
connected dashboard clients over a WebSocket. REST endpoints expose the same
data for one-off queries, historical charts, and the research notebook.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import database
from backend.clustering import HotspotParams, detect_hotspots, sweep_eps
from backend.forecasting import RoadHistory
from backend.simulator import TrafficSimulator
from backend.ws_manager import ConnectionManager
from config.chennai_network import BOUNDS, CENTER, ROADS, ZONES, road_geometry

TICK_SECONDS = 3.0
PERSIST_EVERY_N_TICKS = 7  # ~ every 21s
FORECAST_HORIZONS_S = [300, 900, 1800]  # 5 / 15 / 30 min

simulator = TrafficSimulator()
manager = ConnectionManager()
history = RoadHistory()

state = {
    "latest_pings": [],
    "latest_hotspots": [],
    "latest_labels": [],
    "latest_diagnostics": {},
    "alerts": [],
    "tick_count": 0,
    "started_at": time.time(),
}


def _diff_new_severe_hotspots(prev: list[dict], curr: list[dict]) -> list[dict]:
    """Emit an alert for any severe/high hotspot that has no spatial match
    in the previous snapshot (cheap proximity check, good enough at this
    point density)."""
    alerts = []
    for h in curr:
        if h["severity"] not in ("severe", "high"):
            continue
        matched = any(
            abs(p["centroid"]["lat"] - h["centroid"]["lat"]) < 0.003
            and abs(p["centroid"]["lon"] - h["centroid"]["lon"]) < 0.003
            for p in prev
        )
        if not matched:
            alerts.append({
                "ts": time.time(),
                "severity": h["severity"],
                "message": f"New {h['severity']} congestion hotspot near {h['roads'][0] if h['roads'] else 'unknown road'}",
                "centroid": h["centroid"],
                "mean_congestion": h["mean_congestion"],
            })
    return alerts


async def simulation_loop():
    await database.init_db()
    while True:
        try:
            dt_now = None
            pings = simulator.tick(dt_now)
            result = detect_hotspots(pings, HotspotParams())
            hotspots, labels, diagnostics = result["hotspots"], result["labels"], result["diagnostics"]

            new_alerts = _diff_new_severe_hotspots(state["latest_hotspots"], hotspots)
            state["alerts"] = (new_alerts + state["alerts"])[:50]

            state["latest_pings"] = pings
            state["latest_hotspots"] = hotspots
            state["latest_labels"] = labels
            state["latest_diagnostics"] = diagnostics
            state["tick_count"] += 1

            by_road: dict[str, list[float]] = {}
            for p in pings:
                by_road.setdefault(p["road_id"], []).append(p["congestion"])
            now_s = time.time()
            for road_id, vals in by_road.items():
                history.record(road_id, now_s, sum(vals) / len(vals))

            if state["tick_count"] % PERSIST_EVERY_N_TICKS == 0:
                await database.save_snapshot(pings, hotspots)

            await manager.broadcast({
                "type": "snapshot",
                "pings": pings,
                "labels": labels,
                "hotspots": hotspots,
                "diagnostics": diagnostics,
                "incidents": simulator.active_incident_payload(),
                "alerts": new_alerts,
                "rain_active": simulator.rain_active,
                "tick": state["tick_count"],
                "server_time": now_s,
            })
        except Exception as exc:  # keep the loop alive across transient errors
            print(f"[simulation_loop] error: {exc!r}")

        await asyncio.sleep(TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(simulation_loop())
    yield
    task.cancel()


app = FastAPI(title="Real-Time Chennai Insights Dashboard", lifespan=lifespan)

FRONTEND_DIR = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/meta")
async def meta():
    roads_with_geometry = [{**r, "geometry": road_geometry(r)} for r in ROADS]
    return {"zones": ZONES, "roads": roads_with_geometry, "bounds": BOUNDS, "center": CENTER}


@app.get("/api/snapshot")
async def snapshot():
    return {
        "pings": state["latest_pings"],
        "labels": state["latest_labels"],
        "hotspots": state["latest_hotspots"],
        "diagnostics": state["latest_diagnostics"],
        "incidents": simulator.active_incident_payload(),
        "rain_active": simulator.rain_active,
        "tick": state["tick_count"],
    }


@app.get("/api/alerts")
async def alerts():
    return {"alerts": state["alerts"]}


@app.get("/api/history/{road_id}")
async def road_history(road_id: str, minutes: float = 30):
    since = time.time() - minutes * 60
    rows = await database.get_road_history(road_id, since)
    return {"road_id": road_id, "points": rows}


@app.get("/api/forecast/{road_id}")
async def road_forecast(road_id: str):
    result = history.forecast(road_id, FORECAST_HORIZONS_S)
    if result is None:
        return {"road_id": road_id, "predictions": [], "note": "insufficient history yet"}
    return result


@app.get("/api/stats")
async def stats(minutes: float = 30):
    since = time.time() - minutes * 60
    city = await database.get_city_stats(since)
    uptime_s = time.time() - state["started_at"]
    return {**city, "uptime_s": round(uptime_s), "roads_total": len(ROADS), "zones_total": len(ZONES)}


@app.get("/api/hotspot-events")
async def hotspot_events(minutes: float = 60):
    since = time.time() - minutes * 60
    return {"events": await database.get_recent_hotspot_events(since)}


class EpsSweepRequest(BaseModel):
    eps_values_km: list[float]
    min_samples: int = 6


@app.post("/api/research/eps-sweep")
async def eps_sweep(req: EpsSweepRequest):
    """Research helper: run DBSCAN over a range of eps values against the
    *current* live snapshot so the effect of the neighbourhood-radius
    hyperparameter can be inspected without leaving the dashboard."""
    return {"results": sweep_eps(state["latest_pings"], req.eps_values_km, req.min_samples)}


class RainToggleRequest(BaseModel):
    active: bool | None = None


@app.post("/api/rain/toggle")
async def rain_toggle(req: RainToggleRequest):
    active = simulator.toggle_rain(req.active)
    return {"rain_active": active}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "pings": state["latest_pings"],
            "labels": state["latest_labels"],
            "hotspots": state["latest_hotspots"],
            "diagnostics": state["latest_diagnostics"],
            "incidents": simulator.active_incident_payload(),
            "alerts": [],
            "rain_active": simulator.rain_active,
            "tick": state["tick_count"],
            "server_time": time.time(),
        }))
        while True:
            await websocket.receive_text()  # keep-alive / ignore client pings
    except WebSocketDisconnect:
        manager.disconnect(websocket)
