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


def _context_text(question: str, history: list[dict] | None) -> str:
    """Folds recent turns into one blob of text for entity/date matching and
    embedding, so a follow-up like "그럼 하행은?" still resolves against the
    station named a turn or two earlier instead of starting from scratch."""
    parts = [f"{h.get('question', '')} {h.get('answer', '') or ''}" for h in (history or [])[-6:]]
    parts.append(question)
    return " ".join(parts)


def _history_text(history: list[dict] | None) -> str:
    if not history:
        return "(없음)"
    return "\n".join(f"Q: {h.get('question', '')}\nA: {h.get('answer', '')}" for h in history[-6:])


def _day_window(text: str) -> tuple[float, float] | tuple[None, None]:
    """A schedule question ("오늘/내일 ... 스케줄/경유") implies a KST calendar
    day to filter Trip stops by; anything else gets no date filter."""
    now = datetime.now(config.KST)
    if any(w in text for w in _TOMORROW_WORDS):
        day = now + timedelta(days=1)
    elif any(w in text for w in _TODAY_WORDS):
        day = now
    else:
        return None, None
    start = datetime.combine(day.date(), time.min, tzinfo=config.KST).timestamp()
    return start, start + 86400


async def _match_station(conn: asyncpg.Connection, text: str) -> tuple[str, str] | None:
    """Exact-match a Station node's key against raw text -- embedding
    similarity is unreliable for proper nouns like station names, so this
    handles them separately from vector search."""
    rows = await conn.fetch("SELECT id, key FROM graph_nodes WHERE type = 'Station'")
    candidates = [(r["id"], r["key"]) for r in rows if r["key"] and r["key"] in text]
    if not candidates:
        return None
    return max(candidates, key=lambda c: len(c[1]))


def _hhmm(ts: float | None) -> str:
    if ts is None:
        return "?"
    return datetime.fromtimestamp(ts, config.KST).strftime("%H:%M")


RUSH_WINDOW_S = 1800  # 30 min: departing sooner than this isn't a realistic recommendation


async def _station_trips(conn: asyncpg.Connection, station_id: str, station_key: str,
                         day_start: float | None, day_end: float | None,
                         limit: int = 15) -> tuple[list[dict], dict]:
    """Graph traversal (not vector search): every Trip whose route touches
    this station, optionally windowed to one KST calendar day.

    Split into two buckets instead of one flat list: `recommended` trains are
    at least RUSH_WINDOW_S out (soonest first) -- the only ones worth putting
    in front of someone -- and `too_soon` ones depart within that window, kept
    only so the reply can call them out as too rushed to catch rather than
    silently dropping them."""
    clauses = ["e.dst_id = $1", "e.relation IN ('DEPARTS_FROM', 'STOPS_AT', 'ARRIVES_AT')"]
    base_params: list = [station_id]
    if day_start is not None:
        clauses.append("(e.properties->>'departure_ts')::float BETWEEN $2 AND $3")
        base_params.extend([day_start, day_end])
    where = " AND ".join(clauses)
    now_ts = config.now()
    rush_cutoff = now_ts + RUSH_WINDOW_S

    async def _fetch(extra_clause: str, extra_params: list, fetch_limit: int) -> list[dict]:
        params = base_params + extra_params
        rows = await conn.fetch(
            f"""
            SELECT n.type, n.key, n.label, n.summary, n.properties AS trip_props,
                   e.properties AS stop
            FROM graph_edges e JOIN graph_nodes n ON n.id = e.src_id
            WHERE {where} {extra_clause}
            ORDER BY (e.properties->>'departure_ts')::float
            LIMIT {fetch_limit}
            """,
            *params,
        )
        out = []
        for r in rows:
            stop = json.loads(r["stop"]) if isinstance(r["stop"], str) else r["stop"]
            trip_props = json.loads(r["trip_props"]) if isinstance(r["trip_props"], str) else r["trip_props"]
            dep_ts = stop.get("departure_ts") if stop.get("departure_ts") is not None else stop.get("arrival_ts")
            out.append({
                "type": r["type"], "key": r["key"], "label": r["label"], "summary": r["summary"],
                "time": _hhmm(dep_ts), "name": trip_props.get("type", "?"),
                "number": trip_props.get("number", "?"),
            })
        return out

    recommended = await _fetch(
        f"AND (e.properties->>'departure_ts')::float >= ${len(base_params) + 1}",
        [rush_cutoff], limit,
    )
    too_soon = await _fetch(
        f"AND (e.properties->>'departure_ts')::float >= ${len(base_params) + 1} "
        f"AND (e.properties->>'departure_ts')::float < ${len(base_params) + 2}",
        [now_ts, rush_cutoff], 3,
    )

    nodes = [
        {"type": r["type"], "key": r["key"], "label": r["label"],
         "summary": f"{station_key} {r['time']} 경유 — {r['summary']}",
         "score": None, "neighbors": []}
        for r in recommended
    ] + [
        {"type": r["type"], "key": r["key"], "label": r["label"],
         "summary": f"{station_key} {r['time']} 경유 — {r['summary']} [30분 이내 출발이라 촉박함]",
         "score": None, "neighbors": []}
        for r in too_soon
    ]
    return nodes, {"recommended": recommended, "too_soon": too_soon}


def _schedule_table(rows: list[dict]) -> str:
    lines = ["출발시각 | 기차이름 | 일련번호", "-------- | -------- | --------"]
    for r in rows[:8]:
        lines.append(f"{r['time']} | {r['name']} | {r['number']}")
    return "\n".join(lines)


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


async def retrieve(pool: asyncpg.Pool, question: str, top_k: int = TOP_K,
                   history: list[dict] | None = None) -> tuple[list[dict], dict | None]:
    search_text = _context_text(question, history)
    schedule: dict | None = None
    async with pool.acquire() as conn:
        results: list[dict] = []
        station = await _match_station(conn, search_text)
        if station is not None:
            station_id, station_key = station
            day_start, day_end = _day_window(search_text)
            station_nodes, schedule = await _station_trips(conn, station_id, station_key,
                                                           day_start, day_end)
            results.extend(station_nodes)

        # Exact-match graph traversal above needs no embedding call; vector
        # search is best-effort on top of it, so a missing/quota-exhausted
        # Gemini key degrades to traversal-only instead of failing outright.
        try:
            [vector] = await embed.embed_texts([search_text])
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
    return results, schedule


def _format_context(nodes: list[dict]) -> str:
    lines = []
    for n in nodes:
        lines.append(f"- [{n['type']}] {n['summary']}")
        for nb in n["neighbors"][:6]:
            arrow = "->" if nb["dir"] == "out" else "<-"
            lines.append(f"    {arrow} {nb['relation']} {arrow} [{nb['type']}] {nb['label']}")
    return "\n".join(lines)


GREETING = "구구~ 비둘기 역장입니다! 🕊️"


async def answer(pool: asyncpg.Pool, question: str, generate: bool = True,
                 history: list[dict] | None = None) -> dict:
    nodes, schedule = await retrieve(pool, question, history=history)
    result: dict = {"question": question, "nodes": nodes, "answer": None}
    if not nodes:
        result["answer"] = f"{GREETING}\n\n관련 정보를 찾지 못했습니다."
        return result
    if not generate:
        return result
    context = _format_context(nodes)
    now_str = datetime.now(config.KST).strftime("%H:%M")
    table = _schedule_table(schedule["recommended"]) if schedule and schedule["recommended"] else None

    # The greeting and the schedule table (when there is one) are built
    # deterministically in Python -- asking the model to reproduce a table
    # from prose facts is exactly the kind of formatting it drifts on, so it
    # only ever has to write the short commentary underneath.
    if table:
        shape_instructions = (
            "사용자에게는 이미 '출발시각 | 기차이름 | 일련번호' 표가 보여진 상태다. 표를 다시 "
            "나열하지 말고, 표에 대한 짧은 코멘트만 써라.\n"
            f"지금으로부터 {RUSH_WINDOW_S // 60}분 이내에 출발하는 열차는 타기 너무 촉박해서 "
            "표에서 뺐다. [사실] 중 '[30분 이내 출발이라 촉박함]' 표시가 붙은 열차가 있으면 그건 "
            "지금 타기엔 너무 촉박하다고 한 문장으로 짧게 언급하라 (없으면 언급하지 마라).\n"
        )
    else:
        shape_instructions = "[사실]에서 언급하는 열차명과 시각은 반드시 문장에 포함하라.\n"
    prompt = (
        "너는 철도 운행 그래프에서 검색한 사실을 바탕으로 답하는 역무원 챗봇 '비둘기 역장님'이다. "
        "인사말은 이미 별도로 표시되어 있으니 너는 본문만 작성한다 ('구구~...' 같은 인사말을 "
        "다시 쓰지 마라).\n"
        f"지금 시각은 {now_str}이다.\n"
        + shape_instructions +
        "[사실]에 있는 내용만 근거로 답하고, 근거가 부족하면 모른다고 답하라. 열차가 8건을 넘으면 "
        "전부 나열하지 말고, 상행/하행, 출발 시간대처럼 사용자가 좁혀서 다시 물어볼 수 있는 구체적인 "
        "질문을 한국어로 짧게 덧붙여라. 문장 1~3개로 간결하게 쓰고, 본문 중간에 상황에 맞는 "
        "이모티콘을 1~2개 자연스럽게 섞어라 (예: 🚄 🚉 ⏰). 과하게 남발하지는 마라. "
        "[이전 대화]가 있으면 그 맥락을 이어서 답하라 (예: '상행이요' 같은 짧은 후속 질문).\n\n"
        f"[이전 대화]\n{_history_text(history)}\n\n[사실]\n{context}\n\n[질문]\n{question}"
    )
    try:
        commentary = (await embed.generate_text(prompt)).strip()
        parts = [GREETING]
        if table:
            parts.append(table)
        parts.append(commentary)
        result["answer"] = "\n\n".join(parts)
    except Exception as exc:  # noqa: BLE001 - degrade to retrieval-only
        result["answer"] = None
        result["generation_error"] = str(exc)
    return result
