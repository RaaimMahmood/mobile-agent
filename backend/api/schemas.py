from typing import Literal, Optional
from pydantic import BaseModel


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceStatus(BaseModel):
    connected: bool
    serial: Optional[str] = None
    resolution: Optional[list[int]] = None  # [width, height]
    message: str = ""


class ScreenshotResponse(BaseModel):
    screenshot_b64: str


class TapRequest(BaseModel):
    x: int
    y: int


class KeyEventRequest(BaseModel):
    code: int


class OkResponse(BaseModel):
    ok: bool
    message: str = ""


class PairDeviceRequest(BaseModel):
    ip: str
    port: int
    code: str


class ConnectDeviceRequest(BaseModel):
    ip: str
    port: int


# ── Agent ─────────────────────────────────────────────────────────────────────

class ExploreRequest(BaseModel):
    app_name: str
    max_rounds: int = 20
    device_serial: Optional[str] = None
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_llm_calls: Optional[int] = None


class DeployRequest(BaseModel):
    task: str
    app_name: str
    max_rounds: int = 30
    device_serial: Optional[str] = None
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"
    reasoning_mode: Literal["reasoning", "fast"] = "fast"
    engine: Literal["loop", "workflow"] = "loop"
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_llm_calls: Optional[int] = None


class FanoutDeployRequest(BaseModel):
    task: str
    app_name: str
    device_serials: list[str]
    max_rounds: int = 30
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"
    reasoning_mode: Literal["reasoning", "fast"] = "fast"
    engine: Literal["loop", "workflow"] = "loop"
    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_llm_calls: Optional[int] = None


class FanoutSessionResult(BaseModel):
    device_serial: str
    session_id: Optional[str] = None
    started: bool
    detail: Optional[str] = None


class FanoutDeployResponse(BaseModel):
    results: list[FanoutSessionResult]


class ChatRequest(BaseModel):
    message: str
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"
    device_serial: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    app_name: str
    task: str
    message: str


# ── On-device agent loop (mobile app drives itself; backend only reasons) ──────
#
# The ADB-based Explore/Deploy pipeline needs the backend to physically reach
# the phone, which a cloud deployment cannot do. These schemas back
# POST /agent/decide, where the phone reads its own screen (Android
# AccessibilityService), asks the backend what to do next, and executes the
# action itself. The backend stays stateless — no device, no session, no ADB.

class InterpretRequest(BaseModel):
    message: str
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"


class InterpretResponse(BaseModel):
    app_name: str
    task: str


class DecideElement(BaseModel):
    """One interactive element, mirroring perception/xml_parser.py's shape so
    the existing prompt builders consume it unchanged."""
    id: int
    class_name: str = ""
    text: str = ""
    content_desc: str = ""
    resource_id: str = ""


class DecideHistoryEntry(BaseModel):
    round: int
    action: dict


class DecideRequest(BaseModel):
    task: str
    app_name: str
    elements: list[DecideElement] = []
    round_num: int = 0
    max_rounds: int = 20
    history: list[DecideHistoryEntry] = []
    provider: Literal["gemini", "openai", "anthropic", "ollama", "cerebras", "glm"] = "gemini"


class DecideResponse(BaseModel):
    action: str
    element_id: Optional[int] = None
    text_input: Optional[str] = None
    direction: Optional[str] = None
    thought: str = ""
    observation: str = ""
    # Returned so the caller can enforce its own budget — the phone owns the
    # loop here, so it also owns the stopping decision.
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0


class SessionResponse(BaseModel):
    session_id: str
    message: str = ""


class AgentStatusResponse(BaseModel):
    session_id: str
    status: str
    round_num: int
    task_complete: bool
    failure_reason: Optional[str] = None
    errors: list[str] = []
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0
    llm_call_count: int = 0
    escalation_count: int = 0
    pending_confirmation: Optional[dict] = None


class ConfirmActionRequest(BaseModel):
    approve: bool


class SessionHistoryEvent(BaseModel):
    round_num: int
    action: dict
    element_sig: Optional[str] = None
    created_at: str


# ── Knowledge Base ────────────────────────────────────────────────────────────

class KBDocOut(BaseModel):
    id: str
    app_name: str
    element_sig: str
    class_name: str
    resource_id: str
    content_desc: str
    text: str
    documentation: str
    observed_result: str
    last_explored_at: str


class KBListResponse(BaseModel):
    app_name: str
    count: int
    docs: list[KBDocOut]


class KBDeleteResponse(BaseModel):
    deleted: int


# ── Demonstrations ────────────────────────────────────────────────────────────

class StartRecordingRequest(BaseModel):
    task_description: str
    device_serial: Optional[str] = None


class RecordStepRequest(BaseModel):
    recording_id: str
    action_type: str
    params: dict = {}
    description: Optional[str] = None


class StopRecordingRequest(BaseModel):
    recording_id: str


class ReplayRequest(BaseModel):
    device_serial: Optional[str] = None
    on_drift: Literal["stop", "skip"] = "stop"


class StartRecordingResponse(BaseModel):
    recording_id: str


class RecordStepResponse(BaseModel):
    ok: bool
    steps_recorded: int


class StopRecordingResponse(BaseModel):
    ok: bool
    path: str


class DemoListItem(BaseModel):
    recording_id: str
    task_description: str
    step_count: int
    created_at: str


class DeleteDemoResponse(BaseModel):
    ok: bool
