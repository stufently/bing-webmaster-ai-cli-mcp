"""Environment-first settings. Secret values never appear in representations."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from .errors import AuthFailed, InvalidRequest, PolicyDenied
from .urls import ensure_unambiguous_path, normalise_hostname

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _default_state_dir() -> Path:
    base = Path.home() / ".local" / "state"
    current = base / "bing-webmaster-ai-cli-mcp"
    # The project was called bing-webmaster-mcp until 2026-09-18. An install that
    # predates the rename keeps its plans, audit log and daily write counters
    # there; starting over in an empty directory would silently reset the quota.
    legacy = base / "bing-webmaster-mcp"
    if not current.exists() and legacy.is_dir():
        return legacy
    return current


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BING_WM_", extra="ignore")

    api_key: SecretStr | None = None
    base_url: str = "https://ssl.bing.com/webmaster/api.svc/json"
    calls_per_second: float = Field(default=5.0, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    plan_ttl_seconds: int = Field(default=900, gt=0)
    state_dir: Path = Field(default_factory=_default_state_dir)
    allow_writes: bool = True
    denied_sites: tuple[str, ...] = ()
    max_writes_per_day: int | None = Field(default=None, gt=0)
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8765, ge=1, le=65535)
    http_bearer_token: SecretStr | None = None

    @field_validator("denied_sites")
    @classmethod
    def _denied_sites_must_name_a_host(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unusable = [site for site in value if _policy_key(site) is None]
        if unusable:
            raise ValueError(f"entries name no host: {unusable}")
        return value

    @classmethod
    def load(cls, *, require_api_key: bool = True) -> Settings:
        try:
            settings = cls()
        except ValidationError as exc:
            raise InvalidRequest(
                "invalid BING_WM_* configuration",
                suggestion="check the named environment variables",
                details={"fields": [str(error["loc"][0]) for error in exc.errors()]},
            ) from exc
        except SettingsError as exc:
            # Raised before validation when a list/dict-valued variable is not JSON.
            raise InvalidRequest(
                "invalid BING_WM_* configuration",
                suggestion="list-valued variables must be JSON, e.g. '[\"https://a.example\"]'",
                details={"error": str(exc)},
            ) from exc
        if require_api_key and settings.api_key is None:
            raise AuthFailed(
                "BING_WM_API_KEY is not set",
                suggestion="create a key in Bing Webmaster Tools -> Settings -> API Access",
            )
        return settings

    def check_writes_allowed(self) -> None:
        """Refuse a direct write when the operator has turned the write path off.

        This is the only switch between the one-step path and the reviewed one. It is
        checked again inside the apply boundary rather than only where a tool is
        advertised: an MCP client may hold a tool list from before the setting changed.
        """
        if not self.allow_writes:
            raise PolicyDenied(
                "writing to Bing is disabled by BING_WM_ALLOW_WRITES=false",
                suggestion=(
                    "plan the change instead and apply it with `bing-wm plan apply`, "
                    "or set BING_WM_ALLOW_WRITES=true"
                ),
            )

    def check_site_allowed(self, site_url: str) -> None:
        key = _policy_key(site_url, strict=True)
        if key is None:
            raise InvalidRequest(f"invalid site URL for policy check: {site_url!r}")
        host, path = key
        for denied in self.denied_sites:
            entry = _policy_key(denied)
            if entry is None or entry[0] != host:
                continue
            denied_path = entry[1]
            if not denied_path or path == denied_path or path.startswith(f"{denied_path}/"):
                raise PolicyDenied(
                    f"{site_url} is in BING_WM_DENIED_SITES",
                    suggestion="remove it from the denylist only after reviewing the policy",
                )


def _policy_key(site_url: str, *, strict: bool = False) -> tuple[str, str] | None:
    """Reduce a site to the (host, path) pair the denylist matches on.

    Matching on the raw string lets http/https, an explicit :443, a trailing dot or a
    change of case walk straight past a denied site, so the comparison uses the parsed
    host instead. Entries may be written either as a URL or as a bare hostname; a denied
    entry without a path covers every path on that host. ``None`` means the value names
    no host at all, which is refused for a denylist entry and cannot match anything.
    """
    candidate = site_url.strip().rstrip("/")
    if _SCHEME.match(candidate) is None and not candidate.startswith("//"):
        # Testing for "//" anywhere would read "a.example/shop//eu" as an authority URL
        # and reduce it to nothing a real site could match.
        candidate = f"//{candidate}"
    try:
        parsed = urlsplit(candidate)
        host, port = parsed.hostname, parsed.port
        if host:
            host = normalise_hostname(host, "site policy")
        ensure_unambiguous_path(parsed.path, "site URL")
    except InvalidRequest:
        if strict:
            raise
        return None
    except ValueError:
        if strict:
            raise InvalidRequest(f"invalid site URL for policy check: {site_url!r}") from None
        return None
    if not host:
        return None
    authority = host if port in (None, 80, 443) else f"{host}:{port}"
    return authority, parsed.path.rstrip("/")
