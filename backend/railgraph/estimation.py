# -*- coding: utf-8 -*-
"""Where is the train, given only the timetable?

We never observe position.  We observe a *schedule* and, occasionally, a
trackside report that train X passed station Y about Z seconds late.  So the
unknown is a scalar -- the train's current delay -- and position follows
deterministically once you know it:

    position(t) = scheduled_km(t - delay)

The delay is tracked with a bootstrap particle filter.  Between observations
each particle follows a jump-diffusion: continuous small noise, Poisson
incidents that add a fat positive tail, and steady recovery against the
schedule's padding (delay bleeds off while running, but a train is never
meaningfully early).  When an observation arrives the particles are reweighted
by a Gaussian likelihood and resampled.  Pushing the particle cloud through
`scheduled_km` turns a delay distribution into a *position* distribution -- the
probability that the train is on any given stretch of track.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# --- process parameters ----------------------------------------------------
# Fraction of elapsed running time that can be clawed back (schedule padding).
RECOVERY_RATE = 0.050
# Continuous noise, in seconds of delay per sqrt(second) of running.
DIFFUSION_SIGMA = 0.34
# Poisson incident rate per running second, scaled by (1 - punctuality).
INCIDENT_LAMBDA = 1.66e-4
# Mean severity of one incident, in seconds.
INCIDENT_MEAN_S = 260.0
# A train may run at most this far ahead of the book.
MIN_DELAY_S = -90.0

N_PARTICLES = 512
OBS_SIGMA_S = 20.0
ESS_RATIO = 0.5
ROUGHENING_S = 6.0

STATUS_SCHEDULED = "SCHEDULED"
STATUS_RUNNING = "RUNNING"
STATUS_DWELL = "DWELL"
STATUS_ARRIVED = "ARRIVED"


# ---------------------------------------------------------------------------
# Plan view: the estimator's read model, rebuilt from the Kafka plan message.
# ---------------------------------------------------------------------------
@dataclass
class PlanView:
    train_id: str
    number: int
    name: str
    type: str
    line_id: str
    origin: str
    destination: str
    direction: str | None
    nodes: list[str]
    cum_km: np.ndarray
    coords: list[list[float]]
    stop_idx: list[int]
    timeline: list[tuple[float, float, float]]
    punctuality: float
    profile_t: np.ndarray = field(repr=False, default=None)
    profile_km: np.ndarray = field(repr=False, default=None)

    @classmethod
    def from_json(cls, d: dict, punctuality: float) -> "PlanView":
        tl = [(float(a), float(dep), float(km)) for a, dep, km in d["timeline"]]
        ts: list[float] = []
        ks: list[float] = []
        for arr, dep, km in tl:
            ts.append(arr)
            ks.append(km)
            if dep > arr:
                ts.append(dep)
                ks.append(km)
        pv = cls(
            train_id=d["train_id"], number=d["number"], name=d["name"], type=d["type"],
            line_id=d["line_id"], origin=d["origin"], destination=d["destination"],
            # Plans published before `direction` existed may still sit in the
            # retained topic.  Leave those unknown rather than guessing a side:
            # the estimator replays the log from the beginning, so a guess here
            # would mislabel every train whose stale plan is read first.
            direction=d.get("direction"),
            nodes=d["nodes"], cum_km=np.asarray(d["cum_km"], dtype=float),
            coords=d["coords"], stop_idx=list(d["stop_idx"]), timeline=tl,
            punctuality=punctuality,
        )
        pv.profile_t = np.asarray(ts, dtype=float)
        pv.profile_km = np.asarray(ks, dtype=float)
        return pv

    @property
    def departure_ts(self) -> float:
        return self.timeline[0][1]

    @property
    def arrival_ts(self) -> float:
        return self.timeline[-1][0]

    @property
    def length_km(self) -> float:
        return float(self.cum_km[-1])

    def km_at(self, tau):
        """Scheduled offset(s) in km at schedule-clock time(s) `tau`. Vectorised."""
        return np.interp(tau, self.profile_t, self.profile_km)

    def dwell_station_at(self, tau: float) -> str | None:
        for i in self.stop_idx:
            arr, dep, _ = self.timeline[i]
            if arr <= tau <= dep:
                return self.nodes[i]
        return None

    def remaining_stops(self, tau: float) -> list[tuple[str, float]]:
        return [(self.nodes[i], self.timeline[i][0])
                for i in self.stop_idx if self.timeline[i][0] > tau]


# ---------------------------------------------------------------------------
# Delay dynamics, shared by the filter and by the hidden "truth" simulator so
# the two cannot silently drift apart.
# ---------------------------------------------------------------------------
def initial_delay(rng: np.random.Generator, punctuality: float, size: int) -> np.ndarray:
    on_time = rng.random(size) < punctuality
    punctual = np.abs(rng.normal(0.0, 45.0, size)) - 25.0
    late = 60.0 + rng.exponential(240.0, size)
    return np.maximum(np.where(on_time, punctual, late), MIN_DELAY_S)


def propagate(rng: np.random.Generator, delay: np.ndarray, dt: float,
              punctuality: float) -> np.ndarray:
    """Advance the delay process by `dt` seconds of running time."""
    if dt <= 0:
        return delay
    d = delay - np.minimum(np.maximum(delay, 0.0), RECOVERY_RATE * dt)
    d = d + rng.normal(0.0, DIFFUSION_SIGMA * np.sqrt(dt), d.shape)
    lam = INCIDENT_LAMBDA * (1.0 - punctuality) * dt
    shocks = rng.poisson(lam, d.shape)
    d = d + rng.gamma(shocks, INCIDENT_MEAN_S)
    return np.maximum(d, MIN_DELAY_S)


def _weighted_quantiles(x: np.ndarray, w: np.ndarray, qs) -> list[float]:
    order = np.argsort(x)
    xs, ws = x[order], w[order]
    cw = np.cumsum(ws)
    cw /= cw[-1]
    return [float(np.interp(q, cw, xs)) for q in qs]


# ---------------------------------------------------------------------------
class DelayFilter:
    """Bootstrap particle filter over one train's current delay, in seconds."""

    def __init__(self, plan: PlanView, seed: int, n: int = N_PARTICLES):
        self.plan = plan
        self.rng = np.random.default_rng(seed)
        self.d = initial_delay(self.rng, plan.punctuality, n)
        self.w = np.full(n, 1.0 / n)
        self.t = plan.departure_ts
        self.n_obs = 0
        self.last_obs_ts: float | None = None
        self.last_obs_delay: float | None = None

    # -- prediction -------------------------------------------------------
    def advance(self, now: float) -> None:
        """Propagate to wall-clock `now`, accruing risk only while running."""
        start = max(self.t, self.plan.departure_ts)
        end = min(now, self.plan.arrival_ts + float(np.max(self.d)))
        dt = end - start
        if dt > 0:
            self.d = propagate(self.rng, self.d, dt, self.plan.punctuality)
        self.t = max(self.t, now)

    # -- update -----------------------------------------------------------
    def observe(self, measured_delay: float, sigma: float = OBS_SIGMA_S) -> None:
        resid = (self.d - measured_delay) / sigma
        like = np.exp(-0.5 * np.square(np.clip(resid, -40.0, 40.0)))
        w = self.w * like
        total = w.sum()
        if total <= 1e-300:                       # filter lost the target: reinitialise
            self.d = measured_delay + self.rng.normal(0.0, 3 * sigma, self.d.shape)
            self.w = np.full(self.d.size, 1.0 / self.d.size)
        else:
            self.w = w / total
            if 1.0 / np.sum(np.square(self.w)) < ESS_RATIO * self.d.size:
                self._resample()
        self.n_obs += 1
        self.last_obs_delay = measured_delay

    def _resample(self) -> None:
        n = self.d.size
        positions = (self.rng.random() + np.arange(n)) / n
        cdf = np.cumsum(self.w)
        cdf[-1] = 1.0
        idx = np.searchsorted(cdf, positions)
        self.d = self.d[idx] + self.rng.normal(0.0, ROUGHENING_S, n)
        self.d = np.maximum(self.d, MIN_DELAY_S)
        self.w = np.full(n, 1.0 / n)

    # -- readout ----------------------------------------------------------
    def estimate(self, now: float, n_density_bins: int = 26,
                 n_segments: int = 5, n_next_stops: int = 3) -> dict:
        plan = self.plan
        tau = now - self.d
        km = plan.km_at(tau)

        kq = _weighted_quantiles(km, self.w, (0.05, 0.25, 0.5, 0.75, 0.95))
        dq = _weighted_quantiles(self.d, self.w, (0.05, 0.5, 0.95))
        km_mean = float(np.dot(self.w, km))
        d_mean = float(np.dot(self.w, self.d))

        # Position density along the route, on a bin grid covering the 90% band.
        lo, hi = kq[0], kq[4]
        pad = max(0.4, (hi - lo) * 0.12)
        lo, hi = max(0.0, lo - pad), min(plan.length_km, hi + pad)
        if hi - lo < 1e-3:
            hi = lo + 1e-3
        edges = np.linspace(lo, hi, n_density_bins + 1)
        dens, _ = np.histogram(km, bins=edges, weights=self.w)
        peak = float(dens.max()) or 1.0

        # The same density, already projected onto the track so the map can draw
        # the probability cloud without fetching route geometry per train.
        centers = (edges[:-1] + edges[1:]) * 0.5
        band = []
        for c, v in zip(centers, dens):
            blat, blon = _point_on_route(plan, float(c))
            band.append([round(blon, 5), round(blat, 5), round(float(v) / peak, 3)])

        # Probability mass per inter-station segment: "which stretch is it on?"
        seg = np.clip(np.searchsorted(plan.cum_km, km, side="right") - 1,
                      0, len(plan.nodes) - 2)
        seg_mass = np.bincount(seg, weights=self.w, minlength=len(plan.nodes) - 1)
        top = np.argsort(seg_mass)[::-1][:n_segments]
        segments = [
            {"from": plan.nodes[i], "to": plan.nodes[i + 1], "p": round(float(seg_mass[i]), 4)}
            for i in top if seg_mass[i] > 0.004
        ]

        tau50 = now - dq[1]
        if now < plan.departure_ts + dq[1] - 30:
            status = STATUS_SCHEDULED
        elif kq[2] >= plan.length_km - 0.05:
            status = STATUS_ARRIVED
        else:
            status = STATUS_DWELL if plan.dwell_station_at(tau50) else STATUS_RUNNING

        next_stops = []
        for name, sched_arr in plan.remaining_stops(tau50)[:n_next_stops]:
            eta = sched_arr + self.d
            e = _weighted_quantiles(eta, self.w, (0.1, 0.5, 0.9))
            next_stops.append({
                "station": name, "scheduled": round(sched_arr, 1),
                "eta_p10": round(e[0], 1), "eta_p50": round(e[1], 1),
                "eta_p90": round(e[2], 1),
            })

        lat, lon = _point_on_route(plan, kq[2])
        spread = kq[4] - kq[0]
        return {
            "train_id": plan.train_id,
            "number": plan.number,
            "name": plan.name,
            "type": plan.type,
            "line_id": plan.line_id,
            "origin": plan.origin,
            "destination": plan.destination,
            "direction": plan.direction,
            "ts": round(now, 1),
            "status": status,
            "km": {"p05": round(kq[0], 2), "p25": round(kq[1], 2), "p50": round(kq[2], 2),
                   "p75": round(kq[3], 2), "p95": round(kq[4], 2), "mean": round(km_mean, 2)},
            "route_km": round(plan.length_km, 2),
            "delay": {"p05": round(dq[0], 1), "p50": round(dq[1], 1),
                      "p95": round(dq[2], 1), "mean": round(d_mean, 1)},
            "density": {
                "lo": round(lo, 3), "hi": round(hi, 3),
                "bins": [round(float(v) / peak, 4) for v in dens],
            },
            "band": band,
            "segments": segments,
            "next_stops": next_stops,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "spread_km": round(spread, 2),
            "confidence": round(float(np.exp(-spread / 45.0)), 4),
            "n_obs": self.n_obs,
            "last_obs_ts": self.last_obs_ts,
            "progress": round(min(1.0, kq[2] / max(plan.length_km, 1e-6)), 4),
        }


def _point_on_route(plan: PlanView, km: float) -> tuple[float, float]:
    """(lat, lon) at offset `km`; coords are stored [lon, lat]."""
    cum = plan.cum_km
    i = int(np.clip(np.searchsorted(cum, km, side="right") - 1, 0, len(plan.coords) - 2))
    span = float(cum[i + 1] - cum[i])
    t = 0.0 if span <= 0 else (km - float(cum[i])) / span
    t = min(max(t, 0.0), 1.0)
    (lon0, lat0), (lon1, lat1) = plan.coords[i], plan.coords[i + 1]
    return float(lat0 + (lat1 - lat0) * t), float(lon0 + (lon1 - lon0) * t)


# ---------------------------------------------------------------------------
class DelayTruth:
    """The hidden ground truth the dispatcher simulates and only partly reveals."""

    def __init__(self, punctuality: float, departure_ts: float, seed: int):
        self.rng = np.random.default_rng(seed)
        self.punctuality = punctuality
        self.delay = float(initial_delay(self.rng, punctuality, 1)[0])
        self.t = departure_ts

    def advance(self, now: float) -> float:
        dt = now - self.t
        if dt > 0:
            arr = propagate(self.rng, np.array([self.delay]), dt, self.punctuality)
            self.delay = float(arr[0])
            self.t = now
        return self.delay
