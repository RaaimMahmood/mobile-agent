import asyncio

from ..security.risk_policy import classify_action, RISK_TIERS

POLL_INTERVAL_SECONDS = 0.5

_NEEDS_CONFIRMATION = RISK_TIERS.index("medium")  # tiers >= this index pause


def _elements_signature(elements: list[dict]) -> tuple:
    """Cheap fingerprint of what's on screen, order-independent.

    Not the graph module's screen_signature() — this deliberately avoids
    importing anything from graph.neo4j_client (which pulls in a neo4j
    driver) just to answer "did the screen change while we waited for a
    human to click a button".
    """
    return tuple(sorted(
        (e.get("resource_id", ""), e.get("class_name", ""), e.get("text", ""))
        for e in elements
    ))


async def _screen_changed_since_pause(state, elements_at_pause: list[dict]) -> bool:
    """True if the live screen no longer matches what was on screen when the
    confirmation was raised. A human can take arbitrarily long to respond —
    a notification, an app switch, or a timeout can change what's on screen
    in the meantime, and the action was decided against the *old* screen.
    device is None for the stateless /agent/decide path, which never pauses
    (that caller drives itself), so this is only ever reached with a real
    device attached.
    """
    if state.device is None:
        return False
    try:
        from ..perception.xml_parser import parse_interactive_elements
        raw_xml = await state.device.pull_xml()
        live_elements = [e.to_dict() for e in parse_interactive_elements(raw_xml)]
    except Exception:
        # Can't verify freshness — fail safe by treating it as stale rather
        # than executing against a screen we couldn't re-check.
        return True
    return _elements_signature(live_elements) != _elements_signature(elements_at_pause)


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
        if not state.confirmation_result:
            return False
        if await _screen_changed_since_pause(state, elements):
            await state.broadcast({
                "type": "action_blocked",
                "risk": tier,
                "action": decision.get("action"),
                "reason": "Approved, but the screen changed while waiting — skipping to avoid acting on a stale decision.",
            })
            return False
        return True
    finally:
        state.pending_confirmation = None
        state.confirmation_result = None
        if state.status == "paused":
            state.status = previous_status
