"""Security of the graph/tool layer. The graph must never widen what a caller may see: every tool call is scoped by the
authenticated user, the contest's organization, the role and ownership, and nothing sensitive is projected into Neo4j.
(The Cypher-level scoping is exercised against a real Neo4j in test_graph_store.py.)"""
import asyncio
import inspect
import re

import pytest

from app import agent, assistant, graph_store, graph_sync, tools

from . import graph_fakes as gf
from .test_learner_code import ALICE, BOB_CODE, MY_CODE, PROBLEM_MUL, PROBLEM_SUM, TEACHER, Fake, history

BOB = {"id": "u2", "username": "bob", "role": "LEARNER", "memberships": [{"organizationId": "org", "role": "LEARNER"}]}
OUTSIDER_INSTRUCTOR = {"id": "t9", "username": "other-teach", "role": "INSTRUCTOR", "memberships": [{"organizationId": "other-org", "role": "INSTRUCTOR"}]}
ADMIN = {"id": "a1", "username": "root", "role": "ADMIN", "memberships": []}
HIDDEN_MARKERS = ("HIDDEN-IN-777", "HIDDEN-OUT-777")


def ask(question, me=ALICE, problem_id=None, submission_id=None, be=None):
    async def go():
        ctx = await assistant.gather(question, "c1", problem_id, submission_id, be or Fake(), me)
        return ctx, await assistant.answer(ctx)

    return asyncio.run(go())


def everything(ctx, result) -> str:
    """Every string a caller (or the model) could see: evidence, claims, answer, gaps, trace."""
    return " ".join([e.text + e.title + e.ref for e in ctx.evidence] + [c["text"] for c in result["claims"]] + [result["answer"]] + result["missing"]
                    + [str(ctx.agent.public())])


def make_run(monkeypatch, me=ALICE, staff=False, rows=None):
    fake = gf.install(monkeypatch, gf.FakeGraph(rows))
    ctx = assistant.Ctx(question="q", problems=[dict(PROBLEM_SUM), dict(PROBLEM_MUL)])
    ctx.problem = ctx.problems[0]
    contest = {"id": "c1", "title": "Demo", "organizationId": "org", "status": "RUNNING"}
    subs = [s for s in history() if s["userId"] == me["id"]]
    return agent.AgentRun(tools.ToolContext(ctx, me, contest, staff, subs)), ctx, fake


# ---------------- learner ----------------

def test_learner_cannot_run_contest_wide_graph_analysis(monkeypatch):
    run, ctx, fake = make_run(monkeypatch)
    step = asyncio.run(run.call("traverse_graph", {"start_kind": "contest", "start_id": "c1"}, "t"))
    assert step.status == "denied" and "instructors" in step.summary
    assert not fake.calls and not ctx.evidence  # the graph was never queried


def test_learner_can_only_traverse_their_own_failures(monkeypatch):
    run, _, fake = make_run(monkeypatch)
    step = asyncio.run(run.call("traverse_graph", {"start_kind": "learner", "start_id": BOB["id"]}, "t"))  # asks about another learner
    assert step.status == "denied" and not fake.calls
    step = asyncio.run(run.call("traverse_graph", {"start_kind": "learner"}, "t"))
    assert step.status == "ok"
    name, params = fake.calls[-1]
    assert name == "learner_paths" and params["user_id"] == ALICE["id"] and params["contest_id"] == "c1" and params["org_id"] == "org"


def test_learner_cannot_read_judge_history_of_another_learners_submission(monkeypatch):
    run, _, fake = make_run(monkeypatch)
    bobs = next(s["id"] for s in history() if s["userId"] == "u2")
    step = asyncio.run(run.call("get_judge_history", {"submission_id": bobs}, "t"))
    assert step.status == "denied" and not fake.calls
    unknown = asyncio.run(run.call("get_judge_history", {"submission_id": "not-mine-at-all"}, "t"))
    assert unknown.status == "denied"  # unknown and foreign ids are indistinguishable


def test_learner_graph_queries_are_never_run_as_staff(monkeypatch):
    run, _, fake = make_run(monkeypatch)
    asyncio.run(run.call("get_judge_history", {"submission_id": "s900-aaaa-bbbb"}, "t"))
    asyncio.run(run.call("traverse_graph", {"start_kind": "problem", "start_id": "p1"}, "t"))
    asyncio.run(run.call("resolve_entity", {"name": "sum"}, "t"))
    assert fake.calls and all(p.get("staff") is False for _, p in fake.calls if "staff" in p)
    assert not {"judge_versions", "judge_incidents", "contest_gaps"} & set(fake.names())  # staff-only queries are never issued


def test_learner_never_sees_judge_internals(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph({"judge_conflicts": gf.JUDGE_CONFLICT}))
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    blob = everything(ctx, r)
    assert "SECRET-INCIDENT-MESSAGE" not in blob and "SECRET-JUDGE-DESCRIPTION" not in blob  # incident text + judge notes are staff-only
    assert "UNKNOWN" not in blob  # no incident type either
    assert "judge-v1" in blob  # the version label of the learner's OWN submissions is fine


def test_learner_gets_no_other_learner_names_from_conflicts(monkeypatch):
    other = [{**gf.JUDGE_CONFLICT[0], "learner": "bob"}]
    gf.install(monkeypatch, gf.FakeGraph({"judge_conflicts": other}))
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert "bob" not in everything(ctx, r).lower().replace("bob's", "")  # the query is user-scoped; a learner's view names only "you"
    assert "for you" in everything(ctx, r)


def test_problem_outside_the_authorized_contest_is_denied(monkeypatch):
    run, ctx, fake = make_run(monkeypatch)
    for tool, args in [("traverse_graph", {"start_kind": "problem", "start_id": "problem-of-another-org"}),
                       ("get_submission_history", {"problem_id": "problem-of-another-org"}),
                       ("get_judge_history", {"problem_id": "problem-of-another-org"})]:
        assert asyncio.run(run.call(tool, args, "t")).status == "denied"
    assert not fake.calls


def test_graph_cannot_bypass_backend_authorization_for_problems(monkeypatch):
    """The graph knows a problem of another contest of the org. It may be NAMED (title only), never opened or analysed."""
    other = [{"id": "p-archive", "title": "Archive Mystery Problem", "aliases": [], "contest_id": "c-archive", "contest": "Archive Contest", "updated_at": "x"}]
    gf.install(monkeypatch, gf.FakeGraph({"entity_problems": other}))
    ctx, r = ask("Tell me about the Archive Mystery Problem")
    assert ctx.problem is None  # not one of the problems the backend returned for this contest
    assert not any(e.kind in ("problem", "submission", "source") for e in ctx.evidence)
    assert any("not of this contest" in m for m in r["missing"])
    assert "MY_MARKER" not in everything(ctx, r)


def test_other_organizations_instructor_is_not_staff_here(monkeypatch):
    fake = gf.install(monkeypatch, gf.FakeGraph())
    ctx, r = ask("Which prerequisite concept gaps are shared by several learners?", me=OUTSIDER_INSTRUCTOR)
    assert ctx.mode == "learner"  # instructor of ANOTHER org, no membership here: treated as a learner
    assert "contest_gaps" not in fake.names() and all(p.get("staff") is False for _, p in fake.calls if "staff" in p)


def test_org_scope_comes_from_the_contest_not_from_the_caller(monkeypatch):
    fake = gf.install(monkeypatch, gf.FakeGraph())
    ask("Give me a hint", me=ALICE, problem_id="p1")
    ask("Give me a hint", me=TEACHER, problem_id="p1")
    assert fake.calls and all(p["org_id"] == "org" for _, p in fake.calls if "org_id" in p)


# ---------------- instructor ----------------

def test_instructor_gets_staff_queries_but_hidden_tests_and_other_orgs_stay_out(monkeypatch):
    run, ctx, fake = make_run(monkeypatch, me=TEACHER, staff=True)
    asyncio.run(run.call("traverse_graph", {"start_kind": "contest"}, "t"))
    asyncio.run(run.call("get_judge_history", {}, "t"))
    assert {"contest_gaps", "judge_versions", "judge_incidents"} <= set(fake.names())
    assert all(p["org_id"] == "org" and p.get("contest_id", "c1") == "c1" for _, p in fake.calls if "org_id" in p)


def test_instructor_sees_incident_details_learners_do_not(monkeypatch):
    incidents = [{"id": "i1", "type": "UNKNOWN", "created": "2026-01-01T00:59:10.000Z", "resolved": None, "message": "SECRET-INCIDENT-MESSAGE", "judge_version": "judge-v2", "submission_id": "s900"}]
    gf.install(monkeypatch, gf.FakeGraph({"judge_incidents": incidents}))
    ctx, r = ask("Is there a judge regression affecting Sum of Two Numbers?", me=TEACHER, problem_id="p1")
    assert "SECRET-INCIDENT-MESSAGE" in everything(ctx, r)
    # the same rows for a learner: the learner path never issues the incident query at all
    fake = gf.install(monkeypatch, gf.FakeGraph({"judge_incidents": incidents}))
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert "SECRET-INCIDENT-MESSAGE" not in everything(ctx, r) and "judge_incidents" not in fake.names()


def test_admin_without_membership_is_staff_for_the_contest(monkeypatch):
    fake = gf.install(monkeypatch, gf.FakeGraph())
    ctx, _ = ask("Which prerequisite concept gaps are shared by several learners?", me=ADMIN)
    assert ctx.mode == "instructor" and "contest_gaps" in fake.names()


# ---------------- data that must never reach the graph or the answers ----------------

@pytest.mark.parametrize("me,question", [(ALICE, "Give me a hint for the Sum problem"), (ALICE, "Why did my latest submission get this verdict?"),
                                         (TEACHER, "Which learners struggle with Sum of Two Numbers?"), (TEACHER, "Is there a judge regression?")])
def test_hidden_test_contents_never_appear_anywhere(monkeypatch, me, question):
    gf.install(monkeypatch, gf.FakeGraph())
    ctx, r = ask(question, me=me, problem_id="p1")
    blob = everything(ctx, r)
    assert not any(m in blob for m in HIDDEN_MARKERS)


def test_other_learners_source_code_never_appears(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph())
    for q in ("Why did my latest submission get this verdict?", "Which concepts am I struggling with?", "Give me a hint"):
        ctx, r = ask(q, problem_id="p1")
        blob = everything(ctx, r)
        assert "BOB_PRIVATE_CODE_MARKER" not in blob
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert "OWN_MARKER_ALICE" in " ".join(e.text for e in ctx.evidence)  # the learner's OWN code is still there
    assert "OWN_MARKER_ALICE" not in str(ctx.agent.public())  # ...but not in the trace


def test_instructor_views_never_include_source_code_via_the_tools(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph())
    ctx, r = ask("Which prerequisite concept gaps are shared by several learners?", me=TEACHER)
    blob = everything(ctx, r)
    assert "OWN_MARKER_ALICE" not in blob and "BOB_PRIVATE_CODE_MARKER" not in blob


def test_no_source_code_or_test_data_in_the_projection():
    """The projector reads the source only as md5(...) and the graph schema has no property that could hold code or tests."""
    src = inspect.getsource(graph_sync.read_postgres)
    for m in re.finditer(r'sourceCode', src):
        assert src[max(0, m.start() - 7):m.start()].endswith('md5(s."'), "sourceCode may only be read through md5()"
    assert "TestCase" not in src and "stdout" not in src.lower() and "compilerOutput" not in src and "passwordHash" not in src and "email" not in src.lower()
    props = " ".join(graph_sync.UPSERT.values()).lower()
    for forbidden in ("source", "input", "expected", "stdout", "stderr", "password", "email", "token", "compiler"):
        assert forbidden not in props.replace("sourcehash", ""), f"graph property mentioning '{forbidden}'"


def test_every_graph_query_is_read_only_and_parameterised():
    write = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL)\b", re.I)
    assert graph_store.READ_QUERIES
    for name, q in graph_store.READ_QUERIES.items():
        assert not write.search(q), name
        assert "'" not in re.sub(r"'(DRAFT|COMPLETED|ACCEPTED)'", "", q), f"{name}: literal values must be parameters"


def test_scoped_queries_are_anchored_to_an_organization():
    """Anything that starts from a user-supplied id must be anchored to the caller's org (or contest) inside the Cypher."""
    for name in ("problem_graph", "similar_problems", "learner_paths", "contest_gaps", "judge_submission", "judge_conflicts", "judge_versions", "judge_incidents", "entity_problems"):
        assert "$org_id" in graph_store.READ_QUERIES[name], name
    for name in ("learner_paths", "contest_gaps", "judge_conflicts", "judge_submission"):
        assert "$user_id" in graph_store.READ_QUERIES[name] or "$staff" not in graph_store.READ_QUERIES[name] or name == "contest_gaps"
    for name in ("judge_submission", "judge_conflicts"):
        assert "$staff OR" in graph_store.READ_QUERIES[name] or "($staff OR" in graph_store.READ_QUERIES[name], name
    for name in ("contest_gaps", "judge_versions", "judge_incidents"):  # staff-only data: the role check is part of the query itself
        assert "WHERE $staff" in graph_store.READ_QUERIES[name], name
    assert "$staff" in graph_store.READ_QUERIES["judge_submission"] and "CASE WHEN $staff" in graph_store.READ_QUERIES["judge_submission"]


def test_graph_read_rejects_query_text_and_unknown_names(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://nowhere:7687")
    with pytest.raises(KeyError):
        asyncio.run(graph_store.read("MATCH (n) DETACH DELETE n"))
    with pytest.raises(KeyError):
        asyncio.run(graph_store.read("problem_graph; MATCH (n) DELETE n"))


def test_tool_evidence_is_capped_per_item(monkeypatch):
    huge = [{**gf.PROBLEM_GRAPH_SUM[0], "concept": "X" * 5000}]
    run, ctx, _ = make_run(monkeypatch, rows={"problem_graph": huge})
    asyncio.run(run.call("traverse_graph", {"start_kind": "problem", "start_id": "p1"}, "t"))
    assert all(len(e.text) <= tools.MAX_ITEM_CHARS for e in ctx.evidence)


def test_a_misbehaving_backend_that_leaks_other_learners_rows_does_not_leak_through_tools(monkeypatch):
    """Defence in depth: even if the backend returned everyone's submissions, learner tools filter to the caller's own."""
    gf.install(monkeypatch, gf.FakeGraph())
    ctx, r = ask("How is my history on Sum of Two Numbers?", problem_id="p1", be=Fake(leak_others=True))
    hist = next((e for e in ctx.evidence if e.kind == "submission-history"), None)
    assert hist is None or "bob" not in hist.text.lower()
    assert "BOB_PRIVATE_CODE_MARKER" not in everything(ctx, r)
