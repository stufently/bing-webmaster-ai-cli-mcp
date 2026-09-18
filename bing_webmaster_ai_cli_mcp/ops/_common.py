"""Helpers shared by domain operations."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..client import BingClient
from ..emptiness import ROWS, SHAPE_ATTRIBUTE, SINGLE_RECORD
from ..errors import InvalidRequest
from ..render import redact_secrets, sanitize
from ..urls import validate_http_url

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def row_read[Read: Callable[..., Any]](function: Read) -> Read:
    """Declare that this read returns rows, so an empty answer is silence to be labelled."""
    setattr(function, SHAPE_ATTRIBUTE, ROWS)
    return function


def single_record[Read: Callable[..., Any]](function: Read) -> Read:
    """Declare that this read returns one record, whose own arrays are not rows.

    ``GetCrawlSettings`` carries its ``CrawlRate`` array inside a single record;
    ``GetUrlSubmissionQuota`` carries counts. Judging those structurally would report a
    measurement as "Bing returned nothing", which is the same misreading the empty-read
    label exists to prevent, pointed the other way.
    """
    setattr(function, SHAPE_ATTRIBUTE, SINGLE_RECORD)
    return function


def normalise_site(site_url: str) -> str:
    """Turn operator input into the exact ``siteUrl`` Bing is given.

    The scheme is detected by parsing rather than by a case-sensitive prefix test, so
    ``FTP://x`` and ``HTTPS://x`` are judged on what they are instead of silently
    acquiring a second scheme.
    """
    site = site_url.strip()
    if not site:
        raise InvalidRequest(f"invalid site URL: {site_url!r}")
    if _SCHEME.match(site) is None:
        site = f"https://{site}"
    try:
        parsed, hostname = validate_http_url(site, "site_url")
    except InvalidRequest as exc:
        raise InvalidRequest(f"invalid site URL: {site_url!r}") from exc
    if parsed.query or parsed.fragment:
        raise InvalidRequest(f"invalid site URL: {site_url!r}")
    port = parsed.port
    # urlsplit strips the brackets an IPv6 literal needs to stay a valid authority.
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}{parsed.path.rstrip('/')}"


async def fetch(
    client: BingClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    body: dict[str, Any] | None = None,
    reveal_secrets: bool = False,
) -> Any:
    """Call Bing and return the response with secrets hidden and untrusted text labelled.

    Redaction lives here rather than in the two operations that are known to return a
    verification code today, so a method that starts returning one tomorrow is covered
    without anybody remembering to cover it.
    """
    payload = await client.call(method, params, body=body)
    if not reveal_secrets:
        payload = redact_secrets(payload)
    return sanitize(payload)


def bool_param(value: bool) -> str:
    return "true" if value else "false"
