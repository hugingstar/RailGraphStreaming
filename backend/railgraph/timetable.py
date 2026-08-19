# -*- coding: utf-8 -*-
"""Service patterns and the day's timetable.

Real KORAIL/SR timetables are not redistributable, so this generates a plausible
one: real routes and real stopping patterns, with running times derived from
track distance and rolling-stock line speeds, plus schedule padding (railways
publish slack so a late train can claw time back).
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from .network import NETWORK, Route
from .network_data import TRAIN_TYPES

KST = timezone(timedelta(hours=9))

# Seconds added at each commercial stop for braking + acceleration.
STOP_PENALTY_S = 65.0
# Fraction of pure running time published as recovery margin.
SCHEDULE_PADDING = 0.055
# Global multiplier on headways: <1 packs more trains onto the map.
DENSITY = float(os.getenv("RAILGRAPH_DENSITY", "1.0"))

_SEOUL_OSONG = ["서울", "광명", "천안아산", "오송"]
_SUSEO_OSONG = ["수서", "동탄", "평택지제", "오송"]
_OSONG_DONGDAEGU = ["대전", "김천구미", "동대구"]
_GYEONGBU_CONV = [
    "서울", "영등포", "수원", "오산", "평택", "성환", "천안", "전의", "조치원",
    "신탄진", "대전", "옥천", "영동", "김천", "구미", "왜관", "대구", "동대구",
    "경산", "청도", "밀양", "삼랑진", "물금", "구포", "부산",
]
_HONAM_CONV = [
    "서울", "영등포", "수원", "오산", "평택", "성환", "천안", "전의", "조치원",
    "신탄진", "대전", "서대전", "계룡", "논산", "강경", "익산", "김제", "정읍",
    "장성", "광주송정", "나주", "함평", "무안", "목포",
]
_JUNGANG = [
    "청량리", "상봉", "양평", "만종", "제천", "단양", "풍기", "영주", "안동",
    "의성", "영천", "경주", "태화강", "부전",
]


@dataclass(frozen=True)
class Pattern:
    id: str
    name: str
    type: str
    path: list[str]
    skip: tuple[str, ...] = ()       # traversed but not served
    first: str = "05:10"
    last: str = "22:40"
    headway_min: float = 60.0
    number_base: int = 1000

    def stops(self) -> list[str]:
        return [n for n in self.path if n not in self.skip]


PATTERNS: list[Pattern] = [
    Pattern("ktx_gyeongbu", "KTX 경부", "KTX",
            _SEOUL_OSONG + _OSONG_DONGDAEGU + ["신경주", "울산", "부산"],
            first="05:10", last="22:50", headway_min=15, number_base=100),
    Pattern("ktx_gyeongbu_exp", "KTX 경부 급행", "KTX",
            _SEOUL_OSONG + _OSONG_DONGDAEGU + ["신경주", "울산", "부산"],
            skip=("천안아산", "오송", "김천구미", "신경주", "울산"),
            first="05:30", last="22:10", headway_min=45, number_base=250),
    Pattern("srt_gyeongbu", "SRT 경부", "SRT",
            _SUSEO_OSONG + _OSONG_DONGDAEGU + ["신경주", "울산", "부산"],
            first="05:30", last="22:40", headway_min=25, number_base=300),
    Pattern("ktx_honam", "KTX 호남", "KTX",
            _SEOUL_OSONG + ["공주", "익산", "정읍", "광주송정", "나주", "함평", "무안", "목포"],
            first="05:10", last="22:30", headway_min=30, number_base=400),
    Pattern("srt_honam", "SRT 호남", "SRT",
            _SUSEO_OSONG + ["공주", "익산", "정읍", "광주송정", "나주", "함평", "무안", "목포"],
            first="05:30", last="22:20", headway_min=60, number_base=640),
    Pattern("ktx_jeolla", "KTX 전라", "KTX-산천",
            _SEOUL_OSONG + ["공주", "익산", "삼례", "전주", "임실", "남원", "곡성",
                            "구례구", "순천", "여천", "여수엑스포"],
            skip=("삼례", "임실"),
            first="05:20", last="22:00", headway_min=75, number_base=700),
    Pattern("ktx_pohang", "KTX 포항", "KTX-산천",
            _SEOUL_OSONG + _OSONG_DONGDAEGU + ["신경주", "포항"],
            skip=("신경주",),
            first="05:20", last="21:50", headway_min=65, number_base=800),
    Pattern("ktx_jinju", "KTX 진주", "KTX-산천",
            _SEOUL_OSONG + _OSONG_DONGDAEGU + ["경산", "청도", "밀양", "삼랑진",
                                               "진영", "창원중앙", "창원", "마산", "함안", "진주"],
            skip=("경산", "청도", "삼랑진", "함안"),
            first="05:40", last="21:00", headway_min=110, number_base=210),
    Pattern("ktx_gangneung", "KTX 강릉", "KTX-이음",
            ["청량리", "상봉", "양평", "만종", "횡성", "둔내", "평창", "진부", "강릉"],
            first="05:10", last="22:20", headway_min=50, number_base=830),
    Pattern("ktx_jungang", "KTX-이음 중앙", "KTX-이음", _JUNGANG,
            skip=("단양", "의성"),
            first="05:30", last="21:30", headway_min=95, number_base=730),
    Pattern("itx_saemaeul", "ITX-새마을 경부", "ITX-새마을", _GYEONGBU_CONV,
            skip=("오산", "성환", "전의", "신탄진", "옥천", "왜관", "청도", "삼랑진", "물금"),
            first="05:20", last="22:20", headway_min=90, number_base=1000),
    Pattern("mugunghwa_gyeongbu", "무궁화 경부", "무궁화호", _GYEONGBU_CONV,
            first="05:00", last="22:40", headway_min=85, number_base=1200),
    Pattern("itx_maeum_honam", "ITX-마음 호남", "ITX-마음", _HONAM_CONV,
            skip=("오산", "성환", "전의", "신탄진", "강경", "김제", "함평", "무안"),
            first="05:40", last="21:40", headway_min=130, number_base=1560),
    Pattern("mugunghwa_honam", "무궁화 호남", "무궁화호", _HONAM_CONV,
            first="05:00", last="22:20", headway_min=100, number_base=1400),
    Pattern("mugunghwa_jeolla", "무궁화 전라", "무궁화호",
            ["익산", "삼례", "전주", "임실", "남원", "곡성", "구례구", "순천", "여천", "여수엑스포"],
            first="05:30", last="22:00", headway_min=95, number_base=1530),
    Pattern("itx_cheongchun", "ITX-청춘 경춘", "ITX-청춘",
            ["청량리", "상봉", "평내호평", "마석", "가평", "강촌", "남춘천", "춘천"],
            first="05:40", last="23:00", headway_min=45, number_base=2000),
    Pattern("mugunghwa_janghang", "무궁화 장항", "무궁화호",
            ["천안", "아산", "온양온천", "도고온천", "홍성", "대천", "웅천", "서천", "군산", "익산"],
            first="05:30", last="22:00", headway_min=95, number_base=1600),
    Pattern("mugunghwa_gyeongjeon", "무궁화 경전", "무궁화호",
            ["동대구", "경산", "청도", "밀양", "삼랑진", "진영", "창원중앙", "창원",
             "마산", "함안", "진주"],
            first="05:20", last="22:00", headway_min=120, number_base=1900),
    Pattern("mugunghwa_jungang", "무궁화 중앙", "무궁화호", _JUNGANG,
            first="06:00", last="21:00", headway_min=170, number_base=1700),
]


@dataclass
class TrainPlan:
    """One physical service: everything the estimator needs, and nothing else."""

    train_id: str
    number: int
    name: str
    type: str
    pattern_id: str
    line_id: str
    origin: str
    destination: str
    # "down" runs along the pattern's canonical path, "up" is the reverse.
    direction: str
    route: Route = field(repr=False)
    # per traversed node: (scheduled arrival epoch_s, scheduled departure epoch_s, km)
    timeline: list[tuple[float, float, float]] = field(repr=False, default_factory=list)
    stop_idx: list[int] = field(default_factory=list)   # indices into route.nodes

    @property
    def departure_ts(self) -> float:
        return self.timeline[0][1]

    @property
    def arrival_ts(self) -> float:
        return self.timeline[-1][0]

    def scheduled_km(self, tau: float) -> float:
        """Where the schedule says the train is at schedule-clock time `tau`."""
        tl = self.timeline
        if tau <= tl[0][1]:
            return 0.0
        if tau >= tl[-1][0]:
            return self.route.length_km
        lo, hi = 0, len(tl) - 1
        while lo < hi - 1:                       # last node whose departure precedes tau
            mid = (lo + hi) // 2
            if tl[mid][1] <= tau:
                lo = mid
            else:
                hi = mid
        a_dep, a_km = tl[lo][1], tl[lo][2]
        b_arr, b_km = tl[lo + 1][0], tl[lo + 1][2]
        if tau <= a_dep:                          # still dwelling at node lo
            return a_km
        if tau >= b_arr:                          # already dwelling at node lo+1
            return b_km
        return a_km + (b_km - a_km) * (tau - a_dep) / max(b_arr - a_dep, 1e-6)

    def to_json(self) -> dict:
        return {
            "train_id": self.train_id, "number": self.number, "name": self.name,
            "type": self.type, "pattern_id": self.pattern_id, "line_id": self.line_id,
            "origin": self.origin, "destination": self.destination,
            "direction": self.direction,
            "nodes": self.route.nodes, "cum_km": [round(k, 3) for k in self.route.cum_km],
            "coords": self.route.coords(),
            "stop_idx": self.stop_idx,
            "timeline": [[round(a, 1), round(d, 1), round(k, 3)] for a, d, k in self.timeline],
        }


def _build_timeline(route: Route, stops: set[str], profile: dict,
                    dep_ts: float) -> tuple[list[tuple[float, float, float]], list[int]]:
    timeline: list[tuple[float, float, float]] = [(dep_ts, dep_ts, 0.0)]
    stop_idx = [0]
    t = dep_ts
    n = len(route.nodes)
    for i in range(n - 1):
        seg_km = route.cum_km[i + 1] - route.cum_km[i]
        speed = profile["hsr_kmh"] if route.hsr_flags[i] else profile["conv_kmh"]
        t += seg_km / speed * 3600.0 * (1.0 + SCHEDULE_PADDING)
        node = route.nodes[i + 1]
        if node in stops or i == n - 2:
            t += STOP_PENALTY_S
            arr = t
            dep = arr + (profile["dwell_s"] if i != n - 2 else 0.0)
            timeline.append((arr, dep, route.cum_km[i + 1]))
            stop_idx.append(i + 1)
            t = dep
        else:
            timeline.append((t, t, route.cum_km[i + 1]))
    return timeline, stop_idx


def _dominant_line(route: Route) -> str:
    weight: Counter[str] = Counter()
    for lid, a, b in zip(route.line_ids, route.cum_km, route.cum_km[1:]):
        weight[lid] += b - a
    return weight.most_common(1)[0][0]


def _slots(first: str, last: str, headway_min: float) -> list[float]:
    h0, m0 = (int(x) for x in first.split(":"))
    h1, m1 = (int(x) for x in last.split(":"))
    start, end = h0 * 60 + m0, h1 * 60 + m1
    step = max(headway_min * DENSITY, 3.0)
    out, cur = [], float(start)
    while cur <= end:
        out.append(cur * 60.0)
        cur += step
    return out


def build_day(day: datetime) -> list[TrainPlan]:
    """All services departing on the KST calendar day containing `day`."""
    midnight = datetime.combine(day.astimezone(KST).date(), time(0, 0), tzinfo=KST)
    base = midnight.timestamp()
    daytag = midnight.strftime("%m%d")
    plans: list[TrainPlan] = []

    for pat in PATTERNS:
        profile = TRAIN_TYPES[pat.type]
        stops = set(pat.stops())
        for direction, nodes in (("down", pat.path), ("up", list(reversed(pat.path)))):
            route = NETWORK.build_route(f"{pat.id}_{direction}", nodes)
            line_id = _dominant_line(route)
            for i, offset in enumerate(_slots(pat.first, pat.last, pat.headway_min)):
                number = pat.number_base + i * 2 + (0 if direction == "down" else 1)
                timeline, stop_idx = _build_timeline(route, stops, profile, base + offset)
                plans.append(TrainPlan(
                    train_id=f"{pat.type}-{number}-{daytag}",
                    number=number,
                    name=f"{pat.type} {number}",
                    type=pat.type,
                    pattern_id=pat.id,
                    line_id=line_id,
                    origin=nodes[0],
                    destination=nodes[-1],
                    direction=direction,
                    route=route,
                    timeline=timeline,
                    stop_idx=stop_idx,
                ))
    plans.sort(key=lambda p: p.departure_ts)
    seen: dict[str, str] = {}
    for p in plans:
        if p.train_id in seen:
            raise ValueError(
                f"train_id collision {p.train_id!r}: patterns {seen[p.train_id]!r} and "
                f"{p.pattern_id!r} share a number block -- widen one of their number_base"
            )
        seen[p.train_id] = p.pattern_id
    return plans


def station_weights(plans: list[TrainPlan]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for p in plans:
        for i in p.stop_idx:
            c[p.route.nodes[i]] += 1
    return dict(c)
