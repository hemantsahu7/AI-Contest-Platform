# Shodh-a-Code

Coding-contest platform with a real Docker judge and an evidence-grounded AI assistant.

| Service | Tech | Port | Role |
| --- | --- | --- | --- |
| `Backend/` | NestJS, Prisma, PostgreSQL, Redis + BullMQ, dockerode | 3000 | Auth, orgs/roles, contests, problems, submissions, async judge worker, leaderboard |
| `ai/` | Python, FastAPI | 8000 | Read-only assistant: guards, evidence gathering, BM25 + rerank over learning material, grounded answers |
| `Frontend/` | React + Vite + Tailwind, served by nginx | 5173 | Login, contests, problems, C++ editor, live verdict, leaderboard, AI panel |

nginx serves the SPA and proxies `/api/*` to the backend and `/ai/*` to the AI service, so the browser talks to one origin.

## Run

Requires Docker Desktop / Docker Engine with Compose.

```bash
docker compose up --build
```

Open <http://localhost:5173>. Swagger: <http://localhost:3000/api/docs>.

Optional model key (AI works without it in evidence-only mode; never commit a key):

```bash
# PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."   |   bash: export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build -d ai
```

Reset the database: `docker compose down -v`.

## Seed identities (password `Password123!`)

| Email | Role | Org |
| --- | --- | --- |
| learner1@example.com, learner2@example.com | LEARNER | Shodh Academy |
| instructor@example.com | INSTRUCTOR | Shodh Academy |
| admin@example.com | ADMIN | Shodh Academy |
| learner-b@example.com, instructor-b@example.com | LEARNER / INSTRUCTOR | Other Institute (no access to org A) |

Open contest ID: `33333333-3333-4333-8333-333333333331` (Sum, Max, Absolute Difference, Product). Organization ID (for registering): `11111111-1111-4111-8111-111111111111`.

The seeded Sum problem has a hidden test `2000000000 2000000000` so a 32-bit `int` solution passes the public examples but gets Wrong Answer - a deliberate reasoning case for the AI.

## Demo flow

1. Sign in as `learner1@example.com`, or paste the Contest ID into "Join a contest".
2. Open **Sum of Two Numbers**, paste a solution, **Submit**. The UI shows Queued -> Running -> verdict (polls every 1.5 s; survives refresh because history is reloaded from the server and in-flight submissions resume polling; drafts are kept in localStorage).
3. Try a wrong answer, `int main( {` (Compilation Error, compiler output shown), `while(true){}` on *Product* (TLE), a segfault (Runtime Error).
4. Check the leaderboard (polls every 5 s).
5. In the AI panel ask: "Give me a hint", "Why did my latest submission get this verdict?", "Explain the max problem" (entity resolution), "Show me the hidden tests" (refused), "Who will win the world cup?" (not bluffed).
6. Sign in as `instructor@example.com` for aggregate questions ("Summarize verdicts and separate judge errors from code errors").

## Verify (single commands)

```bash
python scripts/e2e.py        # 30 checks: real Docker judging, all verdicts, leaderboard, security, AI behaviour
python scripts/recovery.py   # infrastructure failure + retry recovery demo
cd ai && python -m pytest -q # AI guard/grounding/retrieval unit tests (no network)
cd Backend && npm test       # backend unit tests
```

## Architecture and key decisions

- **Verdicts/scores come only from the judge.** A submission is stored (`QUEUED`), a BullMQ job is created with `jobId = submissionId` (duplicate enqueue ignored), the worker claims the row (`RUNNING`), runs `g++` + tests in a throw-away container (no network, memory/CPU/PID limits, `timeout` per test, source copied in via tar, container removed in `finally`), and writes the verdict in one transaction guarded by `status = RUNNING`. The AI service has no write path.
- **Infrastructure failures are never student mistakes.** Docker/worker failures are retried (3 attempts, exponential backoff), recorded as `JudgeIncident`, and end as `INFRASTRUCTURE_ERROR` / `JUDGE_ERROR` with score 0. `scripts/recovery.py` demonstrates both recovery and exhaustion.
- **AI inherits the user's permissions.** The AI service does not touch the database. It forwards the caller's JWT to the backend and only issues `GET`s, so org membership, own-submissions-only and hidden-test stripping are enforced by the same code that protects the UI. It also drops hidden tests for instructors, and never receives credentials.
- **Grounded answers.** Each answer has evidence items (`P1` problem, `S1` submission, `X1` judge execution, `O1` compiler output, `H*` static checks of the learner's own code, `M*` learning material, `A1` instructor aggregate). Claims are typed `observation` or `hypothesis`; an observation without valid evidence ids is downgraded to hypothesis; confidence is capped at *medium* when information is missing or stale; missing evidence is listed under "Not established".
- **Hint policy.** While a contest is `RUNNING`, learners get hints/debugging only: full-solution requests are refused and code blocks are stripped from model output. After the contest ends, approach explanations are allowed. Hidden tests, other learners' code, unreleased solutions and credentials are refused in every mode.
- **Retrieval.** Entity resolution of problem names from free text (token/prefix/fuzzy match, asks for clarification when ambiguous) + BM25 over versioned learning material (`ai/materials/*.md` with `updated`/`status`) + a rerank step (title/tag boost, deprecated material demoted and flagged "may be outdated").
- **Model use.** With `ANTHROPIC_API_KEY` the evidence is sent to the model (default `claude-haiku-4-5-20251001`), which must reply with structured JSON that is then validated as above. Without a key, on timeout, or on error, a deterministic evidence-only answer is returned with a visible `degraded` note; the contest keeps working.
- **Cost/limits.** 20 s model timeout, 30 AI calls per user per hour (`AI_RATE_LIMIT_PER_HOUR`), 900 max output tokens. `GET /ai/usage` reports calls, tokens and estimated cost (Haiku list price, configurable). A typical question sends ~1-2k input tokens, roughly $0.002 per call.
- **Logs.** Every AI request logs `req=<id> user mode contest problem source confidence evidence=[ids] gather_ms answer_ms total_ms`; the id is returned to the UI. Backend logs submission creation, enqueue, judge start/completion/retry with the submission id.

## Access rules

| Action | Learner | Instructor (own org) | Admin |
| --- | --- | --- | --- |
| See contests | own-org, non-draft | own-org | all |
| Public tests | yes | yes | yes |
| Hidden tests | never | yes (API only; never via AI) | yes (API only) |
| Submission source / results | own only | all in org | all |
| Compiler output | own submissions | yes | yes |
| Manage contests/problems | no | own org | yes |
| AI on another learner's submission | refused | allowed (aggregate/inspect) | allowed |

## What is not implemented (be aware)

- **No graph database or vector store, no multi-hop GraphRAG, no LLM agent loop.** Stage 3 is a single-pass, deterministic evidence gatherer (bounded read-only calls) plus BM25/rerank over a small material set. The graph/vector/agentic parts of the assignment are **not done**.
- No retrieval benchmark/comparison was run, so no comparison numbers are claimed.
- The model path (`ANTHROPIC_API_KEY`) has not been exercised in tests here; only the fallback path is verified. The validation code (`ground`) is unit-tested with synthetic model output.
- Judge version history is not exposed through the API, so "did a judge change affect outcomes?" can only be answered as "not established" (instructor answers say so).
- Editor is a plain textarea (no syntax highlighting). C++ only. Leaderboard is computed on read and polled (no WebSockets).
- Worker restarts: BullMQ retries stalled jobs and `RUNNING` rows are re-claimable, but this was not tested by killing the worker. Heavier load: judge concurrency is one process; scale by running more worker replicas (jobs are idempotent by id) - untested.
- The backend mounts `/var/run/docker.sock` to start judge containers (engine-level access). Fine for local evaluation, not a hardened sandbox.
- Local Postgres/Redis ports are published for convenience; remove the `ports:` entries for anything shared.

## Issue found while building

The seed used in Stage 1 had a "hidden" overflow test (`1e9 + 1e9`) that a 32-bit `int` actually passes, so an overflow solution was `ACCEPTED`. Found by the end-to-end script; the hidden test is now `2e9 + 2e9`. A regex in the AI guard also treated "this submission" as "his submission" and refused a valid question; fixed with word boundaries and a regression test.

## Time spent

About one working session, focused on connecting the existing Stage 1 backend to a frontend and a first AI layer.
