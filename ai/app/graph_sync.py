"""Project PostgreSQL into the Neo4j graph. PostgreSQL is the source of truth; the graph is a derived, rebuildable index.

    python -m app.graph_sync rebuild      # full re-projection + prune of nodes/edges that no longer exist in PostgreSQL
    python -m app.graph_sync sync         # incremental (what the background loop does)
    python -m app.graph_sync status

Only whitelisted columns are read. NOT projected: source code (only md5("sourceCode") as an equality hash, computed inside
PostgreSQL), test cases, password hashes, judge stdout/stderr. All writes are MERGE (idempotent, no duplicate nodes/edges).
Event tables (submissions, incidents) sync incrementally by updatedAt/createdAt; the small reference tables are re-merged each
cycle. Deletions in PostgreSQL are only reflected by `rebuild` (prune)."""
import asyncio
import datetime as dt
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher

from . import db, graph_store, knowledge
from .problem_analysis import INT32_MAX, statement_bound, worst_case
from .retrieval import tokenize

log = logging.getLogger("ai.graph")

LABELS = ["Organization", "User", "Contest", "Problem", "Concept", "LearningMaterial", "Submission", "JudgeVersion", "JudgeIncident"]
STATE: dict = {"status": "starting", "last_sync": None, "last_error": None, "last_counts": {}, "syncs": 0}
_lock = asyncio.Lock()
_watermark: dt.datetime | None = None


def iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:  # Prisma stores UTC without a zone
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(v)


UPSERT = {
    "Organization": "UNWIND $rows AS r MERGE (n:Organization {id: r.id}) SET n.name = r.name, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "User": "UNWIND $rows AS r MERGE (n:User {id: r.id}) SET n.username = r.username, n.role = r.role, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "Contest": "UNWIND $rows AS r MERGE (n:Contest {id: r.id}) SET n.title = r.title, n.status = r.status, n.orgId = r.orgId, n.startTime = r.startTime, n.endTime = r.endTime, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "Problem": "UNWIND $rows AS r MERGE (n:Problem {id: r.id}) SET n.title = r.title, n.titleNorm = r.titleNorm, n.difficulty = r.difficulty, n.points = r.points, n.contestId = r.contestId, n.updatedAt = r.updatedAt, n.aliases = r.aliases, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "Concept": "UNWIND $rows AS r MERGE (n:Concept {id: r.id}) SET n.name = r.name, n.description = r.description, n.aliases = r.aliases, n.version = r.version, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "LearningMaterial": "UNWIND $rows AS r MERGE (n:LearningMaterial {id: r.id}) SET n.title = r.title, n.status = r.status, n.updated = r.updated, n.version = r.version, n.aliases = r.aliases, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "Submission": "UNWIND $rows AS r MERGE (n:Submission {id: r.id}) SET n.userId = r.userId, n.verdict = r.verdict, n.status = r.status, n.score = r.score, n.language = r.language, n.submittedAt = r.submittedAt, n.completedAt = r.completedAt, n.sourceHash = r.sourceHash, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "JudgeVersion": "UNWIND $rows AS r MERGE (n:JudgeVersion {id: r.id}) SET n.version = r.version, n.description = r.description, n.createdAt = r.createdAt, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
    "JudgeIncident": "UNWIND $rows AS r MERGE (n:JudgeIncident {id: r.id}) SET n.type = r.type, n.message = r.message, n.createdAt = r.createdAt, n.resolvedAt = r.resolvedAt, n.syncAt = CASE WHEN n.syncAt IS NULL OR n.syncAt < $run THEN $run ELSE n.syncAt END",
}

_REL = "UNWIND $rows AS r MATCH (a:{A} {{id: r.a}}) MATCH (b:{B} {{id: r.b}}) MERGE (a)-[x:{T}]->(b) SET x.syncAt = CASE WHEN x.syncAt IS NULL OR x.syncAt < $run THEN $run ELSE x.syncAt END{S}"


def rel_query(a: str, b: str, t: str, props: list[str] | None = None) -> str:
    sets = "".join(f", x.{p} = r.{p}" for p in (props or []))
    return _REL.replace("{A}", a).replace("{B}", b).replace("{T}", t).replace("{S}", sets).replace("{{", "{").replace("}}", "}")


REL_QUERIES = {
    "MEMBER_OF": rel_query("User", "Organization", "MEMBER_OF", ["role"]),
    "OWNED_BY": rel_query("Contest", "Organization", "OWNED_BY"),
    "CONTAINS": rel_query("Contest", "Problem", "CONTAINS"),
    "SUBMITTED": rel_query("User", "Submission", "SUBMITTED"),
    "FOR_PROBLEM": rel_query("Submission", "Problem", "FOR_PROBLEM"),
    "IN_CONTEST": rel_query("Submission", "Contest", "IN_CONTEST"),
    "USED_FOR": rel_query("JudgeVersion", "Submission", "USED_FOR"),
    "AFFECTED": rel_query("JudgeIncident", "Submission", "AFFECTED"),
    "DURING": rel_query("JudgeIncident", "JudgeVersion", "DURING"),
    "COVERS": rel_query("LearningMaterial", "Concept", "COVERS"),
    "SUPERSEDED_BY": rel_query("LearningMaterial", "LearningMaterial", "SUPERSEDED_BY"),
    "PREREQUISITE_OF": rel_query("Concept", "Concept", "PREREQUISITE_OF"),
    "TAGGED_WITH": rel_query("Problem", "Concept", "TAGGED_WITH", ["score"]),
    "REQUIRES": rel_query("Problem", "Concept", "REQUIRES", ["reason"]),
    "SIMILAR_TO": rel_query("Problem", "Problem", "SIMILAR_TO", ["statementScore", "titleScore", "kind"]),
}


async def ensure_schema() -> None:
    for label in LABELS:
        await graph_store.write(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
    await graph_store.write("CREATE INDEX submission_hash IF NOT EXISTS FOR (n:Submission) ON (n.sourceHash)")


# ---------------- PostgreSQL readers (whitelisted columns) ----------------

async def _rows(conn, sql: str, params=()) -> list[dict]:
    cur = await conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def read_postgres(conn, since: dt.datetime | None) -> dict:
    s = {}
    s["orgs"] = await _rows(conn, 'SELECT id, name FROM "Organization"')
    s["users"] = await _rows(conn, 'SELECT id, username, role::text AS role FROM "User"')
    s["members"] = await _rows(conn, 'SELECT "userId", "organizationId", role::text AS role FROM "OrganizationMembership"')
    s["contests"] = await _rows(conn, 'SELECT id, title, status::text AS status, "organizationId", "startTime", "endTime" FROM "Contest"')
    # description is read only to DERIVE concept/similarity edges in memory; it is not stored in the graph
    s["problems"] = await _rows(conn, 'SELECT id, "contestId", title, description, "inputFormat", difficulty::text AS difficulty, points, "updatedAt" FROM "Problem"')
    s["versions"] = await _rows(conn, 'SELECT id, version, description, "createdAt" FROM "JudgeVersion"')
    sub_sql = ('SELECT s.id, s."userId", s."problemId", s."contestId", s.verdict::text AS verdict, s.status::text AS status, s.score, s.language, '
               's."submittedAt", s."completedAt", md5(s."sourceCode") AS "sourceHash", '
               '(SELECT e."judgeVersionId" FROM "JudgeExecution" e WHERE e."submissionId" = s.id ORDER BY e."createdAt" DESC LIMIT 1) AS "judgeVersionId" '
               'FROM "Submission" s')
    s["subs"] = await _rows(conn, sub_sql + (' WHERE s."updatedAt" >= %s' if since else ""), (since,) if since else ())
    inc_sql = ('SELECT i.id, i."submissionId", i.type::text AS type, i.message, i."createdAt", i."resolvedAt", '
               '(SELECT e."judgeVersionId" FROM "JudgeExecution" e WHERE e.id = i."judgeExecutionId") AS "judgeVersionId" FROM "JudgeIncident" i')
    s["incidents"] = await _rows(conn, inc_sql + (' WHERE i."createdAt" >= %s OR i."resolvedAt" >= %s' if since else ""), (since, since) if since else ())
    cur = await conn.execute("SELECT id, title, status, updated, version, concepts, superseded_by FROM ai_learning_materials")
    s["materials"] = [dict(zip(["id", "title", "status", "updated", "version", "concepts", "superseded_by"], r)) for r in await cur.fetchall()]
    s["concepts"] = await _rows(conn, "SELECT id, name, description, keywords, version FROM ai_concepts")
    s["prereqs"] = await _rows(conn, "SELECT concept_id, prereq_id FROM ai_concept_prereqs")
    s["aliases"] = await _rows(conn, "SELECT kind, entity_id, alias FROM ai_entity_aliases")
    return s


# ---------------- derivation (pure functions, unit-tested) ----------------

def _kw_hit(keyword: str, tokens: set[str], text: str) -> bool:
    k = keyword.lower()
    if all(c.isalnum() for c in k):
        return k in tokens or k + "s" in tokens
    return k in text


def _statement_norm(p: dict) -> str:
    """Statement wording with symbols kept (so A+B and |A-B| differ) and numbers masked (so only the wording is compared)."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", (p.get("description") or "").lower())).strip()


DUPLICATE_STATEMENT = 0.97   # near-identical wording
SIMILAR_STATEMENT = 0.94     # very close wording (statements of this platform share boilerplate, so anything lower flags unrelated tasks)
SIMILAR_TITLE = 0.60         # similar title (and a broadly similar statement)
GENERIC_KEYWORD_SHARE = 0.6  # a keyword present in more than 60% of >=5 problems says nothing about any one of them


def derive_problem_edges(problems: list[dict], concepts: list[dict], org_of: dict | None = None) -> dict:
    """TAGGED_WITH (keyword match on the statement), REQUIRES (constraint analysis) and SIMILAR_TO (statement/title similarity)."""
    problems = sorted(problems, key=lambda p: p["id"])  # deterministic output whatever the input order
    known = {c["id"] for c in concepts}
    info = {}
    for p in problems:
        text = f"{p['title']} {p['description']} {p.get('inputFormat') or ''}".lower()
        info[p["id"]] = (set(tokenize(text)), text)
    hits: set[tuple[str, str, str]] = set()  # (problem id, concept id, keyword)
    for p in problems:
        tokens, text = info[p["id"]]
        for c in concepts:
            for k in c.get("keywords") or []:
                if _kw_hit(k, tokens, text):
                    hits.add((p["id"], c["id"], k))
    n = len(problems)
    df: dict[tuple[str, str], int] = {}  # (concept id, keyword) -> number of problems containing it
    for _, cid, k in hits:
        df[(cid, k)] = df.get((cid, k), 0) + 1
    scores: dict[tuple[str, str], int] = {}
    for pid, cid, k in hits:
        if n >= 5 and df[(cid, k)] / n > GENERIC_KEYWORD_SHARE:
            continue
        scores[(pid, cid)] = scores.get((pid, cid), 0) + 1
    tags = [{"a": pid, "b": cid, "score": sc} for (pid, cid), sc in scores.items()]
    requires = []
    for p in problems:
        bound, op = statement_bound(p)
        if bound:
            if bound > INT32_MAX and "integer-types" in known:
                requires.append({"a": p["id"], "b": "integer-types", "reason": f"the statement allows values up to {bound:,}, beyond the 32-bit range"})
            w = worst_case(bound, op)
            if w > INT32_MAX and "integer-overflow" in known:
                requires.append({"a": p["id"], "b": "integer-overflow", "reason": f"worst-case {op} is about {w:,}, above the 32-bit maximum {INT32_MAX:,}"})
    similar = []
    ps = sorted(problems, key=lambda p: p["id"])
    for i, p in enumerate(ps):
        for q in ps[i + 1:]:
            if org_of is not None and org_of.get(p["id"]) != org_of.get(q["id"]):
                continue  # never relate problems of different organizations
            stmt = SequenceMatcher(None, _statement_norm(p), _statement_norm(q)).ratio()
            title = SequenceMatcher(None, knowledge.norm(p["title"]), knowledge.norm(q["title"])).ratio()
            if stmt >= DUPLICATE_STATEMENT:
                kind = "duplicate"
            elif stmt >= SIMILAR_STATEMENT or (title >= SIMILAR_TITLE and stmt >= 0.6):
                kind = "similar"
            else:
                continue
            similar.append({"a": p["id"], "b": q["id"], "statementScore": round(stmt, 3), "titleScore": round(title, 3), "kind": kind})
    return {"tags": tags, "requires": requires, "similar": similar}


# ---------------- projection ----------------

async def _upsert(label: str, rows: list[dict], run: str) -> int:
    for i in range(0, len(rows), 500):
        await graph_store.write(UPSERT[label], rows=rows[i:i + 500], run=run)
    return len(rows)


async def _rels(name: str, rows: list[dict], run: str) -> int:
    for i in range(0, len(rows), 500):
        await graph_store.write(REL_QUERIES[name], rows=rows[i:i + 500], run=run)
    return len(rows)


async def project(full: bool = False, prune: bool = False, light: bool = False) -> dict:
    """One projection pass. `full` re-reads every event row; `prune` (full only) removes graph elements absent from PostgreSQL.
    `light` (used right before answering a question) only merges submissions/incidents changed since the last pass and leaves the
    reference data and derived edges to the periodic pass."""
    global _watermark
    async with _lock:
        t0 = time.perf_counter()
        run = int(time.time() * 1000)  # pass start time: a newer pass never loses to an older one running concurrently (service + CLI, replicas)
        conn = await db.connect()
        try:
            await knowledge.ensure_schema(conn)
            cur = await conn.execute("SELECT now()")
            started = (await cur.fetchone())[0]
            since = None if (full or _watermark is None) else _watermark - dt.timedelta(seconds=5)
            pg = await read_postgres(conn, since)
        finally:
            await conn.close()
        light = light and since is not None
        if light:
            for k in ("orgs", "users", "members", "contests", "problems", "versions", "materials", "concepts", "prereqs", "aliases"):
                pg[k] = []
        await ensure_schema()

        alias = {}
        for a in pg["aliases"]:
            alias.setdefault((a["kind"], a["entity_id"]), []).append(a["alias"])
        org_of_contest = {c["id"]: c["organizationId"] for c in pg["contests"]}
        counts: dict = {}
        counts["Organization"] = await _upsert("Organization", [dict(id=o["id"], name=o["name"]) for o in pg["orgs"]], run)
        counts["User"] = await _upsert("User", [dict(id=u["id"], username=u["username"], role=u["role"]) for u in pg["users"]], run)
        counts["Contest"] = await _upsert("Contest", [dict(id=c["id"], title=c["title"], status=c["status"], orgId=c["organizationId"], startTime=iso(c["startTime"]), endTime=iso(c["endTime"])) for c in pg["contests"]], run)
        problems = [dict(id=p["id"], title=p["title"], titleNorm=knowledge.norm(p["title"]), difficulty=p["difficulty"], points=p["points"], contestId=p["contestId"],
                         updatedAt=iso(p["updatedAt"]), aliases=alias.get(("problem", p["id"]), [])) for p in pg["problems"]]
        counts["Problem"] = await _upsert("Problem", problems, run)
        counts["Concept"] = await _upsert("Concept", [dict(id=c["id"], name=c["name"], description=c["description"], version=c["version"], aliases=alias.get(("concept", c["id"]), [])) for c in pg["concepts"]], run)
        counts["LearningMaterial"] = await _upsert("LearningMaterial", [dict(id=m["id"], title=m["title"], status=m["status"], updated=m["updated"], version=m["version"], aliases=alias.get(("material", m["id"]), [])) for m in pg["materials"]], run)
        counts["JudgeVersion"] = await _upsert("JudgeVersion", [dict(id=v["id"], version=v["version"], description=v["description"], createdAt=iso(v["createdAt"])) for v in pg["versions"]], run)
        counts["Submission"] = await _upsert("Submission", [dict(id=s["id"], userId=s["userId"], verdict=s["verdict"], status=s["status"], score=s["score"], language=s["language"],
                                                                submittedAt=iso(s["submittedAt"]), completedAt=iso(s["completedAt"]), sourceHash=s["sourceHash"]) for s in pg["subs"]], run)
        counts["JudgeIncident"] = await _upsert("JudgeIncident", [dict(id=i["id"], type=i["type"], message=i["message"], createdAt=iso(i["createdAt"]), resolvedAt=iso(i["resolvedAt"])) for i in pg["incidents"]], run)

        rel = {
            "MEMBER_OF": [dict(a=m["userId"], b=m["organizationId"], role=m["role"]) for m in pg["members"]],
            "OWNED_BY": [dict(a=c["id"], b=c["organizationId"]) for c in pg["contests"]],
            "CONTAINS": [dict(a=p["contestId"], b=p["id"]) for p in pg["problems"]],
            "SUBMITTED": [dict(a=s["userId"], b=s["id"]) for s in pg["subs"]],
            "FOR_PROBLEM": [dict(a=s["id"], b=s["problemId"]) for s in pg["subs"]],
            "IN_CONTEST": [dict(a=s["id"], b=s["contestId"]) for s in pg["subs"]],
            "USED_FOR": [dict(a=s["judgeVersionId"], b=s["id"]) for s in pg["subs"] if s["judgeVersionId"]],
            "AFFECTED": [dict(a=i["id"], b=i["submissionId"]) for i in pg["incidents"]],
            "DURING": [dict(a=i["id"], b=i["judgeVersionId"]) for i in pg["incidents"] if i["judgeVersionId"]],
            "COVERS": [dict(a=m["id"], b=c) for m in pg["materials"] for c in m["concepts"]],
            "SUPERSEDED_BY": [dict(a=m["id"], b=m["superseded_by"]) for m in pg["materials"] if m["superseded_by"]],
            "PREREQUISITE_OF": [dict(a=r["prereq_id"], b=r["concept_id"]) for r in pg["prereqs"]],
        }
        # derived, recomputed edges: drop this set and re-create it so removed concepts/similarities disappear
        if not light:
            derived = derive_problem_edges(pg["problems"], pg["concepts"], {p["id"]: org_of_contest.get(p["contestId"]) for p in pg["problems"]})
            rel["TAGGED_WITH"], rel["REQUIRES"], rel["SIMILAR_TO"] = derived["tags"], derived["requires"], derived["similar"]
        for name, rows in rel.items():
            counts["rel:" + name] = await _rels(name, rows, run)
        # re-created above with this run id; whatever still carries an older id no longer holds in PostgreSQL (no read gap)
        if not light:
            await graph_store.write("MATCH ()-[x:TAGGED_WITH|REQUIRES|SIMILAR_TO|PREREQUISITE_OF|COVERS|SUPERSEDED_BY]->() WHERE x.syncAt < $run DELETE x", run=run)

        if full and prune:
            for label in LABELS:
                await graph_store.write(f"MATCH (n:{label}) WHERE n.syncAt IS NOT NULL AND n.syncAt < $run DETACH DELETE n", run=run)
            await graph_store.write("MATCH ()-[x]->() WHERE x.syncAt IS NOT NULL AND x.syncAt < $run DELETE x", run=run)
        _watermark = started
        STATE.update(status="ready", last_sync=iso(dt.datetime.now(dt.timezone.utc)), last_error=None, syncs=STATE["syncs"] + 1, last_ms=int((time.perf_counter() - t0) * 1000), full=full)
        if not light:
            STATE["last_counts"] = counts
        return counts


async def freshen(max_age_s: float = 0.5, light: bool = True) -> bool:
    """Bring the graph up to date if it is older than max_age_s (never raises). light=True (before answering a question) merges only
    changed submissions/incidents; light=False (after knowledge edits) also re-merges the reference data and derived edges."""
    if not graph_store.configured() or STATE["status"] == "starting":
        return False
    last = STATE.get("last_sync")
    if last and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() < max_age_s:
        return True
    try:
        await asyncio.wait_for(project(full=False, light=light), timeout=8)
        return True
    except Exception as exc:
        log.warning("on-demand graph sync skipped: %s", type(exc).__name__)
        return False


async def run_forever() -> None:
    """Background task: wait for PostgreSQL and Neo4j (retrying with backoff, never crashing the service), ingest the authored
    knowledge, do a full projection, then sync incrementally every GRAPH_SYNC_INTERVAL_S seconds."""
    interval = float(os.getenv("GRAPH_SYNC_INTERVAL_S", "20"))
    full_every = float(os.getenv("GRAPH_FULL_REBUILD_INTERVAL_S", "600"))  # incremental passes cannot see deleted rows: a periodic full pass prunes them
    delay, first, last_full = 2.0, True, time.monotonic()
    started = time.monotonic()
    while True:
        try:
            if not db.configured():
                STATE.update(status="disabled", last_error="DATABASE_URL not set")
                return
            if not graph_store.configured():
                STATE.update(status="disabled", last_error="NEO4J_URI not set")
                return
            if first:
                counts = await knowledge.ingest_files()
                log.info("knowledge ingested: %s", counts)
                from . import assistant

                await assistant.reload_index()
                await project(full=True, prune=False)
                log.info("graph projected: %s", {k: v for k, v in STATE["last_counts"].items() if not k.startswith("rel:")})
                await graph_store.warm()
                first, last_full = False, time.monotonic()
            elif full_every > 0 and time.monotonic() - last_full >= full_every:
                await project(full=True, prune=True)
                last_full = time.monotonic()
            else:
                await project(full=False)
            delay = 2.0
            # right after a fresh start the backend may still be seeding: poll quickly (for at most 5 minutes) until there is data
            empty = not STATE["last_counts"].get("Problem") and time.monotonic() - started < 300
            await asyncio.sleep(min(interval, 3.0) if empty else interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            STATE.update(status="unavailable", last_error=f"{type(exc).__name__}: {str(exc)[:160]}")
            log.warning("graph sync failed (%s); retrying in %.0fs", type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)  # PostgreSQL may simply not be migrated yet (fresh start): keep retrying quickly


async def _cli() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("rebuild", "sync"):
        counts = await knowledge.ingest_files()
        print("knowledge:", counts)
        out = await project(full=(cmd == "rebuild"), prune=(cmd == "rebuild"))
        print("graph:", {k: v for k, v in out.items()})
    rows = await graph_store.read("counts")
    print("nodes:", {r["label"]: r["n"] for r in rows})
    print("relationships:", {r["type"]: r["n"] for r in await graph_store.read("rel_counts")})
    await graph_store.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_cli()))
