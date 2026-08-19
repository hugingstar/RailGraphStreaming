# -*- coding: utf-8 -*-
"""Estimator: the stream processor that turns schedules into probabilities.

Joins two input streams keyed by train -- the plan (`rail.plans`, compacted) and
sparse trackside reports (`rail.observations`) -- and keeps one particle filter
per train as its state store.  Once a second it advances every filter to the
current instant and emits a full posterior to `rail.positions`: quantiles of
position, the density along the track, per-segment probabilities and ETAs.

State is rebuilt purely by replaying the log, so the process is restartable.
"""
from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
import time
import zlib
from collections import defaultdict

from .. import bus, config
from ..estimation import DIFFUSION_SIGMA, DelayFilter, PlanView
from ..network_data import TRAIN_TYPES

log = logging.getLogger("estimator")


class Estimator:
    def __init__(self) -> None:
        self.consumer = bus.consumer(config.TOPIC_PLANS, config.TOPIC_OBSERVATIONS,
                                     group_id="railgraph-estimator", offset="earliest")
        self.producer = bus.producer()
        self.filters: dict[str, DelayFilter] = {}
        self.buffered: dict[str, list[dict]] = defaultdict(list)
        self.applied: dict[str, set[tuple]] = defaultdict(set)
        self.alerted: set[str] = set()
        self.n_out = 0
        self.n_obs = 0
        self._stop = asyncio.Event()

    # -- stream handlers ---------------------------------------------------
    def _handle(self, topic: str, key: str | None, value: dict) -> None:
        if topic == config.TOPIC_PLANS:
            self._on_plan(value)
        elif topic == config.TOPIC_OBSERVATIONS:
            self._on_observation(value)

    def _on_plan(self, plan_json: dict | None) -> None:
        if not plan_json:                                  # compaction tombstone
            return
        train_id = plan_json["train_id"]
        if train_id in self.filters:
            return
        punct = TRAIN_TYPES.get(plan_json["type"], {}).get("punctuality", 0.9)
        view = PlanView.from_json(plan_json, punct)
        # Seed from the id: a replayed log reproduces the same posterior.
        seed = zlib.crc32(train_id.encode("utf-8"))
        self.filters[train_id] = DelayFilter(view, seed)
        for obs in self.buffered.pop(train_id, []):
            self._apply(train_id, obs)

    def _on_observation(self, obs: dict) -> None:
        train_id = obs["train_id"]
        if train_id not in self.filters:
            self.buffered[train_id].append(obs)
            return
        self._apply(train_id, obs)

    def _apply(self, train_id: str, obs: dict) -> None:
        # The log is replayed on every restart, so the same report can arrive
        # more than once; conditioning on it twice would fake extra certainty.
        sig = (obs.get("node_index"), obs.get("kind"))
        if sig in self.applied[train_id]:
            return
        self.applied[train_id].add(sig)
        filt = self.filters[train_id]
        obs_ts = float(obs["ts"])
        filt.advance(obs_ts)
        # A report ages: delay drifts after the train passed the sensor, so widen
        # the likelihood by the diffusion accumulated since.
        age = max(0.0, config.now() - obs_ts)
        sigma = math.sqrt(float(obs.get("sigma_s", 20.0)) ** 2 + DIFFUSION_SIGMA ** 2 * age)
        filt.observe(float(obs["delay_s"]), sigma)
        filt.last_obs_ts = obs_ts
        self.n_obs += 1

    # -- loops -------------------------------------------------------------
    async def _consume_loop(self) -> None:
        while not self._stop.is_set():
            try:
                batches = await self.consumer.getmany(timeout_ms=400, max_records=2000)
                for tp, msgs in batches.items():
                    for msg in msgs:
                        self._handle(tp.topic, msg.key, msg.value)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("consume loop error; continuing")
                await asyncio.sleep(1.0)

    async def _tick_loop(self) -> None:
        last_log = 0.0
        while not self._stop.is_set():
            started = time.perf_counter()
            now = config.now()
            try:
                await self._tick(now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick error; continuing")
            elapsed = time.perf_counter() - started
            if now - last_log > 30:
                last_log = now
                log.info("%s | tracking %d | %.0f pos/min | obs %d | tick %.0f ms",
                         config.hhmm(now), len(self.filters),
                         len(self.filters) * 60 / max(config.TICK_S, 1e-6),
                         self.n_obs, elapsed * 1000)
            await asyncio.sleep(max(0.0, config.TICK_S - elapsed))

    async def _tick(self, now: float) -> None:
        """Advance every tracked train and publish its posterior."""
        retired: list[str] = []

        # Snapshot the keys: the consumer task inserts new trains while this
        # loop awaits its sends, which would mutate the dict mid-iteration.
        for train_id, filt in list(self.filters.items()):
            plan = filt.plan
            if now < plan.departure_ts - 300:            # not yet interesting
                continue
            filt.advance(now)
            est = filt.estimate(now)
            await self.producer.send(config.TOPIC_POSITIONS, key=train_id, value=est)
            self.n_out += 1
            await self._maybe_alert(est, now)
            if est["status"] == "ARRIVED" and now > plan.arrival_ts + config.RETIRE_AFTER_S:
                retired.append(train_id)

        for train_id in retired:
            self.filters.pop(train_id, None)
            self.alerted.discard(train_id)
            self.applied.pop(train_id, None)

    async def _maybe_alert(self, est: dict, now: float) -> None:
        train_id = est["train_id"]
        delayed = est["delay"]["p50"] >= config.ALERT_DELAY_S
        if delayed and train_id not in self.alerted:
            self.alerted.add(train_id)
            kind = "DELAYED"
        elif not delayed and train_id in self.alerted and est["delay"]["p50"] < config.ALERT_CLEAR_S:
            self.alerted.discard(train_id)
            kind = "RECOVERED"
        else:
            return
        await self.producer.send(config.TOPIC_ALERTS, key=train_id, value={
            "train_id": train_id, "name": est["name"], "type": est["type"],
            "line_id": est["line_id"], "kind": kind, "ts": round(now, 1),
            "delay_p50": est["delay"]["p50"], "delay_p95": est["delay"]["p95"],
            "origin": est["origin"], "destination": est["destination"],
            "next_stop": est["next_stops"][0]["station"] if est["next_stops"] else None,
        })

    # -- lifecycle ---------------------------------------------------------
    async def run(self) -> None:
        await bus.ensure_topics()
        await self.consumer.start()
        await self.producer.start()
        log.info("estimator up -> %s", config.KAFKA_BOOTSTRAP)
        replayed = await bus.drain_backlog(self.consumer, self._handle)
        log.info("replayed %d records: %d trains in state", replayed, len(self.filters))
        try:
            await asyncio.gather(self._consume_loop(), self._tick_loop())
        finally:
            await self.consumer.stop()
            await self.producer.stop()
            log.info("estimator down")

    def stop(self, *_) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    e = Estimator()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, e.stop)
        except NotImplementedError:
            signal.signal(sig, e.stop)
    await e.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
