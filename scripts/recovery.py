"""Recovery demo: real infrastructure failure (judge image missing) -> bounded retries -> no corrupted verdict/score.
Case A: image restored during retry window -> submission recovers and is judged ACCEPTED exactly once.
Case B: image stays missing -> retries exhausted -> INFRASTRUCTURE_ERROR / JUDGE_ERROR (never WRONG_ANSWER), score 0.
Usage: python scripts/recovery.py  (needs the stack from `docker compose up --build` running)"""
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "scripts")
from e2e_lib import API, CONTEST, SUM, AC, call, login  # noqa: E402

IMG, BAK = "shodh-judge:v1", "shodh-judge:recovery-backup"
ok = True


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def psql(q):
    return sh("docker", "compose", "exec", "-T", "postgres", "psql", "-U", "shodh", "-d", "shodh", "-At", "-c", q)


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))
    ok &= bool(cond)


def submit(token):
    s, b = call("POST", f"{API}/contests/{CONTEST}/problems/{SUM}/submissions", token, {"language": "cpp", "sourceCode": AC})
    return b["submissionId"]


def wait(token, sid, timeout=60):
    for _ in range(timeout * 2):
        _, sub = call("GET", f"{API}/submissions/{sid}", token)
        if sub["status"] in ("COMPLETED", "INFRASTRUCTURE_ERROR"):
            return sub
        time.sleep(0.5)
    raise TimeoutError(sid)


def score_of(token, user):
    _, rows = call("GET", f"{API}/contests/{CONTEST}/leaderboard", token)
    return next(r["score"] for r in rows if r["username"] == user)


l2 = login("learner2@example.com")
before = score_of(l2, "learner2")
sh("docker", "tag", IMG, BAK)
try:
    print("== Case A: judge image disappears, comes back within the retry window ==")
    sh("docker", "rmi", IMG)
    sid = submit(l2)
    time.sleep(1.0)  # attempt 1 has failed; backoff before attempt 2 is 2 s
    sh("docker", "tag", BAK, IMG)
    sub = wait(l2, sid)
    execs = psql(f"select status||':'||verdict from \"JudgeExecution\" where \"submissionId\"='{sid}' order by \"createdAt\"").splitlines()
    incidents = psql(f"select type from \"JudgeIncident\" where \"submissionId\"='{sid}'").splitlines()
    check("recovered submission judged ACCEPTED", sub["verdict"] == "ACCEPTED" and sub["status"] == "COMPLETED", f"{sub['status']}/{sub['verdict']}")
    check("failed attempt recorded as infrastructure incident, not a student verdict", incidents and execs[0] == "FAILED:JUDGE_ERROR", f"{execs} {incidents}")
    check("exactly one COMPLETED execution (no duplicate judging)", sum(e.startswith("COMPLETED") for e in execs) == 1, str(execs))

    print("== Case B: judge image stays missing -> retries exhausted ==")
    sh("docker", "rmi", IMG)
    sid = submit(l2)
    sub = wait(l2, sid)
    check("exhausted retries -> INFRASTRUCTURE_ERROR + JUDGE_ERROR, not WRONG_ANSWER", sub["status"] == "INFRASTRUCTURE_ERROR" and sub["verdict"] == "JUDGE_ERROR", f"{sub['status']}/{sub['verdict']}")
    check("infrastructure failure scores 0", sub["score"] == 0)
    n = int(psql(f"select count(*) from \"JudgeIncident\" where \"submissionId\"='{sid}'") or 0)
    check("incidents recorded for each failed attempt", n >= 3, str(n))
finally:
    sh("docker", "tag", BAK, IMG)
    sh("docker", "rmi", BAK)

after = score_of(l2, "learner2")
check("leaderboard score unchanged by infrastructure failures (no double count)", before == after, f"{before} -> {after}")
print("\nRecovery demo:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
