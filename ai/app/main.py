import logging
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import assistant, gemini
from .backend import Backend, NotAccessible, Unauthorized

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai")

RATE_LIMIT = int(os.getenv("AI_RATE_LIMIT_PER_HOUR", "30"))
_calls: dict[str, deque] = defaultdict(deque)

app = FastAPI(title="Shodh-a-Code AI assistant", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGIN", "*").split(","), allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    contestId: str
    problemId: str | None = None
    submissionId: str | None = None


def _check_rate(user_id: str) -> None:
    now = time.time()
    q = _calls[user_id]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, f"AI limit reached ({RATE_LIMIT}/hour). The contest itself is unaffected; try again later.")
    q.append(now)


@app.get("/ai/health")
def health():
    return {"status": "ok", "provider": "gemini", "model_configured": gemini.configured(), "model": gemini.MODEL if gemini.configured() else None}


@app.get("/ai/usage")
def usage():
    return {**assistant.USAGE, "rate_limit_per_user_per_hour": RATE_LIMIT, "timeout_s": gemini.TIMEOUT_S}


@app.post("/ai/ask")
async def ask(req: AskRequest, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    request_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    be = Backend(authorization.split(" ", 1)[1])
    try:
        me = await be.get("/auth/me")
        t_gather = time.perf_counter()
        ctx = await assistant.gather(req.question, req.contestId, req.problemId, req.submissionId, be, me)
        if not ctx.refusal:  # policy refusals cost no model call, so they are not rate limited
            _check_rate(me["id"])
        t_answer = time.perf_counter()
        result = await assistant.answer(ctx)
    except Unauthorized:
        raise HTTPException(401, "Invalid or expired token")
    except NotAccessible:
        raise HTTPException(403, "You do not have access to that contest")
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("req=%s failed", request_id)
        raise HTTPException(502, f"Could not gather evidence ({type(exc).__name__}). The contest is unaffected.")
    finally:
        await be.close()
    total_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "req=%s user=%s mode=%s contest=%s problem=%s source=%s confidence=%s refusal=%s evidence=%s gather_ms=%d answer_ms=%d total_ms=%d",
        request_id, me["username"], ctx.mode, req.contestId, ctx.problem["id"] if ctx.problem else None,
        result["source"], result["confidence"], result.get("refusal"),
        [e["id"] for e in result["evidence"]], int((t_answer - t_gather) * 1000), int((time.perf_counter() - t_answer) * 1000), total_ms,
    )
    return {**result, "requestId": request_id, "mode": ctx.mode, "policy": ctx.policy, "timingMs": total_ms}
