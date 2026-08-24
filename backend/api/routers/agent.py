import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ..schemas import (
    ExploreRequest, DeployRequest, ChatRequest, ChatResponse,
    DecideRequest, DecideResponse, InterpretRequest, InterpretResponse,
    FanoutDeployRequest, FanoutDeployResponse, FanoutSessionResult,
    SessionResponse, AgentStatusResponse, SessionHistoryEvent,
    ConfirmActionRequest,
)
from ..ws.manager import ws_manager
from ...agent.loop import run_explore, run_deploy
from ...agent.state import AgentState, RunConfig
from ...llm import call_text_llm
from ...llm.pricing import estimate_cost_usd
from ...llm.prompts import build_chat_intent_prompt, build_deploy_prompt
from ...knowledge_base.store import KnowledgeBase
from ...app_cards.loader import AppCardProvider
from ...security.credentials import CredentialManager
from ...graph.neo4j_client import NavigationGraph
from ...persistence import get_session, get_session_events, list_sessions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

_INTENT_FAIL_DETAIL = (
    "Could not understand the task — try rephrasing with a clearer app name and goal."
)

# active sessions: session_id -> AgentState
_sessions: dict[str, AgentState] = {}

# loaded once at import time — secrets.yaml is read from disk, never re-parsed per request
_credentials = CredentialManager()

# loaded once at import time — app_cards.json + markdown files are read from disk, never re-parsed
_app_cards = AppCardProvider()

# constructed once at import time — the neo4j driver itself is lazy (no connection
# attempt happens until first record_transition/find_path call), so this never
# blocks startup even if Neo4j is down
_nav_graph = NavigationGraph()


async def _run_and_release(coro, registry, serial, session_id=None):
    """Run an agent coroutine, then always give the device back and drop the
    in-memory session.

    Both cleanups have to happen on the failure path too. Without the
    release, one crashed run permanently burns a device slot; without the
    _sessions.pop, every run leaks an AgentState (which holds a full
    screenshot) for the lifetime of the process. Dropping the session here is
    safe because get_status() falls back to the persisted row in SQLite,
    which the loops write before returning.
    """
    try:
        await coro
    except Exception as e:
        logger.warning("agent run for session %s failed: %s", session_id, e)
    finally:
        registry.release(serial)
        if session_id is not None:
            _sessions.pop(session_id, None)


def _build_deploy_coro(state: AgentState, engine: str):
    """Pick the deploy implementation. The DeployWorkflow import is kept lazy
    so the optional llama-index-workflows dependency is only required when
    the workflow engine is actually selected."""
    if engine == "workflow":
        from ...agent.deploy_workflow import DeployWorkflow
        return DeployWorkflow(timeout=300).run(state=state)
    return run_deploy(state)


def _start_agent_task(coro_factory, registry, serial, session_id, name):
    """Build the agent coroutine and schedule it, releasing the device if
    construction itself fails.

    The device is already acquired by the time we get here, so anything that
    raises while building the coroutine (e.g. the optional DeployWorkflow
    import) would otherwise leave that device marked busy forever.
    """
    try:
        coro = coro_factory()
    except Exception:
        registry.release(serial)
        _sessions.pop(session_id, None)
        raise
    asyncio.create_task(_run_and_release(coro, registry, serial, session_id), name=name)


def _make_state(
    session_id: str, config: RunConfig, request: Request, device_serial: Optional[str] = None
) -> AgentState:
    registry = request.app.state.devices
    acquired = registry.acquire(device_serial)
    if acquired is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No idle Android device available"
                if device_serial is None
                else f"Device '{device_serial}' not found or busy"
            ),
        )
    serial, device = acquired

    try:
        kb = KnowledgeBase(app_name=config.app_name)
        app_card = _app_cards.get(config.app_name)
        state = AgentState(
            session_id=session_id,
            config=config,
            device=device,
            kb=kb,
            credentials=_credentials,
            app_card=app_card,
            nav_graph=_nav_graph,
            ws_broadcast=ws_manager.broadcast,
        )
    except Exception:
        # KnowledgeBase() hard-requires a live ChromaDB connection (unlike
        # Neo4j/Langfuse, which fail soft) — if it's down, this raises before
        # a state ever exists to release the device. Found live: a ChromaDB
        # outage left a device permanently marked busy until the server was
        # restarted, since nothing downstream of this point ever ran to
        # release it.
        registry.release(serial)
        raise
    return state


@router.post("/explore", response_model=SessionResponse)
async def start_explore(body: ExploreRequest, request: Request):
    session_id = str(uuid.uuid4())
    config = RunConfig(
        app_name=body.app_name,
        task="explore",
        mode="explore",
        provider=body.provider,
        max_rounds=body.max_rounds,
        max_tokens=body.max_tokens,
        max_cost_usd=body.max_cost_usd,
        max_llm_calls=body.max_llm_calls,
    )
    state = _make_state(session_id, config, request, body.device_serial)
    _sessions[session_id] = state
    _start_agent_task(
        lambda: run_explore(state),
        request.app.state.devices,
        state.device.serial,
        session_id,
        name=f"explore-{session_id}",
    )
    return SessionResponse(session_id=session_id, message="Exploration started")


@router.post("/deploy", response_model=SessionResponse)
async def start_deploy(body: DeployRequest, request: Request):
    session_id = str(uuid.uuid4())
    config = RunConfig(
        app_name=body.app_name,
        task=body.task,
        mode="deploy",
        provider=body.provider,
        reasoning_mode=body.reasoning_mode,
        max_rounds=body.max_rounds,
        max_tokens=body.max_tokens,
        max_cost_usd=body.max_cost_usd,
        max_llm_calls=body.max_llm_calls,
    )
    state = _make_state(session_id, config, request, body.device_serial)
    _sessions[session_id] = state
    _start_agent_task(
        lambda: _build_deploy_coro(state, body.engine),
        request.app.state.devices,
        state.device.serial,
        session_id,
        name=f"deploy-{session_id}",
    )
    return SessionResponse(session_id=session_id, message="Deployment started")


@router.post("/chat", response_model=ChatResponse)
async def start_chat(body: ChatRequest, request: Request):
    try:
        result = await call_text_llm(body.provider, build_chat_intent_prompt(body.message))
    except Exception:
        raise HTTPException(status_code=422, detail=_INTENT_FAIL_DETAIL)
    app_name = result.get("app_name")
    task = result.get("task")
    invalid = (
        not isinstance(app_name, str) or not app_name.strip()
        or not isinstance(task, str) or not task.strip()
    )
    if invalid:
        raise HTTPException(status_code=422, detail=_INTENT_FAIL_DETAIL)

    session_id = str(uuid.uuid4())
    config = RunConfig(
        app_name=app_name.strip(),
        task=task.strip(),
        mode="deploy",
        provider=body.provider,
        reasoning_mode="fast",
    )
    state = _make_state(session_id, config, request, body.device_serial)
    _sessions[session_id] = state
    _start_agent_task(
        lambda: run_deploy(state),
        request.app.state.devices,
        state.device.serial,
        session_id,
        name=f"deploy-{session_id}",
    )
    return ChatResponse(
        session_id=session_id,
        app_name=app_name.strip(),
        task=task.strip(),
        message="Deployment started",
    )


@router.post("/interpret", response_model=InterpretResponse)
async def interpret_message(body: InterpretRequest):
    """Turn free text into {app_name, task} without starting anything.

    /agent/chat does the same inference but then acquires a device and kicks
    off an ADB Deploy run, so it 503s on a cloud backend that has no phone
    attached. Callers that drive themselves (the Android app, via
    OnDeviceAgentLoop) need the inference alone.

    Must stay registered above /{session_id}.
    """
    try:
        result = await call_text_llm(body.provider, build_chat_intent_prompt(body.message))
    except Exception as e:
        logger.warning("interpret: LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail="LLM call failed")

    app_name = result.get("app_name")
    task = result.get("task")
    if (
        not isinstance(app_name, str) or not app_name.strip()
        or not isinstance(task, str) or not task.strip()
    ):
        raise HTTPException(status_code=422, detail=_INTENT_FAIL_DETAIL)

    return InterpretResponse(app_name=app_name.strip(), task=task.strip())


@router.post("/decide", response_model=DecideResponse)
async def decide_next_action(body: DecideRequest):
    """Return the next action for a caller that drives the device itself.

    Stateless by design: no device is acquired, no session is stored, nothing
    is broadcast. The ADB pipeline (run_explore/run_deploy) requires the
    backend to reach the phone directly, which is impossible once the backend
    lives in the cloud — so here the phone owns the loop (read screen ->
    ask -> act -> repeat) and the backend only does the reasoning.

    Deliberately reuses build_deploy_prompt and call_text_llm unchanged, so
    on-device decisions come from the same prompt and the same provider
    layer as ADB-driven ones and the two paths can't quietly diverge.

    Must stay registered above /{session_id} or that wildcard swallows it.
    """
    # A device- and KB-less state is enough: build_deploy_prompt only reads
    # task/app_name/round_num/max_rounds/sub_steps/current_step_idx/
    # action_history/app_card/credentials.
    config = RunConfig(
        app_name=body.app_name,
        task=body.task,
        mode="deploy",
        provider=body.provider,
        reasoning_mode="fast",
        max_rounds=body.max_rounds,
    )
    state = AgentState(
        session_id="on-device", config=config, app_card=_app_cards.get(body.app_name)
    )
    state.round_num = body.round_num
    state.action_history = [{"round": h.round, "action": h.action} for h in body.history]

    elements = [e.model_dump() for e in body.elements]
    # docs_context is empty: the KB is populated by Explore runs, which need
    # ADB and so don't exist on this path yet. The prompt already handles an
    # empty KB ("reason from screenshot directly").
    prompt = build_deploy_prompt(state, elements, docs_context="")

    try:
        decision = await call_text_llm(body.provider, prompt)
    except Exception as e:
        logger.warning("decide: LLM call failed: %s", e)
        raise HTTPException(status_code=502, detail="LLM call failed")

    action = decision.get("action")
    if not isinstance(action, str) or not action.strip():
        raise HTTPException(status_code=502, detail="LLM returned no action")

    usage = decision.get("_usage", {}) or {}
    return DecideResponse(
        action=action.strip(),
        element_id=decision.get("element_id"),
        text_input=decision.get("text_input"),
        direction=decision.get("direction"),
        thought=decision.get("thought", "") or "",
        observation=decision.get("observation", "") or "",
        tokens_used=usage.get("total_tokens", 0),
        estimated_cost_usd=estimate_cost_usd(
            body.provider,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        ),
    )


@router.post("/deploy/fanout", response_model=FanoutDeployResponse)
async def start_deploy_fanout(body: FanoutDeployRequest, request: Request):
    registry = request.app.state.devices
    results: list[FanoutSessionResult] = []

    for serial in body.device_serials:
        acquired = registry.acquire(serial)
        if acquired is None:
            results.append(FanoutSessionResult(
                device_serial=serial,
                session_id=None,
                started=False,
                detail="not found or busy",
            ))
            continue

        acquired_serial, device = acquired
        session_id = str(uuid.uuid4())
        config = RunConfig(
            app_name=body.app_name,
            task=body.task,
            mode="deploy",
            provider=body.provider,
            reasoning_mode=body.reasoning_mode,
            max_rounds=body.max_rounds,
            max_tokens=body.max_tokens,
            max_cost_usd=body.max_cost_usd,
            max_llm_calls=body.max_llm_calls,
        )
        kb = KnowledgeBase(app_name=config.app_name)
        app_card = _app_cards.get(config.app_name)
        state = AgentState(
            session_id=session_id,
            config=config,
            device=device,
            kb=kb,
            credentials=_credentials,
            app_card=app_card,
            nav_graph=_nav_graph,
            ws_broadcast=ws_manager.broadcast,
        )
        _sessions[session_id] = state
        try:
            _start_agent_task(
                lambda s=state: _build_deploy_coro(s, body.engine),
                registry,
                acquired_serial,
                session_id,
                name=f"deploy-fanout-{session_id}",
            )
        except Exception as e:
            # One device failing to start must not abort the whole fan-out —
            # _start_agent_task has already released this device.
            logger.warning("fanout start failed for %s: %s", serial, e)
            results.append(FanoutSessionResult(
                device_serial=serial,
                session_id=None,
                started=False,
                detail=f"failed to start: {e}",
            ))
            continue
        results.append(FanoutSessionResult(
            device_serial=serial,
            session_id=session_id,
            started=True,
        ))

    return FanoutDeployResponse(results=results)


@router.get("/sessions")
async def list_sessions_endpoint(limit: int = 50, offset: int = 0):
    return await list_sessions(limit=limit, offset=offset)


@router.get("/{session_id}", response_model=AgentStatusResponse)
async def get_status(session_id: str):
    state = _sessions.get(session_id)
    if state is None:
        db_row = await get_session(session_id)
        if db_row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return AgentStatusResponse(
            session_id=db_row["session_id"],
            status=db_row["status"],
            round_num=db_row["round_num"],
            task_complete=bool(db_row["task_complete"]),
            failure_reason=db_row["failure_reason"],
            errors=[],
            tokens_used=db_row["tokens_used"],
            estimated_cost_usd=db_row["estimated_cost_usd"],
            llm_call_count=db_row["llm_call_count"],
            escalation_count=db_row["escalation_count"],
            pending_confirmation=None,
        )
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


@router.get("/{session_id}/history", response_model=list[SessionHistoryEvent])
async def get_session_history(session_id: str):
    events = await get_session_events(session_id)
    return [
        SessionHistoryEvent(
            round_num=e["round_num"],
            action=json.loads(e["action_json"]),
            element_sig=e["element_sig"],
            created_at=e["created_at"],
        )
        for e in events
    ]


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


@router.delete("/{session_id}", response_model=AgentStatusResponse)
async def stop_agent(session_id: str):
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # stop_requested is what actually breaks the agent loop; status is set too
    # so a status poll between now and the loop noticing reads as stopped.
    state.stop_requested = True
    state.status = "done"
    state.task_complete = False
    return AgentStatusResponse(
        session_id=session_id,
        status="done",
        round_num=state.round_num,
        task_complete=False,
        # Report real usage — these previously defaulted to 0, so stopping a
        # session that had spent thousands of tokens reported it as free.
        failure_reason=state.failure_reason,
        errors=state.errors[-5:],
        tokens_used=state.tokens_used,
        estimated_cost_usd=state.estimated_cost_usd,
        llm_call_count=state.llm_call_count,
        escalation_count=state.escalation_count,
    )
