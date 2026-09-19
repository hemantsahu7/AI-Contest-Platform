"""Learner AI reasoning over the learner's OWN submitted code (+ problem, verdict, execution, material).
Deterministic: an in-memory fake backend, the model is either off (evidence-only) or its network call is mocked."""
import asyncio
import json

from app import assistant, gemini
from app.backend import NotAccessible

ORG = "org"
CONTEST = {"id": "c1", "title": "Demo", "status": "RUNNING", "organizationId": ORG, "startTime": "a", "endTime": "b"}
PROBLEM_SUM = {"id": "p1", "title": "Sum of Two Numbers", "difficulty": "EASY", "points": 100, "timeLimitMs": 1000, "memoryLimitMb": 64,
               "description": "Read two integers A and B and print A+B. Constraints: -4000000000 <= A, B <= 4000000000.",
               "inputFormat": "Two integers A and B.", "outputFormat": "A single integer, A+B.",
               "testCases": [{"input": "1 2\n", "expectedOutput": "3\n", "isHidden": False}, {"input": "HIDDEN-IN-777\n", "expectedOutput": "HIDDEN-OUT-777", "isHidden": True}]}
PROBLEM_MUL = {**PROBLEM_SUM, "id": "p2", "title": "Product", "description": "Read two integers and print A*B. Constraints: -1000000000 <= A, B <= 1000000000.", "testCases": []}
MY_CODE = "#include <iostream>\nusing namespace std;\n// OWN_MARKER_ALICE\nint main() {\n  int a, b;\n  cin >> a >> b;\n  cout << a + b << endl;\n}\n"
BOB_CODE = "// BOB_PRIVATE_CODE_MARKER\nint main(){}\n"
ALICE = {"id": "u1", "username": "alice", "role": "LEARNER", "memberships": [{"organizationId": ORG, "role": "LEARNER"}]}
TEACHER = {"id": "t1", "username": "teach", "role": "INSTRUCTOR", "memberships": [{"organizationId": ORG, "role": "INSTRUCTOR"}]}


def sub(i, uid, name, pid, verdict, minute, code, **extra):
    return {"id": f"s{i:03d}-aaaa-bbbb", "userId": uid, "username": name, "problemId": pid, "problem": {"title": "x"}, "status": "COMPLETED", "verdict": verdict,
            "score": 100 if verdict == "ACCEPTED" else 0, "language": "cpp", "submittedAt": f"2026-01-01T00:{minute:02d}:00Z", "completedAt": f"2026-01-01T00:{minute:02d}:05Z",
            "sourceCode": code, "executions": [{"status": "COMPLETED", "verdict": verdict, "testsPassed": 2, "testsTotal": 4, "executionTimeMs": 12, "finishedAt": "z"}], **extra}


def history(n_before=39):
    """alice: n_before earlier attempts on p1 (mostly WA, two AC) then the latest WA; bob has one submission with his own code."""
    subs = [sub(i, "u1", "alice", "p1", "ACCEPTED" if i in (3, 9) else "WRONG_ANSWER", i % 59, "int main(){}") for i in range(n_before)]
    subs.append(sub(900, "u1", "alice", "p1", "WRONG_ANSWER", 59, MY_CODE))
    subs.append(sub(901, "u2", "bob", "p1", "WRONG_ANSWER", 58, BOB_CODE))
    return subs


class Fake:
    def __init__(self, subs=None, problems=None, leak_others=False):
        self.subs = subs if subs is not None else history()
        self.problems = problems or [PROBLEM_SUM, PROBLEM_MUL]
        self.leak_others = leak_others  # simulate a MISBEHAVING backend that returns other learners' rows

    async def get(self, path):
        if path == "/contests/c1":
            return CONTEST
        if path == "/contests/c1/participants":
            return [{"user": {"id": "u1", "username": "alice"}}, {"user": {"id": "u2", "username": "bob"}}]
        if path == "/contests/c1/problems":
            return [dict(p, testCases=[dict(t) for t in p["testCases"]]) for p in self.problems]  # hidden tests included: the AI must drop them
        if path == "/contests/c1/submissions":
            return self.subs if self.leak_others else [s for s in self.subs if s["userId"] == "u1"]
        if path.startswith("/submissions/"):
            found = next((s for s in self.subs if s["id"] == path.rsplit("/", 1)[1]), None)
            if found and (found["userId"] == "u1" or self.leak_others):
                return found
        raise NotAccessible(path)


def ask(question, me=ALICE, be=None, problem_id=None, submission_id=None):
    async def go():
        ctx = await assistant.gather(question, "c1", problem_id, submission_id, be or Fake(), me)
        return ctx, await assistant.answer(ctx)

    return asyncio.run(go())


Q_WHY = "Why did my latest submission get this verdict?"


def by_id(ctx, eid):
    return next((e for e in ctx.evidence if e.id == eid), None)


def test_own_source_code_is_evidence_for_a_verdict_question():
    ctx, r = ask(Q_WHY, problem_id="p1")
    f1 = by_id(ctx, "F1")
    assert f1 and f1.kind == "source" and "OWN_MARKER_ALICE" in f1.text
    assert "  4| int main() {" in f1.text and "  5|   int a, b;" in f1.text  # line-numbered so answers can point at lines
    assert "S1" in {e["id"] for e in r["evidence"]} or r["source"] == "fallback"
    assert by_id(ctx, "P1") and "4000000000" in by_id(ctx, "P1").text  # the problem statement with its constraints travels with the code
    assert by_id(ctx, "X1") and "2/4 tests passed" in by_id(ctx, "X1").text  # execution evidence too


def test_another_learners_code_is_never_evidence_even_if_the_backend_misbehaves():
    ctx, r = ask(Q_WHY, be=Fake(leak_others=True), problem_id="p1")
    blob = json.dumps(r) + " ".join(e.text for e in ctx.evidence)
    assert "BOB_PRIVATE_CODE_MARKER" not in blob
    ctx, r = ask("Why did this submission fail?", be=Fake(leak_others=True), problem_id="p1", submission_id="s901-aaaa-bbbb")  # bob's id
    assert "BOB_PRIVATE_CODE_MARKER" not in json.dumps(r) + " ".join(e.text for e in ctx.evidence)
    assert any("not accessible" in m for m in r["missing"]) and by_id(ctx, "F1") is None
    ctx, r = ask("Why did this submission fail?", problem_id="p1", submission_id="s901-aaaa-bbbb")  # correct backend: 404
    assert by_id(ctx, "F1") is None and any("not accessible" in m for m in r["missing"])


def test_hidden_tests_stay_unavailable():
    ctx, r = ask(Q_WHY, problem_id="p1")
    blob = json.dumps(r) + " ".join(e.text for e in ctx.evidence)
    assert "HIDDEN-OUT-777" not in blob and "HIDDEN-IN-777" not in blob
    assert any("hidden test failed" in m for m in r["missing"])  # and the answer says the exact failing test is unknown


def test_model_receives_code_problem_and_verdict_but_nothing_protected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")
    sent = {}

    async def call(model, system, user_text, schema):
        sent["payload"] = json.loads(user_text)
        sent["system"] = system
        return json.dumps({"answer": "Line 5 of [F1] declares int [H1].", "claims": [{"text": "Overflow is likely", "kind": "hypothesis", "evidence": ["F1", "H1", "P1"]}],
                           "confidence": "medium", "missing": [], "needs_clarification": False}), {}

    monkeypatch.setattr(gemini, "_call", call)
    ctx, r = ask(Q_WHY, problem_id="p1")
    text = json.dumps(sent["payload"])
    assert r["source"] == "llm"
    assert "OWN_MARKER_ALICE" in text and "int a, b;" in text  # own code
    assert "-4000000000 <= A, B <= 4000000000" in text and "WRONG_ANSWER" in text  # problem constraints + verdict
    assert "BOB_PRIVATE_CODE_MARKER" not in text and "HIDDEN-OUT-777" not in text and "HIDDEN-IN-777" not in text
    assert "SOURCE evidence" in sent["system"] and "never write corrected code" in sent["system"]
    assert any(e["kind"] == "source" for e in r["evidence"])  # the cited code is inspectable in the UI


def test_evidence_only_answer_reasons_over_the_code_and_the_constraints():
    ctx, r = ask(Q_WHY, problem_id="p1")
    assert r["source"] == "fallback"
    obs = [c for c in r["claims"] if c["kind"] == "observation"]
    hyp = [c for c in r["claims"] if c["kind"] == "hypothesis"]
    assert any("WRONG_ANSWER" in c["text"] and "S1" in c["evidence"] for c in obs)  # observed: the judge's verdict
    over = next(c for c in hyp if "overflow" in c["text"].lower())
    assert "Line 5" in over["text"] and "8,000,000,000" in over["text"] and {"H1", "F1"} <= set(over["evidence"])  # inferred from code + statement
    assert any("cannot tell which test failed" in c["text"] for c in r["claims"])  # explicit: exact failing test unavailable
    assert r["confidence"] != "high" and any("hidden test failed" in m for m in r["missing"])
    assert "```" not in r["answer"]  # hint policy: no code blocks


def test_history_evidence_is_compact_not_a_raw_dump():
    ctx, _ = ask(Q_WHY, problem_id="p1")
    s1 = by_id(ctx, "S1").text
    assert "Other attempts on this problem: 39 (accepted before: 2)" in s1
    assert "WRONG_ANSWER x37" in s1 and "ACCEPTED x2" in s1
    assert "earlier verdicts" not in s1 and "[" not in s1 and len(s1) < 520  # no 40-element array


def test_hint_about_my_wrong_answer_on_the_contest_page_finds_the_submission_and_problem():
    ctx, r = ask("Give me a hint about my wrong answer")  # no problemId: as asked from the contest page
    assert by_id(ctx, "S1") and by_id(ctx, "F1") and by_id(ctx, "P1")
    assert any(c["kind"] == "hypothesis" and "Where to look in your code" in c["text"] and "Line 5" in c["text"] for c in r["claims"])
    assert not any("def " in c["text"] or "```" in c["text"] for c in r["claims"])  # a pointer, not a solution


def test_review_and_investigate_questions_use_the_code():
    for q in ("Review my latest submission.", "What part of my code should I investigate?"):
        ctx, r = ask(q)
        assert by_id(ctx, "F1") is not None, q
        assert any("Line 5" in c["text"] for c in r["claims"]), q


def test_live_hint_policy_still_strips_replacement_code(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")

    async def call(model, system, user_text, schema):
        return json.dumps({"answer": "Change it:\n```cpp\nlong long a, b;\n```", "claims": [], "confidence": "medium", "missing": [], "needs_clarification": False}), {}

    monkeypatch.setattr(gemini, "_call", call)
    ctx, r = ask("Give me a hint without giving me the solution.", problem_id="p1")
    assert r.get("refusal") is None  # a hint request is answered, not refused
    assert "```" not in r["answer"] and "long long a, b;" not in r["answer"]


def test_runtime_crash_kind_is_evidence_but_raw_output_is_not():
    crash = sub(950, "u1", "alice", "p2", "RUNTIME_ERROR", 30, "int main(){int*p=0;*p=1;}", runtimeSignal="SEGMENTATION_FAULT")
    crash["executions"][0].update(stdout="RAW-STDOUT-SECRET", stderr="RAW-STDERR-SECRET")
    ctx, r = ask(Q_WHY, be=Fake(subs=[crash]), problem_id="p2")
    assert "segmentation fault" in by_id(ctx, "R1").text
    assert "RAW-STDOUT-SECRET" not in json.dumps(r) + " ".join(e.text for e in ctx.evidence) and "RAW-STDERR-SECRET" not in json.dumps(r)


def test_large_source_is_truncated_explicitly():
    big = "\n".join(f"int v{i} = {i};" for i in range(2000))
    ctx, _ = ask(Q_WHY, be=Fake(subs=[sub(1, "u1", "alice", "p1", "WRONG_ANSWER", 1, big)]), problem_id="p1")
    f1 = by_id(ctx, "F1").text
    assert len(f1) < 6300 and "more lines not shown" in f1


def test_instructor_flow_is_unchanged_no_source_evidence_and_aggregate_still_works():
    ctx, r = ask("Summarize verdicts and separate judge errors from code errors", me=TEACHER, be=Fake(leak_others=True))
    assert ctx.mode == "instructor" and by_id(ctx, "A1") is not None
    assert by_id(ctx, "F1") is None and not any(e.kind == "source" for e in ctx.evidence)
    blob = json.dumps(r) + " ".join(e.text for e in ctx.evidence)
    assert "OWN_MARKER_ALICE" not in blob and "BOB_PRIVATE_CODE_MARKER" not in blob and "HIDDEN-OUT-777" not in blob
    ctx, r = ask("Which learners may share a prerequisite gap?", me=TEACHER, be=Fake(leak_others=True))
    assert any(e.kind == "graph-path" for e in ctx.evidence) and not any(e.kind == "source" for e in ctx.evidence)


def test_static_analysis_uses_the_statement_bounds_not_a_hardcoded_problem():
    """Generic: the same code is judged against whatever bounds the statement gives."""
    code = "int main(){int a,b;cin>>a>>b;cout<<a*b;}"
    over = assistant._static_checks(code, PROBLEM_MUL)
    assert any("does not fit" in n and "1,000,000,000,000,000,000" in n for n in over)  # product of two 1e9 values
    fits = assistant._static_checks(code, {**PROBLEM_MUL, "description": "Read two integers and print A*B. Constraints: -1000 <= A, B <= 1000."})
    assert not any("does not fit" in n for n in fits)
    nobound = assistant._static_checks(code, {**PROBLEM_MUL, "description": "Read two integers and print A*B."})
    assert any("no bounds" in n for n in nobound)
    assert assistant._static_checks("int main(){long long a,b;cin>>a>>b;cout<<a*b;}", PROBLEM_MUL) == []
