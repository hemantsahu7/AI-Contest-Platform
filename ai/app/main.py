import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import assistant, db, gemini, graph_store, graph_sync, knowledge
from .backend import Backend, NotAccessible, Unauthorized

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai")

RATE_LIMIT = int(os.getenv("AI_RATE_LIMIT_PER_HOUR", "30"))
_calls: dict[str, deque] = defaultdict(deque)

@asynccontextmanager
async def lifespan(_app):
    """The graph projection runs in the background: PostgreSQL/Neo4j still starting (or down) never blocks or crashes the service."""
    task = asyncio.create_task(graph_sync.run_forever())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await graph_store.close()


app = FastAPI(title="Shodh-a-Code AI assistant", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGIN", "*").split(","), allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    contestId: str
    problemId: str | None = None
    submissionId: str | None = None


def _check_rate(user_id: str) -> None:
    now = time.time()
    q = _calls[user_id]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, f"AI limit reached ({RATE_LIMIT}/hour). The contest itself is unaffected; try again later.")
    q.append(now)


@app.get("/ai/health")
def health():
    g = graph_sync.STATE
    return {"status": "ok", "provider": "gemini", "model_configured": gemini.configured(), "model": gemini.MODEL if gemini.configured() else None,
            "graph": {"configured": graph_store.configured(), "status": g["status"], "lastSync": g.get("last_sync")}}


async def _admin(authorization: str | None) -> dict:
    """System administrators only: these endpoints change shared knowledge, never contest data."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    be = Backend(authorization.split(" ", 1)[1])
    try:
        me = await be.get("/auth/me")
    except Unauthorized:
        raise HTTPException(401, "Invalid or expired token")
    finally:
        await be.close()
    if me["role"] != "ADMIN":
        raise HTTPException(403, "Administrator role required")
    return me


@app.get("/ai/graph/status")
async def graph_status(authorization: str | None = Header(default=None)):
    await _admin(authorization)
    out = {"configured": graph_store.configured(), "reachable": await graph_store.ping(), **graph_sync.STATE}
    if out["reachable"]:
        try:
            out["nodes"] = {r["label"]: r["n"] for r in await graph_store.read("counts")}
            out["relationships"] = {r["type"]: r["n"] for r in await graph_store.read("rel_counts")}
        except graph_store.GraphUnavailable:
            pass
    return out


@app.post("/ai/graph/rebuild")
async def graph_rebuild(authorization: str | None = Header(default=None)):
    """Re-ingest the authored knowledge into PostgreSQL and rebuild the Neo4j projection from PostgreSQL (removes stale elements)."""
    await _admin(authorization)
    if not (db.configured() and graph_store.configured()):
        raise HTTPException(503, "PostgreSQL and Neo4j must both be configured")
    try:
        ingested = await knowledge.ingest_files()
        await assistant.reload_index()
        counts = await asyncio.wait_for(graph_sync.project(full=True, prune=True), timeout=60)
    except Exception as exc:
        log.warning("graph rebuild failed: %s", type(exc).__name__)
        raise HTTPException(502, f"Graph rebuild failed ({type(exc).__name__}); PostgreSQL data is unaffected.")
    return {"knowledge": ingested, "graph": counts}


class MaterialRequest(BaseModel):
    id: str
    title: str
    body: str
    tags: list[str] = []
    concepts: list[str] = []
    status: str = "current"
    updated: str = "unknown"
    superseded_by: str | None = None


@app.post("/ai/knowledge/materials")
async def add_material(req: MaterialRequest, authorization: str | None = Header(default=None)):
    """Add or update a learning material at runtime (stored in PostgreSQL, re-indexed for BM25/pgvector, projected into Neo4j)."""
    await _admin(authorization)
    if not db.configured():
        raise HTTPException(503, "PostgreSQL is not configured")
    conn = await db.connect()
    try:
        await knowledge.ensure_schema(conn)
        try:
            res = await knowledge.upsert_material(conn, req.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    finally:
        await conn.close()
    await assistant.reload_index()
    projected = await graph_sync.freshen(max_age_s=0, light=False)
    return {**res, "graphProjected": projected}


@app.delete("/ai/knowledge/materials/{material_id}")
async def delete_material(material_id: str, authorization: str | None = Header(default=None)):
    """Remove a runtime-added material (authored notes cannot be deleted here). Rebuilds the graph projection so the node goes too."""
    await _admin(authorization)
    if not db.configured():
        raise HTTPException(503, "PostgreSQL is not configured")
    conn = await db.connect()
    try:
        await knowledge.ensure_schema(conn)
        deleted = await knowledge.delete_material(conn, material_id)
    finally:
        await conn.close()
    if not deleted:
        raise HTTPException(404, "No runtime-added material with that id")
    await assistant.reload_index()
    projected = False
    if graph_store.configured():
        try:
            await asyncio.wait_for(graph_sync.project(full=True, prune=True), timeout=60)
            projected = True
        except Exception as exc:
            log.warning("graph prune after delete failed: %s", type(exc).__name__)
    return {"deleted": material_id, "graphProjected": projected}


class AliasRequest(BaseModel):
    kind: str = Field(pattern="^(problem|concept|material)$")
    entityId: str = Field(min_length=1, max_length=80)
    alias: str = Field(min_length=1, max_length=120)


@app.post("/ai/knowledge/aliases")
async def add_alias(req: AliasRequest, authorization: str | None = Header(default=None)):
    """Record an alternative or former name for a problem/concept/material so entity resolution and the graph know it."""
    await _admin(authorization)
    if not db.configured():
        raise HTTPException(503, "PostgreSQL is not configured")
    conn = await db.connect()
    try:
        await knowledge.ensure_schema(conn)
        await knowledge.add_alias(conn, req.kind, req.entityId, req.alias, "db")
    finally:
        await conn.close()
    return {"ok": True, "graphProjected": await graph_sync.freshen(max_age_s=0, light=False)}


@app.get("/ai/usage")
def usage():
    return {**assistant.USAGE, "rate_limit_per_user_per_hour": RATE_LIMIT, "timeout_s": gemini.TIMEOUT_S}


@app.post("/ai/ask")
async def ask(req: AskRequest, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    request_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    be = Backend(authorization.split(" ", 1)[1])
    try:
        me = await be.get("/auth/me")
        t_gather = time.perf_counter()
        ctx = await assistant.gather(req.question, req.contestId, req.problemId, req.submissionId, be, me)
        if not ctx.refusal:  # policy refusals cost no model call, so they are not rate limited
            _check_rate(me["id"])
        t_answer = time.perf_counter()
        result = await assistant.answer(ctx)
    except Unauthorized:
        raise HTTPException(401, "Invalid or expired token")
    except NotAccessible:
        raise HTTPException(403, "You do not have access to that contest")
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("req=%s failed", request_id)
        raise HTTPException(502, f"Could not gather evidence ({type(exc).__name__}). The contest is unaffected.")
    finally:
        await be.close()
    total_ms = int((time.perf_counter() - t0) * 1000)
    if ctx.agent and ctx.agent.steps:
        log.info("req=%s agent planner=%s stop=%s steps=%s", request_id, ctx.agent.planner_name, ctx.agent.stop_reason, [(s.tool, s.status, s.ms) for s in ctx.agent.steps])
    log.info(
        "req=%s user=%s mode=%s contest=%s problem=%s source=%s confidence=%s refusal=%s retrieval=%r evidence=%s gather_ms=%d answer_ms=%d total_ms=%d",
        request_id, me["username"], ctx.mode, req.contestId, ctx.problem["id"] if ctx.problem else None,
        result["source"], result["confidence"], result.get("refusal"), ctx.retrieval,
        [e["id"] for e in result["evidence"]], int((t_answer - t_gather) * 1000), int((time.perf_counter() - t_answer) * 1000), total_ms,
    )
    return {**result, **({"retrieval": ctx.retrieval, "agent": ctx.agent.public()} if not ctx.refusal and ctx.agent else {}), "requestId": request_id, "mode": ctx.mode, "policy": ctx.policy, "timingMs": total_ms}
