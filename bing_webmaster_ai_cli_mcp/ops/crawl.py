from __future__ import annotations

from collections import Counter
from typing import Any

from ..client import BingClient
from ._common import fetch, normalise_site, row_read, single_record

FILTER_FIELDS = (
    "CrawlDateFilter",
    "DiscoveredDateFilter",
    "DocFlagsFilters",
    "HttpCodeFilters",
)

# Microsoft's UrlWithCrawlIssues.CrawlIssues flags enum, transcribed from its property
# page (fetched 2026-09-01). The bits and the Microsoft names are theirs; only the
# snake_case category label on the right belongs to this project. Bing has no "noindex"
# crawl-issue bit, so no category invents one; nothing in this API reports a robots meta
# tag or X-Robots-Tag header at all.
CRAWL_ISSUE_FLAGS: tuple[tuple[int, str, str], ...] = (
    (1, "Code301", "redirect_301"),
    (2, "Code302", "redirect_302"),
    (4, "Code4xx", "http_4xx"),
    (8, "Code5xx", "http_5xx"),
    (16, "BlockedByRobotsTxt", "blocked_by_robots_txt"),
    (32, "ContainsMalware", "contains_malware"),
    (64, "ImportantUrlBlockedByRobotsTxt", "important_url_blocked_by_robots_txt"),
    (128, "DnsErrors", "dns_errors"),
    (256, "TimeOutErrors", "timeout_errors"),
)
NO_ISSUE_CATEGORY = "none"
OTHER_CATEGORY = "other"
_CODE_4XX_BIT = 4
# Microsoft's enum stops at "Code4xx", but the exact status code is on the same row in
# ``HttpCode``. A 404 and a 403 are different problems - a dead page versus one the
# server refuses to serve - so a row already flagged Code4xx is split by the code Bing
# itself reported. This derives nothing: without the flag, or with any other code, no
# refinement is added.
CRAWL_ISSUE_HTTP_CODE_CATEGORIES: dict[int, str] = {403: "http_403", 404: "http_404"}
CRAWL_ISSUE_CATEGORIES = tuple(
    [category for _bit, _name, category in CRAWL_ISSUE_FLAGS]
    + sorted(CRAWL_ISSUE_HTTP_CODE_CATEGORIES.values())
    + [NO_ISSUE_CATEGORY, OTHER_CATEGORY]
)
_KNOWN_BITS = sum(bit for bit, _name, _category in CRAWL_ISSUE_FLAGS)
_BY_NAME = {name.casefold(): bit for bit, name, _category in CRAWL_ISSUE_FLAGS}
_BY_NAME["none"] = 0


def _issue_bits(value: Any) -> int | None:
    """Read Bing's ``Issues`` field as a bitmask, or ``None`` if it is not one.

    The JSON endpoint serializes the flags enum as a number, which is the only shape
    seen in practice. A numeric string and a comma-separated list of Microsoft's own
    member names are accepted too, because misreading the field would silently drop
    every issue on the row rather than fail loudly.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        number = int(text)
    except ValueError:
        pass
    else:
        return number if number >= 0 else None
    bits = 0
    for token in text.split(","):
        bit = _BY_NAME.get(token.strip().casefold())
        if bit is None:
            return None
        bits |= bit
    return bits


def _status_category(bits: int, http_code: Any) -> str | None:
    """The 404/403 refinement of a ``Code4xx`` row, or ``None`` when it does not apply.

    ``bool`` is excluded before the lookup because it is an ``int`` in Python and
    ``True`` would otherwise never match anything but still read as a status code.
    """
    if not bits & _CODE_4XX_BIT or isinstance(http_code, bool) or not isinstance(http_code, int):
        return None
    return CRAWL_ISSUE_HTTP_CODE_CATEGORIES.get(http_code)


def categorise_issue(value: Any, http_code: Any = None) -> tuple[list[str], int]:
    """Categories for one ``Issues`` value, plus any bits Microsoft has not documented.

    Nothing is dropped: an unreadable field, an unknown bit and a row with no flags all
    land in a category of their own rather than vanishing from the counts. When the row
    carries the ``Code4xx`` flag, its ``HttpCode`` adds ``http_404`` or ``http_403``
    beside the broad ``http_4xx`` - beside it, not instead of it, so a caller counting
    4xx rows keeps counting all of them.
    """
    bits = _issue_bits(value)
    if bits is None:
        return [OTHER_CATEGORY], 0
    if bits == 0:
        return [NO_ISSUE_CATEGORY], 0
    categories = [category for bit, _name, category in CRAWL_ISSUE_FLAGS if bits & bit]
    status = _status_category(bits, http_code)
    if status is not None:
        categories.append(status)
    unknown = bits & ~_KNOWN_BITS
    if unknown:
        categories.append(OTHER_CATEGORY)
    return categories, unknown


def summarise_crawl_issues(rows: Any) -> dict[str, Any]:
    """Count Bing's crawl issues by category without discarding the raw rows.

    Every field Bing sent is passed through untouched; the derived ``categories`` and
    ``unknown_issue_bits`` keys are lower-case, so they cannot collide with Microsoft's
    PascalCase properties.
    """
    if not isinstance(rows, list):
        return {"total": 0, "categories": {}, "http_codes": {}, "issues": rows}
    categories: Counter[str] = Counter()
    http_codes: Counter[int] = Counter()
    issues: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            categories[OTHER_CATEGORY] += 1
            issues.append(row)
            continue
        code = row.get("HttpCode")
        names, unknown = categorise_issue(row.get("Issues"), code)
        categories.update(names)
        if isinstance(code, int) and not isinstance(code, bool):
            http_codes[code] += 1
        entry: dict[str, Any] = {**row, "categories": names}
        if unknown:
            entry["unknown_issue_bits"] = unknown
        issues.append(entry)
    return {
        "total": len(rows),
        "categories": dict(sorted(categories.items())),
        "http_codes": {str(code): count for code, count in sorted(http_codes.items())},
        "issues": issues,
    }


HTTP_STATUS_NOT_REPORTED_NOTE = (
    "Bing reports no HTTP status for this URL: HttpStatus 0 is 'not reported', not a "
    "status code, and it is not 200. The other fields describe what Bing recorded at "
    "LastCrawledDate and say nothing about the URL now - IsPage true with HttpStatus 0 "
    "has been observed on a URL that returns 404 today. Fetch the URL to learn its real "
    "status; this API will not tell you."
)


def annotate_http_status(row: Any) -> Any:
    """Say whether Bing actually reported a status, so a 0 cannot be read as one.

    ``UrlInfo.HttpStatus`` is an ``Int32`` with no documented sentinel, and Bing returns
    0 for a URL it has crawl history for but no status on. Zero is not a status code, so
    the flag is always present and the row keeps every field Bing sent: the fix for an
    unreadable number is to label it, not to delete it and leave the caller guessing.
    """
    if not isinstance(row, dict) or "HttpStatus" not in row:
        return row
    status = row["HttpStatus"]
    reported = isinstance(status, int) and not isinstance(status, bool) and status != 0
    annotated: dict[str, Any] = {**row, "http_status_reported": reported}
    if not reported:
        annotated["http_status_note"] = HTTP_STATUS_NOT_REPORTED_NOTE
    return annotated


@single_record
async def url_info(client: BingClient, site_url: str, url: str) -> dict[str, Any]:
    row = await fetch(client, "GetUrlInfo", {"siteUrl": normalise_site(site_url), "url": url})
    return annotate_http_status(row)


@single_record
async def url_traffic_info(client: BingClient, site_url: str, url: str) -> dict[str, Any]:
    return await fetch(
        client, "GetUrlTrafficInfo", {"siteUrl": normalise_site(site_url), "url": url}
    )


@row_read
async def children_url_info(
    client: BingClient,
    site_url: str,
    url: str,
    page: int = 0,
    filter_properties: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    filters = dict.fromkeys(FILTER_FIELDS, 0)
    if filter_properties:
        unknown = set(filter_properties) - set(FILTER_FIELDS)
        if unknown:
            from ..errors import InvalidRequest

            raise InvalidRequest(f"unknown FilterProperties fields: {sorted(unknown)}")
        filters.update(filter_properties)
    body = {
        "siteUrl": normalise_site(site_url),
        "url": url,
        "page": page,
        "filterProperties": filters,
    }
    rows = await fetch(client, "GetChildrenUrlInfo", body=body)
    if not isinstance(rows, list):
        return rows
    return [annotate_http_status(row) for row in rows]


@row_read
async def children_url_traffic_info(
    client: BingClient, site_url: str, url: str, page: int = 0
) -> list[dict[str, Any]]:
    return await fetch(
        client,
        "GetChildrenUrlTrafficInfo",
        {"siteUrl": normalise_site(site_url), "url": url, "page": page},
    )


@row_read
async def crawl_stats(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetCrawlStats", {"siteUrl": normalise_site(site_url)})


@row_read
async def crawl_issues(client: BingClient, site_url: str) -> dict[str, Any]:
    """Crawl issues with per-category counts, keeping every raw field Bing returned."""
    rows = await fetch(client, "GetCrawlIssues", {"siteUrl": normalise_site(site_url)})
    return summarise_crawl_issues(rows)


@single_record
async def crawl_settings(client: BingClient, site_url: str) -> dict[str, Any]:
    return await fetch(client, "GetCrawlSettings", {"siteUrl": normalise_site(site_url)})


@row_read
async def fetched_urls(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetFetchedUrls", {"siteUrl": normalise_site(site_url)})


@single_record
async def fetched_url_details(client: BingClient, site_url: str, url: str) -> dict[str, Any]:
    return await fetch(
        client, "GetFetchedUrlDetails", {"siteUrl": normalise_site(site_url), "url": url}
    )
