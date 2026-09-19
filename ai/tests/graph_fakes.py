"""Shared test doubles for the graph/agent tests: a recording fake of the Neo4j read side and canned rows in the exact shape
the allowlisted Cypher statements return. The Cypher itself is tested against a real Neo4j in test_graph_store.py."""
import asyncio

from app import graph_store, graph_sync

MAT_OVERFLOW = {"id": "integer-overflow", "title": "Integer overflow", "status": "current", "updated": "2026-01-10", "version": 1, "covers": "integer-overflow", "superseded_by": None}
MAT_OVERFLOW_TYPES = {**MAT_OVERFLOW, "covers": "integer-types"}
MAT_LEGACY = {"id": "legacy-cpp-io", "title": "Legacy", "status": "deprecated", "updated": "2019-03-10", "version": 1, "covers": "input-output", "superseded_by": "cpp-input-output"}
MAT_NEW_IO = {"id": "cpp-input-output", "title": "IO", "status": "current", "updated": "2026-01-02", "version": 1, "covers": "input-output", "superseded_by": None}

PROBLEM_GRAPH_SUM = [
    {"concept_id": "integer-overflow", "concept": "Integer overflow",
     "rels": [{"rel": "REQUIRES", "reason": "worst-case sum is about 10,000,000,000, above the 32-bit maximum", "score": None}],
     "prereqs": [{"id": "integer-types", "name": "Integer types and ranges", "hops": 1, "via": ["integer-types", "integer-overflow"]}],
     "materials": [MAT_OVERFLOW, MAT_OVERFLOW_TYPES]},
    {"concept_id": "arithmetic-operations", "concept": "Basic arithmetic", "rels": [{"rel": "TAGGED_WITH", "reason": None, "score": 3}], "prereqs": [], "materials": []},
]
SIMILAR_SUM = [{"id": "p-dup", "title": "A + B", "contest": "Archive", "statement_score": 1.0, "title_score": 0.4, "kind": "duplicate"}]

JUDGE_SUB = [{"id": "s900-aaaa-bbbb", "verdict": "WRONG_ANSWER", "status": "COMPLETED", "submitted_at": "2026-01-01T00:59:00.000Z", "judge_version": "judge-v2",
              "judge_version_created": "2026-01-01T00:30:00.000Z", "judge_description": "SECRET-JUDGE-DESCRIPTION", "problem_id": "p1", "problem": "Sum of Two Numbers",
              "problem_updated_at": "2026-01-01T00:10:00.000Z",
              "incidents": [{"type": "UNKNOWN", "created": "2026-01-01T00:59:10.000Z", "resolved": None, "message": "SECRET-INCIDENT-MESSAGE"}]}]
JUDGE_CONFLICT = [{"learner": "alice", "problem_id": "p1", "problem": "Sum of Two Numbers", "problem_updated_at": "2026-01-01T00:10:00.000Z",
                   "a_id": "s003-aaaa-bbbb", "a_verdict": "ACCEPTED", "a_at": "2026-01-01T00:03:00.000Z", "a_judge": "judge-v1",
                   "b_id": "s900-aaaa-bbbb", "b_verdict": "WRONG_ANSWER", "b_at": "2026-01-01T00:59:00.000Z", "b_judge": "judge-v2"}]

LEARNER_PATHS = [
    {"problem_id": "p1", "problem": "Sum of Two Numbers", "fails": 3, "accepted": False, "verdicts": ["WRONG_ANSWER"] * 3, "concept_id": "integer-overflow", "concept": "Integer overflow",
     "rels": [{"rel": "REQUIRES", "reason": "x"}], "prereqs": [{"id": "integer-types", "name": "Integer types and ranges", "hops": 1}], "materials": [MAT_OVERFLOW, MAT_OVERFLOW_TYPES]},
    {"problem_id": "p2", "problem": "Product", "fails": 2, "accepted": False, "verdicts": ["WRONG_ANSWER"] * 2, "concept_id": "integer-overflow", "concept": "Integer overflow",
     "rels": [{"rel": "REQUIRES", "reason": "x"}], "prereqs": [{"id": "integer-types", "name": "Integer types and ranges", "hops": 1}], "materials": [MAT_OVERFLOW, MAT_OVERFLOW_TYPES]},
]
CONTEST_GAPS = [
    {"concept_id": "integer-types", "concept": "Integer types and ranges", "via_concepts": ["Integer overflow"], "learners": ["alice", "bob"], "problems": ["Product", "Sum of Two Numbers"], "failing_submissions": 7},
    {"concept_id": "loops", "concept": "Loops and termination", "via_concepts": ["Time complexity"], "learners": ["alice"], "problems": ["Product"], "failing_submissions": 2},
]

ROWS = {
    "problem_graph": PROBLEM_GRAPH_SUM, "similar_problems": SIMILAR_SUM, "judge_submission": JUDGE_SUB, "judge_conflicts": [],
    "learner_paths": LEARNER_PATHS, "contest_gaps": CONTEST_GAPS, "entity_problems": [], "entity_concepts": [], "judge_versions": [], "judge_incidents": [],
    "concept_graph": [{"id": "input-output", "name": "Input and output formatting", "prereqs": [], "materials": [MAT_LEGACY, MAT_NEW_IO], "problems": []}],
}


class FakeGraph:
    """Records every read (name + parameters). `rows` maps a query name to rows or to a callable(params) -> rows."""

    def __init__(self, rows=None, fail=None, delay=0.0):
        self.rows = {**ROWS, **(rows or {})}
        self.fail = fail
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []

    async def read(self, name, **params):
        assert name in graph_store.READ_QUERIES, f"tool used a query that is not on the allowlist: {name}"
        self.calls.append((name, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise graph_store.GraphUnavailable(self.fail)
        r = self.rows.get(name, [])
        return r(params) if callable(r) else r

    def names(self):
        return [n for n, _ in self.calls]


def install(monkeypatch, fake: FakeGraph):
    async def no_sync(*a, **k):
        return True

    monkeypatch.setattr(graph_store, "configured", lambda: True)
    monkeypatch.setattr(graph_store, "read", fake.read)
    monkeypatch.setattr(graph_sync, "freshen", no_sync)
    return fake
