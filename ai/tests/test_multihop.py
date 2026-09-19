"""Multi-hop (user -> submissions -> problems -> verdict history -> learning material) over authorized data."""
import asyncio

from app import assistant
from app.backend import NotAccessible

ORG = "org"
CONTEST = {"id": "c1", "title": "Demo", "status": "RUNNING", "organizationId": ORG, "startTime": "a", "endTime": "b"}
PROBLEMS = [
    {"id": "p1", "title": "Sum of Two Numbers", "description": "Print the sum A+B of two large integers.", "difficulty": "EASY", "points": 100, "timeLimitMs": 1000, "memoryLimitMb": 64, "inputFormat": "A B", "outputFormat": "A+B", "testCases": [{"input": "1 2\n", "expectedOutput": "3\n", "isHidden": False}, {"input": "9 9\n", "expectedOutput": "HIDDEN-OUT", "isHidden": True}]},
    {"id": "p2", "title": "Maximum of Two", "description": "Print the larger integer.", "difficulty": "EASY", "points": 100, "timeLimitMs": 1000, "memoryLimitMb": 64, "inputFormat": "A B", "outputFormat": "max", "testCases": []},
    {"id": "p3", "title": "Product", "description": "Print A*B.", "difficulty": "MEDIUM", "points": 150, "timeLimitMs": 500, "memoryLimitMb": 64, "inputFormat": "A B", "outputFormat": "A*B", "testCases": []},
]
INT_SRC = "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}"


def sub(i, user, uid, pid, verdict, t, src="int main(){}", status="COMPLETED"):
    return {"id": f"s{i}xxxxxxxx", "userId": uid, "username": user, "problemId": pid, "problem": {"title": next(p["title"] for p in PROBLEMS if p["id"] == pid)}, "status": status, "verdict": verdict, "score": 100 if verdict == "ACCEPTED" else 0,
            "submittedAt": f"2026-01-01T00:0{t}:00Z", "completedAt": f"2026-01-01T00:0{t}:05Z", "sourceCode": src, "executions": []}


SUBS = [
    sub(1, "alice", "u1", "p1", "WRONG_ANSWER", 1, INT_SRC), sub(2, "alice", "u1", "p1", "WRONG_ANSWER", 2, INT_SRC),
    sub(3, "alice", "u1", "p2", "ACCEPTED", 3),
    sub(4, "alice", "u1", "p3", "JUDGE_ERROR", 4, status="INFRASTRUCTURE_ERROR"),
    sub(5, "bob", "u2", "p1", "WRONG_ANSWER", 5, "bob-private-code " + INT_SRC),
]
ALICE = {"id": "u1", "username": "alice", "role": "LEARNER", "memberships": [{"organizationId": ORG, "role": "LEARNER"}]}
TEACHER = {"id": "t1", "username": "teach", "role": "INSTRUCTOR", "memberships": [{"organizationId": ORG, "role": "INSTRUCTOR"}]}


class Fake:
    async def get(self, path):
        if path == "/contests/c1":
            return CONTEST
        if path == "/contests/c1/participants":
            return [{"user": {"id": "u1", "username": "alice"}}, {"user": {"id": "u2", "username": "bob"}}]
        if path == "/contests/c1/problems":
            return [dict(p, testCases=list(p["testCases"])) for p in PROBLEMS]
        if path == "/contests/c1/submissions":
            return SUBS  # the real backend would filter this by user; tests below use the learner-filtered variant
        raise NotAccessible(path)


class LearnerFake(Fake):
    async def get(self, path):
        if path == "/contests/c1/submissions":
            return [s for s in SUBS if s["userId"] == "u1"]
        return await super().get(path)


def ask(question, me, be):
    async def go():
        ctx = await assistant.gather(question, "c1", None, None, be, me)
        return ctx, await assistant.answer(ctx)

    return asyncio.run(go())


Q = "What problems have I struggled with, what verdicts did I receive, and what should I study?"


def test_learner_multi_hop_combines_submissions_problems_verdicts_and_material():
    ctx, r = ask(Q, ALICE, LearnerFake())
    kinds = {e["kind"] for e in r["evidence"]}
    assert {"graph-path", "material"} <= kinds
    text = " ".join(c["text"] for c in r["claims"])
    assert "Sum of Two Numbers" in text and "WRONG_ANSWER -> WRONG_ANSWER" in text  # hop: submissions -> problem -> verdict history
    assert any(c["kind"] == "hypothesis" and "Study next" in c["text"] and any(i.startswith("M") for i in c["evidence"]) for c in r["claims"])  # hop: problem -> material
    assert "Maximum of Two" not in text  # accepted on first try: not a struggle
    assert "Product" not in text  # only a judge error: infrastructure failures are not the learner's struggle
    assert any("overflow" in c["text"] for c in r["claims"])  # own-source static pattern
    path = next(e for e in r["evidence"] if e["kind"] == "graph-path")["text"]
    assert "-SUBMITTED->" in path and "-FOR->" in path and "-RELATED_TO->" in path


def test_multi_hop_never_uses_other_learners_data_or_hidden_tests():
    ctx, r = ask(Q, ALICE, LearnerFake())
    blob = str(r) + str([e.text for e in ctx.evidence])
    assert "bob" not in blob and "bob-private-code" not in blob and "HIDDEN-OUT" not in blob


def test_instructor_shared_gap_hypothesis_groups_learners():
    ctx, r = ask("Which learners may share a prerequisite gap despite different failed submissions?", TEACHER, Fake())
    assert ctx.shared and set(ctx.shared[0]["who"]) >= {"alice (Sum of Two Numbers)", "bob (Sum of Two Numbers)"}
    assert any(c["kind"] == "hypothesis" and "shared prerequisite gap" in c["text"] for c in r["claims"])
    assert "HIDDEN-OUT" not in str(r) and "bob-private-code" not in str(r)  # source code never enters staff evidence either
    assert any("not a stored knowledge graph" in m for m in r["missing"])  # honest about what the relationships are


class EmptyFake(Fake):
    async def get(self, path):
        if path == "/contests/c1/submissions":
            return []
        return await super().get(path)


def test_no_history_says_so():
    newbie = {"id": "u9", "username": "newbie", "role": "LEARNER", "memberships": [{"organizationId": ORG, "role": "LEARNER"}]}
    ctx, r = ask(Q, newbie, EmptyFake())
    assert not r["claims"] and r["confidence"] == "low" and any("no submissions" in m.lower() for m in r["missing"])
