"""Shared URL validation for site policy, Bing writes, and IndexNow."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import SplitResult, unquote, urlsplit

from .errors import InvalidRequest

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_MAX_DECODE_PASSES = 8


def split_url(value: str, name: str) -> SplitResult:
    """Parse a URL while keeping lazy ``urllib`` errors in the public taxonomy."""
    try:
        parsed = urlsplit(value)
        _ = parsed.hostname, parsed.port, parsed.username, parsed.password
    except ValueError as exc:
        raise InvalidRequest(f"invalid URL for {name}: {value!r}") from exc
    return parsed


def normalise_hostname(hostname: str, name: str) -> str:
    """Return a canonical IP literal or IDNA hostname with valid DNS labels."""
    candidate = hostname.rstrip(".").casefold()
    if not candidate:
        raise InvalidRequest(f"invalid hostname for {name}: {hostname!r}")
    try:
        return ipaddress.ip_address(candidate).compressed.casefold()
    except ValueError:
        pass
    try:
        ascii_host = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidRequest(f"invalid hostname for {name}: {hostname!r}") from exc
    if len(ascii_host) > 253 or any(
        _DNS_LABEL.fullmatch(label) is None for label in ascii_host.split(".")
    ):
        raise InvalidRequest(f"invalid hostname for {name}: {hostname!r}")
    return ascii_host


def ensure_unambiguous_path(path: str, name: str) -> None:
    """Reject dot segments even when separators or dots are repeatedly percent-encoded."""
    current = path
    for _ in range(_MAX_DECODE_PASSES):
        if "\\" in current or any(segment in {".", ".."} for segment in current.split("/")):
            raise InvalidRequest(f"{name} contains ambiguous dot segments")
        try:
            decoded = unquote(current, errors="strict")
        except UnicodeDecodeError as exc:
            raise InvalidRequest(f"{name} contains invalid percent-encoding") from exc
        if decoded == current:
            return
        current = decoded
    raise InvalidRequest(f"{name} contains excessively nested percent-encoding")


def validate_http_url(value: str, name: str) -> tuple[SplitResult, str]:
    """Validate common absolute HTTP(S) URL syntax and return its canonical host."""
    if (
        not value
        or "\\" in value
        or _INVALID_PERCENT_ESCAPE.search(value)
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
    ):
        if _INVALID_PERCENT_ESCAPE.search(value):
            raise InvalidRequest(f"{name} contains invalid percent-encoding")
        raise InvalidRequest(f"{name} must be an absolute HTTP(S) URL")
    parsed = split_url(value, name)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise InvalidRequest(f"{name} must be an absolute HTTP(S) URL")
    hostname = normalise_hostname(parsed.hostname, name)
    ensure_unambiguous_path(parsed.path, name)
    return parsed, hostname
