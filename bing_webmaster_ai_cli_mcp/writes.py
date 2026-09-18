"""Verified registry of Bing mutations. Registration never executes a write."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult

from .errors import InvalidRequest
from .ops._common import normalise_site
from .render import sanitize_text
from .urls import normalise_hostname, split_url, validate_http_url

MAX_BING_URL_BATCH = 500
MAX_CONTENT_BYTES = 10 * 1024 * 1024

COMPLEX_FIELDS = {
    "blocked_url": frozenset({"Date", "DaysToExpire", "EntityType", "RequestType", "Url"}),
    "crawl_settings": frozenset({"CrawlBoostAvailable", "CrawlBoostEnabled", "CrawlRate"}),
    "country_region_settings": frozenset({"Date", "TwoLetterIsoCountryCode", "Type", "Url"}),
    "site_move_settings": frozenset({"Date", "MoveScope", "MoveType", "SourceUrl", "TargetUrl"}),
    "site_role": frozenset(
        {
            "Date",
            "DelegatedCode",
            "DelegatedCodeOwnerEmail",
            "DelegatorEmail",
            "Email",
            "Expired",
            "Role",
            "Site",
            "VerificationSite",
        }
    ),
}


@dataclass(frozen=True)
class PreparedWrite:
    method: str
    body: dict[str, Any]
    summary: str
    cost: int
    quota_method: str | None = None


@dataclass(frozen=True)
class WriteOp:
    name: str
    method: str
    http: str = "POST"

    def prepare(self, args: dict[str, Any]) -> PreparedWrite:
        return _prepare(self, args)


_METHODS = {
    "add_blocked_url": "AddBlockedUrl",
    "add_connected_page": "AddConnectedPage",
    "add_country_region_settings": "AddCountryRegionSettings",
    "add_deep_link_block": "AddDeepLinkBlock",
    "add_page_preview_block": "AddPagePreviewBlock",
    "add_query_parameter": "AddQueryParameter",
    "add_site": "AddSite",
    "add_site_roles": "AddSiteRoles",
    "enable_disable_query_parameter": "EnableDisableQueryParameter",
    "fetch_url": "FetchUrl",
    "indexnow_submit": "IndexNow",
    "remove_blocked_url": "RemoveBlockedUrl",
    "remove_country_region_settings": "RemoveCountryRegionSettings",
    "remove_deep_link_block": "RemoveDeepLinkBlock",
    "remove_feed": "RemoveFeed",
    "remove_page_preview_block": "RemovePagePreviewBlock",
    "remove_query_parameter": "RemoveQueryParameter",
    "remove_site": "RemoveSite",
    "remove_site_role": "RemoveSiteRole",
    "save_crawl_settings": "SaveCrawlSettings",
    "submit_content": "SubmitContent",
    "submit_feed": "SubmitFeed",
    "submit_site_move": "SubmitSiteMove",
    "submit_url": "SubmitUrl",
    "submit_url_batch": "SubmitUrlBatch",
    "verify_site": "VerifySite",
}

WRITE_OPS = {name: WriteOp(name, method) for name, method in _METHODS.items()}


def prepare_write(name: str, args: dict[str, Any]) -> PreparedWrite:
    try:
        operation = WRITE_OPS[name]
    except KeyError as exc:
        raise InvalidRequest(f"unknown write operation: {name}") from exc
    return operation.prepare(args)


def _prepare(operation: WriteOp, args: dict[str, Any]) -> PreparedWrite:
    if operation.name == "indexnow_submit":
        return _prepare_indexnow(operation, args)
    site = normalise_site(_string(args, "site_url"))
    name = operation.name
    body: dict[str, Any] = {"siteUrl": site}
    summary: str
    cost = 1
    quota_method: str | None = None

    if name in {"add_site", "remove_site", "verify_site"}:
        summary = f"{name.replace('_', ' ')} {site}"
    elif name in {"submit_url", "fetch_url"}:
        url = _site_url(args, "url", site)
        body["url"] = url
        summary = f"{name.replace('_', ' ')} {url} for {site}"
        quota_method = "GetUrlSubmissionQuota" if name == "submit_url" else None
    elif name == "submit_url_batch":
        urls = _url_list(args, site)
        if len(urls) > MAX_BING_URL_BATCH:
            raise InvalidRequest(
                f"SubmitUrlBatch accepts at most {MAX_BING_URL_BATCH} URLs, got {len(urls)}"
            )
        body["urlList"] = urls
        cost = len(urls)
        quota_method = "GetUrlSubmissionQuota"
        summary = f"submit {cost} URLs to Bing for {site}"
    elif name in {"submit_feed", "remove_feed"}:
        feed_url = _site_url(args, "feed_url", site)
        body["feedUrl"] = feed_url
        summary = f"{name.replace('_', ' ')} {feed_url} for {site}"
    elif name == "add_site_roles":
        body.update(
            {
                "delegatedUrl": _absolute_url(args, "delegated_url"),
                "userEmail": _string(args, "user_email"),
                "authenticationCode": _string(args, "authentication_code"),
                "isAdministrator": _boolean(args, "is_administrator"),
                "isReadOnly": _boolean(args, "is_read_only"),
            }
        )
        summary = f"delegate access to {body['userEmail']} for {site}"
    elif name == "remove_site_role":
        body["siteRole"] = _complex(args, "site_role", "SiteRoles")
        summary = f"remove a delegated site role from {site}"
    elif name == "submit_site_move":
        move = _complex(args, "settings", "SiteMoveSettings")
        if "SourceUrl" in move:
            _ensure_site_url(str(move["SourceUrl"]), site)
        if "TargetUrl" in move:
            # A move points somewhere else by definition, so the target is only required
            # to be a real absolute URL, not to belong to the registered site.
            _ensure_absolute(str(move["TargetUrl"]), "TargetUrl")
        body["settings"] = move
        summary = f"submit a site move for {site}"
    elif name == "save_crawl_settings":
        body["crawlSettings"] = _complex(args, "crawl_settings", "CrawlSettings")
        summary = f"replace crawl settings for {site}"
    elif name == "submit_content":
        body.update(_content(args, site))
        quota_method = "GetContentSubmissionQuota"
        summary = f"submit content for {body['url']} to Bing"
    elif name in {"add_blocked_url", "remove_blocked_url"}:
        blocked = _complex(args, "blocked_url", "BlockedUrl")
        if "Url" in blocked:
            _ensure_site_url(str(blocked["Url"]), site)
        body["blockedUrl"] = blocked
        summary = f"{name.replace('_', ' ')} on {site}"
    elif name in {"add_query_parameter", "remove_query_parameter"}:
        body["queryParameter"] = _string(args, "query_parameter")
        summary = f"{name.replace('_', ' ')} {body['queryParameter']} on {site}"
    elif name == "enable_disable_query_parameter":
        body.update(
            {
                "queryParameter": _string(args, "query_parameter"),
                "isEnabled": _boolean(args, "is_enabled"),
            }
        )
        summary = f"set query parameter {body['queryParameter']} enabled={body['isEnabled']}"
    elif name in {"add_country_region_settings", "remove_country_region_settings"}:
        region = _complex(args, "settings", "CountryRegionSettings")
        if "Url" in region:
            _ensure_site_url(str(region["Url"]), site)
        body["settings"] = region
        summary = f"{name.replace('_', ' ')} on {site}"
    elif name == "add_page_preview_block":
        body.update({"url": _site_url(args, "url", site), "reason": _integer(args, "reason")})
        summary = f"block page preview for {body['url']}"
    elif name == "remove_page_preview_block":
        body["url"] = _site_url(args, "url", site)
        summary = f"remove page preview block for {body['url']}"
    elif name in {"add_deep_link_block", "remove_deep_link_block"}:
        body.update(
            {
                "market": _string(args, "market"),
                "searchUrl": _site_url(args, "search_url", site),
                "deepLinkUrl": _site_url(args, "deep_link_url", site),
            }
        )
        summary = f"{name.replace('_', ' ')} for {body['deepLinkUrl']}"
    elif name == "add_connected_page":
        body["masterUrl"] = _absolute_url(args, "master_url")
        summary = f"connect {body['masterUrl']} to {site}"
    else:  # pragma: no cover - the registry and coverage test make this unreachable
        raise InvalidRequest(f"write operation has no request builder: {name}")

    return PreparedWrite(
        operation.method,
        body,
        sanitize_text(summary),
        cost,
        quota_method,
    )


def _prepare_indexnow(operation: WriteOp, args: dict[str, Any]) -> PreparedWrite:
    from .ops.indexnow import validate_urls

    host = _string(args, "host")
    key = _string(args, "key")
    raw_urls = args.get("url_list")
    if not isinstance(raw_urls, list) or not all(isinstance(url, str) for url in raw_urls):
        raise InvalidRequest("url_list must be a list of URL strings")
    location = args.get("key_location")
    if location is not None and not isinstance(location, str):
        raise InvalidRequest("key_location must be a URL string")
    normalized_host, normalized_location, urls = validate_urls(host, key, raw_urls, location)
    return PreparedWrite(
        operation.method,
        {
            "host": normalized_host,
            "key": key,
            "keyLocation": normalized_location,
            "urlList": urls,
        },
        sanitize_text(
            f"submit {len(urls)} URLs for {normalized_host} through IndexNow "
            "(seven participating engines; not Google)"
        ),
        len(urls),
    )


def _string(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{name} must be a non-empty string")
    return value.strip()


def _boolean(args: dict[str, Any], name: str) -> bool:
    value = args.get(name)
    if not isinstance(value, bool):
        raise InvalidRequest(f"{name} must be a boolean")
    return value


def _integer(args: dict[str, Any], name: str) -> int:
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequest(f"{name} must be an integer enum value")
    return value


def _ensure_absolute(value: str, name: str) -> str:
    validate_http_url(value, name)
    return value


def _absolute_url(args: dict[str, Any], name: str) -> str:
    return _ensure_absolute(_string(args, name), name)


def _site_url(args: dict[str, Any], name: str, site: str) -> str:
    value = _absolute_url(args, name)
    _ensure_site_url(value, site)
    return value


def _authority(parsed: SplitResult) -> tuple[str | None, int | None]:
    """Host and non-default port.

    The scheme is deliberately not part of ownership: Bing shows legacy properties as
    ``http://`` and they still submit their live ``https://`` URLs. A port that is not
    the scheme default does identify a different site, so it is compared.
    """
    default_port = {"http": 80, "https": 443}.get(parsed.scheme)
    port = parsed.port if parsed.port not in (None, default_port) else None
    hostname = normalise_hostname(parsed.hostname or "", "url")
    return hostname, port


def _ensure_site_url(value: str, site: str) -> None:
    # Called directly with values lifted out of complex objects, so the scheme is checked
    # here too: matching only the hostname would let "ftp://a.example/p" through.
    _ensure_absolute(value, "url")
    candidate = split_url(value, "url")
    owner = split_url(site, "site_url")
    if _authority(candidate) != _authority(owner):
        raise InvalidRequest(f"URL {value!r} does not belong to site {site!r}")
    owner_path = owner.path.rstrip("/")
    if (
        owner_path
        and not candidate.path.startswith(f"{owner_path}/")
        and candidate.path != owner_path
    ):
        raise InvalidRequest(f"URL {value!r} is outside the registered site path {owner_path!r}")


def _url_list(args: dict[str, Any], site: str) -> list[str]:
    raw = args.get("url_list")
    if not isinstance(raw, list) or not raw:
        raise InvalidRequest("url_list must be a non-empty list")
    return [_site_url({"url": value}, "url", site) for value in raw]


def _complex(args: dict[str, Any], argument: str, type_name: str) -> dict[str, Any]:
    value = args.get(argument)
    if not isinstance(value, dict) or not value:
        raise InvalidRequest(f"{argument} must be a non-empty object")
    key = {
        "BlockedUrl": "blocked_url",
        "CrawlSettings": "crawl_settings",
        "CountryRegionSettings": "country_region_settings",
        "SiteMoveSettings": "site_move_settings",
        "SiteRoles": "site_role",
    }[type_name]
    unknown = set(value) - COMPLEX_FIELDS[key] - {"__type"}
    if unknown:
        raise InvalidRequest(f"unknown {type_name} fields: {sorted(unknown)}")
    result = dict(value)
    result["__type"] = f"{type_name}:#Microsoft.Bing.Webmaster.Api"
    return result


def _content(args: dict[str, Any], site: str) -> dict[str, Any]:
    url = _site_url(args, "url", site)
    http_message, http_size = _validated_base64(args, "http_message", allow_empty=False)
    structured_data, structured_size = _validated_base64(args, "structured_data", allow_empty=True)
    if http_size + structured_size > MAX_CONTENT_BYTES:
        raise InvalidRequest("SubmitContent exceeds the documented 10 MB payload limit")
    dynamic_serving = _integer(args, "dynamic_serving")
    if dynamic_serving not in range(6):
        raise InvalidRequest("dynamic_serving must be an integer from 0 through 5")
    return {
        "url": url,
        "httpMessage": http_message,
        "structuredData": structured_data,
        "dynamicServing": dynamic_serving,
    }


def _validated_base64(args: dict[str, Any], name: str, *, allow_empty: bool) -> tuple[str, int]:
    value = args.get(name)
    if value == "" and allow_empty:
        return "", 0
    if not isinstance(value, str) or not value:
        raise InvalidRequest(f"{name} must be a base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRequest(f"{name} must be valid base64") from exc
    return value, len(decoded)
