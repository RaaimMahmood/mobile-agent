# V2 Phase 1: Action Risk Policy & Confirmation Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every LLM-decided action a risk tier (low/medium/high/critical), require human confirmation before medium/high actions execute, and hard-block critical actions (typing into a password field via the plain `text` action) regardless of confirmation. Also stop shipping the backend API key inside the frontend's built JS bundle.

**Architecture:** A pure classification function (`backend/security/risk_policy.py`) inspects the LLM's proposed action plus the target element's uiautomator attributes and returns a risk tier. `run_explore`/`run_deploy` call it right after the LLM returns a decision and before `execute_action` runs; medium/high tiers pause the loop and wait for a new `POST /agent/{id}/confirm` call (mirrors the existing `stop_requested` pause pattern already in `loop.py`/`state.py`), `critical` is dropped unconditionally with no confirmation offered. The frontend gets a `ConfirmationModal` wired to a new `confirmation_required` WebSocket event, and the API key moves from a Vite build-time env var (baked into the bundle) to a value entered once and kept in `localStorage`.

**Tech Stack:** Python 3 / FastAPI / pytest (backend), React / TypeScript / Vite (frontend). No new dependencies.

## Global Constraints

- No new third-party packages — this phase is pure policy logic, not a new model integration (VLM grounding is a separate future plan; see the closing note).
- Every new backend module gets a matching pytest file in `backend/tests/`, following the existing style in `backend/tests/test_stop_and_cleanup.py` (plain functions, a local `_state(**overrides)` helper, no mocking framework).
- `git commit` messages in this repo never carry a `Co-Authored-By` trailer (breaks the Google CLA bot on other repos this account contributes to).
- Branch: `v2/security-policy-layer`, cut from `main` at `5530f16`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/perception/xml_parser.py` (modify) | Parse uiautomator's `password` node attribute into `InteractiveElement` |
| `backend/security/risk_policy.py` (new) | Pure function: `(action, elements) -> RiskTier` |
| `backend/agent/state.py` (modify) | Add `pending_confirmation` / `confirmation_result` fields to `AgentState` |
| `backend/agent/confirmation.py` (new) | Shared gate helper called by both `run_explore` and `run_deploy` |
| `backend/agent/executor.py` (modify) | Second, unconditional guard against typing plain text into a password element |
| `backend/agent/loop.py` (modify) | Call the risk policy + confirmation gate before `execute_action` in both loops |
| `backend/api/schemas.py` (modify) | `ConfirmActionRequest`, extend `AgentStatusResponse` |
| `backend/api/routers/agent.py` (modify) | `POST /agent/{session_id}/confirm` |
| `frontend/src/api/apiKey.ts` (new) | `getApiKey()` / `setApiKey()` — localStorage, not build-time env |
| `frontend/src/api/client.ts` (modify) | Use `getApiKey()` instead of `import.meta.env.VITE_API_KEY` |
| `frontend/src/api/websocket.ts` (modify) | Same, plus new `ConfirmationRequiredEvent` type |
| `frontend/src/pages/SetupPage.tsx` (modify) | Add an "API Key" field that calls `setApiKey()` |
| `frontend/src/components/ConfirmationModal.tsx` (new) | Renders when a `confirmation_required` event arrives; calls `agentApi.confirm()` |

---

### Task 1: Parse the `password` attribute from uiautomator XML

**Files:**
- Modify: `backend/perception/xml_parser.py`
- Test: `backend/tests/test_xml_parser.py`

**Interfaces:**
- Produces: `InteractiveElement.password: bool` field, included in `to_dict()` under key `"password"`. This is what Task 3's risk policy reads to detect password fields — uiautomator already sets `password="true"` on the XML node for any `EditText` backed by `TYPE_TEXT_VARIATION_PASSWORD` or similar, so no heuristic guessing of "is this a password field" is needed.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_xml_parser.py` (check the existing file first for its exact fixture style — it builds XML strings inline — and match it):

```python
def test_password_attribute_parsed_true():
    xml = """<hierarchy>
        <node class="android.widget.EditText" resource-id="pwd" text=""
              password="true" clickable="true" focusable="true"
              bounds="[0,0][100,50]" />
    </hierarchy>"""
    elements = parse_interactive_elements(xml)
    assert elements[0].password is True
    assert elements[0].to_dict()["password"] is True


def test_password_attribute_defaults_false():
    xml = """<hierarchy>
        <node class="android.widget.EditText" resource-id="username" text=""
              clickable="true" focusable="true"
              bounds="[0,0][100,50]" />
    </hierarchy>"""
    elements = parse_interactive_elements(xml)
    assert elements[0].password is False
    assert elements[0].to_dict()["password"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/bella/mobile-agent && source backend/venv/Scripts/activate && pytest backend/tests/test_xml_parser.py -k password -v`
Expected: FAIL with `AttributeError: 'InteractiveElement' object has no attribute 'password'`

- [ ] **Step 3: Add the field**

In `backend/perception/xml_parser.py`, add to the `InteractiveElement` dataclass (after `checked: bool` at line 18):

```python
    password: bool
```

Add `"password": self.password,` to `to_dict()` (after the `"scrollable": self.scrollable,` line — keep it last since callers unpack this dict positionally nowhere, but appending is the smallest diff).

In `parse_interactive_elements`, in the `InteractiveElement(...)` constructor call, add:

```python
            password=node.get("password", "false") == "true",
```

(same style as the existing `checkable`/`checked` lines directly above it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_xml_parser.py -v`
Expected: all PASS, including the two new tests

- [ ] **Step 5: Commit**

```bash
git add backend/perception/xml_parser.py backend/tests/test_xml_parser.py
git commit -m "feat: parse uiautomator password attribute onto InteractiveElement"
```

---

### Task 2: Risk policy classifier

**Files:**
- Create: `backend/security/risk_policy.py`
- Test: `backend/tests/test_risk_policy.py`

**Interfaces:**
- Consumes: an `action` dict shaped like what `execute_action` already receives (`backend/agent/executor.py:9-15` — keys `action`, `element_id`, `text_input`, `direction`, `grid_cell`, `secret_id`), and `elements: list[dict]` shaped like `InteractiveElement.to_dict()` from Task 1 (now includes `"password"`).
- Produces: `classify_action(action: dict, elements: list[dict]) -> str`, returning one of `"low"`, `"medium"`, `"high"`, `"critical"`. Also exports `RISK_TIERS = ("low", "medium", "high", "critical")` (ordered, lowest first) — Task 4's confirmation gate uses this ordering to decide "does this tier need confirmation".

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_risk_policy.py`:

```python
from backend.security.risk_policy import classify_action, RISK_TIERS


def _elem(**overrides):
    base = {
        "id": 1, "bounds": [0, 0, 100, 50], "class_name": "android.widget.EditText",
        "resource_id": "", "content_desc": "", "text": "", "clickable": True,
        "focusable": True, "scrollable": False, "password": False,
    }
    base.update(overrides)
    return base


def test_tap_on_ordinary_element_is_low():
    elements = [_elem(id=1, resource_id="search_button", text="Search")]
    action = {"action": "tap", "element_id": 1}
    assert classify_action(action, elements) == "low"


def test_back_and_swipe_and_finish_are_low():
    for action in [{"action": "back"}, {"action": "swipe", "direction": "up"}, {"action": "finish"}]:
        assert classify_action(action, []) == "low"


def test_plain_text_into_ordinary_field_is_medium():
    elements = [_elem(id=1)]
    action = {"action": "text", "element_id": 1, "text_input": "hello"}
    assert classify_action(action, elements) == "medium"


def test_type_secret_is_low_the_llm_never_sees_the_value():
    elements = [_elem(id=1, password=True)]
    action = {"action": "type_secret", "element_id": 1, "secret_id": "my_login"}
    assert classify_action(action, elements) == "low"


def test_tap_on_element_with_dangerous_keyword_is_high():
    for keyword in ["send", "pay", "confirm order", "delete", "transfer", "share"]:
        elements = [_elem(id=1, text=keyword)]
        action = {"action": "tap", "element_id": 1}
        assert classify_action(action, elements) == "high", keyword

    elements = [_elem(id=1, content_desc="Send message")]
    assert classify_action({"action": "tap", "element_id": 1}, elements) == "high"


def test_plain_text_into_password_field_is_critical():
    elements = [_elem(id=1, password=True)]
    action = {"action": "text", "element_id": 1, "text_input": "hunter2"}
    assert classify_action(action, elements) == "critical"


def test_unknown_element_id_falls_back_to_action_type_default():
    # element_id doesn't match anything in `elements` — must not crash,
    # must not silently treat it as password-safe.
    action = {"action": "text", "element_id": 99, "text_input": "hi"}
    assert classify_action(action, []) == "medium"


def test_risk_tiers_are_ordered_low_to_critical():
    assert RISK_TIERS == ("low", "medium", "high", "critical")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_risk_policy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.security.risk_policy'`

- [ ] **Step 3: Implement the classifier**

Create `backend/security/risk_policy.py`:

```python
"""Classifies an LLM-proposed action into a risk tier before execution.

Ordered lowest-to-highest so callers can compare tiers positionally
(RISK_TIERS.index(tier) >= RISK_TIERS.index("medium")) instead of hardcoding
string comparisons in more than one place.
"""

RISK_TIERS = ("low", "medium", "high", "critical")

# Keyword match against an element's visible text/content-desc. Deliberately
# coarse — a false positive just costs one extra confirmation click, but a
# false negative lets a real send/pay/delete action run unconfirmed.
_HIGH_RISK_KEYWORDS = (
    "send", "pay", "payment", "confirm", "delete", "remove",
    "transfer", "share", "purchase", "buy now", "checkout",
)

_LOW_RISK_ACTIONS = {"back", "swipe", "long_press", "grid", "finish"}


def _find_element(elements: list[dict], elem_id) -> dict | None:
    if elem_id is None:
        return None
    for e in elements:
        if e.get("id") == elem_id:
            return e
    return None


def _element_text_blob(elem: dict) -> str:
    return f"{elem.get('text', '')} {elem.get('content_desc', '')} {elem.get('resource_id', '')}".lower()


def classify_action(action: dict, elements: list[dict]) -> str:
    action_type = (action.get("action") or "").lower()
    elem = _find_element(elements, action.get("element_id"))

    if action_type == "text":
        if elem is not None and elem.get("password"):
            # The LLM decided to type a literal string into a field the OS
            # itself flags as a password field. type_secret exists precisely
            # so this never has to happen — treat it as critical and let the
            # caller drop it unconditionally, never just "confirm harder".
            return "critical"
        return "medium"

    if action_type == "type_secret":
        # The actual secret value is resolved server-side by CredentialManager
        # and never touches the LLM (see backend/agent/executor.py) — the
        # *decision* to type into a field carries no more risk than a tap.
        return "low"

    if action_type == "tap" and elem is not None:
        blob = _element_text_blob(elem)
        if any(kw in blob for kw in _HIGH_RISK_KEYWORDS):
            return "high"
        return "low"

    if action_type in _LOW_RISK_ACTIONS or action_type == "tap":
        return "low"

    # Unrecognized action type: fail toward requiring a human, not toward
    # silently executing something the policy has no rule for.
    return "medium"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_risk_policy.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/security/risk_policy.py backend/tests/test_risk_policy.py
git commit -m "feat: add action risk classifier (low/medium/high/critical)"
```

---

### Task 3: Confirmation state on `AgentState`

**Files:**
- Modify: `backend/agent/state.py`
- Test: `backend/tests/test_confirmation.py` (created in this task, extended in Task 4)

**Interfaces:**
- Produces: `AgentState.pending_confirmation: Optional[dict]` (holds the decision dict awaiting approval, `None` when nothing is pending) and `AgentState.confirmation_result: Optional[bool]` (`True`/`False` once the user responds, reset to `None` after each gate cycle). Task 4's `confirmation.py` and Task 6's `/confirm` endpoint both read/write these two fields — this is the entire contract between them, no other coupling.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_confirmation.py`:

```python
from backend.agent.state import AgentState, RunConfig


def _state(**overrides):
    config = RunConfig(app_name="youtube", task="do a thing", mode="deploy")
    state = AgentState(session_id="sess-1", config=config)
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def test_confirmation_fields_default_to_none():
    state = _state()
    assert state.pending_confirmation is None
    assert state.confirmation_result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_confirmation.py -v`
Expected: FAIL with `AttributeError: 'AgentState' object has no attribute 'pending_confirmation'`

- [ ] **Step 3: Add the fields**

In `backend/agent/state.py`, add after the `errors: list[str] = field(default_factory=list)` line (part of the "control flow" group):

```python
    # Set when a medium/high-risk action needs a human decision before it
    # can execute; cleared back to None once resolve_confirmation() consumes
    # it. Holds the full decision dict so /confirm's response and any audit
    # log can show exactly what was approved or rejected.
    pending_confirmation: Optional[dict] = None
    confirmation_result: Optional[bool] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_confirmation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/state.py backend/tests/test_confirmation.py
git commit -m "feat: add pending_confirmation/confirmation_result to AgentState"
```

---

### Task 4: Confirmation gate helper

**Files:**
- Create: `backend/agent/confirmation.py`
- Modify: `backend/tests/test_confirmation.py` (extend)

**Interfaces:**
- Consumes: `AgentState` (Task 3's new fields), `classify_action` (Task 2), `state.broadcast()` (already exists on `AgentState`, `backend/agent/state.py:84-86`).
- Produces: `async def gate_action(state: AgentState, decision: dict, elements: list[dict]) -> bool` — returns `True` if the caller (`run_explore`/`run_deploy` in Task 5) should proceed to call `execute_action`, `False` if it should skip execution and just advance the round. Also exports `POLL_INTERVAL_SECONDS = 0.5` (matches the existing pause-poll cadence in `loop.py`'s `_should_continue` branch) so Task 5 doesn't hardcode a second sleep value.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_confirmation.py`:

```python
import asyncio
from backend.agent.confirmation import gate_action


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
    state = _state()
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_confirmation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.agent.confirmation'`

- [ ] **Step 3: Implement the gate**

Create `backend/agent/confirmation.py`:

```python
import asyncio

from ..security.risk_policy import classify_action, RISK_TIERS

POLL_INTERVAL_SECONDS = 0.5

_NEEDS_CONFIRMATION = RISK_TIERS.index("medium")  # tiers >= this index pause


async def gate_action(state, decision: dict, elements: list[dict]) -> bool:
    """Classify `decision` and, if it's risky enough, block until the user
    approves or rejects it (or the run is stopped).

    Returns True if the caller should go on to execute the action, False if
    it should be skipped. Critical actions are dropped immediately with no
    confirmation offered at all — there is no "confirm anyway" for typing a
    literal string into a field the OS itself flags as a password field.
    """
    tier = classify_action(decision, elements)

    if tier == "critical":
        await state.broadcast({
            "type": "action_blocked",
            "risk": tier,
            "action": decision.get("action"),
            "reason": "Blocked: plain-text input into a password field is never allowed.",
        })
        return False

    if RISK_TIERS.index(tier) < _NEEDS_CONFIRMATION:
        return True

    previous_status = state.status
    state.pending_confirmation = decision
    state.confirmation_result = None
    state.status = "paused"
    await state.broadcast({
        "type": "confirmation_required",
        "risk": tier,
        "action": decision.get("action"),
        "element_id": decision.get("element_id"),
        "thought": decision.get("thought", ""),
    })

    try:
        while state.confirmation_result is None:
            if state.stop_requested:
                return False
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        return state.confirmation_result
    finally:
        state.pending_confirmation = None
        state.confirmation_result = None
        if state.status == "paused":
            state.status = previous_status
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_confirmation.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/confirmation.py backend/tests/test_confirmation.py
git commit -m "feat: add confirmation gate for medium/high-risk actions"
```

---

### Task 5: Wire the gate into both agent loops

**Files:**
- Modify: `backend/agent/loop.py`
- Test: `backend/tests/test_loop_confirmation_integration.py`

**Interfaces:**
- Consumes: `gate_action` (Task 4).
- Produces: nothing new for later tasks — this is where the gate actually takes effect.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_loop_confirmation_integration.py`. This tests the integration point directly rather than running a full loop (which needs a real `device`/`kb`) — it asserts that `run_deploy`'s round body calls `gate_action` before `execute_action`, using monkeypatching on the module-level names `loop.execute_action` and `loop.gate_action`.

```python
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

    async def fake_planner(_state):
        return []

    fake_decision = {"action": "text", "element_id": 1, "text_input": "x", "thought": ""}

    with patch("backend.agent.loop.run_planner", AsyncMock(return_value=[])), \
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_loop_confirmation_integration.py -v`
Expected: FAIL — `gate_action` doesn't exist as an attribute of `backend.agent.loop` yet, so `patch("backend.agent.loop.gate_action", ...)` raises `AttributeError`.

- [ ] **Step 3: Wire the gate into `run_explore` and `run_deploy`**

In `backend/agent/loop.py`, add the import (near the other `from .` imports at the top):

```python
from .confirmation import gate_action
```

In `run_explore`, replace step 9 (currently `backend/agent/loop.py:167-168`):

```python
            # ── 9. Execute action ─────────────────────────────────────────────
            await execute_action(state.device, decision, state.elements, state.credentials, state=state)
```

with:

```python
            # ── 9. Risk-gate then execute ──────────────────────────────────────
            if await gate_action(state, decision, state.elements):
                await execute_action(state.device, decision, state.elements, state.credentials, state=state)
```

In `run_deploy`, replace step 6-7 (currently `backend/agent/loop.py:360-362`):

```python
            # ── 6–7: Execute + wait ───────────────────────────────────────────
            await execute_action(state.device, decision, state.elements, state.credentials, state=state)
            await state.device.wait_idle()
```

with:

```python
            # ── 6–7: Risk-gate, execute, wait ─────────────────────────────────
            if await gate_action(state, decision, state.elements):
                await execute_action(state.device, decision, state.elements, state.credentials, state=state)
            await state.device.wait_idle()
```

(`wait_idle()` still runs even when the gate rejected the action — the screen may have changed anyway, e.g. from the confirmation UI, and the next round should read the real current state.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_loop_confirmation_integration.py -v`
Expected: all PASS

Then run the full backend suite to confirm nothing else broke:

Run: `pytest backend/tests/ -q`
Expected: all green (98 previously-passing tests + the new ones from Tasks 1-5)

- [ ] **Step 5: Commit**

```bash
git add backend/agent/loop.py backend/tests/test_loop_confirmation_integration.py
git commit -m "feat: gate risky actions through confirmation before execution"
```

---

### Task 6: Hard-block critical actions a second time, inside the executor

**Files:**
- Modify: `backend/agent/executor.py`
- Test: `backend/tests/test_executor_password_guard.py`

**Interfaces:**
- Consumes: `classify_action` (Task 2).
- Produces: nothing new — this is defense-in-depth so a future caller of `execute_action` that forgets to call `gate_action` first still can't type plain text into a password field.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_executor_password_guard.py`:

```python
import asyncio
from unittest.mock import AsyncMock

from backend.agent.executor import execute_action


class _FakeDevice:
    def __init__(self):
        self.texted = []
        self.tapped = []

    async def tap(self, x, y):
        self.tapped.append((x, y))

    async def clear_text(self):
        return None

    async def text(self, value):
        self.texted.append(value)


def test_text_action_into_password_element_never_reaches_the_device():
    device = _FakeDevice()
    elements = [{
        "id": 1, "bounds": [0, 0, 100, 50], "class_name": "android.widget.EditText",
        "resource_id": "", "content_desc": "", "text": "", "clickable": True,
        "focusable": True, "scrollable": False, "password": True,
    }]
    action = {"action": "text", "element_id": 1, "text_input": "hunter2"}

    asyncio.run(execute_action(device, action, elements))

    assert device.texted == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_executor_password_guard.py -v`
Expected: FAIL — `device.texted == ["hunter2"]`, guard doesn't exist yet

- [ ] **Step 3: Add the guard**

In `backend/agent/executor.py`, add the import at the top:

```python
from ..security.risk_policy import classify_action
```

At the very top of `execute_action`, right after the existing docstring (before `action_type = action.get("action", "").lower()`), add:

```python
    if classify_action(action, elements) == "critical":
        return  # gate_action() should already have caught this; this is the backstop
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_executor_password_guard.py -v`
Expected: PASS

Run the full suite again: `pytest backend/tests/ -q` — expected all green.

- [ ] **Step 5: Commit**

```bash
git add backend/agent/executor.py backend/tests/test_executor_password_guard.py
git commit -m "feat: hard-block critical actions inside execute_action as a backstop"
```

---

### Task 7: `POST /agent/{session_id}/confirm` endpoint

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `backend/api/routers/agent.py`
- Test: `backend/tests/test_confirm_endpoint.py`

**Interfaces:**
- Consumes: `AgentState.pending_confirmation` / `confirmation_result` (Task 3), the module-level `_sessions` dict already in `backend/api/routers/agent.py:36`.
- Produces: `ConfirmActionRequest(approve: bool)` schema; route returns `AgentStatusResponse` extended with `pending_confirmation: Optional[dict] = None` so the frontend's poll-based status check (not just the WebSocket) can also see a pending confirmation.

- [ ] **Step 1: Write the failing tests**

Check `backend/api/schemas.py` for the exact style of `AgentStatusResponse` first (Pydantic model, likely `BaseModel` with typed fields — match it), then create `backend/tests/test_confirm_endpoint.py`:

```python
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers import agent as agent_router
from backend.agent.state import AgentState, RunConfig

client = TestClient(app)


def _register_session(session_id: str, **overrides) -> AgentState:
    config = RunConfig(app_name="youtube", task="t", mode="deploy")
    state = AgentState(session_id=session_id, config=config)
    for k, v in overrides.items():
        setattr(state, k, v)
    agent_router._sessions[session_id] = state
    return state


def test_confirm_approve_sets_confirmation_result_true():
    state = _register_session(
        "confirm-1", pending_confirmation={"action": "text", "element_id": 1}
    )
    resp = client.post("/api/v1/agent/confirm-1/confirm", json={"approve": True})
    assert resp.status_code == 200
    assert state.confirmation_result is True


def test_confirm_reject_sets_confirmation_result_false():
    state = _register_session(
        "confirm-2", pending_confirmation={"action": "text", "element_id": 1}
    )
    resp = client.post("/api/v1/agent/confirm-2/confirm", json={"approve": False})
    assert resp.status_code == 200
    assert state.confirmation_result is False


def test_confirm_unknown_session_returns_404():
    resp = client.post("/api/v1/agent/does-not-exist/confirm", json={"approve": True})
    assert resp.status_code == 404


def test_confirm_when_nothing_pending_returns_409():
    state = _register_session("confirm-3")  # pending_confirmation stays None
    resp = client.post("/api/v1/agent/confirm-3/confirm", json={"approve": True})
    assert resp.status_code == 409


def test_status_endpoint_reports_pending_confirmation():
    _register_session(
        "confirm-4", pending_confirmation={"action": "tap", "element_id": 5}
    )
    resp = client.get("/api/v1/agent/confirm-4")
    assert resp.status_code == 200
    assert resp.json()["pending_confirmation"] == {"action": "tap", "element_id": 5}
```

Check `backend/api/main.py` and an existing test like `backend/tests/test_decide_endpoint.py` first to confirm the exact URL prefix (`/api/v1` shown above is a guess based on `frontend/src/api/client.ts`'s `baseURL: '/api/v1'` — verify against `main.py`'s `app.include_router(..., prefix=...)` and correct the test URLs if it differs) and whether `ApiKeyAuthMiddleware` is active in the test app (if `API_KEY` isn't set in the test environment, `backend/security/auth.py:31-32` shows it fails open, so no header is needed here — confirm this before assuming the tests above need one).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_confirm_endpoint.py -v`
Expected: FAIL with 404 on all of them (route doesn't exist) or a schema import error

- [ ] **Step 3: Add the schema and route**

In `backend/api/schemas.py`, add:

```python
class ConfirmActionRequest(BaseModel):
    approve: bool
```

(match the existing `BaseModel` import and style already in the file.)

Find `AgentStatusResponse` in the same file and add one field to it:

```python
    pending_confirmation: Optional[dict] = None
```

(add `Optional` to the existing typing import if it isn't already imported.)

In `backend/api/routers/agent.py`, add the import:

```python
from ..schemas import ConfirmActionRequest  # alongside the other schema imports
```

Add the route directly above `@router.delete("/{session_id}", ...)` (so it's grouped with the other single-session operations, still below `/sessions` and `/decide` which must stay first per the existing comment about wildcard routes):

```python
@router.post("/{session_id}/confirm", response_model=AgentStatusResponse)
async def confirm_action(session_id: str, body: ConfirmActionRequest):
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if state.pending_confirmation is None:
        raise HTTPException(status_code=409, detail="No action is pending confirmation")
    state.confirmation_result = body.approve
    return AgentStatusResponse(
        session_id=session_id,
        status=state.status,
        round_num=state.round_num,
        task_complete=state.task_complete,
        failure_reason=state.failure_reason,
        errors=state.errors[-5:],
        tokens_used=state.tokens_used,
        estimated_cost_usd=state.estimated_cost_usd,
        llm_call_count=state.llm_call_count,
        escalation_count=state.escalation_count,
        pending_confirmation=state.pending_confirmation,
    )
```

Also add `pending_confirmation=state.pending_confirmation` to the existing `AgentStatusResponse(...)` construction inside `get_status()` (`backend/api/routers/agent.py:408-419`), and `pending_confirmation=None` to the `db_row`-based branch just above it (a session that's fallen out of memory has nothing pending by definition).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_confirm_endpoint.py -v`
Expected: all PASS

Run the full suite: `pytest backend/tests/ -q` — expected all green.

- [ ] **Step 5: Commit**

```bash
git add backend/api/schemas.py backend/api/routers/agent.py backend/tests/test_confirm_endpoint.py
git commit -m "feat: add POST /agent/{id}/confirm endpoint"
```

---

### Task 8: Stop baking the API key into the frontend build

**Files:**
- Create: `frontend/src/api/apiKey.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/websocket.ts`
- Modify: `frontend/src/pages/SetupPage.tsx`

**Interfaces:**
- Produces: `getApiKey(): string | undefined` and `setApiKey(key: string): void`, backed by `localStorage` under key `mobile-agent-api-key`. `import.meta.env.VITE_API_KEY` is kept only as a fallback default the first time `getApiKey()` runs with nothing in storage yet — convenient for solo local dev, but the value never has to be in the bundle for a real deployment, and once a user sets a key via the UI it fully overrides the baked default.

- [ ] **Step 1: Create the key module**

Create `frontend/src/api/apiKey.ts`:

```typescript
// The API key used to live only as VITE_API_KEY, baked into the JS bundle at
// build time — anyone who could load the page could read it out of the
// bundle and drive the phone. It now lives in localStorage, set once via the
// Setup page. VITE_API_KEY is kept only as the first-run default for local
// solo-dev use (backend and frontend on the same machine, same person) —
// once a real key is saved to localStorage it always wins.
const STORAGE_KEY = 'mobile-agent-api-key'

export function getApiKey(): string | undefined {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored) return stored
  return (import.meta.env.VITE_API_KEY as string | undefined) || undefined
}

export function setApiKey(key: string): void {
  if (key) {
    localStorage.setItem(STORAGE_KEY, key)
  } else {
    localStorage.removeItem(STORAGE_KEY)
  }
}
```

- [ ] **Step 2: Use it in the REST client**

In `frontend/src/api/client.ts`, replace lines 1-13:

```typescript
import axios from 'axios'

// VITE_API_KEY is baked in at build time — acceptable for this project's
// single-user local-dev model (the person running the frontend build is the
// same person running the backend on their own machine), not a substitute
// for real auth in a multi-user deployment.
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
  headers: import.meta.env.VITE_API_KEY
    ? { 'X-API-Key': import.meta.env.VITE_API_KEY }
    : {},
})
```

with:

```typescript
import axios from 'axios'
import { getApiKey } from './apiKey'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10_000,
})

api.interceptors.request.use((config) => {
  const key = getApiKey()
  if (key) config.headers['X-API-Key'] = key
  return config
})
```

(the header is now read fresh on every request instead of frozen at module-init time, so calling `setApiKey()` takes effect immediately without a page reload.)

- [ ] **Step 3: Use it in the WebSocket client**

In `frontend/src/api/websocket.ts`, add the import at the top:

```typescript
import { getApiKey } from './apiKey'
```

Replace line 73:

```typescript
    const apiKey = import.meta.env.VITE_API_KEY as string | undefined
```

with:

```typescript
    const apiKey = getApiKey()
```

- [ ] **Step 4: Add the Setup page field**

In `frontend/src/pages/SetupPage.tsx`, add the import:

```typescript
import { useState } from 'react'
import { getApiKey, setApiKey } from '../api/apiKey'
```

(merge with the existing `useState, useEffect` import from `'react'` rather than duplicating it.)

Inside the `SetupPage` component, add local state and a save handler:

```typescript
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey() ?? '')
  const [saved, setSaved] = useState(false)

  function handleSaveApiKey() {
    setApiKey(apiKeyInput.trim())
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }
```

Add a new panel, e.g. right after the "Environment" block (before "System Status"):

```tsx
      {/* API Key */}
      <div className="border border-zinc-800 rounded-xl p-5 space-y-3">
        <div className="text-sm font-medium text-zinc-200">API Key</div>
        <p className="text-xs text-zinc-500">
          Stored only in this browser's local storage — never baked into the app build.
          Must match the backend's <code className="text-sky-400">API_KEY</code> in <code className="text-sky-400">.env</code>.
        </p>
        <div className="flex gap-2">
          <input
            type="password"
            value={apiKeyInput}
            onChange={(e) => setApiKeyInput(e.target.value)}
            placeholder="Backend API key"
            className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-200 font-mono"
          />
          <button
            onClick={handleSaveApiKey}
            className="px-4 py-2 border border-zinc-700 hover:border-zinc-500 rounded-lg text-sm text-zinc-300"
          >
            {saved ? 'Saved' : 'Save'}
          </button>
        </div>
      </div>
```

- [ ] **Step 5: Manually verify in the browser**

Run: `cd /c/Users/bella/mobile-agent/frontend && npm run dev`

Open the app, go to Setup, enter the key that matches `API_KEY` in the backend's `.env`, click Save, then confirm a page that needs auth (e.g. Explore) still works — and confirm in the browser's DevTools → Application → Local Storage that the key is there under `mobile-agent-api-key`, and in DevTools → Sources that a fresh production build (`npm run build`, inspect `dist/assets/*.js`) contains no key unless `VITE_API_KEY` was set at build time.

- [ ] **Step 6: Run frontend checks**

Run: `npm run lint && npm run build`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/apiKey.ts frontend/src/api/client.ts frontend/src/api/websocket.ts frontend/src/pages/SetupPage.tsx
git commit -m "fix: stop baking the API key into the frontend build, use localStorage instead"
```

---

### Task 9: Confirmation modal in the UI

**Files:**
- Modify: `frontend/src/api/websocket.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/components/ConfirmationModal.tsx`
- Modify: `frontend/src/pages/DeployPage.tsx` (check `frontend/src/pages/ExplorePage.tsx` too — wire into whichever page(s) currently render `ActionLog`/`DeviceScreen` and own the WebSocket subscription)

**Interfaces:**
- Consumes: the `confirmation_required` WebSocket event (new in Task 4/5's `gate_action` broadcast), `agentApi.confirm()` (added here).

- [ ] **Step 1: Add the event type and API call**

In `frontend/src/api/websocket.ts`, add:

```typescript
export interface ConfirmationRequiredEvent {
  type: 'confirmation_required'
  risk: 'medium' | 'high'
  action: string
  element_id: number | null
  thought: string
}

export interface ActionBlockedEvent {
  type: 'action_blocked'
  risk: 'critical'
  action: string
  reason: string
}
```

Add both to the `AgentEvent` union:

```typescript
export type AgentEvent =
  | ScreenshotUpdateEvent
  | ActionEvent
  | KBUpdateEvent
  | PlanReadyEvent
  | StatusChangeEvent
  | ErrorEvent
  | ConfirmationRequiredEvent
  | ActionBlockedEvent
```

In `frontend/src/api/client.ts`, add to `agentApi`:

```typescript
  confirm: (session_id: string, approve: boolean) =>
    api.post<AgentStatus>(`/agent/${session_id}/confirm`, { approve }).then((r) => r.data),
```

- [ ] **Step 2: Build the modal component**

Create `frontend/src/components/ConfirmationModal.tsx`:

```tsx
import { agentApi } from '../api/client'
import type { ConfirmationRequiredEvent } from '../api/websocket'

interface Props {
  sessionId: string
  event: ConfirmationRequiredEvent
  onResolved: () => void
}

export function ConfirmationModal({ sessionId, event, onResolved }: Props) {
  async function respond(approve: boolean) {
    await agentApi.confirm(sessionId, approve)
    onResolved()
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-md w-full space-y-4">
        <div className="flex items-center gap-2">
          <span
            className={
              event.risk === 'high'
                ? 'px-2 py-0.5 rounded text-xs font-mono bg-red-900/40 text-red-300'
                : 'px-2 py-0.5 rounded text-xs font-mono bg-amber-900/40 text-amber-300'
            }
          >
            {event.risk.toUpperCase()} RISK
          </span>
          <h2 className="text-sm font-medium text-zinc-100">Confirm action</h2>
        </div>
        <div className="text-sm text-zinc-300">
          <div>
            Action: <span className="font-mono text-zinc-100">{event.action}</span>
            {event.element_id !== null && (
              <span className="text-zinc-500"> on element #{event.element_id}</span>
            )}
          </div>
          {event.thought && <div className="text-zinc-500 mt-1">{event.thought}</div>}
        </div>
        <div className="flex gap-3 justify-end">
          <button
            onClick={() => respond(false)}
            className="px-4 py-2 rounded-lg text-sm border border-zinc-700 text-zinc-300 hover:border-zinc-500"
          >
            Reject
          </button>
          <button
            onClick={() => respond(true)}
            className="px-4 py-2 rounded-lg text-sm bg-sky-600 hover:bg-sky-500 text-white"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Wire it into the page(s) that own the WebSocket**

Read `frontend/src/pages/DeployPage.tsx` and `frontend/src/pages/ExplorePage.tsx` first to find where `AgentWebSocket`/`AgentEvent` is already handled (likely a `switch (event.type)` or a series of `if` checks feeding into `useAgentStore` — match whatever pattern is already there rather than introducing a second one). In that handler, add a case for `confirmation_required` that stores the event in local/store state, and render:

```tsx
{pendingConfirmation && (
  <ConfirmationModal
    sessionId={sessionId}
    event={pendingConfirmation}
    onResolved={() => setPendingConfirmation(null)}
  />
)}
```

Also handle `action_blocked` — at minimum route it into whatever the page already uses for `error`/toast-style notices (check `ActionLog.tsx` — it may already be a reasonable place to append a "blocked" row) so a critical block is visible instead of silently vanishing.

- [ ] **Step 4: Manually verify**

Run: `npm run dev`, then run a Deploy session against a screen with a plain text field and confirm the modal appears and Approve/Reject both unblock the loop (check the backend terminal/logs to confirm `execute_action` runs only after Approve).

- [ ] **Step 5: Run frontend checks**

Run: `npm run lint && npm run build`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/websocket.ts frontend/src/api/client.ts frontend/src/components/ConfirmationModal.tsx frontend/src/pages/DeployPage.tsx frontend/src/pages/ExplorePage.tsx
git commit -m "feat: show confirmation modal for medium/high-risk actions"
```

---

## What's deliberately out of scope here

This plan is V2 Phase 1 only — the security/policy foundation. Per the roadmap discussed, later phases are separate plans, written once this one has landed and been used for real:

- **Phase 2 (GUI VLM grounding via Qwen-VL):** needs picking a specific already-wired provider with a VL-capable model (check `backend/llm/`'s provider list), a new prompt builder, and its own eval before it touches the main loop.
- **Phase 3 additions beyond this plan:** the privacy firewall / sensitive-app screen detector — this plan only covers per-field password detection (cheap, exact, uiautomator-native), not whole-screen sensitive-app classification, which needs its own design pass (allowlist source of truth, false-negative handling).
- **Phase 4 (MediaProjection companion app):** Android-side work, unrelated to this backend/frontend policy layer.

## Self-review notes

- Every task ends in a runnable test command with a concrete expected result — no "add tests for the above" placeholders.
- `critical` never reaches the confirmation UI at all (Task 4 returns before setting `pending_confirmation`) and is independently re-blocked inside `execute_action` (Task 6) — two layers, matching the plan's "never allow the model to enter passwords" requirement literally rather than relying on the LLM's own restraint.
- `type_secret` is deliberately kept at `"low"` risk (Task 2) since the secret value never reaches the LLM (`backend/agent/executor.py`'s existing `type_secret` branch resolves it server-side) — confirming every login autofill would train users to reflexively click Approve, which defeats the point of the gate.
- Task 7's exact URL prefix and auth-header requirement are flagged as "verify against `main.py`" rather than asserted outright, since this plan was written without opening `backend/api/main.py` — the implementer must check it before trusting the test URLs.
