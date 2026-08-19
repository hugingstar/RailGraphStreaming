# -*- coding: utf-8 -*-
"""Gateway: Kafka -> browser.

Consumes `rail.positions` and `rail.alerts` into an in-memory snapshot and fans
it out over one WebSocket tick per second.  The full posterior (density bins,
per-segment probabilities, the whole ETA table) is only sent for the train a
client has selected -- everything else travels slim, so a hundred trains cost
about a hundred kilobytes a second rather than a megabyte.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import orjson
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import auth, bus, config, graphrag
from ..network import NETWORK
from ..timetable import build_day, station_weights

log = logging.getLogger("api")

BROADCAST_S = float(os.getenv("RAILGRAPH_BROADCAST_S", "1.0"))
STALE_AFTER_S = 12.0

# Fields the map and the train list need; the rest rides only on selection.
SLIM_DROP = ("density", "segments", "next_stops", "_seen")


def _slim(est: dict) -> dict:
    out = {k: v for k, v in est.items() if k not in SLIM_DROP}
    nxt = est.get("next_stops") or []
    out["next"] = nxt[0] if nxt else None
    return out


class Hub:
    """Snapshot of the world plus the set of connected browsers."""

    def __init__(self) -> None:
        self.trains: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.alerts: deque[dict] = deque(maxlen=60)
        self.pending_alerts: list[dict] = []
        self.clients: dict[WebSocket, str | None] = {}
        self.n_positions = 0
        self._rate_window: deque[tuple[float, int]] = deque(maxlen=12)
        self._network_payload: dict | None = None

    # -- ingest ------------------------------------------------------------
    def on_position(self, est: dict) -> None:
        est["_seen"] = time.time()
        self.trains[est["train_id"]] = est
        self.n_positions += 1

    def on_alert(self, alert: dict) -> None:
        self.alerts.appendleft(alert)
        self.pending_alerts.append(alert)

    def on_plan(self, plan: dict | None) -> None:
        if plan:
            self.plans[plan["train_id"]] = plan

    # -- derived -----------------------------------------------------------
    def prune(self) -> list[str]:
        cutoff = time.time() - STALE_AFTER_S
        gone = [tid for tid, t in self.trains.items() if t["_seen"] < cutoff]
        for tid in gone:
            self.trains.pop(tid, None)
        return gone

    def stats(self) -> dict:
        now = time.time()
        self._rate_window.append((now, self.n_positions))
        rate = 0.0
        if len(self._rate_window) > 1:
            (t0, c0), (t1, c1) = self._rate_window[0], self._rate_window[-1]
            if t1 > t0:
                rate = (c1 - c0) / (t1 - t0)
        live = [t for t in self.trains.values() if t["status"] in ("RUNNING", "DWELL")]
        delays = [t["delay"]["p50"] for t in live]
        by_type: dict[str, int] = {}
        for t in live:
            by_type[t["type"]] = by_type.get(t["type"], 0) + 1
        spread = [t["spread_km"] for t in live]
        return {
            "sim_ts": round(config.now(), 1),
            "active": len(live),
            "tracked": len(self.trains),
            "delayed": sum(1 for d in delays if d >= config.ALERT_DELAY_S),
            "avg_delay_s": round(sum(delays) / len(delays), 1) if delays else 0.0,
            "max_delay_s": round(max(delays), 1) if delays else 0.0,
            "avg_spread_km": round(sum(spread) / len(spread), 2) if spread else 0.0,
            "msgs_per_sec": round(rate, 1),
            "total_positions": self.n_positions,
            "by_type": by_type,
            "speed": config.SPEED,
        }

    def network_payload(self) -> dict:
        if self._network_payload is None:
            plans = build_day(datetime.now(timezone.utc))
            self._network_payload = NETWORK.to_payload(station_weights(plans))
        return self._network_payload


hub = Hub()


# ---------------------------------------------------------------------------
async def _consume_state(stop: asyncio.Event) -> None:
    """rail.plans replayed from the beginning: route geometry and timetables."""
    cons = bus.consumer(config.TOPIC_PLANS, group_id="railgraph-api-plans",
                        offset="earliest")
    await cons.start()
    try:
        n = await bus.drain_backlog(cons, lambda _t, _k, v: hub.on_plan(v))
        log.info("plan state rebuilt: %d records, %d plans", n, len(hub.plans))
        while not stop.is_set():
            batches = await cons.getmany(timeout_ms=500)
            for _tp, msgs in batches.items():
                for m in msgs:
                    hub.on_plan(m.value)
    finally:
        await cons.stop()


async def _consume_live(stop: asyncio.Event) -> None:
    """rail.positions / rail.alerts, tail only -- history is not interesting."""
    group = f"railgraph-api-live-{uuid.uuid4().hex[:8]}"
    cons = bus.consumer(config.TOPIC_POSITIONS, config.TOPIC_ALERTS,
                        group_id=group, offset="latest")
    await cons.start()
    log.info("live consumer up (%s)", group)
    try:
        while not stop.is_set():
            batches = await cons.getmany(timeout_ms=300, max_records=4000)
            for tp, msgs in batches.items():
                if tp.topic == config.TOPIC_POSITIONS:
                    for m in msgs:
                        hub.on_position(m.value)
                else:
                    for m in msgs:
                        hub.on_alert(m.value)
    finally:
        await cons.stop()


async def _broadcast(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(BROADCAST_S)
        if not hub.clients:
            hub.pending_alerts.clear()
            hub.prune()
            continue
        removed = hub.prune()
        alerts, hub.pending_alerts = hub.pending_alerts, []
        slim = [_slim(t) for t in hub.trains.values()]
        stats = hub.stats()
        dead: list[WebSocket] = []
        for ws, selected in hub.clients.items():
            payload = {"t": "tick", "ts": time.time(), "trains": slim,
                       "removed": removed, "alerts": alerts, "stats": stats}
            if selected and selected in hub.trains:
                payload["detail"] = {k: v for k, v in hub.trains[selected].items()
                                     if not k.startswith("_")}
            try:
                await ws.send_bytes(orjson.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            hub.clients.pop(ws, None)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)-11s %(message)s", datefmt="%H:%M:%S")
    await auth.init_db()
    log.info("db up -> %s", config.DATABASE_URL)
    await bus.ensure_topics()
    stop = asyncio.Event()
    tasks = [asyncio.create_task(c(stop))
             for c in (_consume_state, _consume_live, _broadcast)]
    log.info("api up -> kafka %s", config.KAFKA_BOOTSTRAP)
    try:
        yield
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await auth.close_db()


app = FastAPI(title="RailGraph Streaming", default_response_class=ORJSONResponse,
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(auth.router)


@app.get("/api/network")
async def get_network(_user=Depends(auth.require_user)):
    return hub.network_payload()


@app.get("/api/snapshot")
async def get_snapshot(_user=Depends(auth.require_user)):
    return {"trains": [_slim(t) for t in hub.trains.values()], "stats": hub.stats(),
            "alerts": list(hub.alerts)}


@app.get("/api/stats")
async def get_stats(_user=Depends(auth.require_user)):
    return hub.stats()


@app.get("/api/trains/{train_id}")
async def get_train(train_id: str, _user=Depends(auth.require_user)):
    est = hub.trains.get(train_id)
    est = {k: v for k, v in est.items() if not k.startswith("_")} if est else None
    plan = hub.plans.get(train_id)
    if est is None and plan is None:
        return ORJSONResponse({"error": "unknown train"}, status_code=404)
    return {"position": est, "plan": plan}


class GraphRagQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    generate: bool = True


@app.post("/api/graphrag/query")
async def graphrag_query(body: GraphRagQuery, _user=Depends(auth.require_user)):
    try:
        return await graphrag.answer(auth.get_pool(), body.query, generate=body.generate)
    except RuntimeError as exc:  # GEMINI_API_KEY missing
        raise HTTPException(status_code=503, detail=str(exc))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    user = await auth.user_from_token(ws.cookies.get(config.SESSION_COOKIE))
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.clients[ws] = None
    try:
        await ws.send_bytes(orjson.dumps({
            "t": "hello", "ts": time.time(),
            "trains": [_slim(t) for t in hub.trains.values()],
            "alerts": list(hub.alerts), "stats": hub.stats(),
        }))
        while True:
            msg = orjson.loads(await ws.receive_text())
            if msg.get("t") == "select":
                hub.clients[ws] = msg.get("train_id")
    except WebSocketDisconnect:
        pass
    except Exception as exc:                     # noqa: BLE001 - client-driven
        log.debug("ws closed: %s", exc)
    finally:
        hub.clients.pop(ws, None)


_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Client-side routes (/login, /account, ...) all resolve to the same
        # bundle; React Router picks the right screen from the URL.
        if full_path.startswith("api/") or full_path == "ws":
            raise HTTPException(status_code=404)
        return FileResponse(_dist / "index.html")
