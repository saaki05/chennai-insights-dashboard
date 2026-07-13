"""
Synthetic real-time GPS/traffic ping generator for the Chennai road network.

No live API key or paid data feed is required: this module produces
research-reproducible traffic pings driven by a deterministic time-of-day /
day-of-week congestion model (rush hours, IT-corridor shift traffic, transit
hub churn, occasional random incidents and monsoon-style rain events) plus
bounded stochastic noise. The output schema mirrors what a real GPS/traffic
provider (TomTom, HERE, Google Roads) would return, so `simulator.py` can be
swapped for a live client later without touching the clustering/API layers.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from config.chennai_network import ROADS, ZONES_BY_ID

# Points generated per km of road segment (keeps dense roads denser).
POINTS_PER_KM = 2.2

# Base free-flow speed (km/h) and effective capacity per road category.
CATEGORY_PROFILE = {
    "highway":     {"free_flow_kmph": 70, "capacity": 1.0},
    "ring_road":   {"free_flow_kmph": 60, "capacity": 0.85},
    "arterial":    {"free_flow_kmph": 45, "capacity": 0.65},
    "it_corridor": {"free_flow_kmph": 50, "capacity": 0.55},
}

RushWindow = tuple[float, float, float]  # (start_hour, end_hour, intensity 0-1)

# Morning + evening rush windows, per road category (IT corridor peaks later
# and harder in the evening — shift/cab traffic on OMR is a known Chennai
# pattern; transit hubs get an all-day baseline bump instead of sharp peaks).
RUSH_WINDOWS: dict[str, list[RushWindow]] = {
    "highway":     [(8.0, 10.5, 0.65), (17.5, 20.5, 0.75)],
    "ring_road":   [(8.0, 10.5, 0.60), (17.5, 20.5, 0.70)],
    "arterial":    [(8.5, 10.5, 0.70), (17.0, 20.0, 0.80)],
    "it_corridor": [(9.0, 10.5, 0.60), (17.5, 21.0, 0.90)],
}


def _gaussian_bump(hour: float, start: float, end: float, intensity: float) -> float:
    center = (start + end) / 2
    width = max((end - start) / 2, 0.5)
    return intensity * math.exp(-((hour - center) ** 2) / (2 * width ** 2))


def _time_of_day_factor(dt: datetime, category: str) -> float:
    hour = dt.hour + dt.minute / 60.0
    is_weekend = dt.weekday() >= 5

    base = 0.12  # night-time floor congestion
    for start, end, intensity in RUSH_WINDOWS[category]:
        scaled_intensity = intensity * (0.55 if is_weekend else 1.0)
        base += _gaussian_bump(hour, start, end, scaled_intensity)

    # Late-night deep trough (00:00-05:00) regardless of category.
    if hour < 5:
        base *= 0.35
    return min(base, 1.0)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Incident:
    road_id: str
    position_frac: float  # 0-1 along the segment
    severity: float        # extra congestion 0-1
    expires_at: float      # epoch seconds
    kind: Literal["accident", "waterlogging", "roadwork", "event"] = "accident"


@dataclass
class TrafficSimulator:
    """Stateful generator: call `.tick()` on each cadence to get a fresh
    snapshot of GPS/congestion pings, matching real-time streaming semantics.
    """
    rng_seed: int | None = None
    rain_active: bool = False
    incidents: list[Incident] = field(default_factory=list)
    _rng: random.Random = field(init=False, repr=False)
    _noise_state: dict[tuple[str, int], float] = field(default_factory=dict, init=False, repr=False)

    # AR(1) parameters for the per-point congestion noise process: each
    # point's noise is correlated with its own previous value rather than
    # redrawn independently every tick, so a single road doesn't visually
    # "flicker" between ticks and short-horizon forecasting has real signal
    # to extrapolate (a pure IID-noise feed is unforecastable by design).
    _AR_PHI = 0.86
    _AR_SIGMA = 0.05

    def __post_init__(self):
        self._rng = random.Random(self.rng_seed)

    def _next_noise(self, key: tuple[str, int]) -> float:
        prev = self._noise_state.get(key, 0.0)
        val = self._AR_PHI * prev + self._AR_SIGMA * self._rng.gauss(0, 1)
        val = max(-0.18, min(0.18, val))
        self._noise_state[key] = val
        return val

    # -- incident lifecycle -------------------------------------------------
    def _maybe_spawn_incident(self, now: float):
        if len(self.incidents) >= 4:
            return
        spawn_p = 0.006 if not self.rain_active else 0.018  # monsoon -> more waterlogging/incidents
        if self._rng.random() < spawn_p:
            road = self._rng.choice(ROADS)
            weights = [0.20, 0.45, 0.20, 0.15] if self.rain_active else [0.35, 0.15, 0.35, 0.15]
            kind = self._rng.choices(
                ["accident", "waterlogging", "roadwork", "event"],
                weights=weights,
            )[0]
            duration = self._rng.uniform(180, 900)  # 3-15 min simulated
            self.incidents.append(Incident(
                road_id=road["id"],
                position_frac=self._rng.uniform(0.15, 0.85),
                severity=self._rng.uniform(0.35, 0.85),
                expires_at=now + duration,
                kind=kind,
            ))

    def _active_incidents_for(self, road_id: str, now: float) -> list[Incident]:
        self.incidents = [i for i in self.incidents if i.expires_at > now]
        return [i for i in self.incidents if i.road_id == road_id]

    def toggle_rain(self, active: bool | None = None):
        self.rain_active = (not self.rain_active) if active is None else active
        return self.rain_active

    # -- core generation ------------------------------------------------
    def _segment_points(self, road: dict) -> list[tuple[float, float, float]]:
        """Interpolated (lat, lon, frac) points along a road segment."""
        a, b = ZONES_BY_ID[road["from"]], ZONES_BY_ID[road["to"]]
        dist_km = _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        n = max(3, round(dist_km * POINTS_PER_KM))
        pts = []
        for i in range(n):
            frac = i / max(n - 1, 1)
            lat = a["lat"] + (b["lat"] - a["lat"]) * frac
            lon = a["lon"] + (b["lon"] - a["lon"]) * frac
            # Small perpendicular jitter so points don't sit dead-straight
            # (real GPS traces wander within lane width / road curvature).
            jitter = self._rng.uniform(-0.0006, 0.0006)
            lat += jitter
            lon += jitter * 0.6
            pts.append((lat, lon, frac))
        return pts

    def tick(self, dt: datetime | None = None) -> list[dict]:
        dt = dt or datetime.now()
        now_epoch = time.time()
        self._maybe_spawn_incident(now_epoch)

        pings: list[dict] = []
        for road in ROADS:
            category = road["category"]
            profile = CATEGORY_PROFILE[category]
            tod_factor = _time_of_day_factor(dt, category)
            active_incidents = self._active_incidents_for(road["id"], now_epoch)

            for i, (lat, lon, frac) in enumerate(self._segment_points(road)):
                noise = self._next_noise((road["id"], i))
                congestion = tod_factor * profile["capacity"] + noise

                if self.rain_active:
                    congestion += 0.22

                for inc in active_incidents:
                    dist_along = abs(inc.position_frac - frac)
                    if dist_along < 0.12:
                        falloff = 1 - (dist_along / 0.12)
                        congestion += inc.severity * falloff

                congestion = max(0.02, min(congestion, 1.0))
                speed = profile["free_flow_kmph"] * (1 - 0.85 * congestion)
                speed = max(3.0, speed) * self._rng.uniform(0.95, 1.05)

                pings.append({
                    "id": str(uuid.uuid4()),
                    "road_id": road["id"],
                    "road_name": road["name"],
                    "category": category,
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "congestion": round(congestion, 3),
                    "speed_kmph": round(speed, 1),
                    "timestamp": dt.isoformat(),
                })

        return pings

    def active_incident_payload(self) -> list[dict]:
        now = time.time()
        self.incidents = [i for i in self.incidents if i.expires_at > now]
        out = []
        for inc in self.incidents:
            road = next(r for r in ROADS if r["id"] == inc.road_id)
            a, b = ZONES_BY_ID[road["from"]], ZONES_BY_ID[road["to"]]
            lat = a["lat"] + (b["lat"] - a["lat"]) * inc.position_frac
            lon = a["lon"] + (b["lon"] - a["lon"]) * inc.position_frac
            out.append({
                "road_id": inc.road_id,
                "road_name": road["name"],
                "kind": inc.kind,
                "severity": round(inc.severity, 2),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "expires_in_s": round(inc.expires_at - now),
            })
        return out
