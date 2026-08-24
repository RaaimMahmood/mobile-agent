"""Regression test for a device leak found during live testing: a request
that acquires a device but then fails before AgentState is fully built
(e.g. KnowledgeBase() can't reach ChromaDB) left the device permanently
marked busy — nothing downstream of the failure point ever ran to release
it, and the only way out was restarting the server.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.api.routers.agent import _make_state
from backend.agent.state import RunConfig


class _FakeRegistry:
    def __init__(self):
        self.released = []

    def acquire(self, serial=None):
        return ("serial-1", MagicMock())

    def release(self, serial):
        self.released.append(serial)


def _request_with_registry(registry):
    request = MagicMock()
    request.app.state.devices = registry
    return request


def test_device_is_released_when_knowledge_base_construction_fails():
    registry = _FakeRegistry()
    request = _request_with_registry(registry)
    config = RunConfig(app_name="youtube", task="t", mode="deploy")

    with patch(
        "backend.api.routers.agent.KnowledgeBase",
        side_effect=ValueError("Could not connect to a Chroma server."),
    ):
        with pytest.raises(ValueError):
            _make_state("sess-1", config, request)

    assert registry.released == ["serial-1"]


def test_device_is_not_released_on_success():
    registry = _FakeRegistry()
    request = _request_with_registry(registry)
    config = RunConfig(app_name="youtube", task="t", mode="deploy")

    with patch("backend.api.routers.agent.KnowledgeBase", return_value=MagicMock()):
        state = _make_state("sess-1", config, request)

    assert state is not None
    assert registry.released == []
