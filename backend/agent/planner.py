from ..llm import call_text_llm
from ..llm.prompts import build_planner_prompt
from ..security.error_sanitizer import sanitize_error
from .state import AgentState
from .usage import record_usage


async def run_planner(state: AgentState) -> list[str]:
    """
    Decompose the task into ordered sub-steps using a text-only LLM call.
    Returns a list of step strings. Falls back to [task] on any error.
    """
    try:
        prompt = build_planner_prompt(state.task, state.app_name)
        result = await call_text_llm(state.provider, prompt)
        record_usage(state, result)
        steps = result.get("steps", [])
        if steps and isinstance(steps, list):
            return [str(s) for s in steps]
    except Exception as e:
        state.errors.append(f"Planner failed: {sanitize_error(e)}")

    return [state.task]
