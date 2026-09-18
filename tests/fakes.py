"""Canned transports. No socket is ever opened."""

from __future__ import annotations

from typing import Any

import httpx

from bing_webmaster_ai_cli_mcp.config import Settings


def fake_settings(tmp_path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "api_key": "test-key",
        "state_dir": tmp_path,
        "calls_per_second": 100_000.0,
        "max_attempts": 1,
    }
    values.update(overrides)
    return Settings(**values)


def bing_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        if method not in routes:
            return httpx.Response(400, json={"ErrorCode": 400, "Message": f"no route {method}"})
        return httpx.Response(200, json={"d": routes[method]})

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def error_transport(
    status: int, body: dict[str, Any], *, headers: dict[str, str] | None = None
) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json=body, headers=headers, request=request)
    )
