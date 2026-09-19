import pytest


@pytest.fixture(autouse=True)
def isolate_gemini_env(monkeypatch):
    """Tests must never reach the real Gemini API or use a real key that happens to be in the environment."""
    for var in ("GEMINI_API_KEY", "GEMINI_BASE_URL", "AI_MODEL_DISABLED"):
        monkeypatch.delenv(var, raising=False)
