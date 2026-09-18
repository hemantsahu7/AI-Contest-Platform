# Shodh-a-Code Contest Platform (Stage 1 Backend)

Backend-only take-home implementation of a small LeetCode/Codeforces-style contest platform.

This is **Stage 1**. It does **not** include a React frontend, LLM/agents, RAG/GraphRAG, Neo4j, or pgvector. Verdicts and scores are produced only by a deterministic Docker C++ judge. AI never decides correctness.

## Project overview

Authenticated users belong to organizations, join contests, view problems, submit C++ code, and poll for verdicts and leaderboard updates. Instructors/admins manage contests, problems, and test cases. Hidden tests are never returned to learners.

## Architecture

```
NestJS API + BullMQ worker (same process)
        |
        +-- PostgreSQL / Prisma
        |
        +-- Redis / BullMQ
        |
        +-- Docker judge containers (g++ / C++)
```

There is a single NestJS application. The REST API enqueues a job; a BullMQ processor in the same process runs the Docker judge and writes the result back to PostgreSQL.

## Technology choices

| Piece | Why |
| --- | --- |
| NestJS + TypeScript | Clear modules, guards, DTOs, and Swagger |
| PostgreSQL + Prisma | Relational contest data, constraints, migrations |
| Redis + BullMQ | Async judging without blocking HTTP |
| Docker + dockerode | Real compilation and execution |
| JWT + bcrypt | Standard auth without storing plaintext passwords |
| Jest + Supertest | Focused unit and API tests |

## Why PostgreSQL

Contests, membership, unique participation, submissions, and judge history are relational. Unique constraints such as `(userId, organizationId)` and `(contestId, userId)` prevent duplicates. Indexes cover email, username, contest organization, submission status, and submittedAt.

## Why Redis / BullMQ

HTTP `POST` submissions must return a `submissionId` immediately. BullMQ stores jobs in Redis, retries infrastructure failures, and recovers work after a worker restart. Polling is used instead of WebSockets.

## How Docker judging works

1. Source is stored in PostgreSQL and a BullMQ job is created with `jobId = submissionId`.
2. The worker claims the row (`QUEUED`/`RUNNING` → `RUNNING`).
3. `dockerode` starts `shodh-judge:v1` (Debian + g++, network disabled, memory/CPU/PID limits, non-root run user).
4. Source is copied into the container with `putArchive` (no host bind-mount of student files).
5. `g++ -O2 -std=c++17` compiles `source.cpp`.
6. Each test is executed with `timeout` using the problem time limit.
7. Outputs are compared after trimming trailing whitespace and normalizing line endings.
8. A `JudgeExecution` row records the judge version (`judge-v1`).
9. The container is stopped and removed.

The backend container talks to the **host Docker engine** through `/var/run/docker.sock` so it can start sibling judge containers.

## Submission lifecycle

`QUEUED` → `RUNNING` → `COMPLETED`

If infrastructure retries are exhausted: `INFRASTRUCTURE_ERROR` with verdict `JUDGE_ERROR`.

Poll `GET /submissions/:id` until status is terminal. The frontend can poll the same way for the leaderboard.

## Verdict rules

| Result | Verdict |
| --- | --- |
| `g++` fails | `COMPILATION_ERROR` |
| Process crash / non-zero exit | `RUNTIME_ERROR` |
| Wall time exceeded | `TIME_LIMIT_EXCEEDED` |
| Output mismatch | `WRONG_ANSWER` |
| All tests pass | `ACCEPTED` |
| Docker/worker failure | `JUDGE_ERROR` + `INFRASTRUCTURE_ERROR` |

Student mistakes are **not** retried. Infrastructure errors are retried up to 3 attempts.

## Scoring rules

- Each problem has a fixed `points` value.
- `ACCEPTED` awards those points once per participant per problem.
- Any other verdict scores 0 for that submission.
- Repeat accepts do not add extra points.

## Leaderboard rules

`GET /contests/:contestId/leaderboard`

1. Higher total score first
2. Then lower `totalSolveTimeSeconds` (sum of first-accept time since contest start)
3. Then ascending `userId`

Live updates = HTTP polling. No WebSockets.

## Authentication

- `POST /auth/register` (optional `organizationId`)
- `POST /auth/login` → JWT
- `GET /auth/me`

Passwords are bcrypt-hashed. `passwordHash` is never returned. `JWT_SECRET` and `JWT_EXPIRES_IN` come from the environment.

## Authorization

Roles: `ADMIN`, `INSTRUCTOR`, `LEARNER` (global and per-organization membership).

- **ADMIN**: manage organizations, memberships, contests, problems
- **INSTRUCTOR**: manage contests/problems/tests in their organization; inspect results
- **LEARNER**: join, view public tests, submit, view own submissions, view leaderboard

Guards: global JWT guard, `@Public()`, `@Roles()`, plus organization checks in services. Clients cannot set `userId`/ownership in a way that bypasses server checks.

## Organization membership

Every contest belongs to an organization. Users may only use contests for organizations they belong to. Cross-org access is denied with 403.

## Security controls

- JWT + bcrypt
- DTO validation (`class-validator`)
- Helmet + CORS
- 100 KB source limit (`MAX_SOURCE_CODE_BYTES`)
- Hidden tests stripped for learners
- Other learners' source code is not returned
- No secrets in git (`.env` is ignored)
- Judge containers: no network, memory/CPU/PID limits, timeout, cleanup
- Logs omit passwords, JWT secrets, and full source dumps

## Retry / recovery

- BullMQ `jobId` = submission id (duplicate enqueue is ignored by Redis job ids)
- Completed / infrastructure-final rows are never judged again
- Final write uses `updateMany` on `status = RUNNING` so a late retry cannot overwrite `COMPLETED`
- Worker crash while `RUNNING` can be retried; student verdicts are committed before the job succeeds
- After 3 failed infrastructure attempts the row becomes `INFRASTRUCTURE_ERROR` (not `WRONG_ANSWER`)

## Known limitations (Stage 1)

- C++ only
- Leaderboard is computed on read (fine for a take-home)
- Memory usage is recorded as the configured limit, not cgroup peak RSS
- Contest status is derived from timestamps (plus an explicit `DRAFT` flag)
- Not a production sandbox: students could still probe container limits; this is a reproducible assignment judge, not a hardened isolation product
- AI/RAG/GraphRAG is intentionally absent

## Docker security limitations

Mounting `docker.sock` into the backend gives that process **engine-level Docker access**. A compromised API can start/stop containers on the host. That is required for this assignment's in-process judge and is **not** equivalent to a production gVisor/Firecracker/nsjail setup. Use only on a local evaluation machine.

## Environment variables

See `.env.example`:

```
DATABASE_URL=
REDIS_URL=
JWT_SECRET=
JWT_EXPIRES_IN=
PORT=
CORS_ORIGIN=
JUDGE_IMAGE=
JUDGE_TIMEOUT_MS=
MAX_SOURCE_CODE_BYTES=
JUDGE_VERSION=
```

`docker compose up --build` supplies working defaults. Do not commit a real `.env`.

## Setup

### One command (recommended)

Requires Docker Desktop (Windows/macOS) or Docker Engine (Linux) with Compose.

```bash
docker compose up --build
```

This starts PostgreSQL, Redis, builds `shodh-judge:v1`, runs Prisma migrations + seed, and starts the API+worker on port 3000.

Reset DB volume:

```bash
docker compose down -v
docker compose up --build
```

### Local development

```bash
cp .env.example .env
docker compose up -d postgres redis judge-image
npx prisma migrate deploy
npx prisma db seed
npm install
npm run start:dev
```

## Seed / demo credentials (development only)

Password for every seeded user: `Password123!`

| Email | Role |
| --- | --- |
| admin@example.com | ADMIN |
| instructor@example.com | INSTRUCTOR |
| learner1@example.com | LEARNER |
| learner2@example.com | LEARNER |
| instructor-b@example.com | INSTRUCTOR (org B) |
| learner-b@example.com | LEARNER (org B) |

Open contest id: `33333333-3333-4333-8333-333333333331`  
Sum problem id: `44444444-4444-4444-8444-444444444441`

## API documentation

Swagger UI: [http://localhost:3000/api/docs](http://localhost:3000/api/docs)

Authorize with the JWT from `/auth/login` (`Bearer` token).

## Testing

Unit tests (no Docker required):

```bash
npm test
```

API/e2e tests (Postgres + Redis + seed data; Docker judge tests need a working Docker engine):

```bash
docker compose up -d postgres redis judge-image
npx prisma migrate deploy
npx prisma db seed
npm run test:e2e
```

Skip live Docker judge cases:

```bash
SKIP_DOCKER=1 npm run test:e2e
```

## Example API flow

```bash
# 1. Login as learner
TOKEN=$(curl -s -X POST http://localhost:3000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"learner1@example.com","password":"Password123!"}' | jq -r .accessToken)

# 2. Join contest
curl -X POST http://localhost:3000/contests/33333333-3333-4333-8333-333333333331/join \
  -H "Authorization: Bearer $TOKEN"

# 3. List problems
curl http://localhost:3000/contests/33333333-3333-4333-8333-333333333331/problems \
  -H "Authorization: Bearer $TOKEN"

# 4. Submit C++
SUB=$(curl -s -X POST \
  http://localhost:3000/contests/33333333-3333-4333-8333-333333333331/problems/44444444-4444-4444-8444-444444444441/submissions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"language":"cpp","sourceCode":"#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<std::endl;}"}')
echo "$SUB"
# {"submissionId":"...","status":"QUEUED"}

# 5. Poll
curl http://localhost:3000/submissions/<id> -H "Authorization: Bearer $TOKEN"

# 6. Leaderboard
curl http://localhost:3000/contests/33333333-3333-4333-8333-333333333331/leaderboard \
  -H "Authorization: Bearer $TOKEN"
```

Wrong-answer sample: print `0` instead of `A+B`. Compilation-error sample: send `int main( {`.

## How to evaluate

1. `docker compose up --build`
2. Open `/api/docs`
3. Login as `learner1@example.com`
4. Join the open contest, list problems, submit C++, poll until `COMPLETED`
5. Confirm leaderboard ranks (seed data already has historical accepts)
6. Login as instructor and confirm hidden tests are visible
7. Login as `learner-b@example.com` and confirm org A contests are forbidden
8. Run `npm test` and `npm run test:e2e`

## Stage 1 scope reminder

Stop here. A future FastAPI AI service can read submissions, problems, and judge history over these APIs. Do not add LLM, embeddings, Neo4j, or GraphRAG in this repository yet.
