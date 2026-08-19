# -*- coding: utf-8 -*-
"""Builds the GraphRAG layer: nodes/edges over the network plus persisted trips.

Two halves, both idempotent upserts so this can be re-run freely:

- Static topology (Station/Line/TrainType nodes, SERVES/ADJACENT_TO edges)
  comes straight from `network_data` and never changes at runtime -- built
  once at startup.
- Dynamic facts (Trip/Alert nodes) come from the tables `persist.py` fills
  from Kafka.  Polled on a timer using a `last_seen_at`/id watermark so a
  quiet stretch doesn't mean re-scanning the whole table.

Every node also gets a `summary`: a short natural-language sentence, since
that's what an embedding step (next up) will actually vectorize -- the
structured `properties` stay for exact-match / graph-traversal queries.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg

from .. import config, embed
from ..network import NETWORK
from ..network_data import TRAIN_TYPES

log = logging.getLogger("graphbuild")

LOOP_INTERVAL_S = float(os.getenv("RAILGRAPH_GRAPHBUILD_INTERVAL_S", "30"))
# Kept small: the free Gemini embedding tier's rate limit is easy to blow
# through, and this only needs to keep up with new Trip/Alert nodes trickling
# in, not catch a large backlog in one shot.
EMBED_BATCH = int(os.getenv("RAILGRAPH_EMBED_BATCH", "20"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          UUID PRIMARY KEY,
    type        TEXT NOT NULL,
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    properties  JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (type, key)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id        BIGSERIAL PRIMARY KEY,
    src_id    UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    dst_id    UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    relation  TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS graph_edges_src_idx ON graph_edges (src_id);
CREATE INDEX IF NOT EXISTS graph_edges_dst_idx ON graph_edges (dst_id);
"""

# Embedding vector, added once pgvector is available. Kept out of the base
# SCHEMA block above so an older Postgres without the extension still gets a
# usable graph -- just without semantic search until it's upgraded.
EMBED_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE graph_nodes ADD COLUMN IF NOT EXISTS embedding vector({config.EMBEDDING_DIM});
CREATE INDEX IF NOT EXISTS graph_nodes_embedding_idx
    ON graph_nodes USING hnsw (embedding vector_cosine_ops);
"""

_UPSERT_NODE = """
INSERT INTO graph_nodes (id, type, key, label, summary, properties, updated_at)
VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, now())
ON CONFLICT (type, key) DO UPDATE
    SET label = EXCLUDED.label, summary = EXCLUDED.summary,
        properties = EXCLUDED.properties, updated_at = now()
RETURNING id
"""

_UPSERT_EDGE = """
INSERT INTO graph_edges (src_id, dst_id, relation, properties)
VALUES ($1, $2, $3, $4)
ON CONFLICT (src_id, dst_id, relation) DO UPDATE SET properties = EXCLUDED.properties
"""


async def _node_id(conn: asyncpg.Connection, type_: str, key: str) -> str | None:
    row = await conn.fetchrow("SELECT id FROM graph_nodes WHERE type = $1 AND key = $2", type_, key)
    return row["id"] if row else None


async def _upsert_node(conn: asyncpg.Connection, type_: str, key: str, label: str,
                       summary: str, properties: dict) -> str:
    row = await conn.fetchrow(_UPSERT_NODE, type_, key, label, summary, orjson_dumps(properties))
    return row["id"]


def orjson_dumps(obj: dict) -> str:
    import orjson
    return orjson.dumps(obj).decode()


async def build_static(conn: asyncpg.Connection) -> None:
    station_ids: dict[str, str] = {}
    for name, (lat, lon) in NETWORK.stations.items():
        station_ids[name] = await _upsert_node(
            conn, "Station", name, name, f"{name}역",
            {"lat": lat, "lon": lon},
        )

    for tname, spec in TRAIN_TYPES.items():
        await _upsert_node(
            conn, "TrainType", tname, tname,
            f"{tname}: 표정속도 최대 {spec['hsr_kmh']}km/h, 정시율 {spec['punctuality']*100:.0f}%",
            {"hsr_kmh": spec["hsr_kmh"], "conv_kmh": spec["conv_kmh"],
             "punctuality": spec["punctuality"]},
        )

    for line in NETWORK.lines:
        stations = line["stations"]
        line_id = await _upsert_node(
            conn, "Line", line["id"], line["name"],
            f"{line['name']}: {stations[0]}~{stations[-1]}, {len(stations)}개 역"
            f"{' (고속선)' if line['hsr'] else ''}",
            {"hsr": line["hsr"], "color": line["color"], "n_stations": len(stations)},
        )
        for i, name in enumerate(stations):
            await _upsert_edge(conn, line_id, station_ids[name], "SERVES", {"seq": i})
        for a, b in zip(stations, stations[1:]):
            edge = NETWORK.edge(a, b)
            props = {"km": round(edge.km, 2), "hsr": edge.hsr, "line_id": edge.line_id}
            await _upsert_edge(conn, station_ids[a], station_ids[b], "ADJACENT_TO", props)
            await _upsert_edge(conn, station_ids[b], station_ids[a], "ADJACENT_TO", props)
    log.info("static topology: %d stations, %d lines, %d train types",
             len(station_ids), len(NETWORK.lines), len(TRAIN_TYPES))


async def _upsert_edge(conn: asyncpg.Connection, src: str, dst: str, relation: str,
                       properties: dict) -> None:
    await conn.execute(_UPSERT_EDGE, src, dst, relation, orjson_dumps(properties))


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, config.KST).strftime("%H:%M")


class DynamicSync:
    """Watermarked poll of train_trips / trip_alerts into Trip / Alert nodes."""

    def __init__(self) -> None:
        self.trips_since = datetime.fromtimestamp(0, timezone.utc)
        self.alerts_since = 0

    async def run_once(self, conn: asyncpg.Connection) -> None:
        await self._sync_trips(conn)
        await self._sync_alerts(conn)

    async def _sync_trips(self, conn: asyncpg.Connection) -> None:
        rows = await conn.fetch(
            "SELECT * FROM train_trips WHERE last_seen_at > $1 ORDER BY last_seen_at",
            self.trips_since,
        )
        for r in rows:
            dep, arr = _hhmm(r["departure_ts"]), _hhmm(r["arrival_ts"])
            summary = (f"{r['name']} ({r['direction']}) {r['origin']}→{r['destination']}, "
                       f"{dep} 출발 {arr} 도착")
            trip_id = await _upsert_node(
                conn, "Trip", r["train_id"], r["name"], summary,
                {"number": r["number"], "type": r["type"], "line_id": r["line_id"],
                 "origin": r["origin"], "destination": r["destination"],
                 "direction": r["direction"], "departure_ts": r["departure_ts"],
                 "arrival_ts": r["arrival_ts"]},
            )
            type_id = await _node_id(conn, "TrainType", r["type"])
            line_id = await _node_id(conn, "Line", r["line_id"])
            origin_id = await _node_id(conn, "Station", r["origin"])
            dest_id = await _node_id(conn, "Station", r["destination"])
            if type_id:
                await _upsert_edge(conn, trip_id, type_id, "OF_TYPE", {})
            if line_id:
                await _upsert_edge(conn, trip_id, line_id, "RUNS_ON", {})
            if origin_id:
                await _upsert_edge(conn, trip_id, origin_id, "DEPARTS_FROM", {})
            if dest_id:
                await _upsert_edge(conn, trip_id, dest_id, "ARRIVES_AT", {})
            self.trips_since = max(self.trips_since, r["last_seen_at"])
        if rows:
            log.info("synced %d trip nodes", len(rows))

    async def _sync_alerts(self, conn: asyncpg.Connection) -> None:
        rows = await conn.fetch(
            "SELECT * FROM trip_alerts WHERE id > $1 ORDER BY id", self.alerts_since,
        )
        for r in rows:
            key = str(r["id"])
            when = _hhmm(r["ts"])
            if r["kind"] == "DELAYED":
                summary = f"{r['name']} {when} 지연 발생 (지연 {round((r['delay_p50'] or 0) / 60, 1)}분)"
            else:
                summary = f"{r['name']} {when} 지연 회복"
            alert_id = await _upsert_node(
                conn, "Alert", key, f"{r['name']} {r['kind']}", summary,
                {"kind": r["kind"], "ts": r["ts"], "delay_p50": r["delay_p50"],
                 "delay_p95": r["delay_p95"], "next_stop": r["next_stop"]},
            )
            trip_id = await _node_id(conn, "Trip", r["train_id"])
            if trip_id:
                await _upsert_edge(conn, trip_id, alert_id, "RAISED", {})
            self.alerts_since = max(self.alerts_since, r["id"])
        if rows:
            log.info("synced %d alert nodes", len(rows))


async def embed_pending(conn: asyncpg.Connection, max_batches: int = 1) -> None:
    """Backfills embeddings for nodes that don't have one yet, a few batches at a time."""
    if not config.GEMINI_API_KEY:
        return
    for _ in range(max_batches):
        rows = await conn.fetch(
            "SELECT id, summary FROM graph_nodes WHERE embedding IS NULL LIMIT $1", EMBED_BATCH,
        )
        if not rows:
            return
        vectors = await embed.embed_texts([r["summary"] for r in rows])
        done = 0
        for r, v in zip(rows, vectors):
            vec_str = "[" + ",".join(f"{x:.6f}" for x in v) + "]"
            try:
                await conn.execute("UPDATE graph_nodes SET embedding = $1::vector WHERE id = $2",
                                   vec_str, r["id"])
                done += 1
            except Exception:
                log.exception("failed to store embedding for node %s", r["id"])
        log.info("embedded %d/%d node(s)", done, len(rows))
        if len(rows) < EMBED_BATCH:
            return


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid()
        await conn.execute(SCHEMA)
        try:
            await conn.execute(EMBED_SCHEMA)
        except Exception:
            log.warning("pgvector unavailable -- graph will have no embeddings")
        await build_static(conn)

    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set -- nodes will not be embedded")

    sync = DynamicSync()
    log.info("graphbuild up, polling every %.0fs", LOOP_INTERVAL_S)
    try:
        while True:
            async with pool.acquire() as conn:
                try:
                    await sync.run_once(conn)
                    await embed_pending(conn)
                except Exception:
                    log.exception("sync pass failed")
            await asyncio.sleep(LOOP_INTERVAL_S)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
