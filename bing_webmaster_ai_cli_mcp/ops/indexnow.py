"""Protocol-correct IndexNow key handling and batch submission."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..errors import (
    AuthFailed,
    BingWebmasterError,
    InvalidRequest,
    PlanUnknownOutcome,
    RateLimited,
    UpstreamUnavailable,
)
from ..render import sanitize_text
from ..urls import validate_http_url

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_BATCH = 10_000
_KEY = re.compile(r"^[A-Za-z0-9-]{8,128}$")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
_MEANING = {
    200: "received",
    202: "accepted; not yet processed (normal on first use of a key)",
}


def generate_key() -> str:
    return secrets.token_hex(16)


def validate_key(key: str) -> None:
    if _KEY.fullmatch(key) is None:
        raise InvalidRequest(
            "IndexNow keys must be 8-128 characters from a-z, A-Z, 0-9, and '-'",
            details={"length": len(key)},
        )


def validate_host(host: str) -> str:
    candidate = host.strip().casefold()
    # Checked before urlsplit, which raises a bare ValueError on an unbalanced bracket
    # and happily accepts hostnames containing spaces.
    if _HOST.fullmatch(candidate) is None or "." not in candidate:
        raise InvalidRequest("IndexNow host must be a hostname without a scheme, path, or port")
    parsed = urlsplit(f"//{candidate}")
    if parsed.hostname != candidate or parsed.username is not None or parsed.port is not None:
        raise InvalidRequest("IndexNow host must be a hostname without a scheme, path, or port")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise InvalidRequest("IndexNow host must be a DNS hostname, not an IP address")
    return candidate


def key_location(host: str, key: str) -> str:
    validate_key(key)
    return f"https://{validate_host(host)}/{key}.txt"


def validate_key_location(host: str, key: str, location: str | None) -> str:
    validate_key(key)
    if location is None:
        return key_location(host, key)
    parsed, location_host = validate_http_url(location, "keyLocation")
    if (
        parsed.scheme != "https"
        or location_host != validate_host(host)
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidRequest("keyLocation must be an HTTPS URL on the submitted host")
    return location


def validate_urls(
    host: str,
    key: str,
    urls: list[str],
    location: str | None = None,
) -> tuple[str, str, list[str]]:
    normalized_host = validate_host(host)
    validate_key(key)
    normalized_location = validate_key_location(normalized_host, key, location)
    if not urls:
        raise InvalidRequest("no IndexNow URLs to submit")
    if len(urls) > MAX_BATCH:
        raise InvalidRequest(f"IndexNow accepts at most {MAX_BATCH} URLs, got {len(urls)}")

    key_path = urlsplit(normalized_location).path.rsplit("/", 1)[0]
    for url in urls:
        parsed, url_host = validate_http_url(url, "urlList")
        if url_host != normalized_host:
            raise InvalidRequest(f"URL {url!r} does not belong to IndexNow host {normalized_host}")
        if key_path and parsed.path != key_path and not parsed.path.startswith(f"{key_path}/"):
            raise InvalidRequest(f"URL {url!r} is outside the key path {key_path!r}")
    return normalized_host, normalized_location, urls


async def verify_key_file(
    http: httpx.AsyncClient,
    host: str,
    key: str,
    location: str | None = None,
) -> None:
    url = validate_key_location(host, key, location)
    try:
        async with http.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                raise InvalidRequest(
                    f"IndexNow key file {url} is not reachable (HTTP {response.status_code})",
                    suggestion="serve the UTF-8 key file at that exact path without authentication",
                )
            expected = key.encode()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > len(expected):
                    raise InvalidRequest(
                        f"IndexNow key file {url} does not contain the expected key",
                        suggestion="the file must contain the key and no other content",
                    )
                content.extend(chunk)
    except httpx.HTTPError as exc:
        raise InvalidRequest(f"IndexNow key file {url} is not reachable: {exc}") from exc
    if bytes(content) != expected:
        raise InvalidRequest(
            f"IndexNow key file {url} does not contain the expected key",
            suggestion="the file must contain the key and no other content",
        )


async def key_plan(
    http: httpx.AsyncClient | None,
    host: str,
    key: str | None = None,
    key_location: str | None = None,
    check_key_file: bool | None = None,
) -> dict[str, Any]:
    """Everything needed to publish an IndexNow key, computed locally.

    This is not a write and records no plan: nothing is sent to Bing or to
    ``api.indexnow.org``, no quota is consumed and nothing is stored. A generated key
    exists only in this response, so the operator has to save it and serve the key file
    themselves. The optional reachability check is a plain unauthenticated GET of the
    key file on the operator's own host - the same request the submission preflight
    makes - and it is skipped by default for a key generated here, which cannot be
    published yet.
    """
    normalized_host = validate_host(host)
    generated = key is None
    resolved_key = generate_key() if key is None else key
    validate_key(resolved_key)
    location = validate_key_location(normalized_host, resolved_key, key_location)
    directory = urlsplit(location).path.rsplit("/", 1)[0]
    if check_key_file is None:
        check_key_file = not generated
    plan: dict[str, Any] = {
        "host": normalized_host,
        "key": resolved_key,
        "generated": generated,
        "key_location": location,
        "key_file_contents": resolved_key,
        "authorizes_urls_under": f"https://{normalized_host}{directory}/",
        "key_file": {"checked": False, "present": None},
    }
    if check_key_file:
        plan["key_file"] = await _describe_key_file(http, normalized_host, resolved_key, location)
    return plan


async def _resolve(host: str) -> set[str]:
    """Every address ``host`` resolves to. Separated out so tests can replace it."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return {str(info[4][0]) for info in infos}


async def assert_publicly_routable(host: str) -> None:
    """Refuse a host that resolves anywhere but the public internet.

    ``validate_host`` only judges the shape of a name, and ``foo.localhost`` or
    ``127.0.0.1.nip.io`` are perfectly well-formed multi-label names that resolve to
    loopback. An IndexNow key file is public by definition, so a private answer is never
    the real thing - it is a way to aim this process at the operator's own network.
    Checked here rather than in ``verify_key_file`` because this is the path an MCP
    client can reach without a human approving anything.
    """
    try:
        addresses = await _resolve(host)
    except OSError as exc:
        raise InvalidRequest(f"IndexNow host {host} does not resolve: {exc}") from exc
    if not addresses:
        raise InvalidRequest(f"IndexNow host {host} does not resolve")
    for address in sorted(addresses):
        # The scope id of a link-local IPv6 address is not part of the address itself.
        if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
            raise InvalidRequest(
                f"IndexNow host {host} resolves to the non-public address {address}",
                suggestion="the key file has to be served from the public internet",
            )


async def _describe_key_file(
    http: httpx.AsyncClient | None,
    host: str,
    key: str,
    location: str,
) -> dict[str, Any]:
    """Report whether the key file is already served, rather than raising.

    Absence is the expected answer before the file is published, so it is a result and
    not an error; the reason is still reported so a wrong path or a redirect is visible.
    """
    if http is None:
        return {"checked": False, "present": None}
    try:
        await assert_publicly_routable(host)
        await verify_key_file(http, host, key, location)
    except BingWebmasterError as exc:
        return {"checked": True, "present": False, "detail": sanitize_text(exc.message)}
    return {"checked": True, "present": True}


async def submit(
    http: httpx.AsyncClient,
    host: str,
    key: str,
    urls: list[str],
    location: str | None = None,
) -> dict[str, Any]:
    normalized_host, normalized_location, validated_urls = validate_urls(host, key, urls, location)
    body = {
        "host": normalized_host,
        "key": key,
        "keyLocation": normalized_location,
        "urlList": validated_urls,
    }
    try:
        response = await http.post(
            ENDPOINT,
            json=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise UpstreamUnavailable(f"IndexNow connection failed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PlanUnknownOutcome(
            "IndexNow request was sent but its outcome is unknown",
            suggestion="inspect the audit log before creating a replacement plan",
        ) from exc

    if response.status_code in _MEANING:
        return {
            "submitted": len(validated_urls),
            "status_code": response.status_code,
            "meaning": _MEANING[response.status_code],
        }
    if response.status_code == 400:
        raise InvalidRequest("IndexNow rejected the request as malformed")
    if response.status_code == 403:
        raise AuthFailed("IndexNow key rejected or its key file is unreachable")
    if response.status_code == 422:
        raise InvalidRequest(
            "IndexNow rejected the batch: host/key mismatch, foreign URL, or batch too large"
        )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimited(
            "IndexNow rate limited this key",
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    if response.status_code >= 500:
        raise UpstreamUnavailable(f"IndexNow returned HTTP {response.status_code}")
    raise InvalidRequest(f"IndexNow returned unexpected HTTP {response.status_code}")


async def verify_and_submit(
    http: httpx.AsyncClient,
    host: str,
    key: str,
    urls: list[str],
    location: str | None = None,
) -> dict[str, Any]:
    await verify_key_file(http, host, key, location)
    return await submit(http, host, key, urls, location)
