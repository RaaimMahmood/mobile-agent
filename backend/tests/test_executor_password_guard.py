import asyncio

from backend.agent.executor import execute_action


class _FakeDevice:
    def __init__(self):
        self.texted = []
        self.tapped = []

    async def tap(self, x, y):
        self.tapped.append((x, y))

    async def clear_text(self):
        return None

    async def text(self, value):
        self.texted.append(value)


def test_text_action_into_password_element_never_reaches_the_device():
    device = _FakeDevice()
    elements = [{
        "id": 1, "bounds": [0, 0, 100, 50], "class_name": "android.widget.EditText",
        "resource_id": "", "content_desc": "", "text": "", "clickable": True,
        "focusable": True, "scrollable": False, "password": True,
    }]
    action = {"action": "text", "element_id": 1, "text_input": "hunter2"}

    asyncio.run(execute_action(device, action, elements))

    assert device.texted == []
