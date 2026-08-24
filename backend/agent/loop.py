import asyncio
import base64
import logging

from ..perception import parse_interactive_elements, annotate_screenshot
from ..llm import call_vision_llm, call_text_llm
from ..llm.prompts import build_explore_prompt, build_deploy_prompt, build_progress_prompt
from .state import AgentState
from ..graph.neo4j_client import screen_signature
from ..device.app_registry import resolve_package
from ..security.app_policy import is_app_allowed
from ..security.error_sanitizer import sanitize_error
from .planner import run_planner
from .executor import execute_action
from .confirmation import gate_action
from .reflector import run_reflector
from ..llm.pricing import estimate_cost_usd
from ..persistence import create_session, update_session, append_event

logger = logging.getLogger(__name__)


def _should_continue(state: AgentState) -> bool:
    """Loop-continuation predicate shared by run_explore and run_deploy.

    Extracted into a named helper so the stop path is directly testable.
    The stop_requested check is the important one: previously both loops
    only inspected `status` for "paused", so DELETE /agent/{id} and the
    WebSocket "stop" message set status="done" and nothing happened — the
    agent kept driving the device while the API reported it as stopped.
    """
    return (
        state.round_num < state.max_rounds
        and not state.task_complete
        and not state.stop_requested
    )


async def _launch_target_app(state: AgentState) -> None:
    """Bring the target app to the foreground before the round loop starts.

    Without this, the agent reasons about whatever screen the device already
    happened to be showing (e.g. left over from a previous session) instead
    of the app it was actually asked to operate — it has no action available
    to recover from that (no "launch app" action, "back" isn't in the
    schema), so it gets stuck swiping trying to find UI that isn't there.
    Unknown app_name values fall back to the pre-existing behavior
    (assume the app is already open) rather than failing the run.
    """
    package = resolve_package(state.app_name)
    if package is None:
        logger.warning(
            "No known package for app_name=%r — assuming it's already open", state.app_name
        )
        return
    try:
        await state.device.launch_app(package)
    except Exception as e:
        logger.warning("launch_app failed for %s (%s): %s", state.app_name, package, e)


async def _reject_if_app_not_allowed(state: AgentState) -> bool:
    """True if the run was rejected and the caller should stop before ever
    launching the app or entering the round loop. ALLOWED_APPS is unset by
    default (is_app_allowed() then allows everything) — this is an opt-in
    gate, not a default restriction, matching the fail-open posture the rest
    of this project uses for optional policy.
    """
    if is_app_allowed(state.app_name):
        return False
    state.failure_reason = f"App '{state.app_name}' is not in the allowed apps list"
    state.status = "done"
    try:
        await update_session(state.session_id, status="done", failure_reason=state.failure_reason)
    except Exception as e:
        logger.warning("persistence update_session failed: %s", e)
    await state.broadcast({
        "type": "status_change",
        "status": "done",
        "task_complete": False,
        "failure_reason": state.failure_reason,
    })
    return True


async def run_explore(state: AgentState) -> None:
    """Explore phase: systematically interact with all UI elements and build KB."""
    state.status = "running"
    await state.broadcast({"type": "status_change", "status": "running", "mode": "explore"})
    try:
        await create_session(
            state.session_id, state.app_name, state.task, state.mode, state.provider,
            state.config.reasoning_mode, state.device.serial if state.device else None,
        )
    except Exception as e:
        logger.warning("persistence create_session failed: %s", e)
    if await _reject_if_app_not_allowed(state):
        return
    from ..observability.langfuse_client import start_session_trace, end_session_trace
    trace = start_session_trace(state.session_id, mode="explore", app_name=state.app_name, task=state.task)

    await _launch_target_app(state)

    try:
        while _should_continue(state):
            if state.status == "paused":
                await asyncio.sleep(0.5)
                continue

            # ── 1. Capture ────────────────────────────────────────────────────
            # Two independent ADB round-trips — run them concurrently rather
            # than back-to-back, which halves the capture latency every round.
            raw_png, raw_xml = await asyncio.gather(
                state.device.screenshot(), state.device.pull_xml()
            )

            # ── 2. Parse elements ─────────────────────────────────────────────
            elements = parse_interactive_elements(raw_xml)
            state.elements = [e.to_dict() for e in elements]

            # current_screen_sig is the screen at the START of this round, i.e. the
            # RESULT of the PREVIOUS round's action (or the initial screen if this is
            # round 0). Pair it with state.last_screen_sig / state.last_elem_sig /
            # state.last_action_thought (all captured at the END of the previous
            # round) to record the transition that just became fully knowable.
            # Do NOT pair it with this round's not-yet-decided action.
            current_screen_sig = screen_signature(state.elements)
            if state.nav_graph is not None and state.last_screen_sig is not None:
                try:
                    await asyncio.to_thread(
                        state.nav_graph.record_transition,
                        app_name=state.app_name,
                        from_screen_sig=state.last_screen_sig,
                        element_sig=state.last_elem_sig or "unknown",
                        element_text=state.last_action_thought,
                        to_screen_sig=current_screen_sig,
                    )
                except Exception:
                    pass  # never let graph recording break the explore run

            # ── 3. Annotate screenshot ────────────────────────────────────────
            annotated_b64 = await asyncio.to_thread(annotate_screenshot, raw_png, elements)
            state.screenshot_b64 = annotated_b64
            state.raw_screenshot = raw_png

            # ── 4. Broadcast screenshot update to frontend ────────────────────
            await state.broadcast({
                "type": "screenshot_update",
                "screenshot": annotated_b64,
                "round": state.round_num,
                "element_count": len(elements),
            })

            # ── 5. Retrieve KB context ────────────────────────────────────────
            docs_context = await state.kb.retrieve_context(elements)

            # ── 6. LLM decision ───────────────────────────────────────────────
            prompt = build_explore_prompt(state, state.elements, docs_context)
            try:
                decision = await call_vision_llm(state.provider, annotated_b64, prompt, trace=trace)
            except Exception as e:
                state.errors.append(f"Round {state.round_num} LLM error: {sanitize_error(e)}")
                state.round_num += 1
                continue

            # attach provider for grid mode sub-calls
            decision["_provider"] = state.provider

            usage = decision.pop("_usage", {})
            state.llm_call_count += 1
            state.tokens_used += usage.get("total_tokens", 0)
            state.estimated_cost_usd += estimate_cost_usd(state.provider, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

            cfg = state.config
            if (cfg.max_tokens is not None and state.tokens_used >= cfg.max_tokens) or \
               (cfg.max_cost_usd is not None and state.estimated_cost_usd >= cfg.max_cost_usd) or \
               (cfg.max_llm_calls is not None and state.llm_call_count >= cfg.max_llm_calls):
                state.failure_reason = f"Usage limit reached (tokens={state.tokens_used}, cost=${state.estimated_cost_usd:.4f}, calls={state.llm_call_count})"
                break

            # ── 7. Broadcast action event ─────────────────────────────────────
            await state.broadcast({
                "type": "action_event",
                "round": state.round_num,
                "action": decision.get("action", ""),
                "element_id": decision.get("element_id"),
                "thought": decision.get("thought", ""),
                "observation": decision.get("observation", ""),
            })

            # ── 8. Check for finish ───────────────────────────────────────────
            if decision.get("action") == "finish":
                state.task_complete = True
                break

            # ── 9. Risk-gate then execute ──────────────────────────────────────
            if await gate_action(state, decision, state.elements):
                await execute_action(state.device, decision, state.elements, state.credentials, state=state)

            # ── 10. Wait for UI to settle ─────────────────────────────────────
            await state.device.wait_idle()

            # ── 11. Reflect — generate KB doc for novel elements ──────────────
            is_novel = decision.get("is_novel_element", True)
            elem_id = decision.get("element_id")
            elem_sig = _get_elem_sig(state.elements, elem_id)

            if is_novel and elem_id is not None and elem_sig not in state.explored_elements:
                after_png = await state.device.screenshot()
                doc = await run_reflector(state, raw_png, after_png, decision, state.elements)
                if doc:
                    await state.kb.upsert(doc)
                    state.explored_elements.add(elem_sig)
                    await state.broadcast({"type": "kb_update", "doc": {
                        "id": doc.id,
                        "documentation": doc.documentation,
                        "element_sig": doc.element_sig,
                        "app_name": doc.app_name,
                    }})

            # ── 12. Record history ────────────────────────────────────────────
            state.action_history.append({
                "round": state.round_num,
                "action": decision,
                "element_sig": elem_sig,
            })

            try:
                await append_event(state.session_id, state.round_num, decision, elem_sig)
                await update_session(state.session_id, **_session_fields(state, "running"))
            except Exception as e:
                logger.warning("persistence append_event/update_session failed: %s", e)

            # Stash this round's screen/action for pairing at the TOP of next round
            # (that's when the resulting screen becomes known and the transition
            # this action caused can actually be recorded).
            state.last_screen_sig = current_screen_sig
            state.last_elem_sig = elem_sig
            state.last_action_thought = decision.get("thought", "")[:100]

            state.round_num += 1

    except Exception as e:
        state.status = "error"
        state.errors.append(sanitize_error(e))
        await state.broadcast({"type": "error", "message": sanitize_error(e)})
        try:
            await update_session(state.session_id, **_session_fields(state, "error"))
        except Exception as e2:
            logger.warning("persistence update_session failed: %s", e2)
        end_session_trace(trace, status=state.status, task_complete=state.task_complete, round_num=state.round_num)
        return

    state.status = "done"
    end_session_trace(trace, status=state.status, task_complete=state.task_complete, round_num=state.round_num)
    try:
        await update_session(state.session_id, **_session_fields(state, "done"))
    except Exception as e2:
        logger.warning("persistence update_session failed: %s", e2)
    await state.broadcast({
        "type": "status_change",
        "status": "done",
        "task_complete": state.task_complete,
        "kb_count": state.kb.count(),
    })


async def run_deploy(state: AgentState) -> None:
    """Deploy phase: complete a user-specified task using the KB."""
    state.status = "running"
    await state.broadcast({"type": "status_change", "status": "running", "mode": "deploy"})
    try:
        await create_session(
            state.session_id, state.app_name, state.task, state.mode, state.provider,
            state.config.reasoning_mode, state.device.serial if state.device else None,
        )
    except Exception as e:
        logger.warning("persistence create_session failed: %s", e)
    if await _reject_if_app_not_allowed(state):
        return
    from ..observability.langfuse_client import start_session_trace, end_session_trace
    trace = start_session_trace(state.session_id, mode="deploy", app_name=state.app_name, task=state.task)

    await _launch_target_app(state)

    # ── Planner: decompose task into sub-steps ────────────────────────────────
    state.sub_steps = await run_planner(state)
    await state.broadcast({"type": "plan_ready", "steps": state.sub_steps})

    try:
        while _should_continue(state):
            if state.status == "paused":
                await asyncio.sleep(0.5)
                continue

            # ── 1–3: Same as explore ──────────────────────────────────────────
            # Concurrent capture — see the matching comment in run_explore.
            raw_png, raw_xml = await asyncio.gather(
                state.device.screenshot(), state.device.pull_xml()
            )
            elements = parse_interactive_elements(raw_xml)
            state.elements = [e.to_dict() for e in elements]

            # current_screen_sig is the screen at the START of this round, i.e. the
            # RESULT of the PREVIOUS round's action (or the initial screen if this is
            # round 0). Pair it with state.last_screen_sig / state.last_elem_sig /
            # state.last_action_thought (all captured at the END of the previous
            # round) to record the transition that just became fully knowable.
            # Do NOT pair it with this round's not-yet-decided action.
            current_screen_sig = screen_signature(state.elements)
            if state.nav_graph is not None and state.last_screen_sig is not None:
                try:
                    await asyncio.to_thread(
                        state.nav_graph.record_transition,
                        app_name=state.app_name,
                        from_screen_sig=state.last_screen_sig,
                        element_sig=state.last_elem_sig or "unknown",
                        element_text=state.last_action_thought,
                        to_screen_sig=current_screen_sig,
                    )
                except Exception:
                    pass  # never let graph recording break the deploy run

            known_transitions = []
            if state.nav_graph is not None:
                try:
                    known_transitions = await asyncio.to_thread(
                        state.nav_graph.get_outgoing_transitions, state.app_name, current_screen_sig
                    )
                except Exception:
                    pass

            annotated_b64 = await asyncio.to_thread(annotate_screenshot, raw_png, elements)
            state.screenshot_b64 = annotated_b64

            await state.broadcast({
                "type": "screenshot_update",
                "screenshot": annotated_b64,
                "round": state.round_num,
                "element_count": len(elements),
            })

            docs_context = await state.kb.retrieve_context(elements)

            # ── 4. Deploy LLM decision ────────────────────────────────────────
            prompt = build_deploy_prompt(state, state.elements, docs_context, known_transitions)
            try:
                if state.config.reasoning_mode == "fast":
                    decision = await call_text_llm(state.provider, prompt, trace=trace)
                    if decision.get("action") == "escalate":
                        state.escalation_count += 1
                        decision = await call_vision_llm(state.provider, annotated_b64, prompt, trace=trace)
                else:
                    decision = await call_vision_llm(state.provider, annotated_b64, prompt, trace=trace)
            except Exception as e:
                state.errors.append(f"Round {state.round_num} LLM error: {sanitize_error(e)}")
                state.round_num += 1
                continue

            decision["_provider"] = state.provider

            usage = decision.pop("_usage", {})
            state.llm_call_count += 1
            state.tokens_used += usage.get("total_tokens", 0)
            state.estimated_cost_usd += estimate_cost_usd(state.provider, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

            cfg = state.config
            if (cfg.max_tokens is not None and state.tokens_used >= cfg.max_tokens) or \
               (cfg.max_cost_usd is not None and state.estimated_cost_usd >= cfg.max_cost_usd) or \
               (cfg.max_llm_calls is not None and state.llm_call_count >= cfg.max_llm_calls):
                state.failure_reason = f"Usage limit reached (tokens={state.tokens_used}, cost=${state.estimated_cost_usd:.4f}, calls={state.llm_call_count})"
                break

            await state.broadcast({
                "type": "action_event",
                "round": state.round_num,
                "action": decision.get("action", ""),
                "element_id": decision.get("element_id"),
                "thought": decision.get("thought", ""),
                "observation": decision.get("observation", ""),
            })

            # ── 5. Check for finish or impossible ────────────────────────────
            if decision.get("action") == "finish":
                state.task_complete = True
                break

            thought = decision.get("thought", "").lower()
            if any(w in thought for w in ["cannot", "impossible", "not possible", "unable"]):
                state.failure_reason = decision.get("thought", "Task marked impossible by agent")

            # ── 6–7: Risk-gate, execute, wait ─────────────────────────────────
            if await gate_action(state, decision, state.elements):
                await execute_action(state.device, decision, state.elements, state.credentials, state=state)
            await state.device.wait_idle()

            # ── 8. Advance sub-step index if current step likely complete ─────
            state.current_step_idx = min(
                state.current_step_idx + _should_advance(decision),
                len(state.sub_steps) - 1,
            )

            # ── 9. Progress check every 5 rounds ─────────────────────────────
            if state.round_num > 0 and state.round_num % 5 == 0:
                progress_png = await state.device.screenshot()
                progress_b64 = base64.b64encode(progress_png).decode()
                try:
                    prog = await call_vision_llm(
                        state.provider,
                        progress_b64,
                        build_progress_prompt(state.task),
                        trace=trace,
                    )
                    prog_usage = prog.pop("_usage", {})
                    state.llm_call_count += 1
                    state.tokens_used += prog_usage.get("total_tokens", 0)
                    state.estimated_cost_usd += estimate_cost_usd(state.provider, prog_usage.get("prompt_tokens", 0), prog_usage.get("completion_tokens", 0))
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
                        break
                except Exception:
                    pass

            elem_sig = _get_elem_sig(state.elements, decision.get("element_id"))
            state.action_history.append({
                "round": state.round_num,
                "action": decision,
                "element_sig": elem_sig,
            })
            state.last_screen_sig = current_screen_sig
            state.last_elem_sig = elem_sig
            state.last_action_thought = decision.get("thought", "")[:100]

            try:
                await append_event(state.session_id, state.round_num, decision, elem_sig)
                await update_session(state.session_id, **_session_fields(state, "running"))
            except Exception as e:
                logger.warning("persistence append_event/update_session failed: %s", e)
            state.round_num += 1

    except Exception as e:
        state.status = "error"
        state.errors.append(sanitize_error(e))
        await state.broadcast({"type": "error", "message": sanitize_error(e)})
        try:
            await update_session(state.session_id, **_session_fields(state, "error"))
        except Exception as e2:
            logger.warning("persistence update_session failed: %s", e2)
        end_session_trace(trace, status=state.status, task_complete=state.task_complete, round_num=state.round_num)
        return

    state.status = "done"
    end_session_trace(trace, status=state.status, task_complete=state.task_complete, round_num=state.round_num)
    try:
        await update_session(state.session_id, **_session_fields(state, "done"))
    except Exception as e2:
        logger.warning("persistence update_session failed: %s", e2)
    await state.broadcast({
        "type": "status_change",
        "status": "done",
        "task_complete": state.task_complete,
        "failure_reason": state.failure_reason,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_fields(state: AgentState, status: str) -> dict:
    return {
        "status": status,
        "round_num": state.round_num,
        "task_complete": state.task_complete,
        "failure_reason": state.failure_reason,
        "tokens_used": state.tokens_used,
        "estimated_cost_usd": state.estimated_cost_usd,
        "llm_call_count": state.llm_call_count,
        "escalation_count": state.escalation_count,
    }


def _get_elem_sig(elements: list[dict], elem_id: int | None) -> str:
    if elem_id is None:
        return "unknown"
    for e in elements:
        if e["id"] == elem_id:
            return f"{e.get('resource_id','')}::{e.get('class_name','')}"
    return "unknown"


def _should_advance(decision: dict) -> int:
    """Heuristic: advance sub-step index when agent taps a submit/confirm/search button."""
    text = (decision.get("thought", "") + decision.get("observation", "")).lower()
    advance_signals = ["submitted", "searching", "navigated", "opened", "clicked submit",
                       "applied", "saved", "completed", "confirmed"]
    return 1 if any(s in text for s in advance_signals) else 0
