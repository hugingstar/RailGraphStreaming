# -*- coding: utf-8 -*-
"""The claims this project makes are statistical, so the tests are too.

Two things must hold for the map to be honest:
  * the filter's median position tracks the hidden truth, and observations
    make it measurably better than the timetable alone;
  * the advertised 90% band actually contains the truth about 90% of the time.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import orjson
import pytest

from railgraph.estimation import (
    MIN_DELAY_S,
    OBS_SIGMA_S,
    DelayFilter,
    DelayTruth,
    PlanView,
    initial_delay,
    propagate,
)
from railgraph.network_data import TRAIN_TYPES
from railgraph.timetable import build_day

TICK_S = 5.0
OBS_EVERY = 3


@pytest.fixture(scope="module")
def plans():
    return build_day(datetime.now(timezone.utc))


@pytest.fixture(scope="module")
def ktx_plan(plans):
    return next(p for p in plans if p.pattern_id == "ktx_gyeongbu")


def make_view(plan) -> PlanView:
    return PlanView.from_json(plan.to_json(), TRAIN_TYPES[plan.type]["punctuality"])


def simulate(plan, seed: int, observe: bool):
    """Run one train end to end; return (position errors, band coverage)."""
    view = make_view(plan)
    truth = DelayTruth(view.punctuality, view.departure_ts, seed)
    filt = DelayFilter(view, seed + 99_991)
    rng = np.random.default_rng(seed + 7)

    pending = [view.timeline[i][0] for i in view.stop_idx[1:-1]][::OBS_EVERY]
    errors, inside, n = [], 0, 0
    t = view.departure_ts
    while t <= view.arrival_ts + truth.delay:
        d_true = truth.advance(t)
        filt.advance(t)
        while pending and t >= pending[0] + d_true:
            if observe:
                filt.observe(d_true + rng.normal(0, OBS_SIGMA_S), OBS_SIGMA_S)
            pending.pop(0)
        est = filt.estimate(t)
        true_km = float(view.km_at(t - d_true))
        errors.append(est["km"]["p50"] - true_km)
        inside += est["km"]["p05"] <= true_km <= est["km"]["p95"]
        n += 1
        t += TICK_S
    return np.asarray(errors), inside / n


# --------------------------------------------------------------------------
def test_delay_process_respects_its_floor():
    rng = np.random.default_rng(3)
    d = initial_delay(rng, 0.9, 5000)
    assert d.min() >= MIN_DELAY_S
    for _ in range(60):
        d = propagate(rng, d, 60.0, 0.9)
    assert d.min() >= MIN_DELAY_S


def test_punctuality_matches_published_statistics(plans):
    """KTX is advertised at ~98% within five minutes; 무궁화 rather less."""
    rng = np.random.default_rng(11)
    for train_type, floor in (("KTX", 0.96), ("무궁화호", 0.90)):
        sample = [p for p in plans if p.type == train_type]
        trip = float(np.mean([p.arrival_ts - p.departure_ts for p in sample]))
        punct = TRAIN_TYPES[train_type]["punctuality"]
        d = initial_delay(rng, punct, 20_000)
        for _ in range(int(trip // 60)):
            d = propagate(rng, d, 60.0, punct)
        assert (d < 300).mean() >= floor


def test_plan_view_clamps_outside_the_journey(ktx_plan):
    v = make_view(ktx_plan)
    assert v.km_at(v.departure_ts - 3600) == 0.0
    assert v.km_at(v.arrival_ts + 3600) == pytest.approx(v.length_km)
    mid = v.km_at((v.departure_ts + v.arrival_ts) / 2)
    assert 0 < mid < v.length_km


def test_observations_improve_accuracy(plans):
    sample = [p for p in plans if p.pattern_id == "ktx_gyeongbu"][:24]
    with_obs = np.concatenate([simulate(p, 1000 + i, True)[0] for i, p in enumerate(sample)])
    without = np.concatenate([simulate(p, 1000 + i, False)[0] for i, p in enumerate(sample)])
    assert np.abs(with_obs).mean() < np.abs(without).mean()
    assert np.abs(with_obs).mean() < 3.0          # km


def test_credible_interval_is_calibrated(plans):
    """A 90% band should cover the truth roughly 90% of the time."""
    sample = [p for p in plans if p.pattern_id == "ktx_gyeongbu"][:24]
    coverage = np.mean([simulate(p, 2000 + i, True)[1] for i, p in enumerate(sample)])
    assert 0.75 <= coverage <= 0.99


def test_observation_sharpens_the_posterior(ktx_plan):
    view = make_view(ktx_plan)
    filt = DelayFilter(view, 5)
    filt.advance(view.departure_ts + 3600)
    before = filt.estimate(view.departure_ts + 3600)["spread_km"]
    filt.observe(120.0, OBS_SIGMA_S)
    after = filt.estimate(view.departure_ts + 3600)["spread_km"]
    assert after < before


def test_estimate_payload_is_well_formed(ktx_plan):
    view = make_view(ktx_plan)
    filt = DelayFilter(view, 17)
    t = view.departure_ts + (view.arrival_ts - view.departure_ts) * 0.5
    filt.advance(t)
    est = filt.estimate(t)

    assert est["status"] in {"SCHEDULED", "RUNNING", "DWELL", "ARRIVED"}
    q = est["km"]
    assert q["p05"] <= q["p25"] <= q["p50"] <= q["p75"] <= q["p95"]
    assert 0 <= q["p50"] <= est["route_km"]
    assert est["delay"]["p05"] <= est["delay"]["p50"] <= est["delay"]["p95"]
    assert 0 <= est["progress"] <= 1
    assert 0 < est["confidence"] <= 1

    assert sum(s["p"] for s in est["segments"]) <= 1.0001
    assert all(len(pt) == 3 and 0 <= pt[2] <= 1 for pt in est["band"])
    assert max(est["density"]["bins"]) == pytest.approx(1.0)
    for stop in est["next_stops"]:
        assert stop["eta_p10"] <= stop["eta_p50"] <= stop["eta_p90"]

    orjson.dumps(est)                     # must survive the Kafka codec


def test_filter_recovers_from_a_wildly_wrong_report(ktx_plan):
    """A report far outside the particle cloud must not collapse the filter."""
    view = make_view(ktx_plan)
    filt = DelayFilter(view, 23)
    filt.advance(view.departure_ts + 1800)
    filt.observe(4500.0, OBS_SIGMA_S)     # 75 minutes late, out of nowhere
    est = filt.estimate(view.departure_ts + 1800)
    assert np.isfinite(filt.d).all()
    assert est["delay"]["p50"] == pytest.approx(4500, abs=400)
