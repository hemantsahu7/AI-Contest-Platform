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

_s, _rows = call("GET", f"{API}/contests/{CONTEST}/leaderboard", l1)
score_before = next(r["score"] for r in _rows if r["username"] == "learner1")  # relative check: independent of what was solved before this run
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
re_id = sub["id"]
sub, _ = submit_and_wait(l1, MUL, TLE)
check("infinite loop -> TIME_LIMIT_EXCEEDED", sub["verdict"] == "TIME_LIMIT_EXCEEDED", sub["verdict"])
sub, _ = submit_and_wait(l2, SUM, OVERFLOW_INT)
check("int overflow passes samples but fails hidden test -> WRONG_ANSWER", sub["verdict"] == "WRONG_ANSWER", sub["verdict"])
l2_wa = sub["id"]

s, rows = call("GET", f"{API}/contests/{CONTEST}/leaderboard", l1)
me = next(r for r in rows if r["username"] == "learner1")
check("leaderboard: an accept on an already-solved problem and non-accepted attempts do not change the score (no double counting)", s == 200 and me["score"] == score_before, f"before={score_before} after={me['score']}")

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

print("== Learner AI reasons over the learner's OWN submitted code ==")
import time as _t
MARKER = f"E2E_OWN_CODE_{int(_t.time())}"
INTCODE = f"#include <iostream>\n// {MARKER}\nint main(){{int a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}}"
sub_i, _ = submit_and_wait(l1, SUM, INTCODE)
check("int-based solution fails the hidden overflow test (WRONG_ANSWER)", sub_i["verdict"] == "WRONG_ANSWER", sub_i["verdict"])
s, own = call("GET", f"{API}/submissions/{sub_i['id']}", l1)
check("learner can read their own submission's source via the API (backend owns the ownership check)", s == 200 and own.get("sourceCode") == INTCODE)
s, own_re = call("GET", f"{API}/submissions/{re_id}", l1)
check("crashed submission exposes only a crash kind, never raw runtime output",
      own_re.get("runtimeSignal") in ("SEGMENTATION_FAULT", "FLOATING_POINT_EXCEPTION", "ABORTED_OR_UNCAUGHT_EXCEPTION", "KILLED", "NON_ZERO_EXIT")
      and all(e.get("stderr") is None for e in own_re.get("executions", [])), str(own_re)[:200])
s, a = ask(l1, "Why did my latest submission get this verdict?", problemId=SUM)
src = [e for e in a["evidence"] if e["kind"] == "source"]
check("AI evidence contains the learner's own line-numbered source", s == 200 and bool(src) and MARKER in src[0]["text"] and "  3| int main" in src[0]["text"], str(a)[:200])
check("AI reasons from code + statement constraints: overflow hypothesis with a line number",
      any(c["kind"] == "hypothesis" and "overflow" in c["text"].lower() and "Line 3" in c["text"] and "does not fit" in c["text"] for c in a["claims"]), str([c["text"][:80] for c in a["claims"]]))
check("AI observation (judge verdict) is separate from the hypothesis and cites judge evidence",
      any(c["kind"] == "observation" and "WRONG_ANSWER" in c["text"] and "S1" in c["evidence"] for c in a["claims"]))
check("AI says the exact failing hidden test is unavailable", any("hidden test failed" in m for m in a["missing"]) and any("cannot tell which test failed" in c["text"] for c in a["claims"]))
s1 = next((e["text"] for e in a["evidence"] if e["id"] == "S1"), "")
check("history evidence is compact (counts, no raw verdict array)", "Other attempts on this problem:" in s1 and "earlier verdicts" not in s1 and len(s1) < 600, s1[:200])
check("hidden test input never appears in the answer or evidence", "2000000000 2000000000" not in json.dumps(a))
s, h = ask(l1, "Give me a hint without giving me the solution.", problemId=SUM)
check("hint request about my wrong answer is answered (not refused), points at my code, gives no code block",
      s == 200 and not h.get("refusal") and "```" not in h["answer"] and any("Line 3" in c["text"] for c in h["claims"]), str(h)[:200])
s, r = ask(l1, "Review my latest submission.")
check("contest-page question (no problem selected) still finds my latest submission and its code", s == 200 and any(e["kind"] == "source" for e in r["evidence"]))

print("== Stage 3: Neo4j knowledge graph, GraphRAG and the bounded agent ==")
adm = login("admin@example.com")
JV2_WA = "66666666-6666-4666-8666-666666666664"  # seeded: identical source ACCEPTED under judge-v1, WRONG_ANSWER under judge-v2
PRODUCT_OLD = "66666666-6666-4666-8666-666666666665"  # seeded: judged before the problem statement was revised
s, h = call("GET", AI + "/health")
check("AI health reports the graph store as ready (initialised + projected)", s == 200 and h["graph"]["configured"] and h["graph"]["status"] == "ready", str(h))
s, gs = call("GET", AI + "/graph/status", adm)
check("graph status (admin): nodes and relationships projected from PostgreSQL",
      s == 200 and gs["reachable"] and {"Problem", "Concept", "LearningMaterial", "Submission", "User", "JudgeVersion"} <= set(gs["nodes"]) and {"REQUIRES", "PREREQUISITE_OF", "SIMILAR_TO", "SUPERSEDED_BY"} <= set(gs["relationships"]), str(gs)[:300])
call("POST", AI + "/graph/rebuild", adm)  # a full rebuild first: it also prunes rows the seed/tests deleted since the last one
s, gs = call("GET", AI + "/graph/status", adm)
before_nodes, before_rels = gs["nodes"], gs["relationships"]
s, rb = call("POST", AI + "/graph/rebuild", adm)
s2, gs2 = call("GET", AI + "/graph/status", adm)
check("rebuild from PostgreSQL is idempotent (same nodes and relationships, no duplicates)",
      s == 200 and gs2["nodes"] == before_nodes and gs2["relationships"] == before_rels, f"{before_nodes} vs {gs2.get('nodes')}")
s, tg = call("GET", f"{API}/contests/{CONTEST}/submissions", adm)
check("graph holds every submission PostgreSQL returns for the contest (PostgreSQL stays the source of truth)", gs2["nodes"]["Submission"] >= len(tg), f"{gs2['nodes'].get('Submission')} vs {len(tg)}")


def steps(a):
    return [(t["tool"], t["status"]) for t in a["agent"]["steps"]]


s, a = ask(l1, "How do I approach A plus B?")
check("entity resolution: an old/alternative name resolves to the stable problem (agent step 1)",
      s == 200 and steps(a)[0] == ("resolve_entity", "ok") and any(e["id"] == "P1" and "Sum of Two Numbers" in e["title"] for e in a["evidence"]), str(a)[:300])
check("graph evidence: problem -> concepts -> prerequisite -> material, with the reason derived from the statement bounds",
      any(e["kind"] == "graph" and "Integer overflow" in e["text"] and "Integer types and ranges" in e["text"] for e in a["evidence"]) and any("32-bit" in c["text"] for c in a["claims"]), str([c["text"][:80] for c in a["claims"]]))
check("GraphRAG: retrieval mode reports what the graph contributed", "neo4j graph" in a["retrieval"], a["retrieval"])
check("agent trace: bounded, read-only tools only", a["agent"]["maxSteps"] == 4 and len(a["agent"]["steps"]) <= 4 and {t for t, _ in steps(a)} <= {"resolve_entity", "search_learning_material", "get_submission_history", "get_judge_history", "traverse_graph"}, str(a["agent"]))
check("conclusions drawn from graph paths are hypotheses; recorded relationships are cited observations",
      all(c["evidence"] for c in a["claims"] if c["kind"] == "observation") and any(c["kind"] == "hypothesis" and "prerequisite" in c["text"] for c in a["claims"]))
s, a = ask(l1, "Is there another problem similar to Sum of Two Numbers that I could mix it up with?")
check("similar/duplicate names: the graph reports the archived duplicate 'A + B' with its similarity", any("'A + B'" in c["text"] and "duplicate" in c["text"] for c in a["claims"]), str([c["text"][:90] for c in a["claims"]]))
s, a = ask(l2, "Why did my Sum submission fail when my earlier identical code was accepted?", problemId=SUM, submissionId=JV2_WA)
txt = " ".join(c["text"] for c in a["claims"])
check("conflicting judge evidence is reported with versions and timestamps (identical source, ACCEPTED under judge-v1, WRONG_ANSWER under judge-v2)",
      "CONFLICT" in txt and "judge-v1" in txt and "judge-v2" in txt and "2020-01-01" in txt and "2020-02-01" in txt and "identical" in txt, txt[:400])
check("the AI does not overrule the judge: the recorded verdict stands and a rejudge is left to an instructor", "authoritative" in txt and "rejudge" in txt and "WRONG_ANSWER" in txt)
check("learner never sees judge incident text or judge-version notes", "Suspected regression" not in json.dumps(a) and "output comparison" not in json.dumps(a).lower())
s, a = ask(l2, "Why did my old Product submission fail?", submissionId=PRODUCT_OLD)
check("versioned evidence: a statement revised after the submission is flagged", any("revised at" in c["text"] and "may differ" in c["text"] for c in a["claims"]), str([c["text"][:80] for c in a["claims"]]))
s, a = ask(l2, "Which concepts am I struggling with and what should I study first?")
check("multi-hop (learner -> submissions -> problems -> concepts -> prerequisite -> material) through Neo4j",
      ("traverse_graph", "ok") in steps(a) and any(e["kind"] == "graph" and "Integer types and ranges" in e["text"] for e in a["evidence"]), str(a["agent"]["steps"])[:300])
s, a = ask(ins, "Which prerequisite concept gaps are shared by several learners?")
gap = [c for c in a["claims"] if "shared prerequisite gap" in c["text"]]
check("instructor: shared prerequisite gaps come from the graph and are hypotheses", bool(gap) and all(c["kind"] == "hypothesis" for c in gap) and ("traverse_graph", "ok") in steps(a), str([c["text"][:80] for c in a["claims"]]))
s, a = ask(ins, "Is there a judge regression affecting Sum of Two Numbers?")
check("instructor sees judge versions, incidents and the conflict", any(e["kind"] == "judge-history" and "judge-v2" in e["text"] for e in a["evidence"]), str(a)[:300])
s, a = ask(l1, "Is the legacy C++ I/O note outdated?")
check("stale material: deprecated note flagged and its replacement preferred", any("deprecated" in c["text"] and "prefer the newer note" in c["text"] for c in a["claims"]), str([c["text"][:80] for c in a["claims"]]))
s, a = ask(l1, "What is the weather today?")
check("unanswerable: no graph claims are invented", a["confidence"] == "low" and not a["claims"] and not any(e["kind"] == "graph" for e in a["evidence"]))

print("-- the graph follows changes in PostgreSQL --")
fresh, _ = submit_and_wait(l1, SUM, AC)
s, a = ask(l1, "Why did my latest submission get this verdict?", problemId=SUM, submissionId=fresh["id"])
jh = next((t for t in a["agent"]["steps"] if t["tool"] == "get_judge_history"), {})
check("a submission judged seconds ago is already in the graph (the agent's judge-history step reads its judge version)",
      jh.get("status") == "ok" and fresh["id"][:8] in jh.get("summary", "") and "judged by judge-v" in jh.get("summary", ""), str(a["agent"]["steps"])[:300])
NOTE = "e2e-runtime-note"
s, m = call("POST", AI + "/knowledge/materials", adm, {"id": NOTE, "title": "Flibbertigibbet overflow guidance", "body": "Flibbertigibbet: when a sum exceeds the 32-bit range use long long. Written at runtime.", "tags": ["overflow"], "concepts": ["integer-overflow"], "updated": "2026-09-19"})
check("unseen knowledge: a material added at runtime is stored in PostgreSQL, indexed and projected", s == 200 and m.get("version", 0) >= 1 and m.get("graphProjected"), str(m))
s, a = ask(l1, "Explain flibbertigibbet overflow guidance")
check("...and is retrieved immediately (no restart)", any(NOTE in e["ref"] for e in a["evidence"]), str([e["ref"] for e in a["evidence"]]))
s, gs3 = call("GET", AI + "/graph/status", adm)
check("...and its graph node and COVERS edge exist", gs3["nodes"]["LearningMaterial"] == before_nodes["LearningMaterial"] + 1 and gs3["relationships"]["COVERS"] == before_rels["COVERS"] + 1, str(gs3["nodes"]))
s, d = call("DELETE", f"{AI}/knowledge/materials/{NOTE}", adm)
s, gs4 = call("GET", AI + "/graph/status", adm)
check("removing it removes it from retrieval and from the graph again", s == 200 and gs4["nodes"]["LearningMaterial"] == before_nodes["LearningMaterial"] and gs4["relationships"]["COVERS"] == before_rels["COVERS"], str(gs4["nodes"]))

print(f"\n{e2e_lib.passed} passed, {e2e_lib.failed} failed")
sys.exit(1 if e2e_lib.failed else 0)
