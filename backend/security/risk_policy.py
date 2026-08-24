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

# Bottom-right of the screen is where a lot of real apps put the primary CTA
# — a send/post/confirm FAB with no text and no useful content-desc (a bare
# paper-plane icon, say). The keyword check above can't see those at all, so
# an unlabeled tap landing here is treated as "unknown, not low" rather than
# silently passing through — costs an extra confirmation click on ordinary
# FABs, but closes the silent miss on the ones that actually matter.
_UNLABELED_HOTZONE_X_FRACTION = 0.6
_UNLABELED_HOTZONE_Y_FRACTION = 0.75


def _find_element(elements: list[dict], elem_id) -> dict | None:
    if elem_id is None:
        return None
    for e in elements:
        if e.get("id") == elem_id:
            return e
    return None


def _element_text_blob(elem: dict) -> str:
    return f"{elem.get('text', '')} {elem.get('content_desc', '')} {elem.get('resource_id', '')}".lower()


def _is_unlabeled(elem: dict) -> bool:
    return not elem.get("text") and not elem.get("content_desc")


def _in_bottom_right_hotzone(elem: dict, elements: list[dict]) -> bool:
    """Approximates screen size from the widest/tallest bounds seen this
    round — classify_action has no direct access to the device's actual
    resolution, only the element list for the current screen."""
    bounds = elem.get("bounds")
    if not bounds or len(bounds) != 4:
        return False
    screen_w = max((e["bounds"][2] for e in elements if e.get("bounds")), default=0)
    screen_h = max((e["bounds"][3] for e in elements if e.get("bounds")), default=0)
    if screen_w == 0 or screen_h == 0:
        return False
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    return cx > screen_w * _UNLABELED_HOTZONE_X_FRACTION and cy > screen_h * _UNLABELED_HOTZONE_Y_FRACTION


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
        if _is_unlabeled(elem) and _in_bottom_right_hotzone(elem, elements):
            return "medium"
        return "low"

    if action_type in _LOW_RISK_ACTIONS or action_type == "tap":
        return "low"

    # Unrecognized action type: fail toward requiring a human, not toward
    # silently executing something the policy has no rule for.
    return "medium"
