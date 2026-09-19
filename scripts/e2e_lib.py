"""Shared helpers for e2e.py / recovery.py.
(Original docstring: End-to-end check against the running stack (real Docker judge, real queue, real AI service).
Usage: python scripts/e2e.py   (env BASE=http://localhost:5173 goes through the frontend proxy)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("BASE", "http://localhost:5173")
API, AI = BASE + "/api", BASE + "/ai"
CONTEST = "33333333-3333-4333-8333-333333333331"
SUM, MUL = "44444444-4444-4444-8444-444444444441", "44444444-4444-4444-8444-444444444444"
LEARNER2_ID = "22222222-2222-4222-8222-222222222224"
PW = "Password123!"
passed, failed = 0, 0


def call(method, url, token=None, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200].decode("utf-8", "replace")}


def check(name, cond, detail=""):
    global passed, failed
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    passed += bool(cond)
    failed += not cond


def wait_ready(timeout=90):
    """The stack may still be starting (backend runs migrations + seed): wait for both services."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(API + "/health", timeout=3) as r, urllib.request.urlopen(AI + "/health", timeout=3) as r2:
                if r.status == 200 and r2.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("Stack not ready: is `docker compose up` running?")


wait_ready()


def login(email):
    s, b = call("POST", API + "/auth/login", body={"email": email, "password": PW})
    assert s in (200, 201), (email, s, b)
    return b["accessToken"]


def submit_and_wait(token, problem, code):
    s, b = call("POST", f"{API}/contests/{CONTEST}/problems/{problem}/submissions", token, {"language": "cpp", "sourceCode": code})
    assert s in (200, 201), (s, b)
    sid, seen = b["submissionId"], [b["status"]]
    for _ in range(90):
        s, sub = call("GET", f"{API}/submissions/{sid}", token)
        if sub["status"] != seen[-1]:
            seen.append(sub["status"])
        if sub["status"] in ("COMPLETED", "INFRASTRUCTURE_ERROR"):
            return sub, seen
        time.sleep(0.5)
    raise TimeoutError(sid)


AC = "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}"
WA = "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a-b<<std::endl;}"
CE = "int main( {"
TLE = "#include <iostream>\nint main(){while(true){} }"
RE = "int main(){int*p=0;*p=1;return 0;}"
OVERFLOW_INT = "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}"



def real_key() -> str:
    """The actual Gemini key (env or repo-root .env) so leak checks look for the literal secret, whatever its format."""
    k = os.getenv("GEMINI_API_KEY", "").strip()
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not k and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("GEMINI_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k
