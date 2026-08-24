"""Progress check should try a cheap text-only pass first and only fall
back to a real screenshot + vision call when that pass isn't confident —
see build_text_progress_prompt in llm/prompts.py.
"""
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

    async def key_event(self, code):
        return None


class _FakeKB:
    async def retrieve_context(self, elements):
        return ""

    def count(self):
        return 0


def _state(max_rounds=6):
    config = RunConfig(
        app_name="youtube", task="search for cats", mode="deploy",
        reasoning_mode="reasoning", max_rounds=max_rounds,
    )
    return AgentState(session_id="sess-1", config=config, device=_FakeDevice(), kb=_FakeKB())


def _patched(state, text_llm_result):
    decision = {"action": "back", "thought": ""}
    return patch.multiple(
        loop,
        run_planner=AsyncMock(return_value=[]),
        annotate_screenshot=lambda png, elements: "fake-b64",
        call_vision_llm=AsyncMock(return_value=dict(decision)),
        call_text_llm=AsyncMock(return_value=dict(text_llm_result)),
        create_session=AsyncMock(),
        update_session=AsyncMock(),
        append_event=AsyncMock(),
    )


def test_confident_text_check_skips_the_vision_fallback():
    state = _state(max_rounds=6)
    result = {"complete": False, "confident": True, "progress": "still on search screen"}
    with _patched(state, result):
        asyncio.run(loop.run_deploy(state))
        # One decision-vision call per round (6 rounds) + zero extra vision
        # calls from the progress-check fallback, since the text pass was
        # confident.
        assert loop.call_vision_llm.call_count == 6
        # Exactly one text call: the round-5 progress check (reasoning_mode
        # never calls call_text_llm for the per-round decision itself).
        assert loop.call_text_llm.call_count == 1


def test_unconfident_text_check_escalates_to_one_vision_call():
    state = _state(max_rounds=6)
    with _patched(state, {"complete": False, "confident": False, "progress": "unsure"}):
        asyncio.run(loop.run_deploy(state))
        # 6 per-round decision calls + 1 extra from the progress fallback.
        assert loop.call_vision_llm.call_count == 7
        assert loop.call_text_llm.call_count == 1


def test_confident_complete_finishes_without_vision_fallback():
    state = _state(max_rounds=6)
    with _patched(state, {"complete": True, "confident": True, "progress": "done"}):
        asyncio.run(loop.run_deploy(state))
        assert state.task_complete is True
        # Round 5 (index) is where round_num % 5 == 0 first fires after
        # round 0; finishing there means only rounds 0-5 ran their decision
        # call (6 vision calls), not the full 6-round budget past that point.
        assert loop.call_vision_llm.call_count == 6
