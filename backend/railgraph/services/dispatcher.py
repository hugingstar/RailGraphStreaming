# -*- coding: utf-8 -*-
"""Dispatcher: publishes the operating plan, and leaks a little of the truth.

This stands in for the railway's own systems.  It knows two things nobody
downstream does: the full timetable, and each train's actual delay.  It
publishes the timetable in full (`rail.plans`) but reveals the delay only at
instrumented stations (`rail.observations`) -- roughly one stop in three, which
is what makes the estimator's job a real inference problem rather than a lookup.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import zlib
from dataclasses import dataclass, field

import numpy as np

from .. import bus, config
from ..estimation import OBS_SIGMA_S, DelayTruth
from ..network_data import TRAIN_TYPES
from ..timetable import TrainPlan, build_day

log = logging.getLogger("dispatcher")


@dataclass
class Running:
    plan: TrainPlan
    truth: DelayTruth
    pending: list[tuple[int, float, str]] = field(default_factory=list)  # node, sched, kind
    reported: int = 0


def _observation_points(plan: TrainPlan) -> list[tuple[int, float, str]]:
    """Which stops report, and when they are scheduled to.

    Origin departure and final arrival always report; intermediate stops report
    every Nth, so the estimator spends most of its time extrapolating.
    """
    pts: list[tuple[int, float, str]] = [(plan.stop_idx[0], plan.timeline[plan.stop_idx[0]][1],
                                          "DEPARTURE")]
    middle = plan.stop_idx[1:-1]
    for i in middle[config.OBS_EVERY_N_STOPS - 1::config.OBS_EVERY_N_STOPS]:
        pts.append((i, plan.timeline[i][0], "ARRIVAL"))
    last = plan.stop_idx[-1]
    pts.append((last, plan.timeline[last][0], "ARRIVAL"))
    pts.sort(key=lambda p: p[1])
    return pts


class Dispatcher:
    def __init__(self) -> None:
        self.producer = bus.producer()
        self.days: dict[str, list[TrainPlan]] = {}
        self.queue: list[TrainPlan] = []          # not yet published, ordered by departure
        self.running: dict[str, Running] = {}
        self.rng = np.random.default_rng()
        self.n_plans = 0
        self.n_obs = 0
        self._stop = asyncio.Event()

    # -- timetable bookkeeping -------------------------------------------
    def _ensure_days(self, now: float) -> None:
        for offset in (0, 86400):
            day = config.kst(now + offset)
            tag = day.strftime("%Y-%m-%d")
            if tag in self.days:
                continue
            plans = build_day(day)
            self.days[tag] = plans
            fresh = [p for p in plans if p.arrival_ts + config.RETIRE_AFTER_S > now]
            self.queue.extend(fresh)
            self.queue.sort(key=lambda p: p.departure_ts)
            log.info("timetable %s: %d services (%d still relevant)", tag, len(plans), len(fresh))
        for tag in [t for t in self.days if t < config.kst(now - 86400).strftime("%Y-%m-%d")]:
            del self.days[tag]

    # -- publishing -------------------------------------------------------
    async def _publish_due(self, now: float) -> None:
        while self.queue and self.queue[0].departure_ts - now <= config.PLAN_HORIZON_S:
            plan = self.queue.pop(0)
            await self.producer.send(config.TOPIC_PLANS, key=plan.train_id,
                                     value=plan.to_json())
            self.n_plans += 1
            punct = TRAIN_TYPES[plan.type]["punctuality"]
            self.running[plan.train_id] = Running(
                plan=plan,
                # Seeded from the id, not from chance: a dispatcher that restarts
                # mid-day regenerates the *same* hidden delay it was already
                # reporting, so replayed observations stay consistent.
                truth=DelayTruth(punct, plan.departure_ts,
                                 zlib.crc32(plan.train_id.encode("utf-8"))),
                pending=_observation_points(plan),
            )

    async def _emit_observations(self, now: float) -> None:
        done: list[str] = []
        for train_id, r in self.running.items():
            delay = r.truth.advance(min(now, r.plan.arrival_ts + 7200))
            while r.pending and now >= r.pending[0][1] + delay:
                node, sched, kind = r.pending.pop(0)
                measured = delay + float(self.rng.normal(0.0, OBS_SIGMA_S))
                await self.producer.send(config.TOPIC_OBSERVATIONS, key=train_id, value={
                    "train_id": train_id,
                    "station": r.plan.route.nodes[node],
                    "node_index": node,
                    "kind": kind,
                    "ts": round(sched + delay, 1),
                    "scheduled_ts": round(sched, 1),
                    "delay_s": round(measured, 1),
                    "sigma_s": OBS_SIGMA_S,
                    "source": "trackside",
                })
                r.reported += 1
                self.n_obs += 1
            if not r.pending and now > r.plan.arrival_ts + delay + config.RETIRE_AFTER_S:
                done.append(train_id)
        for train_id in done:
            del self.running[train_id]

    # -- loop -------------------------------------------------------------
    async def run(self) -> None:
        await bus.ensure_topics()
        await self.producer.start()
        log.info("dispatcher up -> %s (speed x%g)", config.KAFKA_BOOTSTRAP, config.SPEED)
        try:
            last_log = 0.0
            while not self._stop.is_set():
                now = config.now()
                self._ensure_days(now)
                await self._publish_due(now)
                await self._emit_observations(now)
                if now - last_log > 30:
                    last_log = now
                    log.info("%s | in flight %d | plans %d | observations %d",
                             config.hhmm(now), len(self.running), self.n_plans, self.n_obs)
                await asyncio.sleep(config.TICK_S)
        finally:
            await self.producer.stop()
            log.info("dispatcher down")

    def stop(self, *_) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    d = Dispatcher()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, d.stop)
        except NotImplementedError:            # Windows
            signal.signal(sig, d.stop)
    await d.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
