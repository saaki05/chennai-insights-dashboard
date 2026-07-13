"""
Congestion-hotspot detection via density-based clustering.

Uses DBSCAN with a haversine metric (true great-circle distance, correct for
lat/lon) and per-point `sample_weight` derived from congestion level, so
dense clusters of *severely congested* points are favoured as cluster cores
over dense clusters of merely busy-but-flowing traffic. This is the
"clustering" piece referenced in the project brief, applied to the live
simulated feed instead of a static dataset.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors

EARTH_RADIUS_KM = 6371.0088


@dataclass
class HotspotParams:
    eps_km: float | None = None  # None => derive from data via k-distance heuristic
    min_samples: int = 6       # min weighted "mass" to form a core point
    congestion_floor: float = 0.35  # ignore free-flowing points entirely
    eps_bounds_km: tuple[float, float] = (0.20, 0.90)


def _auto_eps_km(coords_rad: np.ndarray, k: int) -> float:
    """Data-driven eps: median distance to each point's k-th nearest
    neighbour (the standard DBSCAN elbow heuristic), instead of a hand-tuned
    constant. Lets the model tighten up when congestion is sparse and relax
    when a citywide event (e.g. rain) makes most of the network busy at once.
    """
    k_eff = min(k, len(coords_rad) - 1)
    if k_eff < 1:
        return 0.45
    nn = NearestNeighbors(n_neighbors=k_eff + 1, metric="haversine").fit(coords_rad)
    dist_rad, _ = nn.kneighbors(coords_rad)
    kth_dist_km = dist_rad[:, -1] * EARTH_RADIUS_KM
    return float(np.median(kth_dist_km))


def _severity_tier(mean_congestion: float) -> str:
    if mean_congestion >= 0.75:
        return "severe"
    if mean_congestion >= 0.55:
        return "high"
    if mean_congestion >= 0.35:
        return "moderate"
    return "low"


def detect_hotspots(pings: list[dict], params: HotspotParams = HotspotParams()) -> dict:
    """Cluster congested GPS pings into named hotspots.

    Returns a dict with `hotspots` (list) and `labels` (per-input-ping
    cluster id, -1 = noise) so callers can also render raw point colouring.
    """
    empty_diagnostics = {"eps_km_used": None, "min_samples": params.min_samples,
                          "points_considered": 0, "noise_ratio": None, "mean_cohesion_km": None}
    if not pings:
        return {"hotspots": [], "labels": [], "diagnostics": empty_diagnostics}

    congested = [p for p in pings if p["congestion"] >= params.congestion_floor]
    if len(congested) < params.min_samples:
        return {"hotspots": [], "labels": [-1] * len(pings), "diagnostics": {**empty_diagnostics, "points_considered": len(congested)}}

    coords_rad = np.radians(np.array([[p["lat"], p["lon"]] for p in congested]))
    weights = np.array([p["congestion"] for p in congested]) * 10.0  # scale 0-1 -> 0-10 mass

    lo, hi = params.eps_bounds_km
    eps_km = params.eps_km if params.eps_km is not None else _auto_eps_km(coords_rad, params.min_samples)
    eps_km = float(np.clip(eps_km, lo, hi))

    eps_rad = eps_km / EARTH_RADIUS_KM
    db = DBSCAN(eps=eps_rad, min_samples=params.min_samples, metric="haversine",
                algorithm="ball_tree")
    labels = db.fit_predict(coords_rad, sample_weight=weights)

    hotspots = []
    for cluster_id in sorted(set(labels)):
        if cluster_id == -1:
            continue
        members = [congested[i] for i in range(len(congested)) if labels[i] == cluster_id]
        lats = [m["lat"] for m in members]
        lons = [m["lon"] for m in members]
        congestions = [m["congestion"] for m in members]
        centroid_lat = float(np.mean(lats))
        centroid_lon = float(np.mean(lons))
        mean_congestion = float(np.mean(congestions))

        member_dists = [_haversine_km(centroid_lat, centroid_lon, m["lat"], m["lon"]) for m in members]
        radius_km = max(member_dists, default=0.0)
        roads = sorted({m["road_name"] for m in members})

        hotspots.append({
            "cluster_id": int(cluster_id),
            "centroid": {"lat": round(centroid_lat, 6), "lon": round(centroid_lon, 6)},
            "point_count": len(members),
            "mean_congestion": round(mean_congestion, 3),
            "max_congestion": round(max(congestions), 3),
            "radius_km": round(radius_km, 3),
            "cohesion_km": round(float(np.mean(member_dists)), 3),
            "severity": _severity_tier(mean_congestion),
            "roads": roads[:5],
        })

    hotspots.sort(key=lambda h: h["mean_congestion"], reverse=True)

    # Map labels back onto the *original* pings list (unclustered/free-flow -> -1)
    label_by_id = {congested[i]["id"]: int(labels[i]) for i in range(len(congested))}
    full_labels = [label_by_id.get(p["id"], -1) for p in pings]

    n_noise = int(np.sum(labels == -1))
    diagnostics = {
        "eps_km_used": round(eps_km, 3),
        "min_samples": params.min_samples,
        "points_considered": len(congested),
        "noise_ratio": round(n_noise / len(congested), 3),
        "mean_cohesion_km": round(float(np.mean([h["cohesion_km"] for h in hotspots])), 3) if hotspots else None,
    }

    return {"hotspots": hotspots, "labels": full_labels, "diagnostics": diagnostics}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def sweep_eps(pings: list[dict], eps_values_km: list[float], min_samples: int = 6) -> list[dict]:
    """Parameter-sweep helper for research/notebook use: for each eps value,
    report cluster count and noise ratio so eps can be chosen empirically
    (a simple stand-in for a k-distance elbow plot).
    """
    results = []
    for eps in eps_values_km:
        out = detect_hotspots(pings, HotspotParams(eps_km=eps, min_samples=min_samples))
        n_noise = sum(1 for l in out["labels"] if l == -1)
        results.append({
            "eps_km": eps,
            "n_clusters": len(out["hotspots"]),
            "noise_ratio": round(n_noise / len(out["labels"]), 3) if out["labels"] else None,
        })
    return results
