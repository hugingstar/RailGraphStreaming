# -*- coding: utf-8 -*-
"""The timetable has to be internally consistent before anything downstream
can mean anything: unique ids, monotone clocks, and journey times in the same
ballpark as the real railway.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from railgraph.network import NETWORK
from railgraph.network_data import LINES, STATIONS, TRAIN_TYPES
from railgraph.timetable import PATTERNS, build_day

TODAY = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def plans():
    return build_day(TODAY)


def test_every_line_station_is_known():
    unknown = [s for line in LINES for s in line["stations"] if s not in STATIONS]
    assert unknown == []


def test_every_pattern_path_follows_real_track():
    for pat in PATTERNS:
        for a, b in zip(pat.path, pat.path[1:]):
            NETWORK.edge(a, b)          # raises if the pair is not an edge


def test_pattern_skips_reference_real_stops():
    for pat in PATTERNS:
        assert set(pat.skip) <= set(pat.path), pat.id
        assert pat.path[0] not in pat.skip and pat.path[-1] not in pat.skip


@pytest.mark.parametrize("offset_days", [0, 1, 2])
def test_train_ids_are_unique(offset_days):
    day = build_day(TODAY + timedelta(days=offset_days))
    ids = [p.train_id for p in day]
    assert len(ids) == len(set(ids))


def test_timeline_is_monotone(plans):
    for p in plans:
        prev = float("-inf")
        for arr, dep, kmv in p.timeline:
            assert arr <= dep
            assert arr >= prev
            prev = dep
        kms = [k for _, _, k in p.timeline]
        assert kms == sorted(kms)
        assert kms[0] == 0
        assert kms[-1] == pytest.approx(p.route.length_km, rel=1e-6)


def test_scheduled_km_is_monotone_and_clamped(plans):
    p = plans[len(plans) // 2]
    assert p.scheduled_km(p.departure_ts - 600) == 0.0
    assert p.scheduled_km(p.arrival_ts + 600) == pytest.approx(p.route.length_km)
    span = p.arrival_ts - p.departure_ts
    samples = [p.scheduled_km(p.departure_ts + span * i / 40) for i in range(41)]
    assert samples == sorted(samples)


def test_first_and_last_nodes_always_stop(plans):
    for p in plans:
        assert p.stop_idx[0] == 0
        assert p.stop_idx[-1] == len(p.route.nodes) - 1


# (service, published journey time in minutes) for the real railway.
REFERENCE = {
    "ktx_gyeongbu_exp": 141,     # 서울-부산 최속달
    "ktx_honam": 145,            # 서울-목포
    "ktx_gangneung": 100,        # 청량리-강릉
    "ktx_jungang": 260,          # 청량리-부전
    "itx_saemaeul": 290,         # 서울-부산
    "mugunghwa_gyeongbu": 330,   # 서울-부산
    "itx_cheongchun": 62,        # 청량리-춘천
}


@pytest.mark.parametrize("pattern_id,real_minutes", REFERENCE.items())
def test_journey_times_track_reality(plans, pattern_id, real_minutes):
    first = next(p for p in plans if p.pattern_id == pattern_id)
    modelled = (first.arrival_ts - first.departure_ts) / 60
    assert modelled == pytest.approx(real_minutes, rel=0.15), (
        f"{pattern_id}: {modelled:.0f}min vs {real_minutes}min"
    )


def test_speeds_are_ordered_by_class():
    assert TRAIN_TYPES["KTX"]["hsr_kmh"] > TRAIN_TYPES["ITX-새마을"]["hsr_kmh"]
    assert TRAIN_TYPES["ITX-새마을"]["conv_kmh"] > TRAIN_TYPES["무궁화호"]["conv_kmh"]
    assert TRAIN_TYPES["KTX"]["punctuality"] > TRAIN_TYPES["무궁화호"]["punctuality"]


def test_plan_json_round_trips(plans):
    d = plans[0].to_json()
    assert d["nodes"] and len(d["coords"]) == len(d["nodes"]) == len(d["cum_km"])
    assert len(d["timeline"]) == len(d["nodes"])
    assert all(len(c) == 2 for c in d["coords"])
