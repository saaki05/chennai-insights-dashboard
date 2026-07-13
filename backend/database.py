"""
SQLite persistence for aggregated traffic snapshots and hotspot events.

Raw per-point GPS pings are NOT stored (they're regenerated live and would
bloat the DB for no analytical benefit); instead each tick is aggregated to
one row per road, which is exactly the granularity the research notebook and
the historical-trend chart need.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "chennai_traffic.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS road_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    road_id TEXT NOT NULL,
    road_name TEXT NOT NULL,
    category TEXT NOT NULL,
    mean_congestion REAL NOT NULL,
    mean_speed_kmph REAL NOT NULL,
    point_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_road_snapshots_road_ts ON road_snapshots(road_id, ts);

CREATE TABLE IF NOT EXISTS hotspot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cluster_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    point_count INTEGER NOT NULL,
    mean_congestion REAL NOT NULL,
    severity TEXT NOT NULL,
    roads TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hotspot_events_ts ON hotspot_events(ts);
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def save_snapshot(pings: list[dict], hotspots: list[dict]):
    ts = time.time()

    by_road: dict[str, list[dict]] = {}
    for p in pings:
        by_road.setdefault(p["road_id"], []).append(p)

    rows = []
    for road_id, pts in by_road.items():
        mean_c = sum(p["congestion"] for p in pts) / len(pts)
        mean_s = sum(p["speed_kmph"] for p in pts) / len(pts)
        rows.append((ts, road_id, pts[0]["road_name"], pts[0]["category"], mean_c, mean_s, len(pts)))

    hotspot_rows = [
        (ts, h["cluster_id"], h["centroid"]["lat"], h["centroid"]["lon"],
         h["point_count"], h["mean_congestion"], h["severity"], json.dumps(h["roads"]))
        for h in hotspots
    ]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO road_snapshots (ts, road_id, road_name, category, mean_congestion, mean_speed_kmph, point_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        if hotspot_rows:
            await db.executemany(
                "INSERT INTO hotspot_events (ts, cluster_id, lat, lon, point_count, mean_congestion, severity, roads) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                hotspot_rows,
            )
        await db.commit()


async def get_road_history(road_id: str, since_s: float) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT ts, mean_congestion, mean_speed_kmph FROM road_snapshots "
            "WHERE road_id = ? AND ts >= ? ORDER BY ts ASC",
            (road_id, since_s),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_recent_hotspot_events(since_s: float, limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT ts, cluster_id, lat, lon, point_count, mean_congestion, severity, roads "
            "FROM hotspot_events WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since_s, limit),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["roads"] = json.loads(d["roads"])
            out.append(d)
        return out


async def get_city_stats(since_s: float) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT AVG(mean_congestion), MAX(mean_congestion), COUNT(DISTINCT road_id) "
            "FROM road_snapshots WHERE ts >= ?",
            (since_s,),
        )
        avg_c, max_c, n_roads = await cur.fetchone()
        return {
            "avg_congestion": round(avg_c, 3) if avg_c is not None else None,
            "max_congestion": round(max_c, 3) if max_c is not None else None,
            "roads_tracked": n_roads,
        }
