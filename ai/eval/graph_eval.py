"""GraphRAG evaluation: the same labelled questions through the real answer pipeline, once as the BASELINE (text retrieval only:
BM25 [+ pgvector], agent tools and Neo4j switched off, i.e. how the assistant worked before the graph) and once as GRAPHRAG
(entity resolution + Neo4j traversal + fused retrieval + judge history). Reports pass rates per category and the failures.

Run inside the ai container against the running stack (backend, PostgreSQL, Neo4j must be up and seeded):
    python -m eval.graph_eval [--json out.json]

The model is never called: gather() and the deterministic evidence-only answer are exercised, so the numbers measure what evidence
the graph adds, not how well Gemini phrases it. Expectations live in graph_queries.json (written before any result was computed).
Nothing here is tuned to pass: questions the system misses are reported as misses."""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

os.environ["AI_MODEL_DISABLED"] = "1"  # gather + evidence-only answers; no Gemini call is ever made

from app import assistant, graph_store  # noqa: E402
from app.backend import Backend  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "graph_queries.json").read_text(encoding="utf-8"))
CONTEST = DATA["seed"]["contest"]
BACKEND = os.getenv("BACKEND_URL", "http://backend:3000")
PASSWORD = "Password123!"


async def login(client: httpx.AsyncClient, email: str) -> str:
    r = await client.post(f"{BACKEND}/auth/login", json={"email": email, "password": PASSWORD})
    r.raise_for_status()
    return r.json()["accessToken"]


def blob(result: dict) -> str:
    parts = [result["answer"]] + [c["text"] for c in result["claims"]] + [f"{e['title']} {e['text']}" for e in result["evidence"]] + result["missing"]
    return " ".join(parts).lower()


def judge(q: dict, result: dict) -> tuple[bool, str]:
    exp = q["expect"]
    if "refusal" in exp:
        ok = result.get("refusal") == exp["refusal"]
        return ok, "" if ok else f"refusal={result.get('refusal')}"
    if exp.get("unanswerable"):
        insufficient = "enough evidence" in result["answer"].lower() or result["needs_clarification"]
        ok = result["confidence"] == "low" and not any(c["kind"] == "observation" for c in result["claims"]) and insufficient
        return ok, "" if ok else f"confidence={result['confidence']} claims={len(result['claims'])}"
    b = blob(result)
    missing = [s for s in exp.get("all", []) if s.lower() not in b]
    present = [s for s in exp.get("none", []) if s.lower() in b]
    return (not missing and not present), ("missing: " + ", ".join(missing) if missing else "") + ("forbidden present: " + ", ".join(present) if present else "")


async def run_mode(mode: str, tokens: dict) -> dict[str, dict]:
    """mode 'baseline' switches the graph store and the agent off in this process only (the running service is untouched)."""
    saved = {k: os.environ.get(k) for k in ("NEO4J_URI", "AI_AGENT_ENABLED")}
    if mode == "baseline":
        os.environ.pop("NEO4J_URI", None)
        os.environ["AI_AGENT_ENABLED"] = "0"
    out = {}
    try:
        for q in DATA["questions"]:
            be = Backend(tokens[q["user"]])
            t0 = time.perf_counter()
            try:
                me = await be.get("/auth/me")
                ctx = await assistant.gather(q["question"], CONTEST, q.get("problemId"), q.get("submissionId"), be, me)
                result = await assistant.answer(ctx)
            finally:
                await be.close()
            ok, why = judge(q, result)
            steps = [s.tool for s in ctx.agent.steps] if ctx.agent else []
            out[q["id"]] = {"pass": ok, "why": why, "ms": int((time.perf_counter() - t0) * 1000), "steps": steps,
                            "graph_evidence": sum(1 for e in ctx.evidence if e.kind in ("graph", "judge-history")),
                            "graph_found_material": sum(1 for e in ctx.evidence if e.kind == "material" and e.via and "knowledge graph" in e.via)}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return out


async def main(json_path: str | None) -> int:
    if not graph_store.configured() or not await graph_store.ping(5):
        print("Neo4j is not reachable: start the stack first (docker compose up -d)", file=sys.stderr)
        return 2
    counts = {r["label"]: r["n"] for r in await graph_store.read("counts")}
    if not counts.get("Submission"):
        print("The graph is empty: wait for the first projection (docker compose logs ai) or run the rebuild", file=sys.stderr)
        return 2
    async with httpx.AsyncClient(timeout=20) as client:
        tokens = {email: await login(client, email) for email in {q["user"] for q in DATA["questions"]}}
    base = await run_mode("baseline", tokens)
    graph = await run_mode("graphrag", tokens)
    cats = list(dict.fromkeys(q["category"] for q in DATA["questions"]))
    summary = {}
    print(f"{'category':<22}{'n':>3}{'baseline':>10}{'graphrag':>10}")
    for c in cats + ["all"]:
        qs = [q for q in DATA["questions"] if c == "all" or q["category"] == c]
        b, g = sum(base[q["id"]]["pass"] for q in qs), sum(graph[q["id"]]["pass"] for q in qs)
        summary[c] = {"n": len(qs), "baseline": b, "graphrag": g}
        print(f"{c:<22}{len(qs):>3}{b:>7}/{len(qs):<2}{g:>7}/{len(qs):<2}")
    print()
    for q in DATA["questions"]:
        b, g = base[q["id"]], graph[q["id"]]
        print(f"{q['id']:<4}{'base ' + ('PASS' if b['pass'] else 'FAIL'):<11}{'graph ' + ('PASS' if g['pass'] else 'FAIL'):<12}{q['question'][:62]}")
        if not g["pass"]:
            print(f"      graphrag miss: {g['why']}")
    only_graph = [q["id"] for q in DATA["questions"] if graph[q["id"]]["pass"] and not base[q["id"]]["pass"]]
    regress = [q["id"] for q in DATA["questions"] if base[q["id"]]["pass"] and not graph[q["id"]]["pass"]]
    lat = lambda r: round(statistics.median(v["ms"] for v in r.values()))  # noqa: E731
    print(f"\nanswered only with the graph: {only_graph}\nregressions (baseline passes, graphrag fails): {regress}")
    print(f"median latency (gather + evidence-only answer): baseline {lat(base)} ms, graphrag {lat(graph)} ms; graph counts {counts}")
    result = {"summary": summary, "only_graphrag": only_graph, "regressions": regress, "baseline": base, "graphrag": graph,
              "median_ms": {"baseline": lat(base), "graphrag": lat(graph)}, "graph_nodes": counts}
    print("RESULT_JSON:" + json.dumps(result))
    if json_path:
        Path(json_path).write_text(json.dumps(result, indent=1), encoding="utf-8")
    await graph_store.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    sys.exit(asyncio.run(main(ap.parse_args().json)))
