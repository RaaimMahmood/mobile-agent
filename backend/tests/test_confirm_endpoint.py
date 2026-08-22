import pytest
from fastapi.testclient import TestClient

from backend.api.routers import agent as agent_router
from backend.agent.state import AgentState, RunConfig


@pytest.fixture
def client(monkeypatch):
    # Import first: main.py calls load_dotenv() at import time, which would
    # put the real API_KEY back into the environment after we cleared it.
    from backend.api.main import app
    monkeypatch.delenv("API_KEY", raising=False)  # auth fails open when unset
    return TestClient(app)


def _register_session(session_id: str, **overrides) -> AgentState:
    config = RunConfig(app_name="youtube", task="t", mode="deploy")
    state = AgentState(session_id=session_id, config=config)
    for k, v in overrides.items():
        setattr(state, k, v)
    agent_router._sessions[session_id] = state
    return state


def test_confirm_approve_sets_confirmation_result_true(client):
    state = _register_session(
        "confirm-1", pending_confirmation={"action": "text", "element_id": 1}
    )
    resp = client.post("/api/v1/agent/confirm-1/confirm", json={"approve": True})
    assert resp.status_code == 200
    assert state.confirmation_result is True


def test_confirm_reject_sets_confirmation_result_false(client):
    state = _register_session(
        "confirm-2", pending_confirmation={"action": "text", "element_id": 1}
    )
    resp = client.post("/api/v1/agent/confirm-2/confirm", json={"approve": False})
    assert resp.status_code == 200
    assert state.confirmation_result is False


def test_confirm_unknown_session_returns_404(client):
    resp = client.post("/api/v1/agent/does-not-exist/confirm", json={"approve": True})
    assert resp.status_code == 404


def test_confirm_when_nothing_pending_returns_409(client):
    _register_session("confirm-3")  # pending_confirmation stays None
    resp = client.post("/api/v1/agent/confirm-3/confirm", json={"approve": True})
    assert resp.status_code == 409


def test_status_endpoint_reports_pending_confirmation(client):
    _register_session(
        "confirm-4", pending_confirmation={"action": "tap", "element_id": 5}
    )
    resp = client.get("/api/v1/agent/confirm-4")
    assert resp.status_code == 200
    assert resp.json()["pending_confirmation"] == {"action": "tap", "element_id": 5}
