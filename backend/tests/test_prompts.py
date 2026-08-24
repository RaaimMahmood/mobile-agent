import types

from backend.agent.state import AgentState, RunConfig
from backend.llm.prompts import (
    _app_card_txt,
    _elements_txt,
    _history_txt,
    _known_transitions_txt,
    _secrets_txt,
    build_chat_intent_prompt,
    build_deploy_prompt,
    build_explore_prompt,
    build_grid_prompt,
    build_planner_prompt,
    build_progress_prompt,
    build_reflect_prompt,
    build_text_progress_prompt,
)

ELEMENTS = [
    {
        "id": 1,
        "class_name": "android.widget.ImageButton",
        "text": "",
        "content_desc": "Search",
        "resource_id": "com.youtube:id/search_btn",
    }
]

ELEMENT_LINE = (
    "[1] android.widget.ImageButton | text='' | desc='Search' "
    "| res='com.youtube:id/search_btn'"
)


def _make_state(mode="explore", **overrides):
    config = RunConfig(app_name="youtube", task="search for cats", mode=mode)
    state = AgentState(session_id="sess-1", config=config)
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def _history_entry(round_num, action_type="tap", element_id=1, thought="did a thing"):
    return {
        "round": round_num,
        "action": {"action": action_type, "element_id": element_id, "thought": thought},
    }


def test_explore_prompt_contains_section_markers():
    state = _make_state(
        app_card="Tap the search icon, then type a query.",
        credentials={"pw": "hunter2"},
        action_history=[_history_entry(1)],
    )
    prompt = build_explore_prompt(state, ELEMENTS, "docs context here")
    assert "App: youtube" in prompt
    assert "Round: 0 / 20" in prompt
    assert "APP GUIDE:" in prompt
    assert "Tap the search icon, then type a query." in prompt
    assert "INTERACTIVE ELEMENTS (numbered orange boxes on screenshot):" in prompt
    assert ELEMENT_LINE in prompt
    assert "AVAILABLE SECRETS (reference by name only with type_secret" in prompt
    assert "pw" in prompt
    assert "docs context here" in prompt
    assert "LAST 5 ACTIONS:" in prompt


def test_explore_prompt_empty_edge_cases():
    state = _make_state()
    prompt = build_explore_prompt(state, [], "")
    assert "(no interactive elements detected)" in prompt
    assert "(first round)" in prompt
    assert "(no guide available for this app" in prompt
    assert "(none configured)" in prompt
    assert "(none yet)" in prompt


def test_deploy_prompt_contains_section_markers():
    state = _make_state(
        mode="deploy",
        sub_steps=["open search", "type query", "submit"],
        current_step_idx=1,
        action_history=[_history_entry(1, action_type="text", element_id=None)],
        credentials={"pw": "hunter2"},
        app_card="guide text",
    )
    known_transitions = [
        {
            "element_sig": "com.youtube:id/search_btn::android.widget.ImageButton",
            "element_text": "opened feed",
        }
    ]
    prompt = build_deploy_prompt(state, ELEMENTS, "kb docs", known_transitions)
    assert "KNOWN NAVIGATION FROM THIS SCREEN" in prompt
    assert "TASK: search for cats" in prompt
    assert "App: youtube" in prompt
    assert "Round: 0 / 20" in prompt
    assert "PLAN:" in prompt
    assert "✓ 1." in prompt
    assert "→ 2." in prompt
    assert "○ 3." in prompt
    assert "CURRENT SUB-STEP: type query" in prompt
    assert "com.youtube:id/search_btn::android.widget.ImageButton" in prompt
    assert "opened feed" in prompt
    assert "kb docs" in prompt


def test_deploy_prompt_empty_edge_cases():
    state = _make_state(mode="deploy")
    prompt = build_deploy_prompt(state, [], "", None)
    assert "  → search for cats" in prompt
    assert "CURRENT SUB-STEP: search for cats" in prompt
    assert "(first round)" in prompt
    assert "(no known transitions from this screen yet)" in prompt
    assert "(no guide available for this app" in prompt
    assert "(none configured)" in prompt
    assert "(none — reason from screenshot directly)" in prompt


def test_deploy_prompt_compresses_long_history():
    history = [_history_entry(i) for i in range(1, 9)]
    state = _make_state(mode="deploy", action_history=history)
    prompt = build_deploy_prompt(state, [], "", [])
    assert "(showing first + last 5 of 8 total)" in prompt
    assert "Round 1:" in prompt
    assert "Round 8:" in prompt


def test_elements_txt_formats_and_empty():
    line = _elements_txt(ELEMENTS)
    assert line == ELEMENT_LINE
    assert _elements_txt([]) == "(no interactive elements detected)"


def test_history_txt_caps_at_max_entries_and_empty():
    history = [_history_entry(i) for i in range(1, 8)]
    txt = _history_txt(history, max_entries=5)
    lines = txt.splitlines()
    assert len(lines) == 5
    assert "Round 3" in txt
    assert "Round 7" in txt
    assert "Round 1" not in txt
    assert "TAP" in txt
    assert _history_txt([]) == "(first round)"


def test_history_txt_handles_missing_element_id():
    history = [{"round": 1, "action": {"action": "tap", "thought": "x"}}]
    assert "element –" in _history_txt(history)


def test_known_transitions_txt():
    transitions = [{"element_sig": "sig-1", "element_text": "opened profile"}]
    txt = _known_transitions_txt(transitions)
    assert "sig-1" in txt
    assert "opened profile" in txt
    assert _known_transitions_txt([]) == "(no known transitions from this screen yet)"
    assert _known_transitions_txt(None) == "(no known transitions from this screen yet)"


def test_secrets_txt():
    state = types.SimpleNamespace(credentials={"pin": "1234", "pw": "secret"})
    assert _secrets_txt(state) == "pin, pw"
    assert _secrets_txt(types.SimpleNamespace(credentials=None)) == "(none configured)"


def test_app_card_txt():
    assert _app_card_txt(types.SimpleNamespace(app_card="guide")) == "guide"
    assert "(no guide available" in _app_card_txt(types.SimpleNamespace(app_card=None))
    assert "(no guide available" in _app_card_txt(types.SimpleNamespace(app_card=""))


def test_reflect_prompt():
    elem = {
        "id": 4,
        "class_name": "android.widget.EditText",
        "text": "",
        "content_desc": "Search field",
        "resource_id": "com.youtube:id/search_field",
    }
    prompt = build_reflect_prompt("youtube", elem, "tap")
    assert "App: youtube" in prompt
    assert (
        "[4] android.widget.EditText | text='' | desc='Search field' "
        "| res='com.youtube:id/search_field'" in prompt
    )
    assert "Action taken: tap" in prompt
    assert "documentation" in prompt
    assert "observed_result" in prompt


def test_chat_intent_prompt():
    prompt = build_chat_intent_prompt("open linkedin jobs")
    assert "open linkedin jobs" in prompt
    assert "app_name" in prompt
    assert "task" in prompt


def test_planner_prompt():
    prompt = build_planner_prompt("find a job", "linkedin")
    assert "App: linkedin" in prompt
    assert "Task: find a job" in prompt
    assert "steps" in prompt


def test_progress_prompt():
    prompt = build_progress_prompt("search for cats")
    assert "Task: search for cats" in prompt
    assert "complete" in prompt


def test_text_progress_prompt_contains_task_elements_and_history():
    history = [
        {"action": {"action": "text", "element_id": 3, "thought": "typed the query"}},
        {"action": {"action": "tap", "element_id": 1}},
    ]
    prompt = build_text_progress_prompt("search for cats", ELEMENTS, history)
    assert "Task: search for cats" in prompt
    assert "confident" in prompt
    assert "typed the query" in prompt
    assert "tap on element 1" in prompt


def test_text_progress_prompt_empty_history_and_elements():
    prompt = build_text_progress_prompt("search for cats", [], [])
    assert "(no actions taken yet)" in prompt
    assert "(no interactive elements detected)" in prompt


def test_text_progress_prompt_caps_history_at_five():
    history = [{"action": {"action": "tap", "element_id": i}} for i in range(10)]
    prompt = build_text_progress_prompt("t", [], history)
    # only the last 5 should appear — element 9 (last) present, element 0 (10th-from-last) absent
    assert "element 9" in prompt
    assert "element 0" not in prompt


def test_grid_prompt():
    prompt = build_grid_prompt("find the search button")
    assert "9x9 grid" in prompt
    assert "find the search button" in prompt
    assert "grid_cell" in prompt
    assert "A1" in prompt
    assert "I9" in prompt
