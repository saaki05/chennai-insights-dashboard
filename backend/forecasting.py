"""
Lightweight short-horizon congestion forecasting.

Deliberately simple and transparent (linear trend over a recent rolling
window, not a black-box model) so the method is easy to reason about and
reproduce in the research notebook: forecast_t+k = last_value + slope*k,
clamped to [0, 1]. Swappable later for ARIMA/Prophet/an LSTM without
touching callers, since the interface is just `RoadHistory.forecast()`.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

WINDOW_SIZE = 40  # number of recent ticks kept per road


@dataclass
class RoadHistory:
    maxlen: int = WINDOW_SIZE
    _series: dict[str, deque] = field(default_factory=dict)

    def record(self, road_id: str, timestamp_s: float, mean_congestion: float):
        buf = self._series.setdefault(road_id, deque(maxlen=self.maxlen))
        buf.append((timestamp_s, mean_congestion))

    def forecast(self, road_id: str, horizons_s: list[int]) -> dict | None:
        buf = self._series.get(road_id)
        if not buf or len(buf) < 4:
            return None

        times = np.array([t for t, _ in buf])
        values = np.array([v for _, v in buf])
        t0 = times[0]
        x = times - t0

        slope, intercept = np.polyfit(x, values, 1)
        last_t = x[-1]
        last_v = values[-1]

        # Residual std of the fitted line against observed history: the
        # basis for an uncertainty band that widens with horizon (a random
        # walk's variance grows with sqrt(time) — the same intuition used
        # here) rather than presenting a false-precision single number.
        residuals = values - (intercept + slope * x)
        resid_std = float(np.std(residuals)) if len(residuals) > 2 else 0.05
        resid_std = max(resid_std, 0.02)

        preds = []
        for h in horizons_s:
            raw = intercept + slope * (last_t + h)
            # Blend pure-trend extrapolation with last observed value so
            # short/noisy histories don't produce runaway predictions.
            blended = float(np.clip(0.6 * raw + 0.4 * last_v, 0, 1))
            band = resid_std * math.sqrt(1 + h / 300.0)
            preds.append({
                "horizon_s": h,
                "predicted_congestion": round(blended, 3),
                "lower": round(float(np.clip(blended - band, 0, 1)), 3),
                "upper": round(float(np.clip(blended + band, 0, 1)), 3),
            })

        return {
            "road_id": road_id,
            "current_congestion": round(float(last_v), 3),
            "trend_slope_per_s": round(float(slope), 6),
            "trend": "worsening" if slope > 1e-5 else ("improving" if slope < -1e-5 else "stable"),
            "predictions": preds,
        }

    def all_road_ids(self) -> list[str]:
        return list(self._series.keys())
