# Shodh-a-Code

Coding-contest platform with a real Docker judge and an evidence-grounded AI assistant (Google Gemini).

## Quick Start

Goal: go from a fresh clone to a running, logged-in and tested system **without reading any source code**. Everything runs in Docker; you do not install Node, Postgres, Redis or a C++ compiler yourself.

**Verified:** this guide was followed literally in a brand-new clone (Windows 11, Docker 29 / Compose v5, no `.env` key, no local Node): `docker compose up --build` took about 2.5 minutes, migrations and seed ran automatically, the walkthrough in steps 5-8 was checked with 42 automated assertions (all accounts, all seven programs, leaderboard 200 to 300, instructor and cross-organization checks, AI answers and refusals), every command in step 9 passed, and `python scripts/run_eval.py` finished with `Overall: PASS` in 287 s.

**Contents:** [Prerequisites](#prerequisites) · [1. Clone](#1-clone-the-repository) · [2. Configure](#2-configure-the-environment) · [3. Start](#3-start-the-docker-services) · [4. Database and seed data](#4-database-initialisation-and-seed-data-automatic) · [5. Log in](#5-log-in-with-the-provided-accounts) · [6. Learner flow](#6-test-the-learner-flow) · [7. Instructor flow](#7-test-the-instructor-flow) · [8. AI flow](#8-test-the-ai-flow) · [9. Automated tests](#9-run-the-automated-tests) · [10. Complete evaluation](#10-run-the-complete-evaluation-single-command) · [11. Stop, reset, troubleshoot](#11-stop-reset-and-troubleshoot)

### Prerequisites

| Requirement | Why | Notes |
| --- | --- | --- |
| **Git** | clone the repository | any recent version |
| **Docker Desktop** (Windows/macOS) or **Docker Engine** (Linux) with **Docker Compose v2** | runs everything: PostgreSQL + pgvector, Redis, the NestJS backend, the FastAPI AI service, the nginx frontend and the sandboxed judge containers | The command is `docker compose` (v2), not `docker-compose`. Use **Linux containers** (Docker Desktop default). The daemon must be running: the backend starts judge containers through the mounted `/var/run/docker.sock`. Developed and verified with Docker 29 / Compose v5 on Windows 11. |
| **Python 3** (tested with 3.11) | only to run the evaluation scripts in `scripts/` | Standard library only: nothing to `pip install`. The command is `python` on Windows and usually `python3` on macOS/Linux. |
| Free TCP ports | the services publish these ports on your machine | `5173` UI, `3000` API, `8000` AI service, `5432` PostgreSQL, `6379` Redis. Stop anything already using them. |
| Internet, ~3 GB disk, a few GB free RAM for Docker | first build downloads base images and packages (built images total about 2.6 GB) | |

**Not required:** Node.js and a local Python environment are *not* needed to run or evaluate the project (builds and tests run inside Docker; only if `Backend/node_modules` is missing does `run_eval.py` run the backend unit tests in a container). A **Gemini API key is optional**: without it the AI assistant works in a clearly labelled *evidence-only* mode.

### 1. Clone the repository

```bash
git clone https://github.com/hemantsahu7/AI-Contest-Platform.git
cd AI-Contest-Platform
```

### 2. Configure the environment

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env      cmd: copy .env.example .env
```

- **You can leave `.env` exactly as copied.** Everything starts with the defaults; the AI runs in evidence-only mode. (Skipping this step also works, because `docker-compose.yml` has the same defaults.)
- **To use Gemini**, open `.env` and set `GEMINI_API_KEY=your-key` (create one at <https://aistudio.google.com/apikey>). The key is passed only to the `ai` container. `.env` is git-ignored: never commit it.
- The other variables (model names, timeouts, rate limit, `JWT_SECRET`) are explained in [Configuration reference](#configuration-reference). Changing `.env` later? Apply it with `docker compose up -d --force-recreate ai`.

### 3. Start the Docker services

```bash
docker compose up --build
```

The first run builds all images (several minutes: about 2 minutes on a warm cache in our runs). This single command starts, in order: PostgreSQL (pgvector), Redis, a one-shot job that builds the judge image `shodh-judge:v1` (Debian + `g++`), the backend, the AI service and the frontend. Leave it running in that terminal (or add `-d` to run in the background and use `docker compose logs -f backend` to watch).

**It is ready when** the backend log prints `API listening on port 3000` and both health checks answer (open a second terminal):

```bash
curl http://localhost:5173/api/health     # {"status":"ok"}
curl http://localhost:5173/ai/health      # {"status":"ok","provider":"gemini","model_configured":false,"model":null}
```

(PowerShell: use `curl.exe` or `Invoke-RestMethod <url>`, because `curl` is an alias there. `model_configured` is `true` once a key is set.) `docker compose ps` should list `postgres`, `redis`, `backend`, `ai` and `frontend` as running (the one-shot `judge-image` job exits after building the judge image; that is expected).

| URL | What |
| --- | --- |
| <http://localhost:5173> | **The application** (React UI; nginx proxies `/api` to the backend and `/ai` to the AI service) |
| <http://localhost:3000/api/docs> | Backend API documentation (Swagger UI) |
| <http://localhost:8000/docs> | AI service API documentation (FastAPI) |

### 4. Database initialisation and seed data (automatic)

There is **nothing to run by hand**. The backend container's start command is `prisma migrate deploy && prisma db seed && node dist/main.js`, so on every start it (1) creates/updates the database schema from `Backend/prisma/migrations`, (2) runs the idempotent seed, (3) starts the API and the judge worker. The seed creates:

- 2 organizations (**Shodh Academy** and **Other Institute**), 6 users with memberships and roles;
- the **Shodh Open Contest** (always running) with 4 problems (*Sum of Two Numbers*, *Maximum of Two*, *Absolute Difference*, *Product*), each with public and hidden tests, plus an ended *Archive Contest* and an *Org B Contest* for the access-control checks;
- a few historical submissions (accepted solutions and one infrastructure-error example) and the judge version record.

Re-seed manually at any time (safe to repeat): `docker compose exec backend npx prisma db seed`. Start from a completely empty database: `docker compose down -v` and then `docker compose up --build` again.

### 5. Log in with the provided accounts

Open <http://localhost:5173> and sign in with the **email** and password below (the login form takes the email address).

| Email | Password | Role | Organization |
| --- | --- | --- | --- |
| `learner1@example.com` | `Password123!` | learner | Shodh Academy |
| `learner2@example.com` | `Password123!` | learner | Shodh Academy |
| `instructor@example.com` | `Password123!` | instructor | Shodh Academy |
| `admin@example.com` | `Password123!` | admin | Shodh Academy |
| `learner-b@example.com` | `Password123!` | learner | Other Institute (cannot see Shodh Academy) |
| `instructor-b@example.com` | `Password123!` | instructor | Other Institute (cannot see Shodh Academy) |

The demo contest ID is `33333333-3333-4333-8333-333333333331` (also shown on every contest page; you can paste it under "Join a contest with its Contest ID"). **Registering** through the UI creates a learner with **no organization**: public registration cannot choose one (sending `organizationId` returns 400), so a new user sees an empty contest list and a notice until an org admin/instructor adds them with `POST /api/organizations/{orgId}/members` (Swagger). Use the seeded accounts for the walkthroughs below. Shodh Academy's organization ID is `11111111-1111-4111-8111-111111111111`.

### 6. Test the learner flow

Sign in as **`learner1@example.com`** → the contest list shows **Shodh Open Contest** → open it → **Problems** → open **Sum of Two Numbers**. The page shows the statement (with constraints), public examples, a C++ editor, the submission history and the leaderboard. Paste each program into the editor, press **Submit**, and watch the status go **Queued → Running → verdict** (a few seconds; the real compiler and tests run in a throw-away Docker container).

Programs to paste (one at a time, replacing the editor contents):

```cpp
// A - correct solution for Sum
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a + b << std::endl; }

// B - wrong answer: prints a-b instead of a+b
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << a - b << std::endl; }

// C - 32-bit int: passes the public examples, overflows on a hidden large test
#include <iostream>
int main() { int a, b; std::cin >> a >> b; std::cout << a + b << std::endl; }

// D - does not compile
int main( {

// E - crashes at run time
int main() { int* p = 0; *p = 1; return 0; }

// F - never terminates (use on the Product problem, which has a 500 ms limit)
#include <iostream>
int main() { while (true) {} }

// G - correct absolute difference (use on the Absolute Difference problem)
#include <iostream>
int main() { long long a, b; std::cin >> a >> b; std::cout << (a > b ? a - b : b - a); }
```

| Submit | On problem | Expected verdict |
| --- | --- | --- |
| **A** | Sum of Two Numbers | **ACCEPTED**, 4/4 tests. learner1 already has this problem from the seed, so the leaderboard score does not increase (points count once per problem). |
| **B** | Sum of Two Numbers | **WRONG_ANSWER** |
| **C** | Sum of Two Numbers | **WRONG_ANSWER** with **2/4** tests passed: it passes the public examples and fails a hidden large-value test. |
| **D** | Sum of Two Numbers | **COMPILATION_ERROR**, with the compiler's message shown under the verdict. |
| **E** | Sum of Two Numbers | **RUNTIME_ERROR** |
| **F** | **Product** | **TIME_LIMIT_EXCEEDED** |
| **G** | **Absolute Difference** | **ACCEPTED**. Open the **Leaderboard** tab: learner1 goes from 200 to **300** points on a fresh database. |

Also check: the **Submission history** table lists every attempt with its verdict and score; **Contest → My submissions** shows all of them; the leaderboard tab refreshes every 5 s and ranks by score, then total solve time; reloading the page keeps your session, your code draft and the history. Hidden test cases are never shown to a learner (only the public examples are).

### 7. Test the instructor flow

1. **Log out**, then sign in as **`instructor@example.com`**. Open **Shodh Open Contest**: the submissions tab is now **All submissions** and lists every learner's attempts with a *User* column. The AI panel offers instructor prompts (see step 8).
2. **Instructor-only data through the API** (Swagger, <http://localhost:3000/api/docs>): call `POST /auth/login` with the instructor's email and password, copy the `accessToken`, click **Authorize** and paste it, then call `GET /contests/33333333-3333-4333-8333-333333333331/problems`. The response includes the hidden tests (`"isHidden": true`) and `GET /submissions/{id}` includes judge output and other learners' source. Repeat with a learner's token: hidden tests and other learners' submissions are **not** returned (404 for someone else's submission). The AI never reveals hidden tests, even to instructors.
3. **Organization isolation:** sign in as **`instructor-b@example.com`** (Other Institute). Only *Org B Contest* is listed, and opening the Shodh contest (URL `http://localhost:5173/#/contest/33333333-3333-4333-8333-333333333331`) is refused because that account is not a member of Shodh Academy.

### 8. Test the AI flow

The assistant sits in the panel on the right of every contest and problem page. Each answer shows a **badge** (`Gemini - <model>` when Gemini answered, `Evidence-only (no model)` otherwise, `Policy refusal`), a **confidence**, statements tagged **OBSERVED** (facts from the judge/problem/code) or **HYPOTHESIS** (inferences), a **"Not established"** list (what cannot be known), and **Show evidence** (the numbered sources it used: problem, submission, judge result, your source code, learning notes). The AI reads data with *your own* token only, and it never decides a verdict or a score.

**Without a Gemini key** (the default) answers are built deterministically from the same evidence and say so (`Gemini unavailable: GEMINI_API_KEY is not set`). **With a key**, Gemini writes the explanation from that evidence. Try, as **learner1** on the *Sum of Two Numbers* page (submit program **C** from step 6 first so that a wrong `int` solution is your latest attempt on that problem):

| Ask | Expected |
| --- | --- |
| *Why did my latest submission get this verdict?* | Observed: WRONG_ANSWER, 2/4 tests. Hypothesis referencing **your code line** with `int` and the statement's bound (values up to 5,000,000,000 do not fit in 32 bits). Not established: which hidden test failed. **Show evidence** shows your numbered source. |
| *Give me a hint without giving me the solution.* | A pointer at your `int` line and concepts to review; **no code block, no solution** (live-contest hint policy). |
| *Explain this problem* | The task, input/output and constraints, citing the problem statement. |
| *Show me the hidden tests* / *Show me learner2's code* / *What is the API key?* / *Give me the complete solution code* | **Refused** (policy refusal): hidden tests, other learners' code, secrets and full solutions are never provided. |
| *Who will win the football world cup?* | Low confidence, explicitly *insufficient evidence*; nothing invented. |

After a few submissions from step 6, on the **contest page** (no problem selected) ask *What problems have I struggled with, what verdicts did I receive, and what should I study?*: a multi-step answer that walks your submissions → problems → verdict history → learning material and cites each path. Sign in as **instructor@example.com** and ask *Summarize verdicts and separate judge errors from code errors* and *Which learners may share a prerequisite gap?* (aggregate evidence across the contest, no source code, no hidden tests). Known quirk: the word "summarize" currently matches the problem name "Sum of Two Numbers", so that particular prompt is scoped to that one problem (see "What is implemented vs. not" below).

Gemini notes: the free tier is limited (about 20 requests/day per model with the key used in development). When a model is out of quota the service tries the next model in the fallback list and the badge shows which model answered; if all fail you still get the evidence-only answer with the reason. Turn the model off completely with `AI_MODEL_DISABLED=1` (see step 9).

### 9. Run the automated tests

The stack from step 3 must be running. The deterministic suites call the AI service, so they **refuse to run while Gemini is enabled** (they would spend quota and their wording checks assume evidence-only mode). If you set a key, switch the model off for the run, and back on afterwards:

```bash
# bash / zsh / Git Bash                          # PowerShell
AI_MODEL_DISABLED=1 docker compose up -d --force-recreate ai      # $env:AI_MODEL_DISABLED="1"; docker compose up -d --force-recreate ai; Remove-Item Env:AI_MODEL_DISABLED
# ... run the suites below ...
docker compose up -d --force-recreate ai                          # re-enable Gemini (uses the key in .env)
```

(Without a key there is nothing to switch.) `python scripts/run_eval.py` in step 10 does all of this for you.

| Command (from the repository root) | Covers | Expected on success (this commit) |
| --- | --- | --- |
| `python scripts/e2e.py` | real end-to-end flow: login, join, every verdict from real Docker judging, async lifecycle, leaderboard, learner AI over own code, multi-hop | `46 passed, 0 failed` (about 30 s) |
| `python scripts/security.py` | authentication, RBAC, cross-organization access, blocked org self-registration, private data, hidden-test and secret protection, AI refusals, key not in the browser | `47 passed, 0 failed` |
| `python scripts/recovery.py` | infrastructure failure: judge image removed mid-run → retry recovery, then exhaustion → `JUDGE_ERROR` without corrupting scores (it removes and restores the judge image) | `Recovery demo: OK` (7 checks) |
| `docker compose run --rm --no-deps ai python -m pytest -q` | AI unit tests: guards, grounding, Gemini error handling (fake Gemini server), multi-hop, own-code evidence, real pgvector. Never calls the real Gemini API | `67 passed` |
| `docker build --target build -t shodh-backend-unit Backend` then `docker run --rm shodh-backend-unit npx jest` | backend unit tests (verdict comparison, idempotency, retry policy, leaderboard ranking, contest status, crash labelling). With Node 22 installed you can instead run `cd Backend && npm install && npm test` | `Tests: 10 passed, 10 total` |
| `python scripts/retrieval_eval.py` | offline retrieval comparison (BM25 vs vector vs hybrid) using cached real embeddings; **no Gemini calls** | prints the Hit@1 / Hit@3 / MRR table |

On Linux/macOS use `python3`. The suites add a few submissions and users to the demo database; that is harmless (re-seed or `down -v` to start clean).

### 10. Run the complete evaluation (single command)

```bash
python scripts/run_eval.py        # python3 on Linux/macOS; about 4-5 minutes (287 s in the fresh-clone run above; longer on a cold Docker cache)
```

This one command does everything in step 9 and more: it starts the stack if it is not running, switches the AI service to evidence-only mode (**Gemini is never called, no quota is used**) and restores it afterwards, builds all images (backend `nest build`, frontend `tsc` + `vite build`, ai), then runs the AI unit tests, backend unit tests, the E2E flow, the security suite, the recovery suite and the retrieval comparison. It prints a summary, **writes [`EVALUATION.md`](EVALUATION.md)** (per-check expected vs actual, submission-to-verdict timings, retrieval table, known issues) and **exits non-zero if any stage fails**. Success ends with `Overall: PASS in ...s -> EVALUATION.md`. Options: `--skip-builds`, `--no-start`, `--keep-model-off`. Note that the AI service and backend containers are briefly recreated, so the UI is unavailable for a moment. Results of the last run are in the [Evaluation](#evaluation) section.

Optional, manual, **spends Gemini quota** (needs `GEMINI_API_KEY`; exits with code 2 if none is configured): `python scripts/gemini_live.py` verifies the real Gemini path.

### 11. Stop, reset and troubleshoot

| Task | Command |
| --- | --- |
| Stop (keep data) | `docker compose down` |
| Start again | `docker compose up -d` |
| Stop and delete all data (fresh database) | `docker compose down -v` |
| See logs | `docker compose logs -f backend` (also `ai`, `frontend`, `postgres`, `redis`) |

| Symptom | Fix |
| --- | --- |
| `Cannot connect to the Docker daemon` / build cannot start | Start Docker Desktop (Linux containers) and retry. |
| `port is already allocated` | Free the port (5173, 3000, 8000, 5432 or 6379) or stop the other program. |
| Page loads but login shows a server/502 error right after start | The backend is still running migrations and seeding; wait for `API listening on port 3000` in `docker compose logs backend`. |
| Backend exits with `Foreign key constraint violated` while seeding | You have an old database volume from an earlier version: `docker compose down -v` and start again. |
| Submissions stay `QUEUED`/error immediately | The judge needs the Docker socket and the `shodh-judge:v1` image: check `docker compose ps` and that the `judge-image` job completed (`docker compose logs judge-image`). |
| AI shows `Gemini unavailable: ...` | Expected without a key, or when the free-tier quota is used up; the evidence-only answer still works. Check `curl http://localhost:5173/ai/health`. |

## Overview

| Service | Tech | Port | Role |
| --- | --- | --- | --- |
| `Backend/` | NestJS, Prisma, PostgreSQL, Redis + BullMQ, dockerode | 3000 | Auth, orgs/roles, contests, problems, submissions, async judge worker, leaderboard |
| `ai/` | Python, FastAPI, `google-genai`, psycopg + pgvector | 8000 | Read-only assistant: guards, authorized evidence gathering, hybrid retrieval, relationship traversal, Gemini, grounding |
| `Frontend/` | React + Vite + Tailwind, served by nginx | 5173 | Login, contests, problems, C++ editor, live verdict, leaderboard, AI panel |
| `postgres` | `pgvector/pgvector:pg16` | 5432 | Relational data (core) + one AI-owned vector table |
| `redis` | Redis 7 | 6379 | BullMQ job queue |

```
React (browser) ──same origin──> nginx ──/api──> NestJS ──> PostgreSQL, Redis/BullMQ ──> Docker judge containers
                                   └────/ai────> FastAPI ──(user's own JWT, GET only)──> NestJS   [authorization boundary]
                                                    ├── pgvector (public learning-material embeddings)
                                                    └── Gemini (server-side key; receives only authorized evidence)
```

## Configuration reference

Configuration is read from `.env` (copied from `.env.example`); every variable has a default in `docker-compose.yml`, so the file is optional. After editing it, apply with `docker compose up -d --force-recreate ai` (AI variables) or `docker compose up -d` (others).

| Variable | Default | Meaning |
| --- | --- | --- |
| `GEMINI_API_KEY` | empty | Gemini key (optional). Empty = evidence-only AI. Only the `ai` container receives it. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | primary model |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite` | tried in order when the primary is rate-limited, failing or unavailable |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-001` | embeddings for pgvector retrieval (768 dimensions) |
| `AI_MODEL_DISABLED` | empty | `1` = never call Gemini (evidence-only, no quota use) |
| `AI_TIMEOUT_S` | `20` | per-model-call timeout |
| `AI_RATE_LIMIT_PER_HOUR` | `60` | model calls per user per hour (policy refusals are not counted) |
| `JWT_SECRET` | `dev-only-change-me` | backend token signing secret: change it for anything shared |

Other backend settings (`JWT_EXPIRES_IN`, `CORS_ORIGIN`, `JUDGE_TIMEOUT_MS`, `MAX_SOURCE_CODE_BYTES`) have working defaults in `docker-compose.yml`. Details on the Gemini integration:

- **`GEMINI_API_KEY`** (in `.env`, git-ignored): get one at <https://aistudio.google.com/apikey>. It is passed only to the `ai` container; the frontend container has no access to it. Without it the assistant runs in **evidence-only mode** (visible notice in the UI) and everything else works.
- `GEMINI_MODEL` (default `gemini-3.6-flash`), `GEMINI_FALLBACK_MODELS` (default `gemini-3.5-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`; tried in order when a model is rate-limited, erroring or unavailable), `GEMINI_EMBED_MODEL` (default `gemini-embedding-001`, 768 dims), `AI_TIMEOUT_S` (20), `AI_RATE_LIMIT_PER_HOUR` (60 model calls per user), `AI_MODEL_DISABLED=1` (force evidence-only mode: no Gemini calls, no quota use).
- **Free-tier quota:** with the key used during development, `gemini-3.6-flash` allowed **20 requests/day** (`limit: 20` in Google's 429 message). Quotas are per model, so on a 429/5xx/timeout/404 the service tries the next model in `GEMINI_FALLBACK_MODELS` (max 4 attempts, 45 s budget; a model Google answers 404 for is skipped for an hour, and when every attempt fails the UI reports the most informative reason plus the models tried, so a dead last fallback can no longer hide a quota problem). `gemini-2.5-flash` is listed by the models API but returns 404 *no longer available to new users* for this key, so it is no longer in the defaults and the answer badge shows which model answered; if all fail it degrades to evidence-only. Keep some quota for your demo (the regression scripts below can be run with `AI_MODEL_DISABLED=1`).
- Apply a new key: `docker compose up -d --force-recreate ai`.
- Reset the database (needed once if you previously ran the older `postgres:16-alpine` image, and whenever the seed changes IDs): `docker compose down -v`.

## Learner AI: reasoning over your own submission

Asking "Why did my latest submission get this verdict?", "Review my latest submission.", "What part of my code should I investigate?" or "Give me a hint about my wrong answer" now gives the assistant the learner's **actual code** next to the problem and the judge result. The backend stays the authority: `GET /submissions/{id}` returns `sourceCode` only to the owner (or staff); the AI additionally re-checks `submission.userId == current user` and only ever reads the learner's *own* latest submission. If the requested submission id is not yours, the answer says it is not accessible and does **not** substitute another submission.

| Evidence | What it contains |
| --- | --- |
| `P1` problem | statement **with its stated constraints**, input/output format, limits, public examples (hidden tests are never included) |
| `S1` submission | latest verdict, status, score, language, and a *compact* history: number of other attempts, how many were accepted, counts by verdict and the three most recent (no raw array of every verdict) |
| `X1` judge | tests passed / total, wall time, execution status (the judge does not say which test failed, and the answer says so) |
| `O1` compiler | the compiler's output for a compilation error (own code, no test data involved) |
| `R1` crash kind | for a runtime error only a fixed label (`segmentation fault`, `floating point exception`, ...). Raw runtime stdout/stderr are **withheld** because a program can echo hidden-test data into them |
| `F1` source | the learner's own code, line-numbered (truncated explicitly above 6000 characters), shown in full in the UI's evidence panel |
| `H*` static checks | line-referenced hypotheses from reading the code, e.g. a 32-bit `int` used where the statement's bounds make the worst-case sum/product exceed 2,147,483,647 (bounds are parsed from the statement text, not hardcoded to a problem) |
| `M*` material | retrieved learning notes |

Answers separate **observed** facts (the judge's verdict, tests passed), **hypotheses** inferred from the code and the constraints, and **not established** items (which hidden test failed). During a live contest the hint policy still applies: conceptual guidance pointing at lines of *your own* code, no corrected code, no fenced code blocks (stripped even if the model produced them), no step-by-step solution. The verdict and score are never touched by the AI.

Real output (evidence-only mode) for an intentionally wrong `int a, b; cout << a + b;` solution to *Sum of Two Numbers*, whose statement says `-5000000000 <= A, B <= 5000000000`:

```
OBSERVATION  The judge recorded WRONG_ANSWER ... [S1]
OBSERVATION  Judge recorded 2/4 tests passed ... The judge does not tell learners which test failed. [X1]
HYPOTHESIS   Line 5 declares 32-bit `int` (max 2,147,483,647) and the code uses no 64-bit type, but the statement allows
             values up to 5,000,000,000, so the worst-case sum is about 10,000,000,000, which does not fit. Integer
             overflow is a likely cause (hypothesis: the judge does not say which test failed). [H1, F1]
NOT ESTABLISHED  The judge does not reveal which hidden test failed, so the exact cause is unconfirmed.
```

With a Gemini key the model receives the same evidence (including `F1`) and writes the explanation; the same validation applies. **Privacy note:** the learner's own source code is sent to Google's Gemini API as part of the evidence when the model is enabled. The seeded problem statements were given explicit constraints so this kind of reasoning has something to check against.

## Evaluation

One command runs the whole deterministic evaluation and writes [`EVALUATION.md`](EVALUATION.md) (per-check expected vs actual, timings, retrieval comparison, known issues):

```bash
python scripts/run_eval.py      # about 3 minutes; Windows/macOS/Linux; no shell env-var syntax needed
```

It starts the stack if needed and forces the AI service into evidence-only mode (**Gemini is never called and no quota is used**; the previous mode is restored afterwards, `--keep-model-off` skips that). It then runs the Docker builds (including `tsc` + `vite build`), the AI unit tests (real pgvector), the backend unit tests, the real-Docker E2E flow, the security suite, the recovery suite and the offline retrieval comparison, and exits non-zero if any stage fails. The individual scripts still work on their own (`scripts/e2e.py`, `security.py`, `recovery.py`, `retrieval_eval.py`); `e2e.py` and `security.py` refuse to run while Gemini is enabled (override with `ALLOW_LIVE_MODEL=1`). `scripts/gemini_live.py` is the **manual** live check (needs `GEMINI_API_KEY`, spends quota, exits 2 if not configured) and is deliberately not part of `run_eval.py`.

### Evaluation summary (from the last generated `EVALUATION.md`)

Last run: `python scripts/run_eval.py` on 2026-09-19 07:18 UTC, commit `761c2d7+uncommitted`. Mode: deterministic, AI_MODEL_DISABLED=1 (Gemini was **not** called). Overall: **PASS**. Total evaluation time: **245 s**.

| Area | Expected | Actual | Result | Evidence | Time |
|------|----------|--------|--------|----------|------|
| Docker builds (backend `nest build`, frontend `tsc`+`vite build`, ai image) | all three images build without error | all built | PASS | `docker compose build backend frontend ai` | 124 s |
| AI unit tests (guards, grounding, Gemini error handling via fake SDK server, multi-hop, real pgvector) | 0 failed, 0 skipped (pgvector DB reachable) | 67 passed, 0 failed, 0 skipped | PASS | `ai/tests/*.py` via `docker compose run ai pytest` | 10 s |
| Backend unit tests (verdict compare, idempotency, retry policy, leaderboard ranking, contest status) | all suites and tests pass | 10/10 tests, 6/6 suites passed | PASS | `Backend/src/**/*.spec.ts` via host `npx jest` | 27 s |
| Normal end-to-end flow (login, contest, real Docker judging of all verdicts, leaderboard, async lifecycle, AI + multi-hop) | every check passes | 46/46 checks passed | PASS | `scripts/e2e.py` | 25 s |
| Security / access control (authN, RBAC, cross-org, org self-join blocked, private data, AI protections, key not in browser) | every check passes | 47/47 checks passed | PASS | `scripts/security.py` | 10 s |
| Recovery (judge image lost mid-run: retry recovery + exhaustion -> JUDGE_ERROR, no score corruption) | every check passes | 7/7 checks passed | PASS | `scripts/recovery.py` | 15 s |
| Retrieval comparison (BM25 vs BM25+rerank vs vector vs hybrid, cached real Gemini embeddings, 0 API calls) | runs offline; reports Hit@1 / Hit@3 honestly | BM25 Hit@1 14/15, Hit@3 15/15; hybrid Hit@1 14/15, Hit@3 15/15 | PASS | `scripts/retrieval_eval.py`, `ai/eval/queries.json` | 4 s |
| Live Gemini verification (`scripts/gemini_live.py`) | n/a in the deterministic run | not executed: would consume free-tier quota | NOT RUN | `scripts/gemini_live.py` (manual) | 0 s |
| **Total evaluation time** | - | - | PASS | `scripts/run_eval.py` | 245 s |

Submission -> verdict latency with the real Docker judge (from the E2E flow, n=7, min 2.15 s, median 2.68 s, max 5.17 s. Includes queueing, container start, `g++` compile and test runs; poll granularity adds up to 0.5 s.):

| Problem | Verdict | Seconds (POST -> final status, polled every 0.5 s) | Status sequence |
|---|---|---|---|
| Sum of Two Numbers | ACCEPTED | 5.17 | QUEUED -> RUNNING -> COMPLETED |
| Sum of Two Numbers | WRONG_ANSWER | 2.68 | QUEUED -> RUNNING -> COMPLETED |
| Sum of Two Numbers | COMPILATION_ERROR | 2.17 | QUEUED -> RUNNING -> COMPLETED |
| Sum of Two Numbers | RUNTIME_ERROR | 2.15 | QUEUED -> RUNNING -> COMPLETED |
| Product | TIME_LIMIT_EXCEEDED | 3.81 | QUEUED -> RUNNING -> COMPLETED |
| Sum of Two Numbers | WRONG_ANSWER | 2.69 | QUEUED -> RUNNING -> COMPLETED |
| Sum of Two Numbers | WRONG_ANSWER | 2.64 | QUEUED -> RUNNING -> COMPLETED |

AI response time in evidence-only mode (no model call):

| Answer source | n | median server ms | max server ms |
|---|---|---|---|
| fallback | 11 | 52 | 182 |
| policy | 3 | 28 | 54 |

- **Live Gemini (not part of the deterministic run):** measured over roughly ten live calls, about 1.2-1.7k input and 1.0-1.7k output tokens per answer (output includes thinking tokens), 6-16 s per answer, about $0.003 per answer under the configurable price defaults (`AI_PRICE_*`, assumed rather than confirmed for 3.x); the free tier allowed 20 requests/day/model with this key. The retrieval evaluation's cache capture used exactly 2 batched embedding requests. The live check `gemini_live.py` passed 13/14 once and the failure was fixed and re-checked by hand (details under "What is implemented vs. not").
- **Remaining issues** are listed at the end of `EVALUATION.md` and in "What is implemented vs. not" below. Not run by `run_eval.py`: the live Gemini check and `Backend/test/*.e2e-spec.ts` (they start a second worker and mutate the DB).

## Retrieval evaluation

**Question:** does adding pgvector embeddings to BM25 (hybrid, RRF fusion) retrieve the right learning note better than BM25 alone?

- **Dataset:** 15 queries over the 8 learning notes in `ai/materials/`, in `ai/eval/queries.json` (8 keyword queries, 7 paraphrases with little lexical overlap, e.g. "my sums turn negative for huge inputs"). The queries and their relevant note ids were written **before** any results were computed and not changed afterwards.
- **Method:** four arms scored on the same queries: raw BM25; BM25 plus the heuristic rerank (tag/title boost, deprecated-note demotion; this is the fallback path used when vectors are unavailable); vector-only (pgvector cosine); and the **production hybrid** (`assistant.hybrid_search`: BM25 + pgvector + reciprocal-rank fusion + freshness rerank). Metrics: Hit@1, Hit@3 (any relevant note in the top k) and MRR. The embeddings are real `gemini-embedding-001` (768d) vectors captured once with `--refresh` (2 batched embedding requests) and cached in `ai/eval/embeddings_cache.json`, so the comparison is reproducible with **zero** Gemini calls; the hybrid arm runs the real code path against a scratch pgvector table.

| Approach | Group | n | Hit@1 | Hit@3 | MRR |
|---|---|---|---|---|---|
| BM25 (raw scores) | all | 15 | 14/15 | 15/15 | 0.956 |
| BM25 (raw scores) | keyword | 8 | 8/8 | 8/8 | 1.0 |
| BM25 (raw scores) | paraphrase | 7 | 6/7 | 7/7 | 0.905 |
| BM25 + heuristic rerank (fallback path) | all | 15 | 14/15 | 15/15 | 0.967 |
| BM25 + heuristic rerank (fallback path) | keyword | 8 | 8/8 | 8/8 | 1.0 |
| BM25 + heuristic rerank (fallback path) | paraphrase | 7 | 6/7 | 7/7 | 0.929 |
| Vector only (pgvector cosine) | all | 15 | 14/15 | 15/15 | 0.967 |
| Vector only (pgvector cosine) | keyword | 8 | 8/8 | 8/8 | 1.0 |
| Vector only (pgvector cosine) | paraphrase | 7 | 6/7 | 7/7 | 0.929 |
| Hybrid: BM25 + pgvector + RRF + rerank (production) | all | 15 | 14/15 | 15/15 | 0.967 |
| Hybrid: BM25 + pgvector + RRF + rerank (production) | keyword | 8 | 8/8 | 8/8 | 1.0 |
| Hybrid: BM25 + pgvector + RRF + rerank (production) | paraphrase | 7 | 6/7 | 7/7 | 0.929 |

- **Result, stated plainly:** all four approaches tie on Hit@1 (14/15) and Hit@3 (15/15). Hybrid does **not** beat BM25 on these metrics. The only differences are in MRR: raw BM25 ranked the right note 3rd for one paraphrase ("my sums turn negative for huge inputs"), the vector-only arm ranked it 1st but missed a different paraphrase at rank 1 ("the program never terminates and gets killed"), and hybrid and BM25+rerank both put the first at rank 2 and kept the second at rank 1. The rank-2 improvement over raw BM25 comes from the heuristic rerank, not from the vectors (BM25+rerank scores the same as hybrid).
- **What this demonstrates:** on an 8-note corpus with keyword-rich notes, lexical retrieval is already sufficient, and the vector side is at least not harmful and complements BM25 on paraphrases (each method fixes a query the other ranks lower). It does **not** show that hybrid is better. The live check earlier (a paraphrase retrieved the integer-overflow note via real embeddings) is an anecdote, not evidence.
- **Limitations:** tiny corpus and query set (one query moves a metric by 6.7 points), queries authored by the same person who wrote the notes, single embedding model, no confidence intervals, corpus notes are seed-flavoured. Re-run: `python scripts/retrieval_eval.py` (cached) or `--refresh` (2 embedding requests, needs the key); adding or editing notes requires `--refresh`.

## AI API (`/ai/ask`)

`POST /ai/ask` (through the frontend proxy: `http://localhost:5173/ai/ask`; directly: `http://localhost:8000/ai/ask`; FastAPI's own docs at `http://localhost:8000/docs`). **Authentication is required:** `Authorization: Bearer <JWT>` from `POST /api/auth/login`; the same token is forwarded to the backend, so the caller only ever gets evidence they may see (401 without/with a bad token, 403 for a contest outside the caller's organizations, 429 above the model-call rate limit, 502 if evidence cannot be gathered).

Request body: `question` (1-1000 chars, free text), `contestId` (required), `problemId` (optional), `submissionId` (optional; must be your own unless you are staff).

```bash
TOKEN=$(curl -s -X POST http://localhost:5173/api/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"learner1@example.com","password":"Password123!"}' | python -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")
curl -s -X POST http://localhost:5173/ai/ask -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"Give me a hint for this problem","contestId":"33333333-3333-4333-8333-333333333331","problemId":"44444444-4444-4444-8444-444444444441"}'
```

Response (real output in evidence-only mode, long texts trimmed): `answer`, `claims[]` (`kind` is `observation` or `hypothesis`, each cites `evidence` ids), `confidence` (`high|medium|low`), `missing[]` (what could not be established), `needs_clarification`, `evidence[]` (the items actually cited: `id`, `kind`, `title`, `text`, `ref`, optional `updated` and `stale`), `source` (`llm|fallback|policy`), `model`, `retrieval`, `degraded` (why Gemini was not used), `refusal`, `requestId`, `mode`, `policy`, `timingMs`.

```json
{
  "answer": "Hint 1: restate the task in your own words and check it against the public examples in the statement. [P1]\nHin...",
  "claims": [
    {
      "text": "Hint 1: restate the task in your own words and check it against the public examples in the statement.",
      "kind": "hypothesis",
      "evidence": [
        "P1"
      ]
    },
    {
      "text": "Hint 2: think about what could go wrong with the input values - see 'Checklist for Wrong Answer verdicts'.",
      "kind": "hypothesis",
      "evidence": [
        "M1"
      ]
    }
  ],
  "confidence": "medium",
  "missing": [
    "The judge does not reveal which hidden test failed, so the exact cause is unconfirmed."
  ],
  "needs_clarification": false,
  "evidence": [
    {
      "id": "P1",
      "kind": "problem",
      "title": "Sum of Two Numbers",
      "text": "Read two integers A and B and print A+B. Input: Two integers A and B. ...",
      "ref": "GET /contests/33333333-3333-4333-8333-333333333331/problems/44444444-4444-4444-8444-444444444441",
      "updated": "2026-09-19T05:57:04.957Z"
    },
    {
      "id": "S1",
      "kind": "submission",
      "title": "Submission fa37b57e",
      "text": "Verdict WRONG_ANSWER (status COMPLETED), score 0, submitted 2026-09-19...",
      "ref": "GET /submissions/fa37b57e-e2b8-409a-b892-9ccadd1d0c3b",
      "updated": "2026-09-19T05:58:36.805Z"
    },
    {
      "id": "M1",
      "kind": "material",
      "title": "Checklist for Wrong Answer verdicts",
      "text": "Wrong Answer means the program ran and finished but printed something ...",
      "ref": "materials/edge-cases-wrong-answer.md",
      "updated": "2026-08-01"
    },
    {
      "id": "M2",
      "kind": "material",
      "title": "Reading input and printing output in C++",
      "text": "Read every value the statement describes with `std::cin >> x;` in the ...",
      "ref": "materials/cpp-input-output.md",
      "updated": "2026-08-01"
    }
  ],
  "source": "fallback",
  "degraded": "Gemini unavailable: the model is switched off (AI_MODEL_DISABLED). Showing an evidence-only answer built from your authorized data.",
  "retrieval": "bm25 (vector skipped: AI_MODEL_DISABLED)",
  "requestId": "33fd14c5",
  "mode": "learner",
  "policy": "LIVE CONTEST HINT POLICY: give conceptual hints and debuggin...",
  "timingMs": 216
}
```

## Architecture and decisions

- **Submission flow.** `POST /contests/:id/problems/:pid/submissions` stores the submission (`QUEUED`) and enqueues a BullMQ job with `jobId = submissionId`. The worker claims the row (`RUNNING`), starts a throw-away container from `shodh-judge:v1` (no network, memory/CPU/PID limits, per-test `timeout`, source copied in via tar, container removed in `finally`), compiles with `g++ -O2 -std=c++17`, runs tests, and writes the verdict + score in one transaction guarded by `status = RUNNING`. Verdicts: `ACCEPTED`, `WRONG_ANSWER`, `COMPILATION_ERROR`, `RUNTIME_ERROR`, `TIME_LIMIT_EXCEEDED`, `JUDGE_ERROR`. Score = problem points on first accept; leaderboard = score, then total solve time.
- **Queue/retry/recovery.** Docker/worker failures are retried (3 attempts, exponential backoff), each recorded as a `JudgeIncident`; after exhaustion the submission is `INFRASTRUCTURE_ERROR` / `JUDGE_ERROR` with score 0, never Wrong Answer. Completed rows are never re-judged. `scripts/recovery.py` demonstrates recovery (image restored during backoff -> ACCEPTED, exactly one completed execution) and exhaustion. Worker restarts rely on BullMQ stalled-job recovery plus re-claimable `RUNNING` rows (**not tested by killing the worker**). Heavier load: run more worker replicas (jobs are idempotent by id) - untested.
- **Database responsibilities.** PostgreSQL: users, orgs, memberships, contests, problems, tests, submissions, judge executions/incidents (relational, constraints). pgvector (same PostgreSQL, table `ai_material_embeddings`): embeddings of the public learning notes only. Redis: queue. **There is no graph database.**
- **AI authorization model.** The AI code never queries core tables (its only SQL touches `ai_material_embeddings`; note it does hold the shared DB superuser credentials, see limitations). It forwards the caller's own JWT to the backend and only issues `GET`s, so org membership, own-submissions-only and hidden-test stripping are enforced by the same backend code that protects the UI. Hidden tests are additionally dropped for instructors, and source code of other learners never enters staff evidence. Only evidence gathered this way is sent to Gemini. Question-level guards (hidden tests, other learners' code, instructor-only info, secrets, full solutions while the contest is live) refuse **before** any model call; these are a second layer, not the security boundary.
- **Gemini integration** (`ai/app/gemini.py`, the only provider-specific file): `google-genai` SDK, structured JSON output (`response_schema`), temperature 0.2, 20 s timeout, model-name fallback on 404. Failures (missing key, invalid key, 429, timeout, 5xx, empty/blocked reply, malformed reply) raise `LLMUnavailable`; the assistant then returns an evidence-only answer with a visible `Gemini unavailable: <reason>` notice, and the contest is unaffected. The key is never logged, returned, or sent to the browser; if a model echoes it, it is redacted. The system prompt tells Gemini to answer only from evidence, separate observations from hypotheses, treat evidence text as data, never override the judge, and follow the hint policy.
- **Grounding.** Evidence items: `P` problem, `S` submission, `X` judge execution, `O` compiler output, `H` static checks of the learner's own code, `M` learning material (with `updated` and stale/DEPRECATED flags), `A` instructor aggregate, `G`/`K` relationship paths. Claims are `observation` or `hypothesis`; an observation without valid evidence ids is downgraded; confidence is capped at *medium* when information is missing or stale; gaps are listed under "Not established". During a live contest code blocks are stripped from answers.
- **Retrieval.** (1) Entity resolution: free-text problem names ("the max one", typos) -> contest problem, asking for clarification when ambiguous. (2) BM25 over learning notes + tag/title boost + deprecated-note demotion. (3) When `GEMINI_API_KEY` and the DB are available: Gemini embeddings stored in pgvector, cosine search, fused with BM25 by reciprocal-rank fusion, then freshness rerank (`retrieval: hybrid`). Otherwise BM25 alone and the response says why (`retrieval: bm25 (vector unavailable: ...)`). Indexing is lazy, idempotent (content hash) and removes deleted notes.
- **Multi-hop.** For "what did I struggle with / what should I study" the service builds a per-request typed-edge graph over the caller's authorized submissions (`User -SUBMITTED-> Submission -FOR-> Problem`, `Submission -RESULTED_IN-> Verdict`, `Problem -RELATED_TO-> Material`), walks it (learner -> submissions -> problems + verdict history -> material) and cites each path (`G*`). Judge errors are not counted as struggles. For instructors it groups learners by the material their failures point to and offers a *hypothesis* of a shared prerequisite gap (`K*`).
- **Cost and limits.** Rate limit per user (policy refusals are free), 20 s timeout, `GET /ai/usage` reports calls, tokens and a paid-tier estimate (`AI_PRICE_*` env). Measured over ~10 live calls: roughly 1.2-1.7k input and 1.0-1.7k output tokens per answer (output includes thinking tokens), which the usage endpoint prices at about $0.003 per answer using the configurable `AI_PRICE_*` defaults (assumed Flash list prices, not confirmed for 3.x); on the free tier it costs nothing but is limited to ~20 requests/day per model with this key.
- **Logs.** `req=<id> user mode contest problem source confidence refusal retrieval evidence=[ids] gather_ms answer_ms total_ms` per AI request (id returned to the UI); backend logs submission creation, enqueue, judge start/retry/completion with submission ids.

## Why a bounded pipeline instead of an autonomous agent

The assistant does **not** let the model choose tools or loop. For each question the service runs a fixed, bounded, read-only evidence pipeline (at most about six backend `GET`s plus retrieval), and Gemini only phrases an answer from that evidence; the answer is then validated (`ground()`). This was chosen deliberately, not just for time: (1) **security** - the set of data that can reach the model is decided by code that mirrors the backend's authorization, not by a model's tool choices, so prompt injection in a learner's code or question cannot widen access; (2) **reliability and cost** - a fixed pipeline has predictable latency and a single model call (a tool loop multiplies calls, and the free tier here allowed 20 requests/day/model); (3) **reproducibility** - the same question and data give the same evidence, so refusals, grounding and multi-hop paths can be unit-tested without a model; (4) **scope** - the questions in scope (why did my submission fail, what should I study, which learners may share a gap) need a known handful of lookups. An agent would be useful when the needed evidence is open-ended (e.g. "investigate everything that changed near the time this contest's failures started") and the next lookup depends on what the previous one returned; a bounded tool loop (a handful of read-only tools, max ~4 steps, stop on sufficient evidence) is the natural extension, but it is **not implemented**, so the assignment's "agentic tool use" is not met. The clarification behaviour that exists (ambiguous problem names) is rule-based.

## Worker model, Redis persistence and recovery

- **Worker model.** The BullMQ worker runs inside the NestJS API process with the library-default concurrency of **1**, so submissions are judged one at a time (roughly 3-12 s each in the E2E timings, i.e. on the order of 5-20 submissions per minute). Under heavier load you would raise the processor `concurrency`, and/or run more backend replicas (each starts a worker; jobs are idempotent by `jobId = submissionId` and the row claim is a guarded `updateMany`), ideally splitting a worker-only process from the API. **None of this is implemented or load-tested**; there is no queue-depth limit or backpressure.
- **Retries.** A failed judge attempt (container cannot start, Docker error, timeout) is retried up to 3 attempts with exponential backoff (2 s base). Student errors (compile error, wrong answer, runtime error, TLE) are final and never retried.
- **Infrastructure errors.** Every failed attempt is stored as a `JudgeIncident`; after the last attempt the submission ends as `INFRASTRUCTURE_ERROR` / `JUDGE_ERROR` with score 0 and never as Wrong Answer, and leaderboard scores are unaffected. `scripts/recovery.py` demonstrates both a recovery inside the retry window (judge image restored -> ACCEPTED with exactly one COMPLETED execution) and exhaustion.
- **Worker restart.** A crashed or restarted worker relies on BullMQ's stalled-job handling plus the fact that `RUNNING` rows are re-claimable; what happens to a job that stalls repeatedly was not verified. **Not tested by killing the worker.** A judge container orphaned by a crash is not swept (containers are neither labelled nor auto-removed).
- **Redis persistence.** Redis runs with the `redis:7-alpine` image defaults: **no named volume and no AOF**, so the queue is not configured as a durable deployment; recently enqueued jobs can be lost when the container is restarted or recreated. Because the submission row is written to PostgreSQL first, the row survives, but nothing re-enqueues it: **a submission whose job is lost stays `QUEUED` indefinitely** (no startup reconciliation exists). Fixes would be a Redis volume with `--appendonly yes` and a startup sweeper that re-enqueues stale `QUEUED`/`RUNNING` rows (idempotent thanks to `jobId`); neither is implemented.

## Learning-material ingestion

Learning material is a directory of Markdown files, `ai/materials/*.md`. Format: a front-matter block delimited by `---` lines, then the note body.

```markdown
---
title: Integer overflow and choosing the right type
tags: overflow int long long sum product        # space-separated keywords, boosted in BM25
updated: 2026-08-01                              # shown as evidence date; used for staleness display
status: current                                  # current | deprecated (deprecated notes are demoted and flagged "may be outdated")
superseded_by: cpp-input-output                  # optional, for deprecated notes
---
Plain-text body used for BM25 and for the embedding.
```

To add or change material: add or edit a file in `ai/materials/`, then **rebuild and restart the AI service** (`docker compose up -d --build ai`). A rebuild is currently required: the notes are copied into the image at build time and the BM25 index is built at process start; there is no runtime ingestion API. The vector index needs no manual step: on the first question after the restart, `vectors.ensure_indexed` embeds only new or changed notes (content hash) with Gemini and deletes vectors of removed notes (this needs `GEMINI_API_KEY`; without it retrieval is BM25-only and says so). Contests, problems, users and submissions are ingested through the backend API (Swagger), and the assistant sees them immediately.

## Backend reference: lifecycle, verdicts, scoring and API flow

**Authentication:** `POST /auth/register` (username, email, password; creates a learner with no organization), `POST /auth/login` (returns a JWT), `GET /auth/me`. Passwords are bcrypt-hashed; `passwordHash` is never returned. Roles are `ADMIN`, `INSTRUCTOR`, `LEARNER`, globally and per organization (see "Access rules").

**Submission lifecycle:** `QUEUED` -> `RUNNING` -> `COMPLETED`; if the infrastructure retries are exhausted the submission ends as `INFRASTRUCTURE_ERROR` with verdict `JUDGE_ERROR`. Clients poll `GET /submissions/{id}` until a final status. Student mistakes are never retried; infrastructure failures are retried up to 3 attempts.

| Judge result | Verdict |
| --- | --- |
| `g++` fails to compile | `COMPILATION_ERROR` |
| the program crashes or exits non-zero | `RUNTIME_ERROR` |
| wall-time limit exceeded (an out-of-memory kill, exit code 137, is currently also reported this way) | `TIME_LIMIT_EXCEEDED` |
| output differs from the expected output (line endings and trailing whitespace are normalised) | `WRONG_ANSWER` |
| every test passes | `ACCEPTED` |
| Docker or worker failure | `JUDGE_ERROR` (status `INFRASTRUCTURE_ERROR`), never a student verdict |

**Scoring:** each problem has fixed `points`; the first `ACCEPTED` submission of a participant on a problem awards them, repeat accepts add nothing, every other verdict scores 0. **Leaderboard** (`GET /contests/{id}/leaderboard`, polled by the UI): higher total score first, then lower total solve time (sum of the time from contest start to each problem's first accept), then ascending user id as a deterministic tie-break.

**Example API flow** (bash; the same calls the UI makes, all documented in Swagger at <http://localhost:3000/api/docs>):

```bash
API=http://localhost:5173/api
CONTEST=33333333-3333-4333-8333-333333333331
PROBLEM=44444444-4444-4444-8444-444444444441      # Sum of Two Numbers
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"learner1@example.com","password":"Password123!"}' | python -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")

curl -s -X POST $API/contests/$CONTEST/join -H "Authorization: Bearer $TOKEN"        # 201, or 409 if already joined
curl -s $API/contests/$CONTEST/problems -H "Authorization: Bearer $TOKEN"            # problems + public tests only
SUB=$(curl -s -X POST $API/contests/$CONTEST/problems/$PROBLEM/submissions -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"language":"cpp","sourceCode":"#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}"}')
echo "$SUB"                                                                          # {"submissionId":"...","status":"QUEUED"}
curl -s $API/submissions/<submissionId> -H "Authorization: Bearer $TOKEN"            # poll until status COMPLETED
curl -s $API/contests/$CONTEST/leaderboard -H "Authorization: Bearer $TOKEN"
```

**Developing the backend without rebuilding images:** `docker compose up -d postgres redis judge-image`, then `cd Backend && cp .env.example .env && npm install && npx prisma migrate deploy && npx prisma db seed && npm run start:dev` (do not also start the `backend` compose service: both use port 3000). The specs in `Backend/test/*.e2e-spec.ts` (`npm run test:e2e`) start their own app and worker against that Postgres/Redis; they are not part of `run_eval.py` and were not re-run for the latest commit.

## Access rules

| Action | Learner | Instructor (own org) | Admin |
| --- | --- | --- | --- |
| See contests | own-org, non-draft | own-org | all |
| Public tests | yes | yes | yes |
| Hidden tests | never | API only (never via AI) | API only (never via AI) |
| Submission source / results | own only | all in org | all |
| Compiler output | own submissions | yes | yes |
| Manage contests/problems/tests/members | no | own org | yes |
| Join an organization | not possible by registering; an admin/instructor must add you | can add members to own org | yes |
| AI on another learner's submission | refused / not accessible | allowed (aggregate) | allowed |

## What is implemented vs. not

Implemented and tested: Stage 1 backend + Docker judge, Stage 2 UI, evidence-grounded assistant with hint policy and protections, Gemini provider with error handling and fallback, pgvector hybrid retrieval with an offline comparison, multi-hop relationship traversal, security checks (including blocked organization self-join), recovery demo, single-command evaluation.

**Not implemented / incomplete (please read):**
- **No graph database and no GraphRAG.** Relationship traversal is an in-memory structure rebuilt per request from authorized data. Nothing is stored as a knowledge graph; entity duplicates/conflicting evidence across sources are only handled via problem-name resolution and stale-material flags.
- **No agent loop.** The AI does not choose tools; a deterministic pipeline gathers bounded read-only evidence, then Gemini phrases the answer. "Agentic tool use" from Stage 3 is therefore not met.
- **Live Gemini verification was partial.** With a real key, `python scripts/gemini_live.py` passed 13 of 14 checks (grounded problem explanation, hint obeying the live hint policy, verdict explanation citing judge evidence and not changing the verdict, unanswerable and ambiguous questions answered with low confidence and explicit insufficiency, all four protected-information refusals, no key in the browser bundle). The one failure (instructor aggregate answer came back as incomplete JSON because Gemini 3.x thinking tokens consumed the output cap) was fixed (cap 8192, explicit `truncated` error) and the two instructor questions were then re-run live successfully by hand; **the full script was not re-run afterwards** because the daily free-tier quota was used up. Live hybrid retrieval was also confirmed: a paraphrased question ("sums give strange negative numbers when inputs are huge") retrieved the integer-overflow note via real Gemini embeddings + pgvector. Not measured: answer quality beyond these spot checks, latency under load.
- **Model availability is per key/tier.** `gemini-3.6-flash` answered the first ~20 calls, then hit the free-tier daily quota; answers then came from the fallback model `gemini-3.5-flash`. Latency is 6-16 s per answer (mostly thinking time).
- **pgvector unit tests use a synthetic embedder** (they prove storage, cosine search, idempotent indexing, cleanup and fusion, not Gemini's semantic quality). The separate retrieval evaluation uses real cached Gemini embeddings but is tiny (15 queries, 8 notes) and shows parity, not superiority, of hybrid over BM25 (see Retrieval evaluation).
- The AI service connects to Postgres with the same superuser as the backend (should be a separate restricted role); it only touches its own table.
- Judge version history and judge incidents are stored but not exposed through any API, so "did a judge change affect outcomes?" is answered as *not established*. The seed has a single judge version, no duplicate/conflicting-evidence scenarios and no outdated-problem scenario; conflicting evidence is not detected.
- Judge container hardening gaps: no `CapDrop`/`no-new-privileges`, the container is created as root (commands run as an unprivileged user), an out-of-memory kill (exit 137) is reported as TIME_LIMIT_EXCEEDED, stdout is buffered in memory before being truncated to 8000 characters, and orphaned containers are not swept after a crash.
- Redis is not durable and there is no startup reconciliation of `QUEUED`/`RUNNING` submissions; worker restart is untested; judging concurrency is 1 (see "Worker model, Redis persistence and recovery").
- The frontend has no UI for instructors to create contests/problems or view judge incidents (use Swagger), and AI chat history is lost on page refresh. Login is by email (the username is set at registration and displayed).
- The learner AI reads the code but cannot run it: it reasons from the statement, the public examples and the judge result, so a hypothesis about the failing case stays a hypothesis. Raw runtime output from judged runs is deliberately not shown.
- **Known matcher bug (not fixed):** free-text problem-name matching is prefix-based, so a word such as "summarize" matches the problem "Sum of Two Numbers" and can silently scope an instructor's contest-wide question to that one problem.
- Editor is a plain textarea; C++ only; leaderboard is computed on read and polled (no WebSockets).
- The backend mounts `/var/run/docker.sock` (engine-level access): fine for local evaluation, not a hardened sandbox. Postgres/Redis ports are published for convenience.

## How AI-assisted code and design were verified

Code and design were AI-assisted (Claude Code). Verification was by execution, not review alone: real Docker judging of correct/wrong/CE/RE/TLE/overflow submissions (`e2e.py`, run via `run_eval.py`), fault injection by removing the judge image (`recovery.py`), authorization/leak probes including prompt-injection text in the learner's own code (`security.py`), unit tests around guards/grounding/errors/retrieval, a fake-Gemini HTTP server exercising the real SDK, and real pgvector tests. I did not use hardcoded answers; question guards and retrieval operate on the supplied data (new problems, users and notes can be added through the backend API and `ai/materials/*.md`).

## Issues found and fixed while building

- The seeded "hidden overflow" test (1e9+1e9) did not overflow a 32-bit `int`; a wrong solution was `ACCEPTED`. Found by e2e; hidden test is now 2e9+2e9.
- The guard matched "his" inside "this submission" and refused a valid question; fixed with word boundaries + regression test.
- For an off-topic question the assistant attached the learner's latest submission (own data, but irrelevant and sent to the model); it now attaches submissions only when the question is about one.
- The static check flagged `int main` as "32-bit int overflow risk"; fixed + regression test.
- `docker compose up -d ai` restarts the backend, so scripts run right after it hit a starting backend; the scripts now wait for readiness.
- Live Gemini runs exposed three real problems, all fixed: (1) thinking tokens truncated longer answers (malformed JSON) -> larger output cap + explicit `truncated` error; (2) a 429/503 on the primary model degraded the answer although another model was available -> bounded try-next-model; (3) for unanswerable questions Gemini said "insufficient evidence" but labeled confidence *medium* -> prompt now requires *low*, and evidence-gathering gaps are always merged into "Not established".
- The AI unit tests would have called the real Gemini API whenever a key was in the environment; a `conftest.py` now clears Gemini variables for every test.
- **The learner AI never saw the learner's code.** Found by manual testing: the evidence had only verdict, score, execution summary, history and generic material (the source was used for a few static checks but never given to the model). It is now evidence (`F1`), with statement constraints, a compact history, and a crash-kind label; the earlier raw `earlier verdicts: [...]` dump was removed.
- **"Gemini unavailable: model not found" was misleading.** The real cause was daily free-tier quota (429) on `gemini-3.6-flash` and `gemini-3.5-flash`; the last fallback, `gemini-2.5-flash`, is retired for this key (404) and its error was reported instead. Fixed: dead models are skipped, the most informative error wins, and the fallback list now uses models verified to work.
- Asking about another learner's submission id made the AI silently answer about the learner's own latest submission (no leak, but confusing). It now reports "not accessible" instead.
- **Security gap found in the gap analysis and fixed:** public `/auth/register` accepted an `organizationId` and granted membership, so anyone who knew an organization UUID (the seed UUID is in this README) could join it. Registration no longer accepts the field (400); membership is granted only by an org admin/instructor. Covered by 10 new checks in `security.py` and a backend e2e-spec case.
- While building `run_eval.py`: a mis-escaped regex made the AI-test stage report "0 passed" (the runner now fails loudly when it parses no results), and the first version tested the already-running containers rather than the freshly built images (it now recreates the services after building).

## Time spent

Approximate total implementation and evaluation time: **22 hours** (author-reported). This covers the backend, Docker judge, frontend, AI service (Gemini integration, pgvector retrieval, multi-hop evidence), security fixes, the evaluation tooling and the documentation. The work was AI-assisted (see "How AI-assisted code and design were verified").
