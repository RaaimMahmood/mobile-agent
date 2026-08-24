from ..device.controller import DeviceController, KEYCODE_BACK
from ..perception.grid import overlay_grid
from ..llm import call_vision_llm
from ..llm.prompts import build_grid_prompt
from ..security.credentials import CredentialNotFoundError
from ..security.risk_policy import classify_action
from .usage import record_usage


async def execute_action(
    device: DeviceController,
    action: dict,
    elements: list[dict],
    credentials=None,
    state=None,
) -> None:
    """
    Execute the LLM-decided action on the Android device.
    action: parsed action dict with keys: action, element_id, text_input, direction, grid_cell, secret_id
    credentials: security.CredentialManager, or None if no secrets.yaml is configured
    state: AgentState, or None. Only needed so grid mode's extra vision call
        can be billed to the session — without it that call burns tokens the
        usage limiter never sees.
    """
    if classify_action(action, elements) == "critical":
        return  # gate_action() should already have caught this; this is the backstop

    action_type = action.get("action", "").lower()
    elem_id = action.get("element_id")
    text_input = action.get("text_input")
    direction = action.get("direction", "up")
    grid_cell = action.get("grid_cell")
    secret_id = action.get("secret_id")

    if action_type == "tap" and elem_id is not None:
        elem = _find_element(elements, elem_id)
        if elem:
            cx, cy = _center(elem)
            await device.tap(cx, cy)

    elif action_type == "text" and text_input:
        # tap the focused element first if specified
        if elem_id is not None:
            elem = _find_element(elements, elem_id)
            if elem:
                cx, cy = _center(elem)
                await device.tap(cx, cy)
        # clear any pre-existing content first — without this, a retried or
        # interrupted round appends to whatever's already in the field instead
        # of replacing it, producing garbled concatenated text
        await device.clear_text()
        await device.text(text_input)

    elif action_type == "type_secret" and secret_id:
        # tap the focused element first if specified, same as "text"
        if elem_id is not None:
            elem = _find_element(elements, elem_id)
            if elem:
                cx, cy = _center(elem)
                await device.tap(cx, cy)
        if credentials is not None:
            try:
                secret_value = credentials.resolve(secret_id)  # local var only — never
                await device.clear_text()                       # stored, logged, or returned
                await device.text(secret_value)
            except CredentialNotFoundError:
                pass  # unknown secret_id — no-op, agent sees the field is still empty next round

    elif action_type == "swipe":
        from_x = from_y = None
        if elem_id is not None:
            elem = _find_element(elements, elem_id)
            if elem:
                from_x, from_y = _center(elem)
        await device.swipe(direction or "up", from_x=from_x, from_y=from_y)

    elif action_type == "long_press" and elem_id is not None:
        elem = _find_element(elements, elem_id)
        if elem:
            cx, cy = _center(elem)
            await device.long_press(cx, cy)

    elif action_type == "back":
        await device.key_event(KEYCODE_BACK)

    elif action_type == "grid":
        # grid mode: take raw screenshot, overlay grid, ask LLM which cell, tap center
        raw = await device.screenshot()
        grid_b64, coords = overlay_grid(raw)
        thought = action.get("thought", "")
        prompt = build_grid_prompt(thought)
        # use the same provider that called us — caller passes provider via closure
        # grid_cell may already be set if LLM returned it; otherwise ask again
        if not grid_cell:
            grid_resp = await call_vision_llm(
                action.get("_provider", "gemini"), grid_b64, prompt,
                trace=state.trace if state is not None else None,
            )
            if state is not None:
                record_usage(state, grid_resp)
            grid_cell = grid_resp.get("grid_cell", "E5")

        coord = coords.get(grid_cell.upper())
        if coord:
            await device.tap(coord[0], coord[1])


def _find_element(elements: list[dict], elem_id: int) -> dict | None:
    for e in elements:
        if e["id"] == elem_id:
            return e
    return None


def _center(elem: dict) -> tuple[int, int]:
    b = elem["bounds"]
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
