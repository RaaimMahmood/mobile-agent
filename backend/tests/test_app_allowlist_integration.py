import asyncio
from unittest.mock import AsyncMock, patch

from backend.agent import loop
from backend.agent.state import AgentState, RunConfig


class _FakeDevice:
    serial = "fake-1"

    async def launch_app(self, package):
        self.launched = package

    async def screenshot(self):
        return b"png-bytes"

    async def pull_xml(self):
        return "<hierarchy></hierarchy>"

    async def wait_idle(self):
        return None


class _FakeKB:
    async def retrieve_context(self, elements):
        return ""

    def count(self):
        return 0


def _state():
    config = RunConfig(app_name="whatsapp", task="send a message", mode="deploy", max_rounds=1)
    return AgentState(session_id="sess-1", config=config, device=_FakeDevice(), kb=_FakeKB())


def test_disallowed_app_never_launches_or_enters_the_round_loop(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "youtube, gmail")
    state = _state()

    with patch("backend.agent.loop.create_session", AsyncMock()), \
         patch("backend.agent.loop.update_session", AsyncMock()) as mock_update, \
         patch("backend.agent.loop._launch_target_app", AsyncMock()) as mock_launch, \
         patch("backend.agent.loop.run_planner", AsyncMock(return_value=[])), \
         patch("backend.agent.loop.call_text_llm", AsyncMock()) as mock_llm:
        asyncio.run(loop.run_deploy(state))

    mock_launch.assert_not_awaited()
    mock_llm.assert_not_awaited()
    mock_update.assert_awaited()
    assert state.status == "done"
    assert state.failure_reason is not None and "whatsapp" in state.failure_reason


def test_allowed_app_launches_normally(monkeypatch):
    monkeypatch.setenv("ALLOWED_APPS", "whatsapp")
    state = _state()
    fake_decision = {"action": "back", "thought": ""}

    with patch("backend.agent.loop.create_session", AsyncMock()), \
         patch("backend.agent.loop.update_session", AsyncMock()), \
         patch("backend.agent.loop.append_event", AsyncMock()), \
         patch("backend.agent.loop._launch_target_app", AsyncMock()) as mock_launch, \
         patch("backend.agent.loop.run_planner", AsyncMock(return_value=[])), \
         patch("backend.agent.loop.annotate_screenshot", lambda png, elements: "fake-b64"), \
         patch("backend.agent.loop.call_text_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.call_vision_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.gate_action", AsyncMock(return_value=True)):
        asyncio.run(loop.run_deploy(state))

    mock_launch.assert_awaited()
