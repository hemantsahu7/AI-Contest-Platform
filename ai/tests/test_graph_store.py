"""Neo4j graph store against a REAL Neo4j (skipped, with a reason, when NEO4J_URI is not reachable; the Docker evaluation runs
them). A small synthetic two-organization dataset (ids prefixed `t-`, removed afterwards) exercises the projection statements and
every allowlisted read query, including the authorization scoping that lives inside the Cypher."""
import pytest

from app import graph_store, graph_sync

# Test data carries NO syncAt marker: the live service's stale-edge cleanup and full-rebuild pruning only touch stamped elements,
# so running these tests next to a running stack can neither be disturbed by it nor disturb it.
RUN = None
FAILING = graph_store.FAILING


def rows(label, *items):
    return list(items)


NODES = {
    "Organization": [dict(id="t-orgA", name="Org A"), dict(id="t-orgB", name="Org B")],
    "User": [dict(id="t-alice", username="alice", role="LEARNER"), dict(id="t-bob", username="bob", role="LEARNER"), dict(id="t-carol", username="carol", role="LEARNER")],
    "Contest": [dict(id="t-cA", title="A contest", status="RUNNING", orgId="t-orgA", startTime=None, endTime=None),
                dict(id="t-cDraft", title="A draft", status="DRAFT", orgId="t-orgA", startTime=None, endTime=None),
                dict(id="t-cB", title="B contest", status="RUNNING", orgId="t-orgB", startTime=None, endTime=None)],
    "Problem": [dict(id="t-p1", title="Sum of Two", titleNorm="sum of two", difficulty="EASY", points=100, contestId="t-cA", updatedAt="2026-01-01T00:10:00.000Z", aliases=["Add Two"]),
                dict(id="t-p2", title="Product", titleNorm="product", difficulty="EASY", points=100, contestId="t-cA", updatedAt="2026-01-01T00:10:00.000Z", aliases=[]),
                dict(id="t-pd", title="Unreleased Draft Problem", titleNorm="unreleased draft problem", difficulty="EASY", points=100, contestId="t-cDraft", updatedAt=None, aliases=[]),
                dict(id="t-pB", title="Other Org Problem", titleNorm="other org problem", difficulty="EASY", points=100, contestId="t-cB", updatedAt=None, aliases=[])],
    "Concept": [dict(id="t-k1", name="Overflow", description="", version=1, aliases=[]), dict(id="t-k2", name="Types", description="", version=1, aliases=[]),
                dict(id="t-k3", name="Bits", description="", version=1, aliases=[])],
    "LearningMaterial": [dict(id="t-m1", title="Overflow note", status="current", updated="2026-02-01", version=1, aliases=[]),
                         dict(id="t-mold", title="Old types note", status="deprecated", updated="2019-01-01", version=1, aliases=[]),
                         dict(id="t-m3", title="Bits note", status="current", updated="2026-01-01", version=1, aliases=[])],
    "JudgeVersion": [dict(id="t-jv1", version="t-judge-v1", description="first", createdAt="2020-01-01T00:00:00.000Z"),
                     dict(id="t-jv2", version="t-judge-v2", description="SECRET second", createdAt="2020-02-01T00:00:00.000Z")],
    "Submission": [
        dict(id="t-sa1", userId="t-alice", verdict="ACCEPTED", status="COMPLETED", score=100, language="cpp", submittedAt="2020-01-01T02:00:00.000Z", completedAt=None, sourceHash="H"),
        dict(id="t-sa2", userId="t-alice", verdict="WRONG_ANSWER", status="COMPLETED", score=0, language="cpp", submittedAt="2020-02-01T10:00:00.000Z", completedAt=None, sourceHash="H"),
        dict(id="t-sa3", userId="t-alice", verdict="WRONG_ANSWER", status="COMPLETED", score=0, language="cpp", submittedAt="2020-03-01T10:00:00.000Z", completedAt=None, sourceHash="X"),
        dict(id="t-sb1", userId="t-bob", verdict="WRONG_ANSWER", status="COMPLETED", score=0, language="cpp", submittedAt="2020-02-02T10:00:00.000Z", completedAt=None, sourceHash="H"),
        dict(id="t-sc1", userId="t-carol", verdict="WRONG_ANSWER", status="COMPLETED", score=0, language="cpp", submittedAt="2020-02-03T10:00:00.000Z", completedAt=None, sourceHash="Y"),
    ],
    "JudgeIncident": [dict(id="t-i1", type="UNKNOWN", message="SECRET incident text", createdAt="2020-02-01T10:01:00.000Z", resolvedAt=None)],
}
RELS = {
    "MEMBER_OF": [dict(a="t-alice", b="t-orgA", role="LEARNER"), dict(a="t-bob", b="t-orgA", role="LEARNER"), dict(a="t-carol", b="t-orgB", role="LEARNER")],
    "OWNED_BY": [dict(a="t-cA", b="t-orgA"), dict(a="t-cDraft", b="t-orgA"), dict(a="t-cB", b="t-orgB")],
    "CONTAINS": [dict(a="t-cA", b="t-p1"), dict(a="t-cA", b="t-p2"), dict(a="t-cDraft", b="t-pd"), dict(a="t-cB", b="t-pB")],
    "SUBMITTED": [dict(a="t-alice", b="t-sa1"), dict(a="t-alice", b="t-sa2"), dict(a="t-alice", b="t-sa3"), dict(a="t-bob", b="t-sb1"), dict(a="t-carol", b="t-sc1")],
    "FOR_PROBLEM": [dict(a="t-sa1", b="t-p1"), dict(a="t-sa2", b="t-p1"), dict(a="t-sa3", b="t-p2"), dict(a="t-sb1", b="t-p1"), dict(a="t-sc1", b="t-pB")],
    "IN_CONTEST": [dict(a="t-sa1", b="t-cA"), dict(a="t-sa2", b="t-cA"), dict(a="t-sa3", b="t-cA"), dict(a="t-sb1", b="t-cA"), dict(a="t-sc1", b="t-cB")],
    "USED_FOR": [dict(a="t-jv1", b="t-sa1"), dict(a="t-jv2", b="t-sa2"), dict(a="t-jv2", b="t-sb1")],
    "AFFECTED": [dict(a="t-i1", b="t-sa2")],
    "DURING": [dict(a="t-i1", b="t-jv2")],
    "COVERS": [dict(a="t-m1", b="t-k1"), dict(a="t-mold", b="t-k2"), dict(a="t-m3", b="t-k3")],
    "SUPERSEDED_BY": [dict(a="t-mold", b="t-m1")],
    "PREREQUISITE_OF": [dict(a="t-k2", b="t-k1"), dict(a="t-k3", b="t-k2")],  # bits -> types -> overflow: two hops
    "REQUIRES": [dict(a="t-p1", b="t-k1", reason="worst-case sum exceeds int32"), dict(a="t-p2", b="t-k1", reason="worst-case product exceeds int32"), dict(a="t-pB", b="t-k1", reason="other org")],
    "SIMILAR_TO": [dict(a="t-p1", b="t-p2", statementScore=0.95, titleScore=0.3, kind="similar")],
}


async def load(run=None):
    await graph_sync.ensure_schema()
    for label, items in NODES.items():
        await graph_sync._upsert(label, items, run)
    for name, items in RELS.items():
        await graph_sync._rels(name, items, run)


async def wipe():
    await graph_store.write("MATCH (n) WHERE n.id STARTS WITH 't-' DETACH DELETE n")


async def q(name, **p):
    return await graph_store.read(name, **p)


async def raw(cypher, **p):
    d = await graph_store.driver()
    async with d.session() as s:
        return [r.data() async for r in await s.run(cypher, parameters=p)]


@pytest.fixture
def g(real_neo4j):
    real_neo4j.run(wipe())
    real_neo4j.run(load())
    yield real_neo4j
    real_neo4j.run(wipe())


A = dict(org_id="t-orgA")


# ---------------- connection, schema, idempotency, duplicates ----------------

def test_connection_and_schema_initialisation_are_idempotent(real_neo4j):
    assert real_neo4j.run(graph_store.ping())
    real_neo4j.run(graph_sync.ensure_schema())
    real_neo4j.run(graph_sync.ensure_schema())  # running the initialisation again changes nothing and does not fail
    names = {r["name"] for r in real_neo4j.run(raw("SHOW CONSTRAINTS YIELD name RETURN name"))}
    assert {f"{label.lower()}_id" for label in graph_sync.LABELS} <= names


def test_nodes_and_relationships_are_created_with_the_expected_shape(g):
    counts = {r["label"]: r["n"] for r in g.run(raw("MATCH (n) WHERE n.id STARTS WITH 't-' RETURN labels(n)[0] AS label, count(n) AS n"))}
    assert counts == {label: len(items) for label, items in NODES.items()}
    rels = {r["t"]: r["n"] for r in g.run(raw("MATCH (a)-[r]->(b) WHERE a.id STARTS WITH 't-' RETURN type(r) AS t, count(r) AS n"))}
    assert rels == {name: len(items) for name, items in RELS.items()}


def test_loading_the_same_data_twice_is_idempotent(g):
    before = g.run(raw("MATCH (n) WHERE n.id STARTS WITH 't-' RETURN count(n) AS n"))[0]["n"]
    rel_before = g.run(raw("MATCH (a)-[r]->() WHERE a.id STARTS WITH 't-' RETURN count(r) AS n"))[0]["n"]
    g.run(load())
    g.run(load())
    assert g.run(raw("MATCH (n) WHERE n.id STARTS WITH 't-' RETURN count(n) AS n"))[0]["n"] == before
    assert g.run(raw("MATCH (a)-[r]->() WHERE a.id STARTS WITH 't-' RETURN count(r) AS n"))[0]["n"] == rel_before


def test_concurrent_passes_never_delete_each_others_edges(g):
    """Two projectors (service + CLI rebuild, or two replicas) must not clobber each other: stamps only move forward and a pass
    deletes only elements stamped BEFORE its own start. Stamps in the year 2100 keep the live service's cleanup away from this data."""
    T = 4_102_444_800_000
    g.run(load(T + 2000))  # the newer pass wrote first
    g.run(load(T + 1000))  # the older pass finishes later: it must not move stamps backwards
    stamps = g.run(raw("MATCH (a)-[x:REQUIRES]->() WHERE a.id STARTS WITH 't-' RETURN DISTINCT x.syncAt AS s"))
    assert stamps == [{"s": T + 2000}]
    cleanup = "MATCH (a)-[x:REQUIRES]->() WHERE a.id STARTS WITH 't-' AND x.syncAt < $run DELETE x"
    g.run(graph_store.write(cleanup, run=T + 1500))  # the older pass cleans up: nothing of the newer pass may go
    assert g.run(raw("MATCH (a)-[x:REQUIRES]->() WHERE a.id STARTS WITH 't-' RETURN count(x) AS n"))[0]["n"] == 3
    g.run(graph_store.write(cleanup, run=T + 3000))  # a still newer pass that no longer produces them removes them
    assert g.run(raw("MATCH (a)-[x:REQUIRES]->() WHERE a.id STARTS WITH 't-' RETURN count(x) AS n"))[0]["n"] == 0


def test_duplicate_nodes_are_rejected_by_the_uniqueness_constraints(g):
    with pytest.raises(Exception, match="(?i)already exists|constraint"):
        g.run(graph_store.write("CREATE (:Problem {id: 't-p1', title: 'duplicate'})"))
    assert g.run(raw("MATCH (p:Problem {id: 't-p1'}) RETURN count(p) AS n"))[0]["n"] == 1


def test_an_update_changes_the_node_in_place(g):
    g.run(graph_sync._upsert("Problem", [dict(NODES["Problem"][0], title="Sum of Two (renamed)")], RUN))
    rows = g.run(raw("MATCH (p:Problem {id: 't-p1'}) RETURN p.title AS title"))
    assert rows == [{"title": "Sum of Two (renamed)"}]  # same stable id, no second node


def test_nothing_that_could_hold_source_code_or_tests_is_stored(g):
    keys = {k for r in g.run(raw("MATCH (n) WHERE n.id STARTS WITH 't-' UNWIND keys(n) AS k RETURN DISTINCT k")) for k in r.values()}
    assert not {k for k in keys if any(w in k.lower() for w in ("source", "input", "expected", "stdout", "stderr", "password", "email", "token"))} - {"sourceHash"}


def test_all_allowlisted_queries_compile_and_run(g):
    g.run(graph_store.warm())
    for name in graph_store.READ_QUERIES:
        params = {k: v for k, v in dict(org_id="t-orgA", contest_id="t-cA", user_id="t-alice", problem_id="t-p1", concept_id="t-k1",
                                         submission_id="t-sa2", staff=True, failing=FAILING).items()}
        import re

        needed = set(re.findall(r"\$(\w+)", graph_store.READ_QUERIES[name]))
        g.run(q(name, **{k: params[k] for k in needed}))  # raises if the statement is invalid


# ---------------- multi-hop traversal and unseen data ----------------

def test_multi_hop_problem_to_concept_to_prerequisites_to_materials(g):
    r = g.run(q("problem_graph", problem_id="t-p1", **A))
    assert len(r) == 1 and r[0]["concept_id"] == "t-k1"
    pre = {p["id"]: p["hops"] for p in r[0]["prereqs"] if p["id"]}
    assert pre == {"t-k2": 1, "t-k3": 2}  # a two-hop prerequisite chain
    mats = {m["id"]: m for m in r[0]["materials"] if m["id"]}
    assert set(mats) == {"t-m1", "t-mold", "t-m3"}  # material for the concept and for both prerequisites
    assert mats["t-mold"]["status"] == "deprecated" and mats["t-mold"]["superseded_by"] == "t-m1"
    assert r[0]["rels"][0]["reason"] == "worst-case sum exceeds int32"


def test_concept_traversal_lists_problems_that_need_it_and_hides_drafts_from_learners(g):
    g.run(graph_sync._upsert("Problem", [dict(NODES["Problem"][2], id="t-pd2")], RUN))
    g.run(graph_sync._rels("CONTAINS", [dict(a="t-cDraft", b="t-pd2")], RUN))
    g.run(graph_sync._rels("REQUIRES", [dict(a="t-pd2", b="t-k1", reason="draft")], RUN))
    learner = g.run(q("concept_graph", concept_id="t-k1", staff=False, **A))[0]
    staff = g.run(q("concept_graph", concept_id="t-k1", staff=True, **A))[0]
    assert {p["id"] for p in learner["problems"] if p["id"]} == {"t-p1", "t-p2"}  # no draft problem, no other organization's problem
    assert {p["id"] for p in staff["problems"] if p["id"]} == {"t-p1", "t-p2", "t-pd2"}


def test_unseen_data_is_picked_up_without_any_code_or_configuration_change(g):
    new = dict(id="t-p9", title="Brand New", titleNorm="brand new", difficulty="EASY", points=1, contestId="t-cA", updatedAt=None, aliases=[])
    g.run(graph_sync._upsert("Problem", [new], RUN))
    g.run(graph_sync._rels("CONTAINS", [dict(a="t-cA", b="t-p9")], RUN))
    g.run(graph_sync._rels("REQUIRES", [dict(a="t-p9", b="t-k1", reason="new")], RUN))
    assert [r["concept_id"] for r in g.run(q("problem_graph", problem_id="t-p9", **A))] == ["t-k1"]
    assert "t-p9" in {r["id"] for r in g.run(q("entity_problems", staff=False, **A))}


def test_similar_problems_are_reported_within_the_organization_only(g):
    assert [r["id"] for r in g.run(q("similar_problems", problem_id="t-p1", staff=False, **A))] == ["t-p2"]
    assert g.run(q("similar_problems", problem_id="t-p1", staff=False, org_id="t-orgB")) == []


# ---------------- authorization scoping inside the Cypher ----------------

def test_cross_organization_reads_return_nothing(g):
    B = dict(org_id="t-orgB")
    assert g.run(q("problem_graph", problem_id="t-p1", **B)) == []  # org A's problem asked through org B
    assert g.run(q("problem_graph", problem_id="t-pB", **A)) == []  # org B's problem asked through org A
    assert g.run(q("learner_paths", user_id="t-alice", contest_id="t-cA", failing=FAILING, **B)) == []
    assert g.run(q("contest_gaps", contest_id="t-cA", failing=FAILING, staff=True, **B)) == []
    assert g.run(q("judge_submission", submission_id="t-sa2", contest_id="t-cA", user_id="t-alice", staff=True, **B)) == []
    assert g.run(q("judge_conflicts", contest_id="t-cA", user_id="t-alice", staff=True, problem_id=None, **B)) == []
    assert g.run(q("judge_versions", contest_id="t-cA", staff=True, **B)) == [] and g.run(q("judge_incidents", contest_id="t-cA", staff=True, **B)) == []


def test_entity_lookup_hides_other_orgs_and_drafts(g):
    learner = {r["id"] for r in g.run(q("entity_problems", staff=False, **A))}
    staff = {r["id"] for r in g.run(q("entity_problems", staff=True, **A))}
    assert "t-p1" in learner and "t-pd" not in learner and "t-pB" not in learner
    assert "t-pd" in staff and "t-pB" not in staff


def test_a_learner_path_contains_only_that_learners_submissions(g):
    alice = g.run(q("learner_paths", user_id="t-alice", contest_id="t-cA", failing=FAILING, **A))
    # p1: one failure followed by an accept is not "struggling"; p2: an unresolved failure is
    assert {r["problem_id"] for r in alice} == {"t-p2"} and alice[0]["fails"] == 1 and alice[0]["accepted"] is False
    bob = g.run(q("learner_paths", user_id="t-bob", contest_id="t-cA", failing=FAILING, **A))
    assert [(r["problem_id"], r["fails"], r["accepted"]) for r in bob] == [("t-p1", 1, False)]  # alice's accept on p1 does not leak into bob's path
    assert g.run(q("learner_paths", user_id="t-carol", contest_id="t-cA", failing=FAILING, **A)) == []  # carol has nothing in this contest/org
    assert g.run(q("learner_paths", user_id="t-nobody", contest_id="t-cA", failing=FAILING, **A)) == []
    # the concept chain hangs off each problem: overflow <- types <- bits (2 hops) with their materials
    chain = {p["id"]: p["hops"] for r in bob for p in r["prereqs"] if p["id"]}
    assert chain == {"t-k2": 1, "t-k3": 2}


def test_contest_gaps_group_learners_and_prerequisites_and_stay_in_the_contest(g):
    assert g.run(q("contest_gaps", contest_id="t-cA", failing=FAILING, staff=False, **A)) == []  # role is enforced inside the Cypher, not only by the tool
    gaps = {r["concept_id"]: r for r in g.run(q("contest_gaps", contest_id="t-cA", failing=FAILING, staff=True, **A))}
    assert {"t-k1", "t-k2", "t-k3"} <= set(gaps)  # the failing problems' concept and its prerequisite chain (2 hops)
    assert set(gaps["t-k1"]["learners"]) == {"alice", "bob"} and "carol" not in gaps["t-k1"]["learners"]  # carol is in another org
    assert set(gaps["t-k2"]["via_concepts"]) >= {"Overflow"}  # reached through the concept the problem requires


def test_judge_submission_is_visible_to_its_owner_and_staff_only(g):
    base = dict(submission_id="t-sa2", contest_id="t-cA", **A)
    own = g.run(q("judge_submission", user_id="t-alice", staff=False, **base))
    assert len(own) == 1 and own[0]["judge_version"] == "t-judge-v2"
    # incident text and judge-version notes never leave the query for a learner, even for their own submission
    assert own[0]["judge_description"] is None and not [i for i in own[0]["incidents"] if i.get("type") or i.get("message")]
    assert "SECRET" not in str(own)
    assert g.run(q("judge_submission", user_id="t-bob", staff=False, **base)) == []  # another learner's submission
    assert g.run(q("judge_submission", user_id="t-bob", staff=True, **base))[0]["incidents"][0]["type"] == "UNKNOWN"  # staff role
    assert g.run(q("judge_submission", user_id="t-alice", staff=False, submission_id="t-sa2", contest_id="t-cB", **A)) == []  # wrong contest for the submission


def test_judge_conflicts_pair_only_the_same_learners_identical_source(g):
    base = dict(contest_id="t-cA", problem_id=None, **A)
    own = g.run(q("judge_conflicts", user_id="t-alice", staff=False, **base))
    assert len(own) == 1 and {own[0]["a_verdict"], own[0]["b_verdict"]} == {"ACCEPTED", "WRONG_ANSWER"}
    assert {own[0]["a_judge"], own[0]["b_judge"]} == {"t-judge-v1", "t-judge-v2"} and own[0]["learner"] == "alice"
    # bob's submission has the same source hash and a failing verdict, but it is never paired with alice's
    assert g.run(q("judge_conflicts", user_id="t-bob", staff=False, **base)) == []
    staff = g.run(q("judge_conflicts", user_id="t-someone", staff=True, **base))
    assert {r["learner"] for r in staff} == {"alice"}
    assert g.run(q("judge_conflicts", user_id="t-alice", staff=False, contest_id="t-cA", problem_id="t-p2", **A)) == []


def test_staff_only_queries_report_versions_and_incidents(g):
    versions = g.run(q("judge_versions", contest_id="t-cA", staff=True, **A))
    assert {(v["version"], v["verdict"]) for v in versions} >= {("t-judge-v1", "ACCEPTED"), ("t-judge-v2", "WRONG_ANSWER")}
    assert [i["id"] for i in g.run(q("judge_incidents", contest_id="t-cA", staff=True, **A))] == ["t-i1"]
    assert g.run(q("judge_versions", contest_id="t-cA", staff=False, **A)) == [] and g.run(q("judge_incidents", contest_id="t-cA", staff=False, **A)) == []


# ---------------- projection from the real PostgreSQL ----------------

def test_projection_from_postgres_is_idempotent_and_rebuildable(real_neo4j):
    import os

    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not set")
    from app import db, knowledge

    async def snapshot():
        nodes = {r["label"]: r["n"] for r in await raw("MATCH (n) WHERE n.syncAt IS NOT NULL AND NOT n.id STARTS WITH 't-' RETURN labels(n)[0] AS label, count(n) AS n")}
        rels = {r["t"]: r["n"] for r in await raw("MATCH (a)-[r]->() WHERE a.syncAt IS NOT NULL AND NOT a.id STARTS WITH 't-' RETURN type(r) AS t, count(r) AS n")}
        return nodes, rels

    async def go():
        await knowledge.ingest_files()
        await graph_sync.project(full=True, prune=True)
        first = await snapshot()
        await graph_sync.project(full=True, prune=True)  # a full rebuild from PostgreSQL
        second = await snapshot()
        await graph_sync.project(full=False)  # an incremental pass changes nothing either
        third = await snapshot()
        conn = await db.connect()
        try:
            cur = await conn.execute('SELECT count(*) FROM "Submission"')
            pg_subs = (await cur.fetchone())[0]
            cur = await conn.execute('SELECT count(*) FROM "Problem"')
            pg_problems = (await cur.fetchone())[0]
            cur = await conn.execute("SELECT count(*) FROM ai_learning_materials")
            pg_materials = (await cur.fetchone())[0]
        finally:
            await conn.close()
        return first, second, third, pg_subs, pg_problems, pg_materials

    first, second, third, pg_subs, pg_problems, pg_materials = real_neo4j.run(go())
    assert first == second == third  # same graph after a rebuild and after an incremental sync
    nodes = first[0]
    assert nodes["Submission"] == pg_subs and nodes["Problem"] == pg_problems and nodes["LearningMaterial"] == pg_materials  # PostgreSQL remains the truth
    assert first[1].get("SUBMITTED") == pg_subs and first[1].get("FOR_PROBLEM") == pg_subs
    assert first[1].get("PREREQUISITE_OF") and first[1].get("TAGGED_WITH") is not None
