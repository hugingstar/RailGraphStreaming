# -*- coding: utf-8 -*-
"""Rail network graph and route geometry.

A *line* is a drawn edge chain (what you see on the map).  A *route* is the
ordered node sequence a particular service traverses; it may hop between lines
(a 진주 KTX runs 경부고속선 -> 경부선 -> 경전선).  Everything downstream --
the delay filter, the map, the ETA table -- addresses a train by its scalar
offset in km along its route, so route geometry is the single source of truth.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from .geo import haversine_km, interpolate
from .network_data import LINES, STATIONS, TRAIN_TYPES


@dataclass(frozen=True)
class Edge:
    km: float
    hsr: bool
    line_id: str


@dataclass
class Route:
    """A service's physical path, with cumulative distance at every node."""

    id: str
    nodes: list[str]
    cum_km: list[float]          # cum_km[i] = track km from origin to nodes[i]
    hsr_flags: list[bool]        # hsr_flags[i] = is edge nodes[i]->nodes[i+1] high-speed
    line_ids: list[str]

    @property
    def length_km(self) -> float:
        return self.cum_km[-1]

    def point_at(self, km: float) -> tuple[float, float]:
        """(lat, lon) at a given offset, linearly interpolated within a segment."""
        km = min(max(km, 0.0), self.length_km)
        i = bisect.bisect_right(self.cum_km, km) - 1
        i = min(max(i, 0), len(self.nodes) - 2)
        span = self.cum_km[i + 1] - self.cum_km[i]
        t = 0.0 if span <= 0 else (km - self.cum_km[i]) / span
        return interpolate(STATIONS[self.nodes[i]], STATIONS[self.nodes[i + 1]], t)

    def segment_at(self, km: float) -> int:
        """Index of the edge containing `km`."""
        i = bisect.bisect_right(self.cum_km, km) - 1
        return min(max(i, 0), len(self.nodes) - 2)

    def coords(self) -> list[list[float]]:
        """Polyline as [lon, lat] pairs (GeoJSON axis order)."""
        return [[STATIONS[n][1], STATIONS[n][0]] for n in self.nodes]


class Network:
    def __init__(self) -> None:
        self.stations: dict[str, tuple[float, float]] = dict(STATIONS)
        self.lines: list[dict] = LINES
        self.adj: dict[str, dict[str, Edge]] = {}
        for line in LINES:
            seq = line["stations"]
            for a, b in zip(seq, seq[1:]):
                km = haversine_km(STATIONS[a], STATIONS[b]) * line["tortuosity"]
                edge = Edge(km=km, hsr=line["hsr"], line_id=line["id"])
                # Keep the fastest alignment when two lines share a station pair.
                for u, v in ((a, b), (b, a)):
                    cur = self.adj.setdefault(u, {}).get(v)
                    if cur is None or (edge.hsr and not cur.hsr):
                        self.adj[u][v] = edge

    def edge(self, a: str, b: str) -> Edge:
        try:
            return self.adj[a][b]
        except KeyError:  # pragma: no cover - guards data-entry mistakes
            raise KeyError(f"no track between {a!r} and {b!r}") from None

    def build_route(self, route_id: str, nodes: list[str]) -> Route:
        cum, hsr, lids = [0.0], [], []
        for a, b in zip(nodes, nodes[1:]):
            e = self.edge(a, b)
            cum.append(cum[-1] + e.km)
            hsr.append(e.hsr)
            lids.append(e.line_id)
        return Route(id=route_id, nodes=list(nodes), cum_km=cum,
                     hsr_flags=hsr, line_ids=lids)

    # -- export -----------------------------------------------------------
    def to_payload(self, station_weights: dict[str, int] | None = None) -> dict:
        weights = station_weights or {}
        return {
            "stations": [
                {"name": n, "lat": c[0], "lon": c[1], "weight": weights.get(n, 0)}
                for n, c in self.stations.items()
            ],
            "lines": [
                {
                    "id": ln["id"], "name": ln["name"], "color": ln["color"],
                    "hsr": ln["hsr"],
                    "coords": [[STATIONS[s][1], STATIONS[s][0]] for s in ln["stations"]],
                }
                for ln in self.lines
            ],
            "trainTypes": {
                k: {"color": v["color"], "hsr_kmh": v["hsr_kmh"]}
                for k, v in TRAIN_TYPES.items()
            },
        }


NETWORK = Network()
