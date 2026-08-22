import asyncio

from backend.agent.state import AgentState, RunConfig
from backend.agent.confirmation import gate_action


def _state(**overrides):
    config = RunConfig(app_name="youtube", task="do a thing", mode="deploy")
    state = AgentState(session_id="sess-1", config=config)
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _elem(**overrides):
    base = {
        "id": 1, "bounds": [0, 0, 100, 50], "class_name": "android.widget.EditText",
        "resource_id": "", "content_desc": "", "text": "", "clickable": True,
        "focusable": True, "scrollable": False, "password": False,
    }
    base.update(overrides)
    return base


class _BroadcastRecorder:
    def __init__(self):
        self.events = []

    async def __call__(self, session_id, event):
        self.events.append(event)


def test_confirmation_fields_default_to_none():
    state = _state()
    assert state.pending_confirmation is None
    assert state.confirmation_result is None


def test_low_risk_action_proceeds_without_pausing():
    state = _state()
    decision = {"action": "back"}
    proceed = asyncio.run(gate_action(state, decision, []))
    assert proceed is True
    assert state.status != "paused"


def test_critical_action_is_dropped_without_ever_pausing():
    state = _state()
    decision = {"action": "text", "element_id": 1, "text_input": "hunter2"}
    proceed = asyncio.run(gate_action(state, decision, [_elem(id=1, password=True)]))
    assert proceed is False
    assert state.status != "paused"  # never offered for confirmation at all
    assert state.pending_confirmation is None


def test_medium_risk_action_pauses_and_waits_for_approval():
    state = _state(status="running")
    recorder = _BroadcastRecorder()
    state.ws_broadcast = recorder
    decision = {"action": "text", "element_id": 1, "text_input": "hello"}

    async def approve_after_delay():
        await asyncio.sleep(0.05)
        assert state.status == "paused"
        assert state.pending_confirmation == decision
        state.confirmation_result = True

    async def run_both():
        return await asyncio.gather(
            gate_action(state, decision, [_elem(id=1)]),
            approve_after_delay(),
        )

    proceed, _ = asyncio.run(run_both())
    assert proceed is True
    assert state.status == "running"  # restored after resolving
    assert state.pending_confirmation is None  # consumed
    assert any(e["type"] == "confirmation_required" for e in recorder.events)


def test_medium_risk_action_rejected_skips_execution():
    state = _state()
    decision = {"action": "text", "element_id": 1, "text_input": "hello"}

    async def reject_after_delay():
        await asyncio.sleep(0.05)
        state.confirmation_result = False

    async def run_both():
        return await asyncio.gather(
            gate_action(state, decision, [_elem(id=1)]),
            reject_after_delay(),
        )

    proceed, _ = asyncio.run(run_both())
    assert proceed is False


def test_stop_requested_while_waiting_breaks_out_without_hanging():
    state = _state()
    decision = {"action": "text", "element_id": 1, "text_input": "hello"}

    async def stop_after_delay():
        await asyncio.sleep(0.05)
        state.stop_requested = True

    async def run_both():
        return await asyncio.gather(
            gate_action(state, decision, [_elem(id=1)]),
            stop_after_delay(),
        )

    proceed, _ = asyncio.run(run_both())
    assert proceed is False
