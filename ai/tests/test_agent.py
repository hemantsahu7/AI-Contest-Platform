"""Bounded agent + GraphRAG behaviour. Deterministic: fake backend, recording fake graph, model off.
Covers tool selection, multiple calls, stop conditions, the step limit, empty results, tool errors/timeouts, unanswerable and
under-evidenced questions, clarification, graph contribution to retrieval, multi-hop paths and stale/conflicting evidence."""
import asyncio

import pytest

from app import agent, assistant, gemini, graph_store, knowledge, tools
from app.retrieval import MaterialIndex, load_materials

from . import graph_fakes as gf
from .test_learner_code import ALICE, PROBLEM_MUL, PROBLEM_SUM, TEACHER, Fake, history

CONCEPT_STEP = ("traverse_graph", "get_judge_history", "search_learning_material")


def ask(question, me=ALICE, be=None, problem_id=None, submission_id=None):
    async def go():
        ctx = await assistant.gather(question, "c1", problem_id, submission_id, be or Fake(), me)
        return ctx, await assistant.answer(ctx)

    return asyncio.run(go())


def tools_of(ctx):
    return [s.tool for s in ctx.agent.steps]


def statuses(ctx):
    return {s.tool: s.status for s in ctx.agent.steps}


@pytest.fixture
def graph(monkeypatch):
    return gf.install(monkeypatch, gf.FakeGraph())


# ---------------- tool selection, multiple calls, GraphRAG contribution ----------------

def test_policy_picks_graph_judge_then_graph_expanded_search(graph):
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert tools_of(ctx) == list(CONCEPT_STEP)  # three different read-only tools, each once, chosen from the observations
    assert ctx.agent.stop_reason == "planner_done" and set(statuses(ctx).values()) == {"ok"}
    assert "problem_graph" in graph.names() and "judge_submission" in graph.names() and "concept_graph" in graph.names()
    kinds = {e.kind for e in ctx.evidence}
    assert {"graph", "judge-history", "material"} <= kinds
    assert r["source"] == "fallback" and any(c["kind"] == "observation" and "knowledge graph links" in c["text"] for c in r["claims"])


def test_the_search_step_is_seeded_by_what_the_graph_found(graph):
    ctx, _ = ask("Why did my latest submission get this verdict?", problem_id="p1")
    step = next(s for s in ctx.agent.steps if s.tool == "search_learning_material")
    assert step.args["query"] == "Integer types and ranges"  # the prerequisite concept surfaced by traverse_graph
    assert step.args["concept_ids"] == ["integer-types"]


def test_graph_contributes_material_that_text_retrieval_would_not_return(monkeypatch):
    """The graph says the problem's concept is covered by the time-limit note; the question text has nothing to do with it."""
    only_graph = [{**gf.PROBLEM_GRAPH_SUM[0], "materials": [{**gf.MAT_OVERFLOW, "id": "time-limit", "covers": "integer-overflow"}]}]
    gf.install(monkeypatch, gf.FakeGraph({"problem_graph": only_graph}))
    ctx, r = ask("Give me a hint", problem_id="p1")
    m = [e for e in ctx.evidence if e.kind == "material"]
    assert any(e.ref == "materials/time-limit.md" and "knowledge graph" in (e.via or "") for e in m)
    assert "neo4j graph" in ctx.retrieval
    bm25_only = [d.id for d, _ in assistant.INDEX.search("Give me a hint Sum of Two Numbers", top_k=3)]
    assert "time-limit" not in bm25_only  # the evidence really came from the graph, not from text retrieval


def test_without_a_graph_the_answer_is_unchanged_and_no_graph_tool_runs(monkeypatch):
    monkeypatch.setattr(graph_store, "configured", lambda: False)
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert "traverse_graph" not in tools_of(ctx) and "get_judge_history" not in tools_of(ctx)
    assert not any(e.kind in ("graph", "judge-history") for e in ctx.evidence)
    assert "graph" not in ctx.retrieval
    assert r["claims"] and not any("Neo4j" in m or "knowledge graph could not" in m for m in r["missing"])  # not configured != failing


def test_multi_hop_learner_failures_to_prerequisite_to_material(graph):
    """user -> submissions -> problems -> concepts -> prerequisite concept -> learning material (5 hops), from the Neo4j rows."""
    ctx, r = ask("Which concepts am I struggling with and what should I study first?")
    assert "traverse_graph" in tools_of(ctx)
    n1 = next(e for e in ctx.evidence if e.kind == "graph")
    assert "Integer overflow" in n1.text and "Integer types and ranges" in n1.text
    text = " ".join(c["text"] for c in r["claims"])
    assert "'Product', 'Sum of Two Numbers' all depend on the concept 'Integer overflow'" in text.replace('"', "'") or ("Product" in text and "Integer overflow" in text)
    assert any(c["kind"] == "hypothesis" and "Integer types and ranges" in c["text"] for c in r["claims"])  # a conclusion is a hypothesis
    assert graph.calls[0][1]["user_id"] == ALICE["id"]


def test_instructor_shared_prerequisite_gap_is_a_hypothesis(graph):
    ctx, r = ask("Which prerequisite concept gaps are shared by several learners?", me=TEACHER)
    call = next(c for c in graph.calls if c[0] == "contest_gaps")
    assert call[1]["contest_id"] == "c1"
    gap = next(c for c in r["claims"] if "Integer types and ranges" in c["text"])
    assert gap["kind"] == "hypothesis" and "shared prerequisite gap" in gap["text"] and "alice, bob" in gap["text"]
    assert not any("Only" in c["text"] and "Loops" in c["text"] and c["kind"] == "observation" for c in r["claims"])


def test_conflicting_judge_evidence_is_reported_with_versions_and_timestamps(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph({"judge_conflicts": gf.JUDGE_CONFLICT}))
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    text = " ".join(c["text"] for c in r["claims"])
    assert "CONFLICT" in text and "judge-v1" in text and "judge-v2" in text and "2026-01-01T00:03:00" in text and "2026-01-01T00:59:00" in text
    assert "identical" in text  # same source hash, so the code is not the cause
    assert "authoritative" in text and "rejudge" in text  # the assistant does not pick a winner or change the verdict
    assert r["claims"] and all(c["evidence"] for c in r["claims"] if c["kind"] == "observation")


def test_problem_revised_after_the_submission_is_flagged(monkeypatch):
    rows = [{**gf.JUDGE_SUB[0], "problem_updated_at": "2026-01-01T00:59:30.000Z"}]  # revised 30 s after the submission
    gf.install(monkeypatch, gf.FakeGraph({"judge_submission": rows}))
    _, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert any("revised at 2026-01-01T00:59:30" in c["text"] and "may differ" in c["text"] for c in r["claims"])


def test_stale_material_is_demoted_and_its_replacement_is_preferred(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph({"problem_graph": [{**gf.PROBLEM_GRAPH_SUM[0], "concept": "Input and output formatting", "concept_id": "input-output",
                                                             "prereqs": [], "materials": [gf.MAT_LEGACY]}]}))
    ctx, _ = ask("Give me a hint", problem_id="p1")
    mats = [e for e in ctx.evidence if e.kind == "material"]
    ids = [e.ref for e in mats]
    assert "materials/cpp-input-output.md" in ids  # the superseding note is pulled in
    assert ids.index("materials/cpp-input-output.md") < ids.index("materials/legacy-cpp-io.md") if "materials/legacy-cpp-io.md" in ids else True
    legacy = next((e for e in mats if e.ref == "materials/legacy-cpp-io.md"), None)
    assert legacy is None or (legacy.stale and "DEPRECATED" in legacy.title)


def test_concept_and_material_questions_use_entity_resolution_and_freshness(graph):
    ctx, r = ask("Is the legacy C++ I/O note outdated?")
    assert tools_of(ctx)[0] == "resolve_entity" and "traverse_graph" in tools_of(ctx)
    text = " ".join(c["text"] for c in r["claims"])
    assert "deprecated" in text and "prefer the newer note" in text
    assert graph.calls[0][0] == "entity_problems" or "concept_graph" in graph.names()


def test_alias_resolves_an_old_problem_name_as_the_first_agent_step(graph, monkeypatch):
    async def aliases():
        return {"problem": {"p1": ["Add Two Numbers", "A plus B"]}, "concept": {}, "material": {}}

    monkeypatch.setattr(knowledge, "load_aliases", aliases)  # in the stack these come from PostgreSQL (ai_entity_aliases)
    ctx, r = ask("How do I approach A plus B?")
    assert ctx.problem and ctx.problem["id"] == "p1"
    first = ctx.agent.steps[0]
    assert first.tool == "resolve_entity" and first.status == "ok"
    assert any("resolved to the problem 'Sum of Two Numbers'" in c["text"] for c in r["claims"])


def test_similar_and_duplicate_problem_names_are_reported(graph):
    _, r = ask("Give me a hint", problem_id="p1")
    assert any("'A + B'" in c["text"] and "duplicate" in c["text"] for c in r["claims"])


# ---------------- stop conditions and limits ----------------

class ScriptedPlanner:
    name = "policy"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def next(self, run):
        return self.decisions.pop(0) if self.decisions else None


def make_run(monkeypatch, fake=None, staff=False, me=ALICE):
    gf.install(monkeypatch, fake or gf.FakeGraph())
    ctx = assistant.Ctx(question="q", problems=[dict(PROBLEM_SUM), dict(PROBLEM_MUL)])
    ctx.problem = ctx.problems[0]
    contest = {"id": "c1", "title": "Demo", "organizationId": "org", "status": "RUNNING"}
    subs = [s for s in history() if s["userId"] == me["id"]]
    tc = tools.ToolContext(ctx, me, contest, staff, subs)
    return agent.AgentRun(tc), ctx


def search(q):
    return {"tool": "search_learning_material", "args": {"query": q}, "why": "t"}


def test_step_limit_is_enforced_by_the_loop_not_the_planner(monkeypatch):
    monkeypatch.setenv("AI_AGENT_MAX_STEPS", "3")
    run, _ = make_run(monkeypatch)
    endless = ScriptedPlanner([search(f"overflow {i}") for i in range(50)])
    asyncio.run(run.loop(endless))
    assert len(run.steps) == 3 and run.stop_reason == "max_steps"
    assert asyncio.run(run.call("search_learning_material", {"query": "one more"}, "t")) is None  # a 4th call is refused outright
    assert len(run.steps) == 3


def test_step_limit_default_is_four_and_clamped(monkeypatch):
    assert agent.max_steps() == 4
    monkeypatch.setenv("AI_AGENT_MAX_STEPS", "99")
    assert agent.max_steps() == 6
    monkeypatch.setenv("AI_AGENT_MAX_STEPS", "0")
    assert agent.max_steps() == 1
    monkeypatch.setenv("AI_AGENT_MAX_STEPS", "junk")
    assert agent.max_steps() == 4


def test_full_answer_never_uses_more_than_max_steps(graph):
    ctx, _ = ask("Give me a hint for the Sum of Two Numbers problem", problem_id="p1")
    assert len(ctx.agent.steps) <= agent.max_steps()


def test_empty_results_are_recorded_and_two_in_a_row_stop_the_loop(monkeypatch):
    run, ctx = make_run(monkeypatch, gf.FakeGraph({"problem_graph": [], "similar_problems": []}))
    planner = ScriptedPlanner([{"tool": "traverse_graph", "args": {"start_kind": "problem", "start_id": "p1"}, "why": "t"},
                               {"tool": "get_submission_history", "args": {"problem_id": "p2"}, "why": "t"},  # no attempts on p2: empty too
                               search("overflow")])
    asyncio.run(run.loop(planner))
    assert [s.status for s in run.steps] == ["empty", "empty"] and run.stop_reason == "no_progress"
    assert any("no concept links" in m.lower() or "knowledge graph has no concept links" in m for m in ctx.missing)


def test_a_failing_graph_is_a_recorded_error_not_a_crash(monkeypatch):
    run, ctx = make_run(monkeypatch, gf.FakeGraph(fail="graph store unavailable (ServiceUnavailable)"))
    step = asyncio.run(run.call("traverse_graph", {"start_kind": "problem", "start_id": "p1"}, "t"))
    assert step.status == "error" and "unavailable" in step.summary
    assert any("knowledge graph could not be queried" in m for m in ctx.missing)  # the answer will say what is missing
    assert not [e for e in ctx.evidence if e.kind == "graph"]


def test_the_whole_answer_survives_a_graph_outage(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph(fail="graph store unavailable (ServiceUnavailable)"))
    ctx, r = ask("Why did my latest submission get this verdict?", problem_id="p1")
    assert r["claims"] and r["source"] == "fallback"  # the ordinary evidence-only answer is still produced
    assert any("knowledge graph could not be queried" in m for m in r["missing"]) and r["confidence"] != "high"
    assert {s.status for s in ctx.agent.steps} <= {"error", "timeout"} and ctx.agent.stop_reason == "no_progress"


def test_a_slow_tool_times_out(monkeypatch):
    monkeypatch.setenv("AI_AGENT_TOOL_TIMEOUT_S", "0.5")
    run, ctx = make_run(monkeypatch, gf.FakeGraph(delay=5))
    step = asyncio.run(run.call("traverse_graph", {"start_kind": "problem", "start_id": "p1"}, "t"))
    assert step.status == "timeout" and step.ms < 2500
    assert any("timed out" in m for m in ctx.missing)


def test_an_unexpected_exception_in_a_tool_is_contained(monkeypatch):
    run, ctx = make_run(monkeypatch)

    async def boom(tc, args):
        raise ZeroDivisionError("secret internal detail")

    monkeypatch.setitem(tools.TOOLS, "search_learning_material", tools.Tool("search_learning_material", "x", {"query": ("str", 1, 300)}, ("query",), boom))
    step = asyncio.run(run.call("search_learning_material", {"query": "x"}, "t"))
    assert step.status == "error" and "secret internal detail" not in step.summary and "ZeroDivisionError" in step.summary
    assert all("secret internal detail" not in m for m in ctx.missing)


def test_an_invented_tool_is_rejected_and_still_costs_a_step(monkeypatch):
    run, _ = make_run(monkeypatch)
    for name in ("run_cypher", "drop_database", "submit_code", "rejudge_submission", "__import__", ""):
        step = asyncio.run(run.call(name, {"query": "MATCH (n) DETACH DELETE n"}, "t"))
        assert step is None or step.status == "rejected"
    assert all(s.status == "rejected" for s in run.steps) and len(run.steps) == agent.max_steps()
    assert not run.tc.ctx.evidence


@pytest.mark.parametrize("tool,args", [
    ("traverse_graph", {"start_kind": "problem", "cypher": "MATCH (n) DETACH DELETE n"}),  # query text is not an argument
    ("traverse_graph", {"start_kind": "everything"}),
    ("traverse_graph", {"start_kind": "problem", "start_id": "p1' OR 1=1 //"}),
    ("search_learning_material", {"query": "x" * 301}),
    ("search_learning_material", {"query": ""}),
    ("search_learning_material", {"query": "ok", "concept_ids": ["a", "b", "c", "d"]}),
    ("get_judge_history", {"submission_id": ["s1"]}),
    ("resolve_entity", {"name": "x", "kind": "everything"}),
    ("get_submission_history", "p1"),
])
def test_malformed_arguments_are_rejected_before_anything_runs(monkeypatch, tool, args):
    fake = gf.FakeGraph()
    run, _ = make_run(monkeypatch, fake)
    step = asyncio.run(run.call(tool, args, "t"))
    assert step.status == "rejected" and not fake.calls


def test_duplicate_calls_are_refused(monkeypatch):
    run, _ = make_run(monkeypatch)
    a = asyncio.run(run.call("search_learning_material", {"query": "overflow"}, "t"))
    b = asyncio.run(run.call("search_learning_material", {"query": "overflow"}, "t"))
    assert a.status in ("ok", "empty") and b.status == "duplicate"


def test_evidence_budget_stops_the_loop(monkeypatch):
    monkeypatch.setattr(agent, "MAX_EVIDENCE_CHARS", 50)
    run, ctx = make_run(monkeypatch)
    asyncio.run(run.call("traverse_graph", {"start_kind": "problem", "start_id": "p1"}, "t"))
    assert asyncio.run(run.call("get_submission_history", {"problem_id": "p1"}, "t")) is None
    assert run.stop_reason == "evidence_cap" and len(run.steps) == 1


def test_the_agent_loop_is_not_reentrant(monkeypatch):
    run, _ = make_run(monkeypatch)

    async def sneaky(tc, args):
        await run.call("search_learning_material", {"query": "again"}, "recursion")

    monkeypatch.setitem(tools.TOOLS, "resolve_entity", tools.Tool("resolve_entity", "x", {"name": ("str", 1, 300)}, ("name",), sneaky))
    step = asyncio.run(run.call("resolve_entity", {"name": "x"}, "t"))
    assert step.status == "error" and len(run.steps) == 1  # the nested call failed instead of starting a second loop


def test_the_registry_is_exactly_five_read_only_tools():
    assert set(tools.TOOLS) == {"resolve_entity", "search_learning_material", "get_submission_history", "get_judge_history", "traverse_graph"}
    assert all(n not in " ".join(tools.TOOLS) for n in ("write", "delete", "submit", "update", "create", "execute", "cypher"))


# ---------------- answers: unanswerable, insufficient, clarification ----------------

def test_unanswerable_question_gets_no_invented_graph_claims(graph):
    ctx, r = ask("What is the weather today?")
    assert r["confidence"] == "low" and not r["claims"] and "don't have enough evidence" in r["answer"]
    assert not any(e.kind in ("graph", "judge-history") for e in ctx.evidence)
    assert [s.tool for s in ctx.agent.steps] == ["resolve_entity"] and ctx.agent.steps[0].status == "empty"


def test_a_problem_without_concept_links_is_reported_as_insufficient_graph_evidence(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph({"problem_graph": [], "similar_problems": []}))
    ctx, r = ask("Give me a hint", problem_id="p1")
    assert not any(e.kind == "graph" for e in ctx.evidence)
    assert any("no concept links" in m for m in r["missing"])
    assert r["claims"]  # the ordinary hint is still given


def test_ambiguous_problem_name_asks_for_clarification(monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph())
    squares, cubes = dict(PROBLEM_MUL, id="p3", title="Sum of Squares"), dict(PROBLEM_MUL, id="p4", title="Sum of Cubes")
    ctx, r = ask("Help me with the sum problem", be=Fake(problems=[squares, cubes]))
    assert r["needs_clarification"] and r["confidence"] == "low" and "Which one do you mean" in r["answer"]
    assert not any(e.kind == "graph" for e in ctx.evidence)


# ---------------- planners ----------------

def test_llm_planner_falls_back_to_the_policy_when_the_model_is_unavailable(monkeypatch):
    monkeypatch.setenv("AI_AGENT_PLANNER", "llm")
    monkeypatch.setenv("AI_MODEL_DISABLED", "1")
    gf.install(monkeypatch, gf.FakeGraph())
    ctx, _ = ask("Give me a hint", problem_id="p1")
    assert ctx.agent.planner_name == "llm"
    assert ctx.agent.steps and all(s.planner == "policy (model unavailable)" for s in ctx.agent.steps)


def test_llm_planner_cannot_call_tools_outside_the_allowlist(monkeypatch):
    monkeypatch.setenv("AI_AGENT_PLANNER", "llm")
    fake = gf.install(monkeypatch, gf.FakeGraph())
    replies = iter([{"tool": "drop_database", "args": {}, "reason": "x"}, {"tool": "traverse_graph", "args": {"start_kind": "problem", "query": "MATCH (n) DELETE n"}, "reason": "y"}])

    async def fake_generate(system, payload, schema=None, key="answer"):
        if key != "reason":  # the final answer call: model unavailable, evidence-only answer
            raise gemini.LLMUnavailable("disabled", "off")
        assert "tools" in payload
        return next(replies), {}, "fake-model"

    monkeypatch.setattr(gemini, "generate", fake_generate)
    ctx, _ = ask("Give me a hint", problem_id="p1")
    assert [s.status for s in ctx.agent.steps] == ["rejected", "rejected"] and ctx.agent.stop_reason == "no_progress"
    assert not fake.calls  # nothing reached Neo4j


def test_llm_planner_can_stop_early(monkeypatch):
    monkeypatch.setenv("AI_AGENT_PLANNER", "llm")
    gf.install(monkeypatch, gf.FakeGraph())

    async def fake_generate(system, payload, schema=None, key="answer"):
        if key != "reason":
            raise gemini.LLMUnavailable("disabled", "off")
        return {"tool": "stop", "reason": "enough"}, {}, "fake-model"

    monkeypatch.setattr(gemini, "generate", fake_generate)
    ctx, _ = ask("Give me a hint", problem_id="p1")
    assert ctx.agent.steps == [] and ctx.agent.stop_reason == "planner_done"


def test_the_trace_is_returned_in_a_compact_public_form(graph):
    ctx, _ = ask("Why did my latest submission get this verdict?", problem_id="p1")
    pub = ctx.agent.public()
    assert pub["maxSteps"] == 4 and pub["planner"] == "policy" and pub["stopReason"] == "planner_done"
    assert [s["tool"] for s in pub["steps"]] == list(CONCEPT_STEP)
    assert all({"step", "tool", "args", "status", "summary", "ms", "evidence"} <= set(s) for s in pub["steps"])
    assert "sourceCode" not in str(pub) and "OWN_MARKER" not in str(pub)


def test_material_index_used_by_fusion_is_the_live_index(monkeypatch):
    """Runtime-added materials (POST /ai/knowledge/materials) reach fusion through assistant.INDEX."""
    docs = load_materials()
    extra = type(docs[0])(id="new-note", title="Brand new note", tags=["overflow"], updated="2026-09-01", status="current", superseded_by=None,
                          body="A note that did not exist at start-up about overflow.", concepts=["integer-overflow"])
    extra.tokens = ["brand", "new", "note", "overflow"]
    monkeypatch.setattr(assistant, "INDEX", MaterialIndex(docs + [extra]))
    rows = [{**gf.PROBLEM_GRAPH_SUM[0], "materials": [{**gf.MAT_OVERFLOW, "id": "new-note"}]}]
    gf.install(monkeypatch, gf.FakeGraph({"problem_graph": rows}))
    ctx, _ = ask("Give me a hint", problem_id="p1")
    assert any(e.ref == "materials/new-note.md" for e in ctx.evidence)
