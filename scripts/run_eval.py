#!/usr/bin/env python3
"""Single-command deterministic evaluation of the whole system. Writes EVALUATION.md and exits non-zero on failure.

    python scripts/run_eval.py               # (works on Windows/macOS/Linux; no shell env syntax needed)
    AI_MODEL_DISABLED=1 python scripts/run_eval.py   # equivalent; the script always forces model-off itself

What it does: makes sure the docker compose stack is up, switches the AI service to evidence-only mode (so Gemini is
NEVER called and no quota is used), runs builds, AI unit tests (incl. real pgvector and real Neo4j), backend unit tests, the
real-Docker E2E flow, the security suite, the recovery suite, the offline retrieval comparison and the GraphRAG evaluation
(baseline vs graph), captures per-check results and timings, writes
EVALUATION.md, then restores the AI service to its previous mode (use --keep-model-off to leave it off).
Options: --no-start (do not start the stack), --skip-builds, --keep-model-off."""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
BASE = os.getenv("BASE", "http://localhost:5173")
API, AI = BASE + "/api", BASE + "/ai"
PY = sys.executable
PROBLEM_NAMES = {"41": "Sum of Two Numbers", "42": "Maximum of Two", "43": "Absolute Difference", "44": "Product"}


def run(cmd, env=None, timeout=1800, cwd=ROOT):
    t0 = time.perf_counter()
    e = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
    try:
        p = subprocess.run(cmd, cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout, time.perf_counter() - t0
    except subprocess.TimeoutExpired as ex:
        return 124, (ex.stdout or "") if isinstance(ex.stdout, str) else "", time.perf_counter() - t0
    except FileNotFoundError as ex:
        return 127, str(ex), time.perf_counter() - t0


def health(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def wait_ready(timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        if health(API + "/health") and health(AI + "/health"):
            return True
        time.sleep(2)
    return False


def wait_graph_ready(timeout=180):
    """The graph is projected in the background after the AI service starts: wait until it reports `ready` (never fatal here;
    the suites report a failing graph themselves)."""
    end = time.time() + timeout
    while time.time() < end:
        h = health(AI + "/health")
        if h and h.get("graph", {}).get("status") == "ready":
            return True
        time.sleep(2)
    return False


def parse_checks(out):
    """PASS/FAIL lines printed by scripts/e2e.py, security.py and recovery.py, grouped by '== section ==' headers."""
    checks, section = [], ""
    for line in out.splitlines():
        m = re.match(r"^== (.*) ==$", line.strip())
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^(PASS|FAIL) (.*)$", line.rstrip())
        if m:
            name, detail = m.group(2), ""
            d = re.match(r"^(.*?)\s{2}\[(.*)\]$", name)
            if d:
                name, detail = d.group(1), d.group(2)
            checks.append({"section": section, "name": name, "result": m.group(1), "detail": detail})
    return checks


def summarize_checks(checks):
    p = sum(c["result"] == "PASS" for c in checks)
    return p, len(checks) - p


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


class Report:
    def __init__(self):
        self.rows, self.details, self.notes = [], [], []

    def add(self, area, expected, actual, result, evidence, seconds):
        self.rows.append({"area": area, "expected": expected, "actual": actual, "result": result, "evidence": evidence, "seconds": seconds})


def stage_builds(rep, skip):
    if skip:
        rep.add("Docker builds (backend `nest build`, frontend `tsc`+`vite build`, ai image)", "all images build", "skipped by --skip-builds", "NOT RUN", "`docker compose build`", 0)
        return True
    rc, out, sec = run(["docker", "compose", "build", "backend", "frontend", "ai"], timeout=1800)
    if rc == 0:  # make sure the RUNNING containers are the freshly built ones (model forced off), not stale ones
        rc2, out2, sec2 = run(["docker", "compose", "up", "-d"], env={"AI_MODEL_DISABLED": "1"}, timeout=900)
        sec += sec2
        if rc2 != 0 or not wait_ready(300):
            rc, out = rc2 or 1, out2 or "stack did not become healthy after `docker compose up -d`"
    rep.add("Docker builds (backend `nest build`, frontend `tsc`+`vite build`, ai image)", "all three images build without error",
            "all built" if rc == 0 else "build failed: " + out.strip().splitlines()[-1][:160] if out.strip() else "build failed", "PASS" if rc == 0 else "FAIL",
            "`docker compose build backend frontend ai`", sec)
    return rc == 0


def stage_ai_tests(rep):
    rc, out, sec = run(["docker", "compose", "run", "--rm", "--no-deps", "-e", "AI_MODEL_DISABLED=1", "ai", "python", "-m", "pytest", "-v", "--tb=short", "-p", "no:cacheprovider"], timeout=900)
    per_file, passed, failed, skipped = {}, 0, 0, 0
    for m in re.finditer(r"^(?:tests/)?(test_\w+\.py)::(.+?)\s+(PASSED|FAILED|SKIPPED|ERROR)", out, re.M):
        f, _, res = m.groups()
        per_file.setdefault(f, {"PASSED": 0, "FAILED": 0, "SKIPPED": 0, "ERROR": 0})[res] += 1
    for v in per_file.values():
        passed += v["PASSED"]; failed += v["FAILED"] + v["ERROR"]; skipped += v["SKIPPED"]
    ok = rc == 0 and failed == 0 and skipped == 0 and passed > 0
    if passed + failed + skipped == 0:
        out_tail = " ".join(out.strip().splitlines()[-1:])[:160]
        rep.add("AI unit tests (guards, grounding, Gemini error handling via fake SDK server, multi-hop, real pgvector, agent/tools, entity resolution, real Neo4j)", "0 failed, 0 skipped (pgvector and Neo4j reachable)",
                f"no pytest results parsed (rc={rc}): {out_tail}", "FAIL", "`ai/tests/*.py` via `docker compose run ai pytest`", sec)
        return False
    rep.add("AI unit tests (guards, grounding, Gemini error handling via fake SDK server, multi-hop, real pgvector, agent/tools, entity resolution, real Neo4j)", "0 failed, 0 skipped (pgvector and Neo4j reachable)",
            f"{passed} passed, {failed} failed, {skipped} skipped", "PASS" if ok else "FAIL", "`ai/tests/*.py` via `docker compose run ai pytest`", sec)
    rep.details.append(("AI unit tests by file", ["| File | Passed | Failed | Skipped |", "|---|---|---|---|"] + [f"| `{f}` | {v['PASSED']} | {v['FAILED'] + v['ERROR']} | {v['SKIPPED']} |" for f, v in sorted(per_file.items())]))
    return ok


def stage_backend_tests(rep):
    b = ROOT / "Backend"
    if (b / "node_modules").exists() and shutil.which("npx"):
        cmd, cwd, how = [shutil.which("npx"), "jest", "--ci"], b, "host `npx jest`"
        rc, out, sec = run(cmd, cwd=cwd, timeout=900)
    else:
        rc0, out0, s0 = run(["docker", "build", "--target", "build", "-t", "shodh-backend-unit", str(b)], timeout=1800)
        if rc0 != 0:
            rep.add("Backend unit tests (jest)", "all suites pass", "could not build test image", "FAIL", "`docker build --target build Backend`", s0)
            return False
        rc, out, sec = run(["docker", "run", "--rm", "shodh-backend-unit", "npx", "jest", "--ci"], timeout=900)
        sec += s0
        how = "`docker run` of the Backend build stage"
    suites = re.search(r"Test Suites:\s+(?:(\d+) failed, )?(\d+) passed, (\d+) total", out)
    tests = re.search(r"Tests:\s+(?:(\d+) failed, )?(\d+) passed, (\d+) total", out)
    ok = rc == 0 and tests is not None and not tests.group(1)
    actual = f"{tests.group(2)}/{tests.group(3)} tests, {suites.group(2)}/{suites.group(3)} suites passed" if tests and suites else "jest output not parsable"
    rep.add("Backend unit tests (verdict compare, idempotency, retry policy, leaderboard ranking, contest status)", "all suites and tests pass", actual, "PASS" if ok else "FAIL", f"`Backend/src/**/*.spec.ts` via {how}", sec)
    return ok


def stage_suite(rep, area, expected, script, evidence, timings_file=None, extra_env=None):
    env = dict(extra_env or {})
    if timings_file:
        env["EVAL_TIMINGS_FILE"] = timings_file
    rc, out, sec = run([PY, str(SCRIPTS / script)], env=env, timeout=1500)
    checks = parse_checks(out)
    p, f = summarize_checks(checks)
    ok = rc == 0 and f == 0 and p > 0
    if not checks:
        actual = "no check output: " + " ".join(out.strip().splitlines()[-1:])[:200]
    else:
        actual = f"{p}/{len(checks)} checks passed" + (f"; failing: {', '.join(c['name'][:50] for c in checks if c['result'] == 'FAIL')[:200]}" if f else "")
    rep.add(area, expected, actual, "PASS" if ok else "FAIL", evidence, sec)
    rows = ["| Section | Case (expected behaviour) | Actual |", "|---|---|---|"]
    for c in checks:
        rows.append(f"| {esc(c['section'])} | {esc(c['name'])} | {c['result']}{(': ' + esc(c['detail'])) if c['detail'] else ''} |")
    rep.details.append((f"{area.split(' (')[0]} - individual checks", rows))
    return ok


def stage_retrieval(rep):
    tmp = tempfile.mktemp(suffix=".json")
    rc, out, sec = run([PY, str(SCRIPTS / "retrieval_eval.py"), "--json", tmp], timeout=900)
    if rc != 0 or not os.path.exists(tmp):
        rep.add("Retrieval comparison (BM25 vs hybrid)", "runs offline and reports Hit@1/Hit@3", "failed: " + out.strip()[-160:], "FAIL", "`scripts/retrieval_eval.py`", sec)
        return False, None
    res = json.load(open(tmp, encoding="utf-8"))
    os.unlink(tmp)
    a = res["arms"]
    bm, hy = a["bm25_raw"]["metrics"]["all"], a["hybrid"]["metrics"]["all"]
    rep.add("Retrieval comparison (BM25 vs BM25+rerank vs vector vs hybrid, cached real Gemini embeddings, 0 API calls)", "runs offline; reports Hit@1 / Hit@3 honestly",
            f"BM25 Hit@1 {bm['hit@1']}/{bm['n']}, Hit@3 {bm['hit@3']}/{bm['n']}; hybrid Hit@1 {hy['hit@1']}/{hy['n']}, Hit@3 {hy['hit@3']}/{hy['n']}", "PASS",
            "`scripts/retrieval_eval.py`, `ai/eval/queries.json`", sec)
    return True, res


def stage_graph_eval(rep):
    tmp = tempfile.mktemp(suffix=".json")
    rc, out, sec = run([PY, str(SCRIPTS / "graph_eval.py"), "--json", tmp], timeout=900)
    if rc != 0 or not os.path.exists(tmp):
        rep.add("GraphRAG evaluation (baseline text retrieval vs graph-augmented pipeline)", "runs against the seeded stack and reports pass rates per category", "failed: " + out.strip()[-200:], "FAIL", "`scripts/graph_eval.py`", sec)
        return False, None
    res = json.load(open(tmp, encoding="utf-8"))
    os.unlink(tmp)
    allr = res["summary"]["all"]
    ok = not res["regressions"] and allr["graphrag"] >= allr["baseline"]
    rep.add("GraphRAG evaluation (baseline text retrieval vs graph-augmented pipeline; 0 model calls)", "graph pipeline answers at least what the baseline answers (no regressions); misses are reported, not hidden",
            f"baseline {allr['baseline']}/{allr['n']}, GraphRAG {allr['graphrag']}/{allr['n']}; regressions: {res['regressions'] or 'none'}", "PASS" if ok else "FAIL",
            "`scripts/graph_eval.py`, `ai/eval/graph_queries.json`", sec)
    return ok, res


def timing_sections(timings):
    lines = []
    subs = timings.get("submissions", [])
    if subs:
        secs = [s["seconds"] for s in subs]
        lines += ["### Submission -> verdict latency (real Docker judge, from the E2E flow)", "",
                  "| Problem | Verdict | Seconds (POST -> final status, polled every 0.5 s) | Status sequence |", "|---|---|---|---|"]
        lines += [f"| {PROBLEM_NAMES.get(s['problem'], s['problem'])} | {s['verdict']} | {s['seconds']} | {' -> '.join(s['statuses'])} |" for s in subs]
        lines += ["", f"n={len(secs)}, min {min(secs)} s, median {statistics.median(secs):.2f} s, max {max(secs)} s. Includes queueing, container start, `g++` compile and test runs; "
                  "poll granularity adds up to 0.5 s.", ""]
    ai = timings.get("ai", [])
    if ai:
        by = {}
        for x in ai:
            by.setdefault(x["source"], []).append(x["server_ms"])
        lines += ["### AI response time (deterministic evidence-only mode, no Gemini call)", "", "| Answer source | n | median server ms | max server ms |", "|---|---|---|---|"]
        for src, v in sorted(by.items()):
            lines.append(f"| {src} | {len(v)} | {statistics.median(v):.0f} | {max(v)} |")
        modes = sorted({x["retrieval"] for x in ai if x.get("retrieval")})
        lines += ["", "Server time covers authorization + evidence gathering via the backend + BM25 + grounding. Retrieval modes seen: " + "; ".join(modes) + ". "
                  "Live Gemini latency (6-16 s per answer in the last manual run) is NOT measured here.", ""]
    return lines


LIMITATIONS = [
    "The knowledge graph is a derived projection with a 20 s background sync plus an on-demand pass before each answer; deletions in PostgreSQL only reach it on the next periodic full rebuild (10 min) or `POST /ai/graph/rebuild`. Concept tagging and problem similarity are heuristic (keyword and statement-text based), not learned.",
    "The agent's default planner is deterministic (policy over role, intent and earlier observations); the optional Gemini planner (`AI_AGENT_PLANNER=llm`) is implemented and unit-tested against a fake model but was not run live in this evaluation.",
    "Judge versions and incidents reach the AI through the graph projection (read straight from PostgreSQL by the AI service), not through a backend API; instructors see incident text, learners never do.",
    "Instructor questions the assistant cannot map to a problem, verdict or concept still fall back to the contest-wide verdict aggregate instead of 'insufficient evidence' (GraphRAG evaluation question U3 documents this).",
    "The GraphRAG evaluation set is small (20 questions) and was written by the implementer against the seeded data; it demonstrates that the graph adds evidence, not general accuracy.",
    "Read-only graph access is enforced by code (a fixed allowlist of parameterised statements checked at import, no query text in any tool, staff-only data gated by `$staff` inside the statements) and by tests, not by a Neo4j role: Neo4j Community has a single user, so the AI service reads and writes with the same credentials.",
    "The seeded concept taxonomy only has 1-hop prerequisites; 3-hop prerequisite traversal is implemented and a real-Neo4j test covers a 2-hop chain, while the seeded multi-hop paths get their length from other relations (user -> submission -> problem -> concept <- prerequisite <- note).",
    "Retrieval evaluation is tiny (8 notes, 15 queries) and does not separate BM25 from hybrid at Hit@1/Hit@3; it shows parity, not superiority.",
    "Live Gemini verification is manual and only partially repeated: the deterministic suite never calls Gemini (`gemini_live.py` is NOT RUN here). Free-tier quota was 20 requests/day/model.",
    "Worker-restart recovery is untested (only image-loss recovery is demonstrated); Redis has no persistence volume and there is no startup reconciliation of QUEUED/RUNNING submissions.",
    "Judge container hardening gaps: no CapDrop / no-new-privileges, exit code 137 is reported as TIME_LIMIT_EXCEEDED (OOM not distinguished), stdout buffered before truncation, orphaned containers are not swept after a crash.",
    "Backend `Backend/test/*.e2e-spec.ts` (needs a DB and would start a second worker) is not executed by this script.",
    "The AI service connects to Postgres with the shared superuser credentials (it writes only its own schema `ai` and vector table, and reads core tables only in the graph projector).",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--skip-builds", action="store_true")
    ap.add_argument("--keep-model-off", action="store_true")
    args = ap.parse_args()
    t_start = time.perf_counter()
    os.environ["AI_MODEL_DISABLED"] = "1"

    rc, out, _ = run(["docker", "compose", "version"])
    if rc != 0:
        print("docker compose is required:", out.strip())
        return 2

    restore = False
    if not (health(API + "/health") and health(AI + "/health")):
        if args.no_start:
            print("Stack is not running and --no-start was given.")
            return 2
        print("Starting the stack (docker compose up --build -d) with the model disabled ...")
        rc, out, _ = run(["docker", "compose", "up", "--build", "-d"], env={"AI_MODEL_DISABLED": "1"}, timeout=2400)
        if rc != 0 or not wait_ready():
            print("Could not start the stack:\n", out[-1500:])
            return 2
    h = health(AI + "/health")
    if h.get("model_configured"):
        print("Gemini is enabled on the ai service: recreating it with AI_MODEL_DISABLED=1 so this run makes no Gemini calls ...")
        rc, out, _ = run(["docker", "compose", "up", "-d", "--force-recreate", "ai"], env={"AI_MODEL_DISABLED": "1"})
        restore = rc == 0
        if rc != 0 or not wait_ready():
            print("Could not switch the ai service to evidence-only mode:\n", out[-800:])
            return 2
        if health(AI + "/health").get("model_configured"):
            print("Refusing to continue: the model is still enabled.")
            return 2

    rep = Report()
    timings_file = tempfile.mktemp(suffix=".json")
    results = {}
    retrieval, graph_eval = None, None
    try:
        results["build"] = stage_builds(rep, args.skip_builds)
        if not wait_ready(120):
            print("stack not healthy after builds")
        if not wait_graph_ready():
            print("graph store did not report ready within 180 s (the graph suites below will show what fails)")
        results["ai"] = stage_ai_tests(rep)
        results["backend"] = stage_backend_tests(rep)
        results["e2e"] = stage_suite(rep, "Normal end-to-end flow (login, contest, real Docker judging of all verdicts, leaderboard, async lifecycle, AI + multi-hop)",
                                     "every check passes", "e2e.py", "`scripts/e2e.py`", timings_file=timings_file)
        results["security"] = stage_suite(rep, "Security / access control (authN, RBAC, cross-org, org self-join blocked, private data, AI protections, key not in browser)",
                                          "every check passes", "security.py", "`scripts/security.py`")
        results["recovery"] = stage_suite(rep, "Recovery (judge image lost mid-run: retry recovery + exhaustion -> JUDGE_ERROR, no score corruption)",
                                          "every check passes", "recovery.py", "`scripts/recovery.py`")
        ok_r, retrieval = stage_retrieval(rep)
        results["retrieval"] = ok_r
        ok_g, graph_eval = stage_graph_eval(rep)
        results["graph_eval"] = ok_g
    finally:
        if restore and not args.keep_model_off:
            print("Restoring the ai service to its previous (model-enabled) mode ...")
            env = {k: v for k, v in os.environ.items() if k != "AI_MODEL_DISABLED"}
            subprocess.run(["docker", "compose", "up", "-d", "--force-recreate", "ai"], cwd=ROOT, env=env, capture_output=True)
            wait_ready(300)

    total = time.perf_counter() - t_start
    rep.add("Live Gemini verification (`scripts/gemini_live.py`)", "n/a in the deterministic run", "not executed: would consume free-tier quota", "NOT RUN", "`scripts/gemini_live.py` (manual)", 0)
    timings = {}
    if os.path.exists(timings_file):
        timings = json.load(open(timings_file, encoding="utf-8"))
        os.unlink(timings_file)
    rc, sha, _ = run(["git", "rev-parse", "--short", "HEAD"])
    dirty = run(["git", "status", "--porcelain", "--", ".", ":!EVALUATION.md"])[1].strip() != ""
    all_ok = all(results.values())

    L = ["# Evaluation report", "",
         f"Generated by `python scripts/run_eval.py` on {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}, commit `{sha.strip()}{'+uncommitted' if dirty else ''}`. "
         f"Mode: deterministic, AI_MODEL_DISABLED=1 (Gemini was **not** called). Overall: **{'PASS' if all_ok else 'FAIL'}**. Total evaluation time: **{total:.0f} s**.", "",
         "## Summary", "", "| Area | Expected | Actual | Result | Evidence | Time |", "|------|----------|--------|--------|----------|------|"]
    for r in rep.rows:
        L.append(f"| {esc(r['area'])} | {esc(r['expected'])} | {esc(r['actual'])} | {r['result']} | {r['evidence']} | {r['seconds']:.0f} s |")
    L.append(f"| **Total evaluation time** | - | - | {'PASS' if all_ok else 'FAIL'} | `scripts/run_eval.py` | {total:.0f} s |")
    L += [""] + timing_sections(timings)
    if retrieval:
        L += ["## Retrieval comparison", "",
              f"{retrieval['queries']} labelled queries over {retrieval['corpus_docs']} learning notes; embeddings: `{retrieval['embedding_model']}` captured {retrieval['embeddings_captured_utc']} and cached (this run made 0 API calls). "
              "The 'Hybrid' arm runs the production code path (`assistant.hybrid_search`: BM25 + pgvector + RRF + freshness rerank).", "",
              "| Approach | Group | n | Hit@1 | Hit@3 | MRR |", "|---|---|---|---|---|---|"]
        for arm in retrieval["arms"].values():
            for g in ("all", "keyword", "paraphrase"):
                m = arm["metrics"][g]
                L.append(f"| {arm['label']} | {g} | {m['n']} | {m['hit@1']}/{m['n']} | {m['hit@3']}/{m['n']} | {m['mrr']} |")
        L += ["", "Per-query top-3 results are printed by `python scripts/retrieval_eval.py`.", ""]
    if graph_eval:
        S = graph_eval["summary"]
        L += ["## GraphRAG evaluation (baseline vs graph)", "",
              f"{S['all']['n']} labelled questions (`ai/eval/graph_queries.json`, written from the seed data's semantics before any result existed), each answered twice through the real pipeline with the model switched off: "
              "**baseline** = text retrieval only (BM25 [+ pgvector], no agent tools, no Neo4j: how the assistant worked before the graph) and **GraphRAG** = entity resolution + Neo4j traversal + fused retrieval + judge history. "
              "A question passes when every expected string appears in the evidence, claims or answer shown to the user (and no forbidden string does). This measures which *evidence* the graph adds, not how Gemini would phrase it, and the set is small and authored by the implementer: it shows the graph "
              "contributes evidence the text pipeline cannot produce, not a general accuracy figure.", "",
              "| Category | n | Baseline | GraphRAG |", "|---|---|---|---|"]
        for c, v in S.items():
            L.append(f"| {c} | {v['n']} | {v['baseline']}/{v['n']} | {v['graphrag']}/{v['n']} |")
        L += ["", f"Answered only with the graph: {', '.join(graph_eval['only_graphrag']) or 'none'}. Regressions: {', '.join(graph_eval['regressions']) or 'none'}. "
              f"Median gather + answer time (no model): baseline {graph_eval['median_ms']['baseline']} ms, GraphRAG {graph_eval['median_ms']['graphrag']} ms.", "",
              "| Question | Baseline | GraphRAG | Why GraphRAG missed |", "|---|---|---|---|"]
        qs = {q["id"]: q for q in json.load(open(ROOT / "ai" / "eval" / "graph_queries.json", encoding="utf-8"))["questions"]}
        for qid, q in qs.items():
            b, g = graph_eval["baseline"][qid], graph_eval["graphrag"][qid]
            L.append(f"| {qid} ({q['category']}): {esc(q['question'])} | {'PASS' if b['pass'] else 'FAIL'} | {'PASS' if g['pass'] else 'FAIL'} | {esc(g['why']) if not g['pass'] else ''} |")
        L.append("")
    for title, lines in rep.details:
        L += [f"## {title}", ""] + lines + [""]
    L += ["## NOT RUN", "", "- `scripts/gemini_live.py` (live Gemini): skipped by design; results of the last manual run are described in the README, not here.",
          "- `Backend/test/*.e2e-spec.ts`: not executed by this script (they start a second worker and mutate the DB).", "",
          "### Known Issues / Limitations", ""] + [f"- {x}" for x in LIMITATIONS] + [""]
    (ROOT / "EVALUATION.md").write_text("\n".join(L), encoding="utf-8")

    print("\n" + "=" * 78)
    for r in rep.rows:
        print(f"{r['result']:8} {r['seconds']:6.0f}s  {r['area'][:70]}\n           {r['actual'][:110]}")
    print("=" * 78)
    print(f"Overall: {'PASS' if all_ok else 'FAIL'} in {total:.0f}s -> EVALUATION.md")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
