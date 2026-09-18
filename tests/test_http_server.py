from __future__ import annotations

import httpx
import pytest
from starlette.responses import JSONResponse

from bing_webmaster_ai_cli_mcp.config import Settings
from bing_webmaster_ai_cli_mcp.errors import AuthFailed, InvalidRequest
from bing_webmaster_ai_cli_mcp.http_server import BearerGate, validate_http_settings


async def _ok(scope, receive, send) -> None:
    await JSONResponse({"ok": True})(scope, receive, send)


async def test_bearer_gate_rejects_missing_and_wrong_tokens() -> None:
    app = BearerGate(_ok, "x" * 32)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/mcp")).status_code == 401
        assert (
            await client.get("/mcp", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401


async def test_bearer_gate_accepts_exact_token() -> None:
    token = "x" * 32
    app = BearerGate(_ok, token)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/mcp", headers={"Authorization": f"Bearer {token}"})
    assert response.json() == {"ok": True}


def test_http_server_is_loopback_only(tmp_path) -> None:
    with pytest.raises(InvalidRequest, match="loopback"):
        validate_http_settings(
            Settings(
                state_dir=tmp_path,
                http_host="0.0.0.0",  # noqa: S104 - deliberately verify unsafe bind rejection
                http_bearer_token="x" * 32,
            )
        )


def test_http_server_requires_strong_token(tmp_path) -> None:
    with pytest.raises(AuthFailed, match="HTTP_BEARER_TOKEN"):
        validate_http_settings(
            Settings(
                state_dir=tmp_path,
                http_bearer_token="short",  # noqa: S106 - deliberately invalid test token
            )
        )
