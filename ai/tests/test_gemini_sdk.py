"""Runs the REAL google-genai SDK code path (`gemini.generate` / `gemini.embed`) against a local fake Gemini
HTTP server: verifies request shape, structured-output schema, response/usage parsing and 429 handling.
It cannot prove the live Google API accepts the model name or key - see scripts/gemini_live.py."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import assistant, gemini

CAPTURED: list[dict] = []
MODE = {"kind": "ok"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CAPTURED.append({"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body})
        kind = MODE["kind"]
        if kind == "429":
            return self._send(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        if kind == "invalid":
            return self._send(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
        if kind == "notfound" and "notfound-model" in self.path:
            return self._send(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
        if kind == "empty":
            return self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": ""}]}, "finishReason": "STOP"}]})
        if kind == "blocked":
            return self._send(200, {"promptFeedback": {"blockReason": "SAFETY"}})
        if "embed" in self.path.lower():
            n = len(body.get("requests", [])) or 1
            return self._send(200, {"embeddings": [{"values": [3.0, 4.0] + [0.0] * 6} for _ in range(n)]})
        answer = {"answer": "From the fake server [P1]", "claims": [], "confidence": "medium", "missing": [], "needs_clarification": False}
        self._send(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(answer)}]}, "finishReason": "STOP"}],
                         "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30, "thoughtsTokenCount": 10, "totalTokenCount": 160}})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def env(monkeypatch, server):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")
    monkeypatch.setenv("GEMINI_BASE_URL", server)
    monkeypatch.setattr(gemini, "_client", None)
    CAPTURED.clear()
    MODE["kind"] = "ok"


def gen():
    return asyncio.run(gemini.generate("SYS", {"question": "q", "authorized_evidence": []}, assistant.AnswerModel))


def test_real_sdk_request_and_response_parsing():
    out, usage, model = gen()
    assert out["answer"].startswith("From the fake server") and model == gemini.MODEL
    assert usage == {"input_tokens": 120, "output_tokens": 40}  # thinking tokens are billed as output
    req = CAPTURED[0]
    assert req["key"] == "AIza-fake"  # key travels in a header from this process only
    assert f"models/{gemini.MODEL}:generateContent" in req["path"]
    assert req["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    cfg = req["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and "responseSchema" in cfg or "responseJsonSchema" in cfg
    assert json.loads(req["body"]["contents"][0]["parts"][0]["text"])["question"] == "q"


@pytest.mark.parametrize("kind,exp", [("429", "rate_limited"), ("invalid", "invalid_key"), ("empty", "empty"), ("blocked", "empty")])
def test_real_sdk_error_paths(kind, exp):
    MODE["kind"] = kind
    with pytest.raises(gemini.LLMUnavailable) as e:
        gen()
    assert e.value.kind == exp
    assert "AIza-fake" not in e.value.reason


def test_model_fallback_through_real_sdk(monkeypatch):
    MODE["kind"] = "notfound"
    monkeypatch.setattr(gemini, "MODEL", "notfound-model")
    out, _, model = gen()
    assert model != "notfound-model" and out["answer"]
    assert len(CAPTURED) == 2


def test_embeddings_are_normalised():
    vecs = asyncio.run(gemini.embed(["a", "b"], "RETRIEVAL_DOCUMENT", dims=8))
    assert len(vecs) == 2 and abs(sum(x * x for x in vecs[0]) - 1) < 1e-6 and abs(vecs[0][0] - 0.6) < 1e-6
