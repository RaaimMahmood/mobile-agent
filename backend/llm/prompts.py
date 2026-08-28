from typing import TYPE_CHECKING

from ..security.redact import sanitize_screen_text

if TYPE_CHECKING:
    from ..agent.state import AgentState
    from ..perception.xml_parser import InteractiveElement


# ── Schema descriptions ────────────────────────────────────────────────────────

_EXPLORE_SCHEMA = """{
  "thought":        "Your reasoning about what to explore next",
  "observation":    "What you see on screen right now",
  "action":         "tap | text | swipe | long_press | grid | type_secret | finish",
  "element_id":     <integer ID or null>,
  "text_input":     "<string to type or null>",
  "direction":      "up | down | left | right | null",
  "grid_cell":      "<cell label e.g. E5 or null>",
  "secret_id":      "<name from AVAILABLE SECRETS, only when action is type_secret, else null>",
  "is_novel_element": <true if this element has not been explored before, false otherwise>
}"""

_DEPLOY_SCHEMA = """{
  "thought":     "Your reasoning about advancing the task",
  "observation": "Current screen state relevant to the task",
  "action":      "tap | text | swipe | long_press | grid | type_secret | back | escalate | finish",
  "element_id":  <integer ID or null>,
  "text_input":  "<string to type or null>",
  "direction":   "up | down | left | right | null",
  "grid_cell":   "<cell label e.g. E5 or null>",
  "secret_id":   "<name from AVAILABLE SECRETS, only when action is type_secret, else null>"
}"""


def _elements_txt(elements: list) -> str:
    # text/content_desc come straight off the real screen — a malicious or
    # compromised app can put arbitrary text there aimed at this prompt, not
    # the user. resource_id is developer-set (Android build metadata), not
    # attacker-reachable at runtime, so it's left unfiltered.
    return "\n".join(
        f"[{e['id']}] {e['class_name']} | text='{sanitize_screen_text(e['text'])}' "
        f"| desc='{sanitize_screen_text(e['content_desc'])}' | res='{e['resource_id']}'"
        for e in elements
    ) or "(no interactive elements detected)"


def _history_txt(history: list, max_entries: int = 5) -> str:
    if not history:
        return "(first round)"
    shown = history[-max_entries:]
    return "\n".join(
        f"Round {h['round']}: {h['action']['action'].upper()} "
        f"element {h['action'].get('element_id', '–')} "
        f"— {h['action'].get('thought', '')[:80]}"
        for h in shown
    )


# ── Explore prompt ─────────────────────────────────────────────────────────────

EXPLORE_SYSTEM = """You are an Android UI exploration agent. Your goal is to systematically \
interact with every reachable UI element in the app and build a knowledge base of their behaviors.

Rules:
- Prefer elements you have NOT explored yet (set is_novel_element: true for those)
- When you tap something and a new sub-screen opens, explore THAT screen before going back
- Swipe to reveal hidden content when all visible elements have been explored
- Set is_novel_element: false for elements you have already reflected on
- Use action "finish" ONLY when you believe all reachable screens and elements are documented
- NEVER type a password, PIN, or other credential yourself using the "text" action — you must \
not guess or fabricate one. If the field needs a known credential, use "type_secret" with the \
exact secret_id from AVAILABLE SECRETS instead. If no matching secret_id exists, leave the field \
alone and use "finish" or explore elsewhere.
- Respond ONLY with valid JSON — no prose, no explanation outside the JSON"""


def _secrets_txt(state: "AgentState") -> str:
    creds = getattr(state, "credentials", None)
    keys = creds.keys() if creds is not None else []
    return ", ".join(keys) if keys else "(none configured)"


def _app_card_txt(state: "AgentState") -> str:
    card = getattr(state, "app_card", None)
    return card if card else "(no guide available for this app — explore to learn it)"


def _known_transitions_txt(known_transitions: list[dict]) -> str:
    if not known_transitions:
        return "(no known transitions from this screen yet)"
    return "\n".join(
        f"- tapping element matching '{t.get('element_sig')}' previously led to: {t.get('element_text')}"
        for t in known_transitions
    )


def build_explore_prompt(state: "AgentState", elements: list, docs_context: str) -> str:
    return (
        f"{EXPLORE_SYSTEM}\n\n"
        f"App: {state.app_name} | Round: {state.round_num} / {state.max_rounds}\n\n"
        f"APP GUIDE:\n{_app_card_txt(state)}\n\n"
        f"INTERACTIVE ELEMENTS (numbered orange boxes on screenshot):\n"
        f"{_elements_txt(elements)}\n\n"
        f"AVAILABLE SECRETS (reference by name only with type_secret — you cannot see their values):\n"
        f"{_secrets_txt(state)}\n\n"
        # docs_context is retrieved from the persistent KB — text an earlier
        # session's LLM call wrote after looking at a (possibly different
        # app's) screenshot. Same untrusted-content path as live screen text.
        f"EXISTING KB DOCS FOR VISIBLE ELEMENTS:\n"
        f"{sanitize_screen_text(docs_context) or '(none yet)'}\n\n"
        f"LAST 5 ACTIONS:\n"
        f"{_history_txt(state.action_history)}\n\n"
        f"Respond ONLY with valid JSON matching this schema:\n{_EXPLORE_SCHEMA}"
    )


# ── Reflect prompt ─────────────────────────────────────────────────────────────

def build_reflect_prompt(app_name: str, elem: dict, action_type: str) -> str:
    return (
        f"App: {app_name}\n"
        f"Element: [{elem['id']}] {elem['class_name']} "
        f"| text='{elem['text']}' | desc='{elem['content_desc']}' | res='{elem['resource_id']}'\n"
        f"Action taken: {action_type}\n\n"
        f"The BEFORE screenshot (first image) and AFTER screenshot (second image) are attached.\n"
        f"Describe what CHANGED between them to generate accurate, factual documentation.\n\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{"documentation": "one or two sentences on what this element does and when to use it", '
        f'"observed_result": "what visibly changed on screen, or \\"no visible change\\""}}'
    )


# ── Chat intent prompt ─────────────────────────────────────────────────────────

def build_chat_intent_prompt(message: str) -> str:
    return (
        f"You turn a free-text user message into a structured mobile automation task.\n\n"
        f"Message: {message}\n\n"
        f"Return exactly two fields:\n"
        f"- app_name: a short lowercase identifier for the Android app the task refers to "
        f"(e.g. 'youtube', 'gmail', 'linkedin'). Infer it from context if not explicit.\n"
        f"- task: a clear, complete restatement of what the user wants done, suitable to hand "
        f"directly to a task-automation agent.\n\n"
        f'Respond ONLY with valid JSON matching this schema: {{"app_name": "...", "task": "..."}}'
    )


# ── Planner prompt ─────────────────────────────────────────────────────────────

def build_planner_prompt(task: str, app_name: str) -> str:
    return (
        f"You are a mobile task planner. Break down this task into 3–8 ordered sub-steps.\n\n"
        f"App: {app_name}\n"
        f"Task: {task}\n\n"
        f"Each sub-step should be a single, concrete UI interaction goal.\n"
        f"Examples: 'Tap the search button', 'Type \"SDE Bangalore\" in the search field', "
        f"'Tap the Search/Submit button'\n\n"
        f'Respond ONLY with valid JSON: {{"steps": ["step 1", "step 2", ...]}}'
    )


# ── Deploy prompt ─────────────────────────────────────────────────────────────

DEPLOY_SYSTEM = """You are an Android task automation agent. Complete the given task \
by interacting with the app's UI. Be efficient — complete the task in as few steps as possible.

Rules:
- Follow the plan's current sub-step
- Use knowledge base docs to understand element behaviors
- Use action "finish" when the FULL task is visibly complete (result shown on screen)
- If a required UI element is not visible, swipe to find it
- If you swiped and landed somewhere unexpected (e.g. the notification shade or quick \
settings instead of app content — recognizable by system elements like Wi-Fi/Bluetooth tiles, \
brightness sliders, or a list of notifications instead of the target app's UI), use action \
"back" to return, rather than continuing to swipe from there
- NEVER tap the same element twice in a row unless the screen changed
- When the task specifies exact content to search/enter (a query, name, topic), TYPE it yourself \
using the "text" action — do NOT tap a pre-existing recent-search suggestion, autocomplete entry, \
or history item just because it looks related. Only use a suggestion if its text is an EXACT match \
for what the task requires. Verify the field shows the task's exact content before treating a \
search as done — a search for different or unrelated content is not progress on this task.
- NEVER type a password, PIN, or other credential yourself using the "text" action — you must \
not guess or fabricate one. If the field needs a known credential, use "type_secret" with the \
exact secret_id from AVAILABLE SECRETS instead. If no matching secret_id exists, do not attempt \
the login — use "finish" with failure_reason-style thought explaining the missing credential.
- If you're reasoning from the element list alone (no screenshot) and cannot confidently identify the right element from resource_id/class_name/text/content_desc — e.g. multiple ambiguous candidates, or the element you need has no identifying text/icon description — use action "escalate" instead of guessing. This requests the actual screenshot for this round.
- Respond ONLY with valid JSON — no prose outside the JSON"""


def build_deploy_prompt(state: "AgentState", elements: list, docs_context: str, known_transitions: list = None) -> str:
    # compress history: first entry + last 5
    history = state.action_history
    if len(history) > 6:
        shown = [history[0]] + history[-5:]
        hist_note = f"(showing first + last 5 of {len(history)} total)"
    else:
        shown = history
        hist_note = ""

    # plan with current step indicator
    if state.sub_steps:
        steps_lines = []
        for i, step in enumerate(state.sub_steps):
            if i < state.current_step_idx:
                icon = "✓"
            elif i == state.current_step_idx:
                icon = "→"
            else:
                icon = "○"
            steps_lines.append(f"  {icon} {i+1}. {step}")
        plan_txt = "\n".join(steps_lines)
        current_step = state.sub_steps[min(state.current_step_idx, len(state.sub_steps)-1)]
    else:
        plan_txt = f"  → {state.task}"
        current_step = state.task

    history_lines = "\n".join(
        f"Round {h['round']}: {h['action']['action'].upper()} "
        f"element {h['action'].get('element_id', '–')} "
        f"— {h['action'].get('thought', '')[:80]}"
        for h in shown
    ) or "(first round)"

    return (
        f"{DEPLOY_SYSTEM}\n\n"
        f"TASK: {state.task}\n"
        f"App: {state.app_name} | Round: {state.round_num} / {state.max_rounds}\n\n"
        f"APP GUIDE:\n{_app_card_txt(state)}\n\n"
        f"KNOWN NAVIGATION FROM THIS SCREEN (from past Explore/Deploy runs):\n{_known_transitions_txt(known_transitions or [])}\n\n"
        f"PLAN:\n{plan_txt}\n\n"
        f"CURRENT SUB-STEP: {current_step}\n\n"
        f"INTERACTIVE ELEMENTS:\n{_elements_txt(elements)}\n\n"
        f"AVAILABLE SECRETS (reference by name only with type_secret — you cannot see their values):\n"
        f"{_secrets_txt(state)}\n\n"
        f"KNOWLEDGE BASE DOCS:\n{sanitize_screen_text(docs_context) or '(none — reason from screenshot directly)'}\n\n"
        f"ACTION HISTORY {hist_note}:\n{history_lines}\n\n"
        f"Respond ONLY with valid JSON matching this schema:\n{_DEPLOY_SCHEMA}"
    )


# ── Progress check prompt ──────────────────────────────────────────────────────

def build_text_progress_prompt(task: str, elements: list, action_history: list) -> str:
    """Text-only progress check — reads the current element list and recent
    action history instead of a screenshot. Cheaper than build_progress_prompt
    (no vision call), used as the first pass; the caller falls back to the
    vision version only when this one reports confident=false.
    """
    recent = action_history[-5:]
    history_txt = "\n".join(
        f"- {a['action'].get('action', '?')}"
        + (f" on element {a['action'].get('element_id')}" if a['action'].get('element_id') is not None else "")
        + (f" (thought: {a['action'].get('thought', '')[:80]})" if a['action'].get('thought') else "")
        for a in recent
    ) or "(no actions taken yet)"

    return (
        f"Task: {task}\n\n"
        f"Recent actions taken:\n{history_txt}\n\n"
        f"Current screen elements:\n{_elements_txt(elements)}\n\n"
        f"Based on the element list and action history alone (no screenshot), has the task "
        f"been completed? Be strict: if the task specifies exact content (a search query, a "
        f"name, a topic), only report complete if that exact content is visible in the element "
        f"list. If you cannot tell from the element list alone — e.g. the confirmation would "
        f"only be visible visually (an image, a color, a layout state text can't capture) — set "
        f"confident to false rather than guessing.\n\n"
        f'Respond ONLY with valid JSON: {{"complete": true|false, "confident": true|false, '
        f'"progress": "brief description of what the element list shows"}}'
    )


def build_progress_prompt(task: str) -> str:
    return (
        f"Look at this screenshot. Has the following task been completed?\n\n"
        f"Task: {task}\n\n"
        f"Be strict: if the task specifies particular content (a search query, a name, a topic), "
        f"verify that EXACT content is what's shown — not just that similar-looking content is present. "
        f"A search box showing a different or unrelated query than what the task asked for is NOT complete, "
        f"even if it's showing valid search results for something else.\n\n"
        f'Respond ONLY with valid JSON: {{"complete": true|false, "progress": "brief description of current state, quoting the exact visible text you checked against the task"}}'
    )


# ── Grid mode prompt ───────────────────────────────────────────────────────────

def build_grid_prompt(task_or_thought: str) -> str:
    return (
        f"A 9x9 grid has been overlaid on the screenshot. "
        f"Cells are labelled A1 (top-left) through I9 (bottom-right).\n\n"
        f"Context: {task_or_thought}\n\n"
        f"Which cell contains the element you need to interact with?\n\n"
        f'Respond ONLY with valid JSON: {{"thought": "...", "grid_cell": "E5", "confidence": "high|medium|low"}}'
    )
