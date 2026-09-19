"""Vector retrieval over PUBLIC learning material only, stored in the existing PostgreSQL via pgvector.
Embeddings come from Gemini. No user, submission or hidden-test data is ever embedded or stored here, so
there is nothing permission-scoped in this index. If the DB, pgvector or Gemini embeddings are unavailable,
retrieval degrades to BM25 and the response says so."""
import asyncio
import hashlib
import logging
import os
import time
from typing import Awaitable, Callable

from . import gemini
from .retrieval import Material

log = logging.getLogger("ai.vectors")

DATABASE_URL = os.getenv("DATABASE_URL", "")
DIMS = 768
TABLE = "ai_material_embeddings"  # module constant (never user input); the offline retrieval eval points it at a scratch table
Embedder = Callable[[list[str], str], Awaitable[list[list[float]]]]

_lock = asyncio.Lock()
_state = {"indexed": False, "last_error": None, "retry_at": 0.0}


def enabled() -> bool:
    return bool(DATABASE_URL)


def _lit(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def _doc_text(m: Material) -> str:
    return f"{m.title}\n{' '.join(m.tags)}\n{m.body}"


async def _connect():
    import psycopg

    return await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)


async def _default_embed(texts: list[str], task: str) -> list[list[float]]:
    return await gemini.embed(texts, task, DIMS)


async def index_materials(docs: list[Material], embed: Embedder = _default_embed) -> int:
    """Idempotent: only new/changed documents (by content hash + model) are embedded. Returns #embedded."""
    async with await _connect() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            id text PRIMARY KEY, content_hash text NOT NULL, model text NOT NULL,
            embedding vector({DIMS}) NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())""")
        cur = await conn.execute(f"SELECT id, content_hash, model FROM {TABLE}")
        have = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
        todo = []
        for d in docs:
            h = hashlib.sha256(_doc_text(d).encode()).hexdigest()
            if have.get(d.id) != (h, gemini.EMBED_MODEL):
                todo.append((d, h))
        if todo:
            vecs = await embed([_doc_text(d) for d, _ in todo], "RETRIEVAL_DOCUMENT")
            for (d, h), v in zip(todo, vecs):
                await conn.execute(
                    f"""INSERT INTO {TABLE} (id, content_hash, model, embedding) VALUES (%s, %s, %s, %s::vector)
                       ON CONFLICT (id) DO UPDATE SET content_hash = EXCLUDED.content_hash, model = EXCLUDED.model,
                       embedding = EXCLUDED.embedding, updated_at = now()""",
                    (d.id, h, gemini.EMBED_MODEL, _lit(v)),
                )
        # drop embeddings of deleted material so stale documents cannot be retrieved
        await conn.execute(f"DELETE FROM {TABLE} WHERE NOT (id = ANY(%s))", ([d.id for d in docs],))
        return len(todo)


async def vector_search(query_vec: list[float], top_k: int = 5) -> list[tuple[str, float]]:
    async with await _connect() as conn:
        cur = await conn.execute(
            f"SELECT id, 1 - (embedding <=> %s::vector) AS sim FROM {TABLE} ORDER BY embedding <=> %s::vector LIMIT %s",
            (_lit(query_vec), _lit(query_vec), top_k),
        )
        return [(r[0], float(r[1])) for r in await cur.fetchall()]


async def ensure_indexed(docs: list[Material], embed: Embedder = _default_embed) -> None:
    if _state["indexed"]:
        return
    if time.time() < _state["retry_at"]:
        raise RuntimeError(_state["last_error"] or "vector index unavailable")
    async with _lock:
        if _state["indexed"]:
            return
        try:
            n = await index_materials(docs, embed)
            _state.update(indexed=True, last_error=None)
            log.info("vector index ready (embedded %d new/changed documents)", n)
        except Exception as exc:
            _state.update(last_error=f"{type(exc).__name__}", retry_at=time.time() + 60)
            raise


def reset_state() -> None:
    _state.update(indexed=False, last_error=None, retry_at=0.0)


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion of several ranked id lists."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
