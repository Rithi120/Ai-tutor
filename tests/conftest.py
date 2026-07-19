"""Global test safety: ordinary tests cannot open network connections."""

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch, request):
    live_requested = (
        request.node.get_closest_marker("live_ai") is not None
        and os.getenv("RUN_LIVE_AI_TEST") == "1"
    )
    if live_requested:
        return

    def blocked(*_args, **_kwargs):
        raise AssertionError("Unmocked network request attempted during automated tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)

