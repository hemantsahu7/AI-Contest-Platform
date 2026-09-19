"""Derivation of graph edges from problems + the authored concept taxonomy (pure functions, no services), and the ingestion
format. What the derived edges say must follow from the statement text and constraints, not from example answers."""
import json

from app import graph_sync, knowledge
from app.problem_analysis import INT32_MAX, statement_bound, worst_case

CONCEPTS = json.loads((knowledge.KNOWLEDGE_DIR / "concepts.json").read_text(encoding="utf-8"))["concepts"]


def prob(pid, title, description, input_format="Two integers A and B."):
    return {"id": pid, "contestId": "c1", "title": title, "description": description, "inputFormat": input_format}


SUM = prob("sum", "Sum of Two Numbers", "Read two integers A and B and print A+B. Constraints: -5000000000 <= A, B <= 5000000000.")
DUP = prob("dup", "A + B", "Read two integers A and B and print A+B. Constraints: -5000000000 <= A, B <= 5000000000.")
MAX = prob("max", "Maximum of Two", "Read two integers A and B and print the larger one. Constraints: -1000000000 <= A, B <= 1000000000.")
DIFF = prob("diff", "Absolute Difference", "Read two integers A and B and print the absolute value of A-B. Constraints: -1000000000 <= A, B <= 1000000000.")
PRODUCT = prob("prod", "Product", "Read two integers and print A*B. Constraints: -1000000000 <= A, B <= 1000000000.")
ORG = {"sum": "o1", "dup": "o1", "max": "o1", "diff": "o1", "prod": "o1"}


def edges(problems, org_of=ORG):
    return graph_sync.derive_problem_edges(problems, CONCEPTS, org_of)


def test_statement_bounds_are_read_from_the_text():
    assert statement_bound(SUM)[0] == 5_000_000_000
    assert statement_bound(PRODUCT) == (1_000_000_000, "product")
    assert worst_case(*statement_bound(PRODUCT)) == 10 ** 18 > INT32_MAX
    assert statement_bound(prob("x", "Hello", "Print hello."))[0] is None


def test_requires_edges_carry_a_reason_derived_from_the_constraints():
    req = {(r["a"], r["b"]): r["reason"] for r in edges([SUM, MAX, PRODUCT])["requires"]}
    assert ("sum", "integer-types") in req and "5,000,000,000" in req[("sum", "integer-types")]  # value range beyond 32 bits
    assert ("sum", "integer-overflow") in req and ("prod", "integer-overflow") in req  # worst-case sum / product exceeds int32
    assert ("max", "integer-overflow") not in req and ("max", "integer-types") not in req  # max of two values <= 1e9 fits in int32


def test_tags_follow_the_statement_keywords():
    tags = {(t["a"], t["b"]) for t in edges([SUM, MAX, DIFF, PRODUCT])["tags"]}
    assert ("sum", "arithmetic-operations") in tags and ("prod", "arithmetic-operations") in tags
    assert ("max", "comparison-logic") in tags and ("diff", "absolute-value") in tags
    assert ("max", "arithmetic-operations") not in tags


def test_generic_keywords_do_not_tag_every_problem():
    many = [prob(f"p{i}", f"Problem {i}", "Read the input format carefully and print the answer.") for i in range(6)]
    tagged_io = [t for t in edges(many, {p["id"]: "o1" for p in many})["tags"] if t["b"] == "input-output"]
    assert tagged_io == []  # a keyword present in (nearly) every statement says nothing about any one of them


def test_duplicate_and_similar_problems_are_detected_by_statement_not_by_title():
    sim = {(s["a"], s["b"]): s for s in edges([SUM, DUP, MAX, DIFF])["similar"]}
    dup = sim[("dup", "sum")] if ("dup", "sum") in sim else sim[("sum", "dup")]
    assert dup["kind"] == "duplicate" and dup["statementScore"] >= graph_sync.DUPLICATE_STATEMENT  # different titles, same statement
    assert not any({"max", "sum"} == {a, b} for a, b in sim)  # different tasks are not similar
    assert all(s["statementScore"] < 1.0 for s in sim.values() if s["kind"] == "similar")


def test_problems_of_different_organizations_are_never_related():
    other_org = {**ORG, "dup": "o2"}
    assert edges([SUM, DUP], other_org)["similar"] == []


def test_edge_derivation_is_deterministic():
    assert edges([SUM, DUP, MAX, DIFF, PRODUCT]) == edges([DIFF, PRODUCT, MAX, DUP, SUM])  # input order does not matter


def test_unseen_problems_get_edges_without_any_per_problem_configuration():
    fresh = prob("new", "Sum of Three Numbers", "Read three integers and print their sum. Constraints: -3000000000 <= A, B, C <= 3000000000.", "Three integers.")
    e = edges([fresh], {"new": "o1"})
    assert {(t["a"], t["b"]) for t in e["tags"]} == {("new", "arithmetic-operations")}
    assert {(r["a"], r["b"]) for r in e["requires"]} >= {("new", "integer-types")}


def test_taxonomy_is_consistent():
    ids = {c["id"] for c in CONCEPTS}
    assert len(ids) == len(CONCEPTS)
    for c in CONCEPTS:
        assert set(c.get("requires", [])) <= ids and c["id"] not in c.get("requires", [])
    data = json.loads((knowledge.KNOWLEDGE_DIR / "concepts.json").read_text(encoding="utf-8"))
    from app.retrieval import load_materials

    assert set(data["material_concepts"]) == {m.id for m in load_materials()}  # every authored note is mapped to concepts
    assert all(set(v) <= ids for v in data["material_concepts"].values())
    # prerequisites form a DAG (no concept is its own ancestor)
    graph = {c["id"]: c.get("requires", []) for c in CONCEPTS}

    def ancestors(cid, seen=()):
        for p in graph[cid]:
            assert p not in seen and p != cid
            ancestors(p, seen + (cid,))

    for cid in graph:
        ancestors(cid)


def test_material_files_carry_concepts_and_stay_ingestible():
    from app.retrieval import load_materials

    mats = {m.id: m for m in load_materials()}
    assert mats["integer-overflow"].concepts and "integer-types" in mats["integer-overflow"].concepts
    assert mats["legacy-cpp-io"].status == "deprecated" and mats["legacy-cpp-io"].superseded_by == "cpp-input-output"


def test_iso_timestamps_are_normalised():
    import datetime as dt

    assert graph_sync.iso(dt.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05.000Z"  # naive datetimes are UTC (Prisma)
    assert graph_sync.iso(None) is None


def test_rel_queries_merge_and_never_create_duplicates():
    for name, q in graph_sync.REL_QUERIES.items():
        assert "MERGE (a)-[x:" in q and "CREATE" not in q.upper().replace("CONSTRAINT", ""), name
    for label, q in graph_sync.UPSERT.items():
        assert q.startswith("UNWIND $rows AS r MERGE (n:" + label + " {id: r.id})"), label
