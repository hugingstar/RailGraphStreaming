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

import asyncpg

from . import embed

TOP_K = 8


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
    [vector] = await embed.embed_texts([question])
    vec_str = "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
    async with pool.acquire() as conn:
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
        results = []
        for r in rows:
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
