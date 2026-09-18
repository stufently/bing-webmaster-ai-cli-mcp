from __future__ import annotations

import socket

import pytest

import bing_webmaster_ai_cli_mcp


def test_version_is_exposed() -> None:
    assert isinstance(bing_webmaster_ai_cli_mcp.__version__, str)
    assert bing_webmaster_ai_cli_mcp.__version__.count(".") >= 2


def test_network_is_blocked_in_tests() -> None:
    with pytest.raises(RuntimeError, match="network access"):
        socket.socket().connect(("example.com", 80))
