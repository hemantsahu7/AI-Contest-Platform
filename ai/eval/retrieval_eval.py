"""Offline retrieval comparison over the learning-material corpus: BM25 vs BM25+rerank vs vector-only vs the
production hybrid path (BM25 + pgvector + RRF + freshness rerank, i.e. `assistant.hybrid_search`).

Run inside the ai container (needs the pgvector database):
    python -m eval.retrieval_eval             # default: cached Gemini embeddings, ZERO Gemini calls
    python -m eval.retrieval_eval --refresh   # re-embed docs + queries with Gemini: exactly 2 batched embedding requests

Embeddings in `embeddings_cache.json` are real `gemini-embedding-001` vectors captured once with --refresh, keyed by the
sha256 of the embedded text. The dataset (`queries.json`) was written before any results were computed.
The hybrid arm exercises the real production code path; only the embedding *network call* is served from the cache.
A scratch table is used so the production index is never touched."""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUERIES = json.loads((HERE / "queries.json").read_text(encoding="utf-8"))
CACHE = HERE / "embeddings_cache.json"
SCRATCH_TABLE = "ai_eval_material_embeddings"
KS = (1, 3)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def metrics(ranked: dict[str, list[str]]) -> dict:
    """ranked: query id -> ranked doc ids."""
    out = {}
    for group in ("all", "keyword", "paraphrase"):
        qs = [q for q in QUERIES if group == "all" or q["type"] == group]
        m = {"n": len(qs)}
        for k in KS:
            m[f"hit@{k}"] = sum(1 for q in qs if set(ranked[q["id"]][:k]) & set(q["relevant"]))
        m["mrr"] = round(sum(next((1 / (i + 1) for i, d in enumerate(ranked[q["id"]]) if d in q["relevant"]), 0.0) for q in qs) / len(qs), 3)
        out[group] = m
    return out


async def refresh(docs) -> None:
    from app import gemini, vectors

    if not gemini.configured():
        sys.exit("--refresh needs GEMINI_API_KEY and AI_MODEL_DISABLED unset (it makes 2 batched embedding requests, no generation calls).")
    doc_texts = [vectors._doc_text(d) for d in docs]
    q_texts = [q["query"] for q in QUERIES]
    dvec = await gemini.embed(doc_texts, "RETRIEVAL_DOCUMENT", vectors.DIMS)
    qvec = await gemini.embed(q_texts, "RETRIEVAL_QUERY", vectors.DIMS)
    payload = {
        "model": gemini.EMBED_MODEL, "dims": vectors.DIMS, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "documents": {sha(t): [round(x, 6) for x in v] for t, v in zip(doc_texts, dvec)},
        "queries": {sha("Q:" + t): [round(x, 6) for x in v] for t, v in zip(q_texts, qvec)},
    }
    CACHE.write_text(json.dumps(payload), encoding="utf-8")
    print(f"cache written: {len(dvec)} document + {len(qvec)} query embeddings ({gemini.EMBED_MODEL}, {vectors.DIMS}d)")


async def run() -> dict:
    os.environ.pop("AI_MODEL_DISABLED", None)
    os.environ["GEMINI_API_KEY"] = "cached-embeddings-no-network"  # only flips configured(); no request is ever made
    from app import assistant, gemini, vectors
    from app.retrieval import STOP, tokenize

    if not CACHE.exists():
        sys.exit("eval/embeddings_cache.json missing: run once with --refresh (2 batched embedding requests).")
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    if cache["model"] != gemini.EMBED_MODEL or cache["dims"] != vectors.DIMS:
        sys.exit(f"cache was built with {cache['model']}/{cache['dims']}d, current is {gemini.EMBED_MODEL}/{vectors.DIMS}d: run --refresh")

    async def cached_embed(texts, task, dims=768):
        table = cache["documents"] if task == "RETRIEVAL_DOCUMENT" else cache["queries"]
        out = []
        for t in texts:
            key = sha(t if task == "RETRIEVAL_DOCUMENT" else "Q:" + t)
            if key not in table:
                sys.exit(f"cache miss for {task} text {t[:50]!r}: corpus or queries changed, run --refresh")
            out.append(table[key])
        return out

    gemini.embed = cached_embed  # the ONLY thing replaced: the embedding network call
    vectors.TABLE = SCRATCH_TABLE
    vectors.reset_state()
    async with await vectors._connect() as c:
        await c.execute(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")

    index = assistant.INDEX
    ranked: dict[str, dict[str, list[str]]] = {"bm25_raw": {}, "bm25_rerank": {}, "vector_only": {}, "hybrid": {}}
    modes = set()
    for q in QUERIES:
        text = q["query"]
        toks = [t for t in tokenize(text) if t not in STOP]
        scored = sorted(((index.bm25(toks, d), d.id) for d in index.docs), reverse=True)
        ranked["bm25_raw"][q["id"]] = [i for s, i in scored if s > 0]
        ranked["bm25_rerank"][q["id"]] = [d.id for d, _ in index.search(text, top_k=8)]
        await vectors.ensure_indexed(index.docs)
        qv = (await gemini.embed([text], "RETRIEVAL_QUERY"))[0]
        ranked["vector_only"][q["id"]] = [i for i, _ in await vectors.vector_search(qv, 8)]
        hits, mode = await assistant.hybrid_search(text, None, top_k=8)
        modes.add(mode)
        ranked["hybrid"][q["id"]] = [d.id for d, _ in hits]
    async with await vectors._connect() as c:
        await c.execute(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")

    labels = {"bm25_raw": "BM25 (raw scores)", "bm25_rerank": "BM25 + heuristic rerank (fallback path)", "vector_only": "Vector only (pgvector cosine)", "hybrid": "Hybrid: BM25 + pgvector + RRF + rerank (production)"}
    result = {
        "corpus_docs": len(index.docs), "queries": len(QUERIES), "embedding_model": cache["model"], "embeddings_captured_utc": cache["created_utc"],
        "hybrid_modes_seen": sorted(modes), "arms": {a: {"label": labels[a], "metrics": metrics(r)} for a, r in ranked.items()},
        "per_query": [{"id": q["id"], "type": q["type"], "query": q["query"], "relevant": q["relevant"], **{a: ranked[a][q["id"]][:3] for a in ranked}} for q in QUERIES],
    }
    return result


def report(res: dict) -> None:
    print(f"Corpus: {res['corpus_docs']} notes | Queries: {res['queries']} | embeddings: {res['embedding_model']} (captured {res['embeddings_captured_utc']})")
    print(f"Hybrid mode seen: {res['hybrid_modes_seen']}\n")
    print(f"{'arm':58} {'group':11} {'n':>2} {'Hit@1':>6} {'Hit@3':>6} {'MRR':>6}")
    for a, v in res["arms"].items():
        for g in ("all", "keyword", "paraphrase"):
            m = v["metrics"][g]
            print(f"{v['label']:58} {g:11} {m['n']:>2} {m['hit@1']:>3}/{m['n']:<2} {m['hit@3']:>3}/{m['n']:<2} {m['mrr']:>6}")
    print("\nPer query (top-3 ids; * = relevant):")
    for p in res["per_query"]:
        def fmt(ids):
            return ",".join(("*" if i in p["relevant"] else "") + i for i in ids)
        print(f" {p['id']} [{p['type'][:4]}] {p['query'][:52]!r}\n     bm25={fmt(p['bm25_raw'])} | hybrid={fmt(p['hybrid'])} | vector={fmt(p['vector_only'])}")
    print("RESULT_JSON:" + json.dumps(res))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-embed corpus + queries with Gemini (2 batched embedding requests)")
    args = ap.parse_args()
    if args.refresh:
        from app import assistant

        asyncio.run(refresh(assistant.INDEX.docs))
    report(asyncio.run(run()))
