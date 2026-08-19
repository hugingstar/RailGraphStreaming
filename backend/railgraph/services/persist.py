# -*- coding: utf-8 -*-
"""Kafka -> Postgres: persists the facts GraphRAG needs once the topics roll off.

rail.positions ticks at ~1Hz per train and rail.positions itself only keeps
10 minutes of history -- nothing here needs that resolution once the day is
over.  So this keeps one delay sample per train every SAMPLE_INTERVAL_S (plus
one on every status change), and stores plans/alerts as-is since those are
already sparse.  Everything lands downstream of a small in-memory buffer that
flushes on a timer rather than per message, since a write per Kafka record
would be needless round-trips for data this sparse.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

from .. import bus, config

log = logging.getLogger("persist")

SAMPLE_INTERVAL_S = float(os.getenv("RAILGRAPH_SAMPLE_INTERVAL_S", "60"))
FLUSH_INTERVAL_S = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS train_trips (
    train_id      TEXT PRIMARY KEY,
    number        INTEGER NOT NULL,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    pattern_id    TEXT NOT NULL,
    line_id       TEXT NOT NULL,
    origin        TEXT NOT NULL,
    destination   TEXT NOT NULL,
    direction     TEXT NOT NULL,
    departure_ts  DOUBLE PRECISION NOT NULL,
    arrival_ts    DOUBLE PRECISION NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trip_delay_samples (
    id          BIGSERIAL PRIMARY KEY,
    train_id    TEXT NOT NULL REFERENCES train_trips(train_id) ON DELETE CASCADE,
    ts          DOUBLE PRECISION NOT NULL,
    status      TEXT NOT NULL,
    progress    DOUBLE PRECISION NOT NULL,
    km_p50      DOUBLE PRECISION NOT NULL,
    delay_p05   DOUBLE PRECISION NOT NULL,
    delay_p50   DOUBLE PRECISION NOT NULL,
    delay_p95   DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS trip_delay_samples_train_idx ON trip_delay_samples (train_id, ts);

CREATE TABLE IF NOT EXISTS trip_alerts (
    id          BIGSERIAL PRIMARY KEY,
    train_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    line_id     TEXT,
    kind        TEXT NOT NULL,
    ts          DOUBLE PRECISION NOT NULL,
    delay_p50   DOUBLE PRECISION,
    delay_p95   DOUBLE PRECISION,
    next_stop   TEXT
);
CREATE INDEX IF NOT EXISTS trip_alerts_train_idx ON trip_alerts (train_id, ts);
"""


class Writer:
    """Buffers rows in memory and flushes them to Postgres on a timer."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self.trip_upserts: dict[str, tuple] = {}
        self.samples: list[tuple] = []
        self.alerts: list[tuple] = []
        self._last_sample: dict[str, tuple[float, str]] = {}

    def on_plan(self, plan: dict) -> None:
        try:
            self.trip_upserts[plan["train_id"]] = (
                plan["train_id"], plan["number"], plan["name"], plan["type"],
                plan["pattern_id"], plan["line_id"], plan["origin"], plan["destination"],
                plan["direction"], plan["timeline"][0][1], plan["timeline"][-1][0],
            )
        except (KeyError, IndexError):
            # A stale record from a since-changed plan schema, still sitting in
            # the compacted topic under an old key -- not worth failing over.
            log.warning("skipping malformed plan record: %s", plan.get("train_id", "?"))

    def on_position(self, est: dict) -> None:
        train_id, status, ts = est["train_id"], est["status"], est["ts"]
        last = self._last_sample.get(train_id)
        due = last is None or ts - last[0] >= SAMPLE_INTERVAL_S or status != last[1]
        if not due:
            return
        self._last_sample[train_id] = (ts, status)
        self.samples.append((
            train_id, ts, status, est["progress"], est["km"]["p50"],
            est["delay"]["p05"], est["delay"]["p50"], est["delay"]["p95"],
        ))

    def on_alert(self, alert: dict) -> None:
        self.alerts.append((
            alert["train_id"], alert["name"], alert["type"], alert.get("line_id"),
            alert["kind"], alert["ts"], alert.get("delay_p50"), alert.get("delay_p95"),
            alert.get("next_stop"),
        ))

    async def flush(self) -> None:
        n = len(self.trip_upserts) + len(self.samples) + len(self.alerts)
        if not n:
            return
        trips, samples, alerts = list(self.trip_upserts.values()), self.samples, self.alerts
        self.trip_upserts, self.samples, self.alerts = {}, [], []
        async with self.pool.acquire() as conn, conn.transaction():
            if trips:
                await conn.executemany(
                    """INSERT INTO train_trips
                           (train_id, number, name, type, pattern_id, line_id,
                            origin, destination, direction, departure_ts, arrival_ts)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                       ON CONFLICT (train_id) DO UPDATE SET last_seen_at = now()""",
                    trips,
                )
            if samples:
                await conn.executemany(
                    """INSERT INTO trip_delay_samples
                           (train_id, ts, status, progress, km_p50, delay_p05, delay_p50, delay_p95)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    samples,
                )
            if alerts:
                await conn.executemany(
                    """INSERT INTO trip_alerts
                           (train_id, name, type, line_id, kind, ts, delay_p50, delay_p95, next_stop)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    alerts,
                )
        log.info("flushed %d rows (%d trips, %d samples, %d alerts)",
                 n, len(trips), len(samples), len(alerts))


async def _consume_plans(writer: Writer, stop: asyncio.Event) -> None:
    cons = bus.consumer(config.TOPIC_PLANS, group_id="railgraph-persist-plans", offset="earliest")
    await cons.start()
    try:
        n = await bus.drain_backlog(cons, lambda _t, _k, v: writer.on_plan(v))
        log.info("plan backlog replayed: %d records", n)
        while not stop.is_set():
            batches = await cons.getmany(timeout_ms=500)
            for _tp, msgs in batches.items():
                for m in msgs:
                    writer.on_plan(m.value)
    finally:
        await cons.stop()


async def _consume_live(writer: Writer, stop: asyncio.Event) -> None:
    cons = bus.consumer(config.TOPIC_POSITIONS, config.TOPIC_ALERTS,
                        group_id="railgraph-persist-live", offset="latest")
    await cons.start()
    try:
        while not stop.is_set():
            batches = await cons.getmany(timeout_ms=500, max_records=4000)
            for tp, msgs in batches.items():
                if tp.topic == config.TOPIC_POSITIONS:
                    for m in msgs:
                        writer.on_position(m.value)
                else:
                    for m in msgs:
                        writer.on_alert(m.value)
    finally:
        await cons.stop()


async def _flush_loop(writer: Writer, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(FLUSH_INTERVAL_S)
        try:
            await writer.flush()
        except Exception:
            log.exception("flush failed")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
    log.info("db ready -> %s", config.DATABASE_URL)

    await bus.ensure_topics()
    writer = Writer(pool)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(c(writer, stop))
             for c in (_consume_plans, _consume_live, _flush_loop)]
    log.info("persist up -> kafka %s, sampling every %.0fs", config.KAFKA_BOOTSTRAP, SAMPLE_INTERVAL_S)
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        await writer.flush()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
