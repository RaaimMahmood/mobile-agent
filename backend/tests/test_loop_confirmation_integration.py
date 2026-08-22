import asyncio
from unittest.mock import AsyncMock, patch

from backend.agent import loop
from backend.agent.state import AgentState, RunConfig


class _FakeDevice:
    serial = "fake-1"

    async def screenshot(self):
        return b"png-bytes"

    async def pull_xml(self):
        return "<hierarchy></hierarchy>"

    async def wait_idle(self):
        return None

    async def launch_app(self, package):
        return None


class _FakeKB:
    async def retrieve_context(self, elements):
        return ""

    def count(self):
        return 0


def _state():
    config = RunConfig(app_name="youtube", task="search for cats", mode="deploy", max_rounds=1)
    return AgentState(session_id="sess-1", config=config, device=_FakeDevice(), kb=_FakeKB())


def test_execute_action_is_skipped_when_gate_rejects():
    state = _state()
    fake_decision = {"action": "text", "element_id": 1, "text_input": "x", "thought": ""}

    with patch("backend.agent.loop.run_planner", AsyncMock(return_value=[])), \
         patch("backend.agent.loop.annotate_screenshot", lambda png, elements: "fake-b64"), \
         patch("backend.agent.loop.call_text_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.call_vision_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.gate_action", AsyncMock(return_value=False)) as mock_gate, \
         patch("backend.agent.loop.execute_action", AsyncMock()) as mock_execute, \
         patch("backend.agent.loop.create_session", AsyncMock()), \
         patch("backend.agent.loop.update_session", AsyncMock()), \
         patch("backend.agent.loop.append_event", AsyncMock()):
        asyncio.run(loop.run_deploy(state))

    mock_gate.assert_awaited()
    mock_execute.assert_not_awaited()


def test_execute_action_runs_when_gate_approves():
    state = _state()
    fake_decision = {"action": "back", "thought": ""}

    with patch("backend.agent.loop.run_planner", AsyncMock(return_value=[])), \
         patch("backend.agent.loop.annotate_screenshot", lambda png, elements: "fake-b64"), \
         patch("backend.agent.loop.call_text_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.call_vision_llm", AsyncMock(return_value=dict(fake_decision))), \
         patch("backend.agent.loop.gate_action", AsyncMock(return_value=True)) as mock_gate, \
         patch("backend.agent.loop.execute_action", AsyncMock()) as mock_execute, \
         patch("backend.agent.loop.create_session", AsyncMock()), \
         patch("backend.agent.loop.update_session", AsyncMock()), \
         patch("backend.agent.loop.append_event", AsyncMock()):
        asyncio.run(loop.run_deploy(state))

    mock_gate.assert_awaited()
    mock_execute.assert_awaited()
