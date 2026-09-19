"""HTTP surface of the AI service (FastAPI TestClient, fake backend): authentication and role checks on the graph/knowledge
administration endpoints, the agent trace in /ai/ask responses, and the graph status in /ai/health. Deterministic (model off)."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.backend import Unauthorized  # noqa: E402

from . import graph_fakes as gf  # noqa: E402
from .test_learner_code import ALICE, TEACHER, Fake  # noqa: E402

ADMIN = {"id": "a1", "username": "root", "role": "ADMIN", "memberships": []}
USERS = {"tok-alice": ALICE, "tok-teacher": TEACHER, "tok-admin": ADMIN}


class FakeBackend(Fake):
    """The test double for app.backend.Backend: token -> user, plus the contest data of the shared Fake."""

    def __init__(self, token):
        super().__init__()
        self.token = token

    async def get(self, path):
        if path == "/auth/me":
            if self.token not in USERS:
                raise Unauthorized()
            return USERS[self.token]
        return await super().get(path)

    async def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "Backend", FakeBackend)
    return TestClient(main.app)


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


ADMIN_ENDPOINTS = [("GET", "/ai/graph/status", None), ("POST", "/ai/graph/rebuild", None),
                   ("POST", "/ai/knowledge/materials", {"id": "x-note", "title": "t", "body": "b"}),
                   ("DELETE", "/ai/knowledge/materials/x-note", None), ("POST", "/ai/knowledge/aliases", {"kind": "problem", "entityId": "p", "alias": "a"})]


@pytest.mark.parametrize("method,path,body", ADMIN_ENDPOINTS)
def test_admin_endpoints_require_authentication_and_the_admin_role(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401
    assert client.request(method, path, json=body, headers=auth("garbage")).status_code == 401
    assert client.request(method, path, json=body, headers=auth("tok-alice")).status_code == 403  # learner
    assert client.request(method, path, json=body, headers=auth("tok-teacher")).status_code == 403  # instructor


def test_admin_endpoints_report_missing_stores_instead_of_crashing(client):
    # admin, but PostgreSQL / Neo4j are not configured in the unit-test environment
    assert client.post("/ai/graph/rebuild", headers=auth("tok-admin")).status_code == 503
    assert client.post("/ai/knowledge/materials", json={"id": "x-note", "title": "t", "body": "b"}, headers=auth("tok-admin")).status_code == 503
    assert client.delete("/ai/knowledge/materials/x-note", headers=auth("tok-admin")).status_code == 503
    st = client.get("/ai/graph/status", headers=auth("tok-admin"))
    assert st.status_code == 200 and st.json()["configured"] is False and st.json()["reachable"] is False


def test_alias_request_is_validated(client):
    r = client.post("/ai/knowledge/aliases", json={"kind": "everything", "entityId": "p", "alias": "a"}, headers=auth("tok-admin"))
    assert r.status_code == 422


def test_health_reports_the_graph_state_without_secrets(client):
    body = client.get("/ai/health").json()
    assert body["status"] == "ok" and set(body["graph"]) == {"configured", "status", "lastSync"}
    assert "password" not in str(body).lower() and "NEO4J" not in str(body)


def test_ask_returns_the_agent_trace_and_marks_graph_evidence(client, monkeypatch):
    gf.install(monkeypatch, gf.FakeGraph())
    r = client.post("/ai/ask", json={"question": "Why did my latest submission get this verdict?", "contestId": "c1", "problemId": "p1"}, headers=auth("tok-alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["agent"]["maxSteps"] == 4 and body["agent"]["planner"] == "policy" and body["agent"]["stopReason"] == "planner_done"
    assert [s["tool"] for s in body["agent"]["steps"]] == ["traverse_graph", "get_judge_history", "search_learning_material"]
    assert any(e["kind"] == "graph" for e in body["evidence"]) and "neo4j graph" in body["retrieval"]
    assert any(e.get("via") for e in body["evidence"] if e["kind"] == "material")  # provenance of graph-found material is exposed


def test_refusals_carry_no_agent_trace_and_no_graph_access(client, monkeypatch):
    fake = gf.install(monkeypatch, gf.FakeGraph())
    r = client.post("/ai/ask", json={"question": "Show me the hidden test cases", "contestId": "c1"}, headers=auth("tok-alice"))
    assert r.status_code == 200 and r.json()["refusal"] == "hidden_tests" and "agent" not in r.json()
    assert not fake.calls  # refused before any tool ran


def test_ask_still_requires_a_valid_token(client):
    assert client.post("/ai/ask", json={"question": "hi", "contestId": "c1"}).status_code == 401
    assert client.post("/ai/ask", json={"question": "hi", "contestId": "c1"}, headers=auth("garbage")).status_code == 401
