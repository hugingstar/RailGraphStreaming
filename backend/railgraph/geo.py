"""Geodesy helpers. Small enough to keep dependency-free."""
from __future__ import annotations

import math

EARTH_R_KM = 6371.0088


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in km."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


def interpolate(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    """Linear interpolation in lat/lon space; fine at inter-station scale."""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
