"""Gemini provider (server-side only). The API key comes from GEMINI_API_KEY and never leaves this process.
Everything provider-specific lives here; assistant.py only sees `generate()` and `LLMUnavailable`."""
import asyncio
import json
import logging
import os
import re

log = logging.getLogger("ai.gemini")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-2.5-flash-lite").split(",") if m.strip()]
EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
TIMEOUT_S = float(os.getenv("AI_TIMEOUT_S", "20"))
MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2048"))
# Paid-tier list prices per 1M tokens, only used for the rough estimate (free tier costs 0).
PRICE_IN = float(os.getenv("AI_PRICE_IN_PER_MTOK", "0.30"))
PRICE_OUT = float(os.getenv("AI_PRICE_OUT_PER_MTOK", "2.50"))


def api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


class LLMUnavailable(Exception):
    """The model could not produce a usable answer. `reason` is safe to show to the user (never contains the key)."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai  # imported lazily so the service starts without the SDK key path being touched

        from google.genai import types

        # GEMINI_BASE_URL is only for proxies/tests; unset means Google's endpoint.
        _client = genai.Client(api_key=api_key(), http_options=types.HttpOptions(base_url=os.getenv("GEMINI_BASE_URL") or None))
    return _client


def classify(exc: Exception) -> LLMUnavailable:
    """Map SDK/network errors to a short, key-free explanation."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return LLMUnavailable("timeout", f"Gemini did not answer within {TIMEOUT_S:.0f}s")
    code = getattr(exc, "code", None)
    msg = str(exc)
    if code == 429 or "RESOURCE_EXHAUSTED" in msg:
        return LLMUnavailable("rate_limited", "Gemini rate limit / quota reached (429)")
    if code in (401, 403) or "API key not valid" in msg or "API_KEY_INVALID" in msg or "PERMISSION_DENIED" in msg:
        return LLMUnavailable("invalid_key", "GEMINI_API_KEY was rejected by Gemini (invalid or lacking permission)")
    if code == 404 or "NOT_FOUND" in msg:
        return LLMUnavailable("model_not_found", "Gemini model not found")
    if code is not None and 500 <= int(code) < 600:
        return LLMUnavailable("api_error", f"Gemini service error ({code})")
    name = type(exc).__name__
    if "Timeout" in name:
        return LLMUnavailable("timeout", f"Gemini did not answer within {TIMEOUT_S:.0f}s")
    return LLMUnavailable("api_error", f"Gemini API error ({name})")


async def _call(model: str, system: str, user_text: str, schema) -> tuple[str, dict]:
    """One Gemini request. Returns (text, usage). Isolated so tests can replace it."""
    from google.genai import types

    resp = await asyncio.wait_for(
        _get_client().aio.models.generate_content(
            model=model,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=schema,
                http_options=types.HttpOptions(timeout=int(TIMEOUT_S * 1000)),
            ),
        ),
        timeout=TIMEOUT_S + 2,
    )
    um = getattr(resp, "usage_metadata", None)
    usage = {
        "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
        "output_tokens": (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0),
    }
    try:
        text = resp.text or ""
    except Exception:  # blocked/safety-filtered responses raise on .text access
        text = ""
    return text, usage


def parse(text: str) -> dict:
    if not text.strip():
        raise LLMUnavailable("empty", "Gemini returned an empty response")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict) or not isinstance(out.get("answer"), str) or not out["answer"].strip():
        raise LLMUnavailable("malformed", "Gemini returned a malformed (non-JSON or incomplete) response")
    if not isinstance(out.get("claims", []), list):
        out["claims"] = []
    out["claims"] = [c for c in out["claims"] if isinstance(c, dict)]
    return out


async def generate(system: str, payload: dict, schema=None) -> tuple[dict, dict, str]:
    """Returns (parsed_json, usage, model_used). Raises LLMUnavailable on any failure."""
    if not configured():
        raise LLMUnavailable("no_key", "GEMINI_API_KEY is not set")
    user_text = json.dumps(payload, ensure_ascii=False)
    last: LLMUnavailable | None = None
    for model in [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]:
        try:
            text, usage = await _call(model, system, user_text, schema)
            return parse(text), usage, model
        except LLMUnavailable as e:
            last = e
            if e.kind != "model_not_found":
                raise
        except Exception as exc:
            last = classify(exc)
            log.warning("gemini model=%s failed kind=%s", model, last.kind)
            if last.kind != "model_not_found":
                raise last from None
    raise last or LLMUnavailable("api_error", "Gemini API error")


async def embed(texts: list[str], task_type: str, dims: int = 768) -> list[list[float]]:
    """Gemini embeddings, L2-normalised (required for truncated output dimensionality)."""
    if not configured():
        raise LLMUnavailable("no_key", "GEMINI_API_KEY is not set")
    from google.genai import types

    try:
        resp = await asyncio.wait_for(
            _get_client().aio.models.embed_content(
                model=EMBED_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=dims),
            ),
            timeout=TIMEOUT_S + 2,
        )
    except Exception as exc:
        raise classify(exc) from None
    out = []
    for e in resp.embeddings:
        v = list(e.values)
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    if len(out) != len(texts):
        raise LLMUnavailable("malformed", "Gemini returned the wrong number of embeddings")
    return out
