import base64
from datetime import datetime, timezone

from ..knowledge_base.store import ElementDoc
from ..llm import call_dual_vision_llm
from ..llm.prompts import build_reflect_prompt
from ..security.error_sanitizer import sanitize_error
from .state import AgentState
from .usage import record_usage


async def run_reflector(
    state: AgentState,
    before_png: bytes,
    after_png: bytes,
    action: dict,
    elements: list[dict],
) -> ElementDoc | None:
    """
    Generate KB documentation for the element that was just interacted with.
    Returns None if reflection is not applicable or fails.
    """
    elem_id = action.get("element_id")
    if elem_id is None:
        return None

    elem = next((e for e in elements if e["id"] == elem_id), None)
    if elem is None:
        return None

    before_b64 = base64.b64encode(before_png).decode()
    after_b64  = base64.b64encode(after_png).decode()
    action_type = action.get("action", "tap")

    try:
        prompt = build_reflect_prompt(state.app_name, elem, action_type)
        result = await call_dual_vision_llm(state.provider, before_b64, after_b64, prompt)
        record_usage(state, result)
    except Exception as e:
        state.errors.append(f"Reflector failed for element {elem_id}: {sanitize_error(e)}")
        return None

    doc_id = f"{state.app_name}::{elem.get('resource_id','')}::{elem.get('class_name','')}"
    return ElementDoc(
        id=doc_id,
        app_name=state.app_name,
        element_sig=f"{elem.get('resource_id','')}::{elem.get('class_name','')}",
        class_name=elem.get("class_name", ""),
        resource_id=elem.get("resource_id", ""),
        content_desc=elem.get("content_desc", ""),
        text=elem.get("text", ""),
        documentation=result.get("documentation", ""),
        observed_result=result.get("observed_result", ""),
        last_explored_at=datetime.now(timezone.utc).isoformat(),
    )
