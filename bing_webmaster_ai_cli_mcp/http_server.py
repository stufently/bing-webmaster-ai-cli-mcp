"""Optional loopback-only, bearer-gated MCP Streamable HTTP transport."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Any

import uvicorn
from starlette.responses import JSONResponse

from .config import Settings
from .errors import AuthFailed, InvalidRequest
from .mcp_server import build_server


class BearerGate:
    """Small ASGI boundary in front of the SDK's Streamable HTTP app."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._authorization = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            values = [
                value for key, value in scope.get("headers", []) if key.lower() == b"authorization"
            ]
            authorized = len(values) == 1 and secrets.compare_digest(values[0], self._authorization)
            if not authorized:
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": "Bearer",
                        "Cache-Control": "no-store",
                    },
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def validate_http_settings(settings: Settings) -> tuple[str, str]:
    try:
        address = ipaddress.ip_address(settings.http_host)
    except ValueError as exc:
        raise InvalidRequest(
            "BING_WM_HTTP_HOST must be a literal loopback address such as 127.0.0.1 or ::1"
        ) from exc
    if not address.is_loopback:
        raise InvalidRequest("the MCP HTTP server is loopback-only")
    if settings.http_bearer_token is None:
        raise AuthFailed("BING_WM_HTTP_BEARER_TOKEN is required for the HTTP server")
    token = settings.http_bearer_token.get_secret_value()
    if len(token) < 32:
        raise AuthFailed("BING_WM_HTTP_BEARER_TOKEN must contain at least 32 characters")
    return str(address), token


def build_app(settings: Settings) -> BearerGate:
    host, token = validate_http_settings(settings)
    app = build_server().streamable_http_app(
        streamable_http_path="/mcp",
        host=host,
    )
    return BearerGate(app, token)


def main() -> None:
    settings = Settings.load()
    host, _token = validate_http_settings(settings)
    uvicorn.run(build_app(settings), host=host, port=settings.http_port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
