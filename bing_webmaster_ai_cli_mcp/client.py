"""The only transport for the Bing Webmaster Tools JSON API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from types import TracebackType
from typing import Any

import httpx

from ._serialize import decode, unwrap
from .auth import build_auth
from .config import Settings
from .errors import (
    AuthFailed,
    BingWebmasterError,
    InvalidRequest,
    MalformedResponse,
    PlanUnknownOutcome,
    RateLimited,
    UpstreamUnavailable,
)
from .render import REDACTED_CREDENTIAL, redact, redact_text, secret_values


class _Throttle:
    """A local politeness bound; Bing does not publish a QPS quota."""

    def __init__(self, calls_per_second: float) -> None:
        self._interval = 1.0 / calls_per_second
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._interval


class BingClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._auth = build_auth(settings)
        self._throttle = _Throttle(settings.calls_per_second)
        self._http = httpx.AsyncClient(transport=transport, timeout=30.0)

    async def __aenter__(self) -> BingClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        body: dict[str, Any] | None = None,
        mutating: bool = False,
    ) -> Any:
        query: dict[str, Any] = dict(params or {})
        headers: dict[str, str] = {}
        self._auth.apply(query, headers)
        url = f"{self._settings.base_url.rstrip('/')}/{method}"
        # The request body is the only way a verification secret enters a Bing call, so
        # the literals to hide are derived here rather than passed in by every caller: a
        # write that starts carrying a new secret is covered without anybody remembering.
        secrets = secret_values(body) if body else frozenset()
        # The credential goes in the query string, so every URL built above carries a live
        # one. Nothing here ever prints a URL, but a message that came from underneath - a
        # proxy naming the address it could not reach, or Bing quoting our request back -
        # does, and that message becomes the error, the MCP reply and the audit entry.
        # Read after ``apply`` rather than once at construction: a provider that refreshes
        # a token would otherwise stay covered only for the value it started with.
        credentials = _credential_forms(self._auth.secrets())

        for attempt in range(1, self._settings.max_attempts + 1):
            await self._throttle.wait()
            try:
                response = await self._request(url, query, headers, body)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                error: BingWebmasterError = UpstreamUnavailable(
                    _scrub(f"{method}: {exc}", secrets, credentials)
                )
            except httpx.HTTPError as exc:
                if mutating:
                    # Deliberately not chained from ``exc``. A transport that names the
                    # address it could not reach puts the credential in that exception's
                    # own text, and a chained cause reaches every traceback anybody
                    # formats or logs, where ``to_dict`` cannot cover it. What the
                    # operator needs from it - what actually failed - is carried scrubbed
                    # in ``details``, which also reaches the audit trail.
                    raise PlanUnknownOutcome(
                        f"{method}: request was sent but its outcome is unknown",
                        suggestion="inspect Bing and the audit log before creating a new plan",
                        details={
                            "cause": _scrub(f"{type(exc).__name__}: {exc}", secrets, credentials)
                        },
                    ) from None
                error = UpstreamUnavailable(_scrub(f"{method}: {exc}", secrets, credentials))
            else:
                if response.status_code < 400:
                    if not mutating:
                        return _decode_success(method, response)
                    try:
                        return _decode_success(method, response)
                    except Exception as exc:
                        # Bing accepted the write, so nothing that happens while reading
                        # the answer may leave the plan retryable.
                        raise PlanUnknownOutcome(
                            f"{method}: Bing accepted the request but its response could "
                            "not be read",
                            suggestion="inspect Bing and the audit log before creating a new plan",
                        ) from exc
                if mutating and response.status_code >= 500:
                    raise PlanUnknownOutcome(
                        f"{method}: Bing answered HTTP {response.status_code}; "
                        "the write may or may not have been applied",
                        suggestion="inspect Bing and the audit log before creating a new plan",
                    )
                error = _map_error(method, response, secrets, credentials)

            if mutating or not error.retryable or attempt == self._settings.max_attempts:
                raise error
            delay = error.retry_after if error.retry_after is not None else 2 ** (attempt - 1)
            await asyncio.sleep(delay)
        raise AssertionError("retry loop must return or raise")

    async def _request(
        self,
        url: str,
        query: dict[str, Any],
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> httpx.Response:
        if body is None:
            return await self._http.get(url, params=query, headers=headers)
        return await self._http.post(url, params=query, headers=headers, json=body)


def _decode_success(method: str, response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MalformedResponse(f"{method}: response was not JSON") from exc
    return decode(unwrap(payload))


def _credential_forms(credentials: Iterable[str]) -> frozenset[str]:
    """Each credential as we hold it, and as httpx writes it into a URL.

    The key is a query parameter, so the form a proxy or Bing quotes back is the encoded
    one: a key containing ``/`` or a space reaches the wire as ``%2F`` or ``+``, and a
    search for the literal we hold would walk straight past it. Microsoft documents no
    alphabet for the key, so the encoding is asked of httpx rather than guessed at a
    second time here.
    """
    forms: set[str] = set()
    for credential in credentials:
        forms.add(credential)
        forms.add(str(httpx.QueryParams({"v": credential})).removeprefix("v="))
    return frozenset(forms)


def _scrub(text: str, secrets: Iterable[str], credentials: Iterable[str]) -> str:
    """Hide both kinds of literal we ourselves put into the request, each under its name.

    A verification code travels in the body; the credential travels in the URL. Either
    can come back inside somebody else's words, and the reader of the result needs to
    know which one it was.
    """
    return redact_text(redact_text(text, credentials, REDACTED_CREDENTIAL), secrets)


def _map_error(
    method: str,
    response: httpx.Response,
    secrets: Iterable[str] = (),
    credentials: Iterable[str] = (),
) -> BingWebmasterError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    message = body.get("Message") if isinstance(body, dict) else None
    # Bing's own words about our request, and our request may have contained a
    # credential. Whatever it quoted back is scrubbed before the message exists as an
    # error, so the audit trail, the MCP error and the terminal all get the same text.
    reported = message or response.reason_phrase or "request failed"
    text = _scrub(f"{method}: {reported}", secrets, credentials)
    details = None
    if isinstance(body, dict) and "ErrorCode" in body:
        details = redact(
            redact({"ErrorCode": body["ErrorCode"]}, credentials, REDACTED_CREDENTIAL), secrets
        )

    if response.status_code in {401, 403}:
        return AuthFailed(
            text,
            suggestion="check BING_WM_API_KEY and ownership of the target site",
            details=details,
        )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        return RateLimited(
            text,
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
            details=details,
        )
    if response.status_code >= 500:
        return UpstreamUnavailable(text, details=details)
    return InvalidRequest(text, details=details)
