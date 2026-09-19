import asyncio
import os

import pytest

# Connection settings as the container/host provides them, captured before any fixture removes them from the environment.
REAL_ENV = {k: os.environ[k] for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "DATABASE_URL") if k in os.environ}


@pytest.fixture(autouse=True)
def isolate_gemini_env(monkeypatch):
    """Tests must never reach the real Gemini API or use a real key that happens to be in the environment."""
    for var in ("GEMINI_API_KEY", "GEMINI_BASE_URL", "AI_MODEL_DISABLED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def isolate_stores(monkeypatch):
    """Unit tests run without PostgreSQL/Neo4j (the in-memory paths); tests that need a real store opt in with the
    `real_neo4j` fixture below (they skip, and say so, when Neo4j is not reachable)."""
    for var in ("NEO4J_URI", "DATABASE_URL", "AI_AGENT_PLANNER", "AI_AGENT_MAX_STEPS", "AI_AGENT_TOOL_TIMEOUT_S", "AI_AGENT_ENABLED"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def reset_gemini_state():
    from app import gemini

    gemini._dead.clear()
    yield
    gemini._dead.clear()


@pytest.fixture
def real_neo4j(monkeypatch):
    """Point the graph store at the real Neo4j from the environment; skip when it is not reachable. Yields a helper with
    run(coro) so tests stay synchronous like the rest of the suite (one event loop, closed at the end)."""
    from app import graph_store

    if "NEO4J_URI" not in REAL_ENV:
        pytest.skip("NEO4J_URI is not set (run inside docker compose: docker compose run --rm ai pytest)")
    for k, v in REAL_ENV.items():
        monkeypatch.setenv(k, v)
    loop = asyncio.new_event_loop()

    async def _ping():
        await graph_store.close()
        return await graph_store.ping(timeout=5)

    if not loop.run_until_complete(_ping()):
        loop.close()
        pytest.skip("Neo4j is not reachable at NEO4J_URI")

    class Runner:
        def run(self, coro):
            return loop.run_until_complete(coro)

    try:
        yield Runner()
    finally:
        loop.run_until_complete(graph_store.close())
        loop.close()
