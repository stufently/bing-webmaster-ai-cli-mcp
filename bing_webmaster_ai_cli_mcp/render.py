"""Neutralise and label attacker-influenced text, and hide secrets, before returning it."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

UNTRUSTED_FIELDS = frozenset(
    {"AnchorText", "Description", "Message", "Query", "QueryString", "Title", "Url"}
)

# Ownership proofs Bing hands back beside a site: the code that goes in the verification
# file or meta tag, the one that goes in a DNS TXT record, and the delegation code on a
# site role. Whoever holds one can claim the site in another Bing account, so they are
# credentials, not site metadata - and none of them is needed to list, audit or report
# on a site. Anything that reaches a caller reaches its transcript, its logs and its
# reports, so these are redacted everywhere by default and revealed only when an
# operator at the CLI asks for them by name.
SECRET_FIELDS = frozenset({"AuthenticationCode", "DelegatedCode", "DnsVerificationCode"})
# The same three values under the spellings a request uses: Bing's response says
# ``AuthenticationCode``, its request body says ``authenticationCode`` and a plan's
# recorded arguments say ``authentication_code``. One secret with three names would need
# three lists to stay in step, so the name is compared with its underscores removed and
# its case folded instead.
_SECRET_KEYS = frozenset(name.replace("_", "").casefold() for name in SECRET_FIELDS)
REDACTED = "[redacted: verification secret]"
# The credential that authenticates the call itself, as opposed to an ownership proof
# Bing hands back about a site. It gets its own marker because a reader who finds
# "verification secret" in place of an API key learns the wrong thing about what leaked
# and what has to be rotated.
REDACTED_CREDENTIAL = "[redacted: API credential]"
# A literal shorter than this is not searched for in free text. Bing's codes are long
# hex strings; replacing every occurrence of a two-character value would shred an error
# message without hiding anything that was ever a credential.
_MIN_SECRET_LENGTH = 4
_MAX_LENGTH = 2000
_KEEP_CONTROLS = {"\n", "\t"}
_TRUNCATED = "… [truncated]"


def sanitize_text(value: str) -> str:
    cleaned = "".join(
        character
        for character in value
        if character in _KEEP_CONTROLS or not unicodedata.category(character).startswith("C")
    )
    if len(cleaned) > _MAX_LENGTH:
        return cleaned[:_MAX_LENGTH] + _TRUNCATED
    return cleaned


def _is_present(value: Any) -> bool:
    """Whether a secret field actually carries one.

    A code Bing did not set stays ``null``: replacing it with the marker would claim an
    ownership proof exists where there is none.
    """
    return value is not None and value != ""


def _is_secret_key(key: Any) -> bool:
    return isinstance(key, str) and key.replace("_", "").casefold() in _SECRET_KEYS


def secret_values(value: Any) -> frozenset[str]:
    """Every verification secret carried as a value by a request body or plan arguments.

    Field names hide a secret we are handing back. This finds the other direction: the
    literal we ourselves sent, so it can be recognised again in text that was never keyed
    - an upstream error message quoting the code it rejected.
    """
    found: set[str] = set()

    def walk(node: Any, inside_secret: bool) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, inside_secret or _is_secret_key(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, inside_secret)
        elif inside_secret and isinstance(node, str) and len(node) >= _MIN_SECRET_LENGTH:
            found.add(node)

    walk(value, False)
    return frozenset(found)


def redact_text(value: str, secrets: Iterable[str], marker: str = REDACTED) -> str:
    """Replace known secret literals wherever they appear in a piece of free text.

    ``marker`` names the kind of literal being hidden. Two kinds travel in a request -
    an ownership proof in the body and the credential in the query string - and the
    replacement is the only thing a reader ever sees of either.
    """
    for secret in secrets:
        if len(secret) >= _MIN_SECRET_LENGTH:
            value = value.replace(secret, marker)
    return value


def redact(value: Any, secrets: Iterable[str] = (), marker: str = REDACTED) -> Any:
    """Hide every verification secret leaving this process, by name and by value.

    This is the exit boundary: a response, a write result, an error message and anything
    written to the audit trail goes through it. Redacting only where a secret is expected
    means covering the places somebody thought of, and the code Bing quotes back inside
    an error string is exactly the place nobody thinks of.
    """
    literals = tuple(
        sorted(
            {secret for secret in secrets if len(secret) >= _MIN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )
    )
    return _redact(value, literals, marker)


def _redact(value: Any, literals: tuple[str, ...], marker: str) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED
            if _is_secret_key(key) and _is_present(item)
            else _redact(item, literals, marker)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, literals, marker) for item in value]
    if isinstance(value, str) and literals:
        return redact_text(value, literals, marker)
    return value


def redact_secrets(value: Any) -> Any:
    """Replace every verification secret in a response with a self-describing marker."""
    return redact(value)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in UNTRUSTED_FIELDS and isinstance(item, str):
                output[key] = {"value": sanitize_text(item), "untrusted": True}
            else:
                output[key] = sanitize(item)
        return output
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value
