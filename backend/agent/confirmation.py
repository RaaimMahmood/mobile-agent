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
