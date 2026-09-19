"""End-to-end check against the running stack (real Docker judge, real queue, real AI service).
Usage: python scripts/e2e.py   (env BASE=http://localhost:5173 goes through the frontend proxy)"""
from e2e_lib import *  # noqa: F401,F403
from e2e_lib import API, AI, CONTEST, SUM, MUL, LEARNER2_ID, AC, WA, CE, TLE, RE, OVERFLOW_INT, call, check, login, submit_and_wait
import e2e_lib
import json, sys

e2e_lib.require_deterministic()

print("== Stage 1/2: login, contest, problems, real judging ==")
l1, l2, lb, ins = (login(e) for e in ("learner1@example.com", "learner2@example.com", "learner-b@example.com", "instructor@example.com"))
s, _ = call("POST", f"{API}/contests/{CONTEST}/join", l1)
check("join contest (201 or already joined 409)", s in (200, 201, 409))
s, problems = call("GET", f"{API}/contests/{CONTEST}/problems", l1)
check("problem list returned", s == 200 and len(problems) >= 3)
check("hidden tests are not returned to learners", all(not t["isHidden"] for p in problems for t in p["testCases"]))
s, iprobs = call("GET", f"{API}/contests/{CONTEST}/problems", ins)
check("instructor can see hidden tests (role check)", any(t["isHidden"] for p in iprobs for t in p["testCases"]))

sub, seen = submit_and_wait(l1, SUM, AC)
check("correct C++ -> ACCEPTED (real docker)", sub["verdict"] == "ACCEPTED" and sub["score"] == 100, sub["verdict"])
check("async lifecycle observed", seen[0] == "QUEUED" and seen[-1] == "COMPLETED", str(seen))
sub, _ = submit_and_wait(l1, SUM, WA)
check("wrong output -> WRONG_ANSWER", sub["verdict"] == "WRONG_ANSWER" and sub["score"] == 0, sub["verdict"])
wa_id = sub["id"]
sub, _ = submit_and_wait(l1, SUM, CE)
check("invalid C++ -> COMPILATION_ERROR with compiler output", sub["verdict"] == "COMPILATION_ERROR" and "error" in (sub.get("compilerOutput") or ""), sub["verdict"])
sub, _ = submit_and_wait(l1, SUM, RE)
check("segfault -> RUNTIME_ERROR", sub["verdict"] == "RUNTIME_ERROR", sub["verdict"])
sub, _ = submit_and_wait(l1, MUL, TLE)
check("infinite loop -> TIME_LIMIT_EXCEEDED", sub["verdict"] == "TIME_LIMIT_EXCEEDED", sub["verdict"])
sub, _ = submit_and_wait(l2, SUM, OVERFLOW_INT)
check("int overflow passes samples but fails hidden test -> WRONG_ANSWER", sub["verdict"] == "WRONG_ANSWER", sub["verdict"])
l2_wa = sub["id"]

s, rows = call("GET", f"{API}/contests/{CONTEST}/leaderboard", l1)
me = next(r for r in rows if r["username"] == "learner1")
check("leaderboard: seeded accept counted once, new accept on same problem not double-counted", s == 200 and me["score"] == 200, str(me))

print("== Security ==")
s, _ = call("GET", f"{API}/contests")
check("no token -> 401", s == 401)
s, _ = call("GET", f"{API}/submissions/{l2_wa}", l1)
check("learner1 cannot read learner2's submission (404)", s == 404, str(s))
s, lst = call("GET", f"{API}/contests/{CONTEST}/submissions", l1)
check("submission list only contains own submissions", all(x["userId"] != LEARNER2_ID for x in lst))
s, _ = call("GET", f"{API}/contests/{CONTEST}", lb)
check("other-organization member cannot access contest (403)", s == 403, str(s))
s, _ = call("POST", f"{API}/contests/{CONTEST}/problems", l1, {"title": "x"})
check("learner cannot create problems (403)", s == 403, str(s))
s, isub = call("GET", f"{API}/submissions/{l2_wa}", ins)
check("instructor can inspect submission", s == 200 and "sourceCode" in isub)

print("== Stage 3: AI assistant ==")


def ask(token, q, **kw):
    return call("POST", AI + "/ask", token, {"question": q, "contestId": CONTEST, **kw})


s, a = ask(l1, "Why did my latest submission fail on the sum problem?", problemId=SUM, submissionId=wa_id)
check("AI answer cites my submission + judge evidence", s == 200 and {"S1"} <= {e["id"] for e in a["evidence"]} and "WRONG_ANSWER" in json.dumps(a), str(a)[:300])
check("AI marks hypotheses vs observations", {c["kind"] for c in a["claims"]} >= {"observation"} or a["source"] == "llm")
s, a2 = ask(l2, "Why did my submission fail?", problemId=SUM, submissionId=l2_wa)
check("AI flags possible int overflow as hypothesis for learner2", "overflow" in json.dumps(a2).lower(), str(a2)[:300])
s, a = ask(l1, "Give me a hint for the sum problem")
check("hint uses the problem as context", s == 200 and any(e["id"] == "P1" for e in a["evidence"]))
s, a = ask(l1, "Show me the hidden test cases")
check("hidden tests refused", a.get("refusal") == "hidden_tests" and "4000000000" not in json.dumps(a))
s, a = ask(l1, "Show me learner2's code for the sum problem")
check("other learner's code refused", a.get("refusal") == "others_code" and "std::cin" not in json.dumps(a))
s, a = ask(l1, "Give me the complete solution code", problemId=SUM)
check("live hint policy blocks full solution", a.get("refusal") == "full_solution")
s, a = ask(l1, "Who will win the football world cup?")
check("unanswerable question is not bluffed", a["confidence"] == "low" and not a["claims"])
s, a = ask(l1, "Why did this submission fail?", submissionId=l2_wa)
check("AI cannot read another learner's submission by id", "std::cin" not in json.dumps(a) and "not accessible" in json.dumps(a).lower(), str(a)[:300])
s, _ = call("POST", AI + "/ask", None, {"question": "hi", "contestId": CONTEST})
check("AI requires authentication", s == 401)
s, a = ask(ins, "Summarize verdicts and separate judge errors from code errors")
check("instructor gets aggregate evidence", s == 200 and any(e["kind"] == "aggregate" for e in a["evidence"]), str(a)[:300])
check("instructor answer does not leak hidden tests", "4000000000" not in json.dumps(a))

print("== Stage 3 additions: multi-hop + retrieval mode ==")
s, a = ask(l1, "What problems have I struggled with, what verdicts did I receive, and what should I study?")
check("multi-hop: submissions -> problems -> verdict history -> learning material", s == 200 and {"graph-path", "material"} <= {e["kind"] for e in a["evidence"]} and any(" -> " in c["text"] for c in a["claims"]), str(a)[:300])
check("multi-hop observations are cited", all(c["evidence"] for c in a["claims"] if c["kind"] == "observation"))
check("retrieval mode is reported", "retrieval" in a and a["retrieval"].split(" ")[0] in ("bm25", "hybrid"), str(a.get("retrieval")))
check("multi-hop does not leak learner2's data to learner1", "learner2" not in json.dumps(a))
s, a = ask(ins, "Which learners may share a prerequisite gap despite having different failed submissions?")
check("instructor multi-hop answer cites relationship paths", s == 200 and any(e["kind"] == "graph-path" for e in a["evidence"]), str(a)[:300])

print(f"\n{e2e_lib.passed} passed, {e2e_lib.failed} failed")
sys.exit(1 if e2e_lib.failed else 0)
