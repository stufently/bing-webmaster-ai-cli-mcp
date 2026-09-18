"""Credential application with an interface that can later host OAuth2."""

from __future__ import annotations

from typing import Any, Protocol

from .config import Settings


class AuthProvider(Protocol):
    def apply(self, params: dict[str, Any], headers: dict[str, str]) -> None: ...

    def secrets(self) -> frozenset[str]: ...


class ApiKeyAuth:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def apply(self, params: dict[str, Any], headers: dict[str, str]) -> None:
        # Microsoft documents the API key only as a query-string parameter; the header
        # form it documents (``Authorization: Bearer``) belongs to the separate OAuth2
        # flow, not to a key. So the credential is part of every URL this client builds,
        # and ``secrets`` exists to cover what that implies.
        params["apikey"] = self._api_key

    def secrets(self) -> frozenset[str]:
        """The literals this provider puts into a request, for the redaction boundary.

        Whoever applies a credential is the only one that knows what it is. Asking the
        provider instead of reaching into the settings keeps the two in step: a provider
        added later hides its own token by implementing this, and nothing else changes.
        """
        return frozenset({self._api_key})


def build_auth(settings: Settings) -> AuthProvider:
    if settings.api_key is None:
        from .errors import AuthFailed

        raise AuthFailed("BING_WM_API_KEY is required for Bing Webmaster API calls")
    return ApiKeyAuth(settings.api_key.get_secret_value())
