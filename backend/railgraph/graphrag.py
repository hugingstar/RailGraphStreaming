# -*- coding: utf-8 -*-
"""GraphRAG retrieval: embed the question, find nearest graph nodes by cosine
distance, pull each one's one-hop neighborhood for context, then (optionally)
ask Gemini to answer using only that context.

Retrieval and generation are split on purpose: retrieval only needs Postgres
and always works once nodes are embedded; generation needs a live Gemini call
and a spare quota, so a 429 there degrades to "here are the facts" instead of
a failed request.
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import asyncpg

from . import config, embed

TOP_K = 8

_TODAY_WORDS = ("오늘", "금일", "지나가는", "지나가", "경유", "스케줄", "시간표")
_TOMORROW_WORDS = ("내일", "명일")


def _day_window(question: str) -> tuple[float, float] | tuple[None, None]:
    """A schedule question ("오늘/내일 ... 스케줄/경유") implies a KST calendar
    day to filter Trip stops by; anything else gets no date filter."""
    now = datetime.now(config.KST)
    if any(w in question for w in _TOMORROW_WORDS):
        day = now + timedelta(days=1)
    elif any(w in question for w in _TODAY_WORDS):
        day = now
    else:
        return None, None
    start = datetime.combine(day.date(), time.min, tzinfo=config.KST).timestamp()
    return start, start + 86400


async def _match_station(conn: asyncpg.Connection, question: str) -> tuple[str, str] | None:
    """Exact-match a Station node's key against the raw question text --
    embedding similarity is unreliable for proper nouns like station names,
    so this handles them separately from vector search."""
    rows = await conn.fetch("SELECT id, key FROM graph_nodes WHERE type = 'Station'")
    candidates = [(r["id"], r["key"]) for r in rows if r["key"] and r["key"] in question]
    if not candidates:
        return None
    return max(candidates, key=lambda c: len(c[1]))


def _hhmm(ts: float | None) -> str:
    if ts is None:
        return "?"
    return datetime.fromtimestamp(ts, config.KST).strftime("%H:%M")


async def _station_trips(conn: asyncpg.Connection, station_id: str, station_key: str,
                         day_start: float | None, day_end: float | None,
                         limit: int = 15) -> list[dict]:
    """Graph traversal (not vector search): every Trip whose route touches
    this station, optionally windowed to one KST calendar day, newest
    scheduled stop-time first."""
    clauses = ["e.dst_id = $1", "e.relation IN ('DEPARTS_FROM', 'STOPS_AT', 'ARRIVES_AT')"]
    params: list = [station_id]
    if day_start is not None:
        clauses.append("(e.properties->>'departure_ts')::float BETWEEN $2 AND $3")
        params.extend([day_start, day_end])
    rows = await conn.fetch(
        f"""
        SELECT n.type, n.key, n.label, n.summary, e.properties AS stop
        FROM graph_edges e JOIN graph_nodes n ON n.id = e.src_id
        WHERE {" AND ".join(clauses)}
        ORDER BY (e.properties->>'departure_ts')::float
        LIMIT {limit}
        """,
        *params,
    )
    results = []
    for r in rows:
        stop = json.loads(r["stop"]) if isinstance(r["stop"], str) else r["stop"]
        when = _hhmm(stop.get("departure_ts") if stop.get("departure_ts") is not None
                     else stop.get("arrival_ts"))
        results.append({
            "type": r["type"], "key": r["key"], "label": r["label"],
            "summary": f"{station_key} {when} 경유 — {r['summary']}",
            "score": None, "neighbors": [],
        })
    return results


async def _neighbors(conn: asyncpg.Connection, node_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT e.relation, n.type, n.key, n.label, 'out' AS dir
        FROM graph_edges e JOIN graph_nodes n ON n.id = e.dst_id
        WHERE e.src_id = $1
        UNION ALL
        SELECT e.relation, n.type, n.key, n.label, 'in' AS dir
        FROM graph_edges e JOIN graph_nodes n ON n.id = e.src_id
        WHERE e.dst_id = $1
        LIMIT 20
        """,
        node_id,
    )
    return [dict(r) for r in rows]


async def retrieve(pool: asyncpg.Pool, question: str, top_k: int = TOP_K) -> list[dict]:
    async with pool.acquire() as conn:
        results: list[dict] = []
        station = await _match_station(conn, question)
        if station is not None:
            station_id, station_key = station
            day_start, day_end = _day_window(question)
            results.extend(await _station_trips(conn, station_id, station_key, day_start, day_end))

        # Exact-match graph traversal above needs no embedding call; vector
        # search is best-effort on top of it, so a missing/quota-exhausted
        # Gemini key degrades to traversal-only instead of failing outright.
        try:
            [vector] = await embed.embed_texts([question])
        except Exception:
            vector = None
        if vector is not None:
            vec_str = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
            rows = await conn.fetch(
                """
                SELECT id, type, key, label, summary, properties,
                       1 - (embedding <=> $1::vector) AS score
                FROM graph_nodes
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vec_str, top_k,
            )
            seen = {(n["type"], n["key"]) for n in results}
            for r in rows:
                if (r["type"], r["key"]) in seen:
                    continue
                neighbors = await _neighbors(conn, r["id"])
                results.append({
                    "type": r["type"], "key": r["key"], "label": r["label"],
                    "summary": r["summary"], "score": round(r["score"], 4),
                    "neighbors": neighbors,
                })
    return results


def _format_context(nodes: list[dict]) -> str:
    lines = []
    for n in nodes:
        lines.append(f"- [{n['type']}] {n['summary']}")
        for nb in n["neighbors"][:6]:
            arrow = "->" if nb["dir"] == "out" else "<-"
            lines.append(f"    {arrow} {nb['relation']} {arrow} [{nb['type']}] {nb['label']}")
    return "\n".join(lines)


async def answer(pool: asyncpg.Pool, question: str, generate: bool = True) -> dict:
    nodes = await retrieve(pool, question)
    result: dict = {"question": question, "nodes": nodes, "answer": None}
    if not nodes:
        result["answer"] = "관련 정보를 찾지 못했습니다."
        return result
    if not generate:
        return result
    context = _format_context(nodes)
    prompt = (
        "다음은 철도 운행 그래프에서 검색한 사실들이다. 이 사실만 근거로 질문에 "
        "한국어로 간결하게 답하라. 근거가 부족하면 모른다고 답하라.\n\n"
        f"[사실]\n{context}\n\n[질문]\n{question}"
    )
    try:
        result["answer"] = (await embed.generate_text(prompt)).strip()
    except Exception as exc:  # noqa: BLE001 - degrade to retrieval-only
        result["answer"] = None
        result["generation_error"] = str(exc)
    return result
