"""Experimental workflow-based implementation of the deploy phase.

Drop-in async alternative to ``run_deploy`` in loop.py, built on the
``llama-index-workflows`` library. Completely decoupled — does not import
from or modify ``loop.py``.

Usage::

    from backend.agent.deploy_workflow import DeployWorkflow
    result = await DeployWorkflow(timeout=300).run(state=state)
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from workflows import Workflow, step
from workflows.context import Context
from workflows.events import Event, StartEvent, StopEvent

from ..perception import annotate_screenshot, parse_interactive_elements
from ..llm import call_vision_llm
from ..llm.prompts import build_deploy_prompt, build_progress_prompt
from ..llm.pricing import estimate_cost_usd
from .state import AgentState
from .planner import run_planner
from .executor import execute_action
from .loop import _launch_target_app
from ..security.error_sanitizer import sanitize_error


# ---------------------------------------------------------------------------
# Event definitions — typed Pydantic models for every workflow transition
# ---------------------------------------------------------------------------

class RoundStarted(Event):
    """Emitted at the top of each deploy loop iteration."""
    round_num: int


class ScreenCaptured(Event):
    """Raw screenshot and XML tree captured from the device."""
    raw_png: bytes
    raw_xml: str
    round_num: int


class ElementsParsed(Event):
    """Interactive elements extracted and annotated screenshot produced."""
    elements: list[dict]
    screenshot_b64: str
    raw_png: bytes
    round_num: int


class ContextRetrieved(Event):
    """Knowledge-base docs context retrieved for current elements."""
    elements: list[dict]
    screenshot_b64: str
    docs_context: str
    round_num: int


class DecisionMade(Event):
    """LLM decision received (action, thought, element_id, …)."""
    decision: dict
    elements: list[dict]
    round_num: int


class ActionExecuted(Event):
    """Action executed on device, ready for next round."""
    decision: dict
    elements: list[dict]
    round_num: int


# ---------------------------------------------------------------------------
# DeployWorkflow
# ---------------------------------------------------------------------------

class DeployWorkflow(Workflow):
    """Event-driven reimplementation of ``run_deploy``.

    Invocable as a drop-in replacement::

        result = await DeployWorkflow(timeout=300).run(state=state)

    The ``state`` argument must be a fully constructed ``AgentState`` dataclass.
    All side-effects (broadcasts, state mutations, device interactions) are
    identical to those produced by ``run_deploy``.
    """

    @step
    async def start_step(
        self, ctx: Context, ev: StartEvent
    ) -> RoundStarted:
        """Initialise the deploy phase: run planner, store state, emit first round."""
        state: AgentState = ev.get("state")
        await ctx.store.set("state", state)

        state.status = "running"
        await state.broadcast({"type": "status_change", "status": "running", "mode": "deploy"})

        await _launch_target_app(state)

        state.sub_steps = await run_planner(state)
        await state.broadcast({"type": "plan_ready", "steps": state.sub_steps})

        return RoundStarted(round_num=state.round_num)

    @step
    async def capture_step(
        self, ctx: Context, ev: RoundStarted
    ) -> ScreenCaptured | RoundStarted:
        """Pause check + device screenshot/XML capture."""
        state: AgentState = await ctx.store.get("state")

        try:
            if state.status == "paused":
                await asyncio.sleep(0.5)
                return RoundStarted(round_num=state.round_num)

            raw_png = await state.device.screenshot()
            raw_xml = await state.device.pull_xml()

            return ScreenCaptured(
                raw_png=raw_png,
                raw_xml=raw_xml,
                round_num=state.round_num,
            )

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })

    @step
    async def parse_step(
        self, ctx: Context, ev: ScreenCaptured
    ) -> ElementsParsed:
        """Parse interactive elements and annotate screenshot."""
        state: AgentState = await ctx.store.get("state")

        try:
            elements = parse_interactive_elements(ev.raw_xml)
            state.elements = [e.to_dict() for e in elements]
            annotated_b64 = annotate_screenshot(ev.raw_png, elements)
            state.screenshot_b64 = annotated_b64

            await state.broadcast({
                "type": "screenshot_update",
                "screenshot": annotated_b64,
                "round": state.round_num,
                "element_count": len(elements),
            })

            return ElementsParsed(
                elements=state.elements,
                screenshot_b64=annotated_b64,
                raw_png=ev.raw_png,
                round_num=state.round_num,
            )

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })

    @step
    async def context_step(
        self, ctx: Context, ev: ElementsParsed
    ) -> ContextRetrieved:
        """Retrieve knowledge-base context for current elements."""
        state: AgentState = await ctx.store.get("state")

        try:
            # retrieve_context() does attribute access (e.class_name, not e['class_name']) —
            # it expects raw InteractiveElement-shaped objects, but ev.elements here is the
            # dict-serialized form (needed everywhere else: prompts, broadcasts, executor).
            # SimpleNamespace gives attribute access over the same keys without re-parsing.
            from types import SimpleNamespace
            shim_elements = [SimpleNamespace(**e) for e in ev.elements]
            docs_context = await state.kb.retrieve_context(shim_elements)

            return ContextRetrieved(
                elements=ev.elements,
                screenshot_b64=ev.screenshot_b64,
                docs_context=docs_context,
                round_num=state.round_num,
            )

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })

    @step
    async def decision_step(
        self, ctx: Context, ev: ContextRetrieved
    ) -> DecisionMade | RoundStarted | StopEvent:
        """LLM decision, usage tracking, limit enforcement, finish/impossible checks."""
        state: AgentState = await ctx.store.get("state")

        try:
            prompt = build_deploy_prompt(state, ev.elements, ev.docs_context)
            try:
                decision = await call_vision_llm(state.provider, ev.screenshot_b64, prompt)
            except Exception as e:
                state.errors.append(f"Round {state.round_num} LLM error: {sanitize_error(e)}")
                state.round_num += 1
                return RoundStarted(round_num=state.round_num)

            decision["_provider"] = state.provider

            usage = decision.pop("_usage", {})
            state.llm_call_count += 1
            state.tokens_used += usage.get("total_tokens", 0)
            state.estimated_cost_usd += estimate_cost_usd(
                state.provider,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

            cfg = state.config
            if (
                (cfg.max_tokens is not None and state.tokens_used >= cfg.max_tokens)
                or (cfg.max_cost_usd is not None and state.estimated_cost_usd >= cfg.max_cost_usd)
                or (cfg.max_llm_calls is not None and state.llm_call_count >= cfg.max_llm_calls)
            ):
                state.failure_reason = (
                    f"Usage limit reached (tokens={state.tokens_used}, "
                    f"cost=${state.estimated_cost_usd:.4f}, calls={state.llm_call_count})"
                )
                return await _finish_deploy(state)

            await state.broadcast({
                "type": "action_event",
                "round": state.round_num,
                "action": decision.get("action", ""),
                "element_id": decision.get("element_id"),
                "thought": decision.get("thought", ""),
                "observation": decision.get("observation", ""),
            })

            if decision.get("action") == "finish":
                state.task_complete = True
                return await _finish_deploy(state)

            thought = decision.get("thought", "").lower()
            if any(
                w in thought
                for w in ["cannot", "impossible", "not possible", "unable"]
            ):
                state.failure_reason = decision.get(
                    "thought", "Task marked impossible by agent"
                )

            return DecisionMade(
                decision=decision,
                elements=ev.elements,
                round_num=state.round_num,
            )

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })

    @step
    async def execute_step(
        self, ctx: Context, ev: DecisionMade
    ) -> ActionExecuted:
        """Execute action on device and advance sub-step index."""
        state: AgentState = await ctx.store.get("state")

        try:
            await execute_action(
                state.device, ev.decision, ev.elements, state.credentials, state=state
            )
            await state.device.wait_idle()

            state.current_step_idx = min(
                state.current_step_idx + _should_advance(ev.decision),
                len(state.sub_steps) - 1,
            )

            return ActionExecuted(
                decision=ev.decision,
                elements=ev.elements,
                round_num=state.round_num,
            )

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })

    @step
    async def advance_step(
        self, ctx: Context, ev: ActionExecuted
    ) -> RoundStarted | StopEvent:
        """Progress check, history recording, round increment, termination check."""
        state: AgentState = await ctx.store.get("state")

        try:
            if state.round_num > 0 and state.round_num % 5 == 0:
                progress_png = await state.device.screenshot()
                progress_b64 = base64.b64encode(progress_png).decode()
                try:
                    prog = await call_vision_llm(
                        state.provider,
                        progress_b64,
                        build_progress_prompt(state.task),
                    )
                    prog_usage = prog.pop("_usage", {})
                    state.llm_call_count += 1
                    state.tokens_used += prog_usage.get("total_tokens", 0)
                    state.estimated_cost_usd += estimate_cost_usd(
                        state.provider,
                        prog_usage.get("prompt_tokens", 0),
                        prog_usage.get("completion_tokens", 0),
                    )
                    if prog.get("complete"):
                        state.task_complete = True
                        await state.broadcast({
                            "type": "action_event",
                            "round": state.round_num,
                            "action": "finish",
                            "element_id": None,
                            "thought": f"Progress check: {prog.get('progress', 'task complete')}",
                            "observation": "",
                        })
                        return await _finish_deploy(state)
                except Exception:
                    pass

            state.action_history.append({
                "round": state.round_num,
                "action": ev.decision,
                "element_sig": _get_elem_sig(
                    ev.elements, ev.decision.get("element_id")
                ),
            })
            state.round_num += 1

            if state.round_num >= state.max_rounds or state.task_complete:
                return await _finish_deploy(state)

            return RoundStarted(round_num=state.round_num)

        except Exception as e:
            state.status = "error"
            state.errors.append(sanitize_error(e))
            await state.broadcast({"type": "error", "message": sanitize_error(e)})
            return StopEvent(result={
                "status": "error",
                "error": sanitize_error(e),
                "task_complete": state.task_complete,
            })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _finish_deploy(state: AgentState) -> StopEvent:
    """Set terminal status, broadcast, and return a StopEvent."""
    state.status = "done"
    await state.broadcast({
        "type": "status_change",
        "status": "done",
        "task_complete": state.task_complete,
        "failure_reason": state.failure_reason,
    })
    return StopEvent(result={
        "status": "done",
        "task_complete": state.task_complete,
        "failure_reason": state.failure_reason,
        "round_num": state.round_num,
        "tokens_used": state.tokens_used,
        "estimated_cost_usd": state.estimated_cost_usd,
        "llm_call_count": state.llm_call_count,
    })


def _get_elem_sig(elements: list[dict], elem_id: int | None) -> str:
    if elem_id is None:
        return "unknown"
    for e in elements:
        if e["id"] == elem_id:
            return f"{e.get('resource_id', '')}::{e.get('class_name', '')}"
    return "unknown"


def _should_advance(decision: dict) -> int:
    """Heuristic: advance sub-step index when agent taps a submit/confirm/search button."""
    text = (decision.get("thought", "") + decision.get("observation", "")).lower()
    advance_signals = [
        "submitted", "searching", "navigated", "opened", "clicked submit",
        "applied", "saved", "completed", "confirmed",
    ]
    return 1 if any(s in text for s in advance_signals) else 0


# ---------------------------------------------------------------------------
# Diagram generation
# ---------------------------------------------------------------------------

def generate_diagram(path: str = "docs/deploy_workflow.html") -> str:
    """Render a Mermaid-based HTML diagram of the workflow graph.

    Uses ``get_workflow_representation`` from the workflows library to
    introspect the step/event graph, then emits a self-contained HTML file
    with an embedded Mermaid diagram.  Returns the output path.
    """
    from workflows.representation import get_workflow_representation
    import os, json

    graph = get_workflow_representation(DeployWorkflow)

    # Build adjacency list from graph edges
    nodes = [(n.id, n.label, getattr(n, "node_type", "unknown")) for n in graph.nodes]
    edges = [(e.source, e.target, e.label) for e in graph.edges]

    # Build Mermaid flowchart
    mermaid_lines = ["flowchart TD"]
    for node_id, label, node_type in nodes:
        safe_id = node_id.replace("-", "_").replace(".", "_").replace(" ", "_")
        safe_label = label.replace('"', "'")
        if node_type == "step":
            mermaid_lines.append(f'    {safe_id}["{safe_label}"]')
        elif node_type == "event":
            mermaid_lines.append(f'    {safe_id}{{{{"{safe_label}"}}}}')
        else:
            mermaid_lines.append(f'    {safe_id}["{safe_label}"]')

    for src, tgt, lbl in edges:
        safe_src = src.replace("-", "_").replace(".", "_").replace(" ", "_")
        safe_tgt = tgt.replace("-", "_").replace(".", "_").replace(" ", "_")
        if lbl:
            mermaid_lines.append(f'    {safe_src} -->|"{lbl}"| {safe_tgt}')
        else:
            mermaid_lines.append(f'    {safe_src} --> {safe_tgt}')

    mermaid_def = "\n".join(mermaid_lines)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>DeployWorkflow Diagram</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: sans-serif; margin: 2rem; background: #fafafa; }}
  h1 {{ font-size: 1.4rem; }}
  .mermaid {{ background: #fff; padding: 1rem; border: 1px solid #ddd; border-radius: 6px; }}
</style>
</head>
<body>
<h1>DeployWorkflow — step/event graph</h1>
<div class="mermaid">
{mermaid_def}
</div>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'default' }});</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.getcwd())
    out = generate_diagram()
    print(f"Diagram written to {out}")
