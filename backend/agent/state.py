from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Literal, Optional


@dataclass
class RunConfig:
    app_name: str
    task: str
    mode: Literal["explore", "deploy"]
    reasoning_mode: Literal["reasoning", "fast"] = "reasoning"
    provider: str = "gemini"
    max_rounds: int = 20
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_llm_calls: Optional[int] = None


@dataclass
class AgentState:
    session_id: str
    config: RunConfig

    # device + KB — injected by API layer
    device: Any = None
    kb: Any = None
    credentials: Any = None          # security.CredentialManager — resolves type_secret values
    app_card: Optional[str] = None    # static per-app guidance from app_cards.AppCardProvider, or None
    nav_graph: Any = None             # graph.neo4j_client.NavigationGraph — records screen transitions during Explore
    ws_broadcast: Optional[Callable[..., Coroutine]] = None
    # observability.langfuse_client trace/span for this session, or None if
    # Langfuse is disabled/unconfigured. Stored on state (rather than
    # threaded through every function signature) so any code holding a
    # state reference — planner, reflector, executor's grid fallback — can
    # log its LLM calls without a separate trace parameter everywhere.
    trace: Any = None

    # per-round (overwritten each round)
    round_num: int = 0
    screenshot_b64: str = ""          # current annotated screenshot
    raw_screenshot: bytes = b""       # raw bytes before annotation
    elements: list[dict] = field(default_factory=list)
    last_screen_sig: Optional[str] = None      # screen signature from the previous round (before that round's action)
    last_elem_sig: Optional[str] = None        # element signature of the action taken in the previous round
    last_action_thought: str = ""              # decision["thought"] (truncated) from the previous round's action

    # multi-agent
    sub_steps: list[str] = field(default_factory=list)
    current_step_idx: int = 0

    # running history
    action_history: list[dict] = field(default_factory=list)
    explored_elements: set[str] = field(default_factory=set)

    # control flow
    status: Literal["idle", "running", "paused", "done", "error"] = "idle"
    # Set by DELETE /agent/{id} or a WebSocket "stop" message. Checked by both
    # agent loops via loop._should_continue(). This is deliberately separate
    # from `status`: the loops themselves set status="done" when they finish
    # normally, so overloading it as the stop signal would be ambiguous.
    stop_requested: bool = False
    task_complete: bool = False
    failure_reason: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    # Set when a medium/high-risk action needs a human decision before it
    # can execute; cleared back to None once resolve_confirmation() consumes
    # it. Holds the full decision dict so /confirm's response and any audit
    # log can show exactly what was approved or rejected.
    pending_confirmation: Optional[dict] = None
    confirmation_result: Optional[bool] = None
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0
    escalation_count: int = 0

    # convenience accessors
    @property
    def app_name(self) -> str:
        return self.config.app_name

    @property
    def task(self) -> str:
        return self.config.task

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def max_rounds(self) -> int:
        return self.config.max_rounds

    async def broadcast(self, event: dict) -> None:
        if self.ws_broadcast:
            await self.ws_broadcast(self.session_id, event)
