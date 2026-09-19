"""pgvector storage/retrieval mechanics against a REAL Postgres+pgvector (skipped when DATABASE_URL is unreachable).
Embeddings here come from a deterministic synthetic embedder (word -> concept dimension), so these tests prove the
storage, cosine search, idempotent indexing, cleanup and BM25+vector fusion - NOT the semantic quality of Gemini embeddings."""
import asyncio
import hashlib
import os

import pytest

from app import assistant, gemini, vectors
from app.retrieval import Material

DB = os.getenv("DATABASE_URL", "")


def _reachable() -> bool:
    if not DB:
        return False
    try:
        import psycopg

        psycopg.connect(DB, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="no reachable DATABASE_URL with pgvector")

CONCEPTS = {"overflow": 0, "gigantic": 0, "wrap": 0, "syntax": 1, "compile": 1, "loop": 2, "infinite": 2}


def _vec(text: str) -> list[float]:
    v = [0.0] * vectors.DIMS
    for w in text.lower().replace("\n", " ").split():
        w = w.strip(".,;:()`'\"")
        idx = CONCEPTS.get(w, 3 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (vectors.DIMS - 3))
        v[idx] += 1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


CALLS: list[list[str]] = []


async def fake_embed(texts, task, dims=768):
    CALLS.append(texts)
    return [_vec(t) for t in texts]


def mat(i, title, body, status="current"):
    return Material(id=i, title=title, tags=[], updated="2026-01-01", status=status, superseded_by=None, body=body)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(vectors, "DATABASE_URL", DB)
    vectors.reset_state()
    CALLS.clear()

    async def wipe():
        async with await vectors._connect() as c:
            await c.execute("DROP TABLE IF EXISTS ai_material_embeddings")

    asyncio.run(wipe())


def test_index_is_idempotent_and_reembeds_only_changes():
    docs = [mat("a", "Overflow", "wrap around gigantic values"), mat("b", "Compile", "syntax compile errors")]
    assert asyncio.run(vectors.index_materials(docs, fake_embed)) == 2
    assert asyncio.run(vectors.index_materials(docs, fake_embed)) == 0  # unchanged -> no embedding calls
    docs[1] = mat("b", "Compile", "syntax compile errors and missing semicolon")
    assert asyncio.run(vectors.index_materials(docs, fake_embed)) == 1
    assert asyncio.run(vectors.index_materials(docs[:1], fake_embed)) == 0  # deleted doc is removed from the index
    assert [i for i, _ in asyncio.run(vectors.vector_search(_vec("compile syntax"), 5))] == ["a"]


def test_cosine_search_orders_by_similarity():
    docs = [mat("a", "Overflow", "wrap gigantic overflow"), mat("b", "Loops", "infinite loop"), mat("c", "Compile", "syntax compile")]
    asyncio.run(vectors.index_materials(docs, fake_embed))
    hits = asyncio.run(vectors.vector_search(_vec("gigantic overflow wrap"), 3))
    assert hits[0][0] == "a" and hits[0][1] > 0.9 and hits[0][1] > hits[-1][1]


def test_hybrid_finds_document_that_bm25_alone_misses(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")
    monkeypatch.setattr(gemini, "embed", fake_embed)
    q = "gigantic"  # appears in no material, so BM25 alone cannot match anything
    assert assistant.INDEX.search(q) == []
    hits, mode = asyncio.run(assistant.hybrid_search(q))
    assert mode.startswith("hybrid (bm25 + pgvector")
    assert hits and hits[0][0].id == "integer-overflow"


def test_hybrid_degrades_to_bm25_with_reason_when_embeddings_fail(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")

    async def broken(*a, **k):
        raise gemini.LLMUnavailable("rate_limited", "429")

    monkeypatch.setattr(gemini, "embed", broken)
    hits, mode = asyncio.run(assistant.hybrid_search("integer overflow"))
    assert mode == "bm25 (vector unavailable: rate_limited)" and hits


def test_no_key_means_bm25_with_explicit_reason(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    hits, mode = asyncio.run(assistant.hybrid_search("integer overflow"))
    assert "no GEMINI_API_KEY" in mode and hits
