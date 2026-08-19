# -*- coding: utf-8 -*-
"""Gemini embeddings for graph-node summaries.

The SDK is synchronous, so calls run in a thread; nothing here is on any
per-request hot path (embedding happens once per node, in the graph builder),
so that's a fine trade for not pulling in a second async HTTP client.
"""
from __future__ import annotations

from google import genai
from google.genai import types

from . import config

_client: genai.Client | None = None


def _client_or_raise() -> genai.Client:
    global _client
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """Batch embed. Gemini caps batches at 100 texts, so this chunks internally."""
    client = _client_or_raise()
    out: list[list[float]] = []
    for i in range(0, len(texts), 100):
        chunk = texts[i:i + 100]
        resp = client.models.embed_content(
            model=config.GEMINI_EMBEDDING_MODEL, contents=chunk,
            config=types.EmbedContentConfig(output_dimensionality=config.EMBEDDING_DIM),
        )
        out.extend(e.values for e in resp.embeddings)
    return out


async def embed_texts(texts: list[str]) -> list[list[float]]:
    import asyncio
    return await asyncio.to_thread(embed_texts_sync, texts)


def generate_text_sync(prompt: str) -> str:
    client = _client_or_raise()
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    return resp.text or ""


async def generate_text(prompt: str) -> str:
    import asyncio
    return await asyncio.to_thread(generate_text_sync, prompt)
