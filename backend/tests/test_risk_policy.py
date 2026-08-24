from backend.security.risk_policy import classify_action, RISK_TIERS


def _elem(**overrides):
    base = {
        "id": 1, "bounds": [0, 0, 100, 50], "class_name": "android.widget.EditText",
        "resource_id": "", "content_desc": "", "text": "", "clickable": True,
        "focusable": True, "scrollable": False, "password": False,
    }
    base.update(overrides)
    return base


def test_tap_on_ordinary_element_is_low():
    elements = [_elem(id=1, resource_id="search_button", text="Search")]
    action = {"action": "tap", "element_id": 1}
    assert classify_action(action, elements) == "low"


def test_back_and_swipe_and_finish_are_low():
    for action in [{"action": "back"}, {"action": "swipe", "direction": "up"}, {"action": "finish"}]:
        assert classify_action(action, []) == "low"


def test_plain_text_into_ordinary_field_is_medium():
    elements = [_elem(id=1)]
    action = {"action": "text", "element_id": 1, "text_input": "hello"}
    assert classify_action(action, elements) == "medium"


def test_type_secret_is_low_the_llm_never_sees_the_value():
    elements = [_elem(id=1, password=True)]
    action = {"action": "type_secret", "element_id": 1, "secret_id": "my_login"}
    assert classify_action(action, elements) == "low"


def test_tap_on_element_with_dangerous_keyword_is_high():
    for keyword in ["send", "pay", "confirm order", "delete", "transfer", "share"]:
        elements = [_elem(id=1, text=keyword)]
        action = {"action": "tap", "element_id": 1}
        assert classify_action(action, elements) == "high", keyword

    elements = [_elem(id=1, content_desc="Send message")]
    assert classify_action({"action": "tap", "element_id": 1}, elements) == "high"


def test_plain_text_into_password_field_is_critical():
    elements = [_elem(id=1, password=True)]
    action = {"action": "text", "element_id": 1, "text_input": "hunter2"}
    assert classify_action(action, elements) == "critical"


def test_unknown_element_id_falls_back_to_action_type_default():
    # element_id doesn't match anything in `elements` — must not crash,
    # must not silently treat it as password-safe.
    action = {"action": "text", "element_id": 99, "text_input": "hi"}
    assert classify_action(action, []) == "medium"


def test_risk_tiers_are_ordered_low_to_critical():
    assert RISK_TIERS == ("low", "medium", "high", "critical")


def test_unlabeled_tap_in_bottom_right_hotzone_is_medium():
    # A screen-wide element establishes the "screen size" the heuristic
    # infers from bounds, plus an icon-only FAB in the bottom-right corner.
    elements = [
        _elem(id=1, bounds=[0, 0, 1080, 2280], resource_id="root", clickable=False),
        _elem(id=2, bounds=[950, 2100, 1050, 2200], text="", content_desc=""),
    ]
    action = {"action": "tap", "element_id": 2}
    assert classify_action(action, elements) == "medium"


def test_unlabeled_tap_elsewhere_on_screen_stays_low():
    elements = [
        _elem(id=1, bounds=[0, 0, 1080, 2280], resource_id="root", clickable=False),
        _elem(id=2, bounds=[20, 20, 120, 120], text="", content_desc=""),
    ]
    action = {"action": "tap", "element_id": 2}
    assert classify_action(action, elements) == "low"


def test_labeled_tap_in_bottom_right_hotzone_stays_low():
    elements = [
        _elem(id=1, bounds=[0, 0, 1080, 2280], resource_id="root", clickable=False),
        _elem(id=2, bounds=[950, 2100, 1050, 2200], text="Home"),
    ]
    action = {"action": "tap", "element_id": 2}
    assert classify_action(action, elements) == "low"
