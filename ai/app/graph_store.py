"""Neo4j access. The graph is a PROJECTION of PostgreSQL (see graph_sync.py); nothing is authoritative here.

* READ side: a fixed allowlist of parameterised Cypher statements (READ_QUERIES). Callers pass parameter values only, never
  query text, and every statement is checked at import time to contain no write clause. Authorization scoping lives in the
  queries themselves ($org_id, $contest_id, $user_id / $staff), on top of the checks the tools make against the backend.
* WRITE side: only graph_sync uses `write()`, with MERGE statements.
The graph deliberately holds no source code, no test cases, no credentials: only ids, titles, verdicts, timestamps,
a source hash and relationships."""
import asyncio
import logging
import os
import re

log = logging.getLogger("ai.graph")
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)  # "constraint already exists" notices on every idempotent sync

QUERY_TIMEOUT_S = float(os.getenv("GRAPH_QUERY_TIMEOUT_S", "6"))
FAILING = ["WRONG_ANSWER", "COMPILATION_ERROR", "RUNTIME_ERROR", "TIME_LIMIT_EXCEEDED"]  # JUDGE_ERROR is never the learner's fault

READ_QUERIES: dict[str, str] = {
    # Problem -> Concept (-> prerequisite Concepts, up to 3 hops) -> LearningMaterial (with superseded_by for stale guidance)
    "problem_graph": """
        MATCH (o:Organization {id: $org_id})<-[:OWNED_BY]-(:Contest)-[:CONTAINS]->(p:Problem {id: $problem_id})
        MATCH (p)-[r:TAGGED_WITH|REQUIRES]->(k:Concept)
        OPTIONAL MATCH chain = (pre:Concept)-[:PREREQUISITE_OF*1..3]->(k)
        WITH p, k, collect(DISTINCT {rel: type(r), reason: r.reason, score: r.score}) AS rels,
             collect(DISTINCT {id: pre.id, name: pre.name, hops: length(chain), via: [n IN nodes(chain) | n.id]}) AS prereqs
        OPTIONAL MATCH (m:LearningMaterial)-[:COVERS]->(t:Concept)
        WHERE t.id = k.id OR t.id IN [x IN prereqs WHERE x.id IS NOT NULL | x.id]
        OPTIONAL MATCH (m)-[:SUPERSEDED_BY]->(newer:LearningMaterial)
        RETURN k.id AS concept_id, k.name AS concept, rels, prereqs,
               collect(DISTINCT {id: m.id, title: m.title, status: m.status, updated: m.updated, version: m.version, covers: t.id, superseded_by: newer.id}) AS materials
        ORDER BY size(rels) DESC, k.id""",
    # Concept -> prerequisites -> materials, and the org's problems that need the concept
    "concept_graph": """
        MATCH (k:Concept {id: $concept_id})
        OPTIONAL MATCH chain = (pre:Concept)-[:PREREQUISITE_OF*1..3]->(k)
        WITH k, collect(DISTINCT {id: pre.id, name: pre.name, hops: length(chain), via: [n IN nodes(chain) | n.id]}) AS prereqs
        OPTIONAL MATCH (m:LearningMaterial)-[:COVERS]->(t:Concept)
        WHERE t.id = k.id OR t.id IN [x IN prereqs WHERE x.id IS NOT NULL | x.id]
        OPTIONAL MATCH (m)-[:SUPERSEDED_BY]->(newer:LearningMaterial)
        WITH k, prereqs, collect(DISTINCT {id: m.id, title: m.title, status: m.status, updated: m.updated, version: m.version, covers: t.id, superseded_by: newer.id}) AS materials
        OPTIONAL MATCH (:Organization {id: $org_id})<-[:OWNED_BY]-(c:Contest)-[:CONTAINS]->(p:Problem)-[:TAGGED_WITH|REQUIRES]->(k)
        WHERE $staff OR c.status <> 'DRAFT'
        RETURN k.id AS id, k.name AS name, prereqs, materials,
               collect(DISTINCT {id: p.id, title: p.title, contest: c.title}) AS problems""",
    # User -> Submission -> Problem -> Concept -> prerequisite Concept, for ONE user (the caller) in ONE authorized contest
    "learner_paths": """
        MATCH (u:User {id: $user_id})-[:SUBMITTED]->(s:Submission)-[:FOR_PROBLEM]->(p:Problem)<-[:CONTAINS]-(c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        WITH p, collect(s) AS subs
        WITH p, [x IN subs WHERE x.status = 'COMPLETED' AND x.verdict IN $failing] AS fails, any(x IN subs WHERE x.verdict = 'ACCEPTED') AS accepted
        WHERE size(fails) >= 1 AND (NOT accepted OR size(fails) >= 2)
        MATCH (p)-[r:TAGGED_WITH|REQUIRES]->(k:Concept)
        OPTIONAL MATCH chain = (pre:Concept)-[:PREREQUISITE_OF*1..3]->(k)
        WITH p, fails, accepted, k, collect(DISTINCT {rel: type(r), reason: r.reason}) AS rels,
             collect(DISTINCT {id: pre.id, name: pre.name, hops: length(chain)}) AS prereqs
        OPTIONAL MATCH (m:LearningMaterial)-[:COVERS]->(t:Concept)
        WHERE t.id = k.id OR t.id IN [x IN prereqs WHERE x.id IS NOT NULL | x.id]
        OPTIONAL MATCH (m)-[:SUPERSEDED_BY]->(newer:LearningMaterial)
        RETURN p.id AS problem_id, p.title AS problem, size(fails) AS fails, accepted, [x IN fails | x.verdict] AS verdicts,
               k.id AS concept_id, k.name AS concept, rels, prereqs,
               collect(DISTINCT {id: m.id, title: m.title, status: m.status, updated: m.updated, covers: t.id, superseded_by: newer.id}) AS materials
        ORDER BY fails DESC, problem""",
    # staff only (the tool enforces the role): which concepts / prerequisite concepts do several learners' failures share
    "contest_gaps": """
        MATCH (c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        WHERE $staff
        MATCH (u:User)-[:SUBMITTED]->(s:Submission)-[:FOR_PROBLEM]->(p:Problem)<-[:CONTAINS]-(c)
        WHERE s.status = 'COMPLETED' AND s.verdict IN $failing
        MATCH (p)-[:TAGGED_WITH|REQUIRES]->(k:Concept)
        OPTIONAL MATCH (pre:Concept)-[:PREREQUISITE_OF*0..2]->(k)
        WITH coalesce(pre, k) AS gap, k, u, p, s
        RETURN gap.id AS concept_id, gap.name AS concept, collect(DISTINCT k.name) AS via_concepts,
               collect(DISTINCT u.username) AS learners, collect(DISTINCT p.title) AS problems, count(DISTINCT s) AS failing_submissions
        ORDER BY size(learners) DESC, failing_submissions DESC LIMIT 12""",
    # Judge version + incidents for one submission ($staff OR own submission)
    "judge_submission": """
        MATCH (s:Submission {id: $submission_id})-[:IN_CONTEST]->(c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        WHERE $staff OR s.userId = $user_id
        OPTIONAL MATCH (j:JudgeVersion)-[:USED_FOR]->(s)
        OPTIONAL MATCH (i:JudgeIncident)-[:AFFECTED]->(s) WHERE $staff
        OPTIONAL MATCH (s)-[:FOR_PROBLEM]->(p:Problem)
        RETURN s.id AS id, s.verdict AS verdict, s.status AS status, s.submittedAt AS submitted_at, j.version AS judge_version,
               j.createdAt AS judge_version_created, CASE WHEN $staff THEN j.description END AS judge_description, p.id AS problem_id, p.title AS problem,
               p.updatedAt AS problem_updated_at,
               collect(DISTINCT {type: i.type, created: i.createdAt, resolved: i.resolvedAt, message: i.message}) AS incidents""",
    # Same learner, same problem, IDENTICAL source hash, different verdicts: conflicting judge evidence (no source is stored)
    "judge_conflicts": """
        MATCH (c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        MATCH (u:User)-[:SUBMITTED]->(a:Submission)-[:IN_CONTEST]->(c), (u)-[:SUBMITTED]->(b:Submission)-[:IN_CONTEST]->(c)
        WHERE ($staff OR u.id = $user_id) AND a.id < b.id AND a.sourceHash = b.sourceHash AND a.verdict <> b.verdict
          AND a.status = 'COMPLETED' AND b.status = 'COMPLETED'
        MATCH (a)-[:FOR_PROBLEM]->(p:Problem)<-[:FOR_PROBLEM]-(b)
        WHERE $problem_id IS NULL OR p.id = $problem_id
        OPTIONAL MATCH (ja:JudgeVersion)-[:USED_FOR]->(a)
        OPTIONAL MATCH (jb:JudgeVersion)-[:USED_FOR]->(b)
        RETURN u.username AS learner, p.id AS problem_id, p.title AS problem, p.updatedAt AS problem_updated_at,
               a.id AS a_id, a.verdict AS a_verdict, a.submittedAt AS a_at, ja.version AS a_judge,
               b.id AS b_id, b.verdict AS b_verdict, b.submittedAt AS b_at, jb.version AS b_judge
        ORDER BY a.submittedAt LIMIT 10""",
    # staff only: outcomes by judge version in the contest
    "judge_versions": """
        MATCH (c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        WHERE $staff
        MATCH (j:JudgeVersion)-[:USED_FOR]->(s:Submission)-[:IN_CONTEST]->(c)
        WHERE s.status = 'COMPLETED'
        RETURN j.version AS version, j.createdAt AS created, j.description AS description, s.verdict AS verdict, count(s) AS n
        ORDER BY created, verdict""",
    "judge_incidents": """
        MATCH (c:Contest {id: $contest_id})-[:OWNED_BY]->(:Organization {id: $org_id})
        WHERE $staff
        MATCH (i:JudgeIncident)-[:AFFECTED]->(s:Submission)-[:IN_CONTEST]->(c)
        OPTIONAL MATCH (i)-[:DURING]->(j:JudgeVersion)
        RETURN i.id AS id, i.type AS type, i.createdAt AS created, i.resolvedAt AS resolved, i.message AS message, j.version AS judge_version, s.id AS submission_id
        ORDER BY created LIMIT 20""",
    "entity_problems": """
        MATCH (o:Organization {id: $org_id})<-[:OWNED_BY]-(c:Contest)-[:CONTAINS]->(p:Problem)
        WHERE $staff OR c.status <> 'DRAFT'
        RETURN p.id AS id, p.title AS title, p.aliases AS aliases, c.id AS contest_id, c.title AS contest, p.updatedAt AS updated_at""",
    "entity_concepts": "MATCH (k:Concept) RETURN k.id AS id, k.name AS title, k.aliases AS aliases",
    "entity_materials": """
        MATCH (m:LearningMaterial) OPTIONAL MATCH (m)-[:SUPERSEDED_BY]->(n:LearningMaterial)
        RETURN m.id AS id, m.title AS title, m.aliases AS aliases, m.status AS status, m.updated AS updated, n.id AS superseded_by""",
    "similar_problems": """
        MATCH (o:Organization {id: $org_id})<-[:OWNED_BY]-(:Contest)-[:CONTAINS]->(p:Problem {id: $problem_id})
        MATCH (p)-[r:SIMILAR_TO]-(q:Problem)<-[:CONTAINS]-(c2:Contest)-[:OWNED_BY]->(o)
        WHERE $staff OR c2.status <> 'DRAFT'
        RETURN q.id AS id, q.title AS title, c2.title AS contest, r.statementScore AS statement_score, r.titleScore AS title_score, r.kind AS kind
        ORDER BY r.statementScore DESC LIMIT 5""",
    "counts": "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY label",
    "rel_counts": "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY type",
}

_WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL)\b", re.I)
for _name, _q in READ_QUERIES.items():  # a read query that could modify data is a programming error: fail at import
    assert not _WRITE.search(_q), f"read query {_name} contains a write clause"

_driver = None
_driver_key: tuple | None = None


def uri() -> str:
    return os.getenv("NEO4J_URI", "").strip()


def configured() -> bool:
    return bool(uri())


async def driver():
    """Lazily created driver (the connection is only attempted on first use, so a slow Neo4j never blocks startup)."""
    global _driver, _driver_key
    from neo4j import AsyncGraphDatabase

    key = (uri(), os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", ""))
    if _driver is not None and _driver_key != key:
        await close()
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(key[0], auth=(key[1], key[2]), connection_timeout=3, connection_acquisition_timeout=4, max_connection_lifetime=600)
        _driver_key = key
    return _driver


async def close() -> None:
    global _driver, _driver_key
    if _driver is not None:
        try:
            await _driver.close()
        finally:
            _driver, _driver_key = None, None


async def ping(timeout: float = 3.0) -> bool:
    if not configured():
        return False
    try:
        async with asyncio.timeout(timeout):
            d = await driver()
            async with d.session() as s:
                res = await s.run("RETURN 1 AS ok")
                return (await res.single())["ok"] == 1
    except Exception:
        return False


class GraphUnavailable(Exception):
    pass


async def read(name: str, **params) -> list[dict]:
    """Run one allowlisted read query. Unknown names raise KeyError; parameters are values, never query text."""
    query = READ_QUERIES[name]
    if not configured():
        raise GraphUnavailable("graph store not configured")
    try:
        async with asyncio.timeout(QUERY_TIMEOUT_S):
            d = await driver()
            async with d.session(default_access_mode="READ") as s:
                res = await s.run(query, parameters=params)
                return [r.data() async for r in res]
    except (asyncio.TimeoutError, TimeoutError):
        raise GraphUnavailable(f"graph query '{name}' timed out after {QUERY_TIMEOUT_S:.0f}s") from None
    except GraphUnavailable:
        raise
    except Exception as exc:
        log.warning("graph read %s failed: %s", name, type(exc).__name__)
        raise GraphUnavailable(f"graph store unavailable ({type(exc).__name__})") from None


_DUMMY = {"org_id": "-", "contest_id": "-", "user_id": "-", "problem_id": "-", "concept_id": "-", "submission_id": "-", "staff": False, "failing": FAILING}


async def warm() -> None:
    """Run every allowlisted query once with placeholder ids so Neo4j compiles and caches the plans before the first question."""
    for name, q in READ_QUERIES.items():
        params = {p: _DUMMY[p] for p in set(re.findall(r"\$(\w+)", q)) if p in _DUMMY}
        try:
            await read(name, **params)
        except Exception:
            pass


async def write(query: str, **params) -> list[dict]:
    """Projection writes only (graph_sync). Uses an explicit write transaction."""
    d = await driver()
    async with d.session() as s:
        async def work(tx):
            res = await tx.run(query, parameters=params)
            return [r.data() async for r in res]

        return await s.execute_write(work)
