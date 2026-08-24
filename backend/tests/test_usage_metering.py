"""Regression tests for unmetered LLM calls found in a code audit.

The planner, reflector and grid-mode calls all spent real tokens without
recording them: they never counted toward max_tokens / max_cost_usd /
max_llm_calls, and never showed up in the session's reported cost. The
reflector was the worst of the three — a two-image call made on every single
Explore round.
"""

import asyncio

from backend.agent.state import AgentState, RunConfig
from backend.agent.usage import record_usage


def _state(provider="gemini"):
    config = RunConfig(app_name="youtube", task="t", mode="explore", provider=provider)
    return AgentState(session_id="s", config=config)


def _result(prompt=100, completion=50, total=150):
    return {
        "steps": ["a"],
        "_usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    }


def test_record_usage_accumulates_tokens_and_calls():
    state = _state()
    record_usage(state, _result())
    assert state.llm_call_count == 1
    assert state.tokens_used == 150

    record_usage(state, _result())
    assert state.llm_call_count == 2
    assert state.tokens_used == 300


def test_record_usage_strips_the_usage_key():
    # Callers read their own fields off the result afterwards; the private
    # _usage key must not leak into decision dicts or KB documents.
    state = _state()
    result = record_usage(state, _result())
    assert "_usage" not in result
    assert result["steps"] == ["a"]


def test_record_usage_counts_the_call_even_without_usage_data():
    # Some providers return no usage block. The call still happened and still
    # cost money, so it must count toward max_llm_calls.
    state = _state()
    record_usage(state, {"steps": []})
    assert state.llm_call_count == 1
    assert state.tokens_used == 0


def test_record_usage_tolerates_non_dict_results():
    state = _state()
    record_usage(state, None)
    assert state.llm_call_count == 1


def test_planner_records_its_usage():
    import backend.agent.planner as planner

    state = _state()
    captured = {}

    async def fake_call(provider, prompt, trace=None):
        captured["called"] = True
        return _result()

    original = planner.call_text_llm
    planner.call_text_llm = fake_call
    try:
        steps = asyncio.run(planner.run_planner(state))
    finally:
        planner.call_text_llm = original

    assert captured["called"] is True
    assert steps == ["a"]
    # The planner call used to be invisible to the usage limiter.
    assert state.llm_call_count == 1
    assert state.tokens_used == 150


def test_planner_fallback_does_not_record_usage_on_failure():
    import backend.agent.planner as planner

    state = _state()

    async def failing_call(provider, prompt):
        raise RuntimeError("provider down")

    original = planner.call_text_llm
    planner.call_text_llm = failing_call
    try:
        steps = asyncio.run(planner.run_planner(state))
    finally:
        planner.call_text_llm = original

    assert steps == [state.task]
    assert state.llm_call_count == 0
