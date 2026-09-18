from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from fakes import bing_transport, fake_settings

from bing_webmaster_ai_cli_mcp.client import BingClient
from bing_webmaster_ai_cli_mcp.ops import (
    blocking,
    crawl,
    geo,
    keywords,
    links,
    params,
    sitemaps,
    sites,
    submission,
    traffic,
)
from bing_webmaster_ai_cli_mcp.ops._common import normalise_site
from bing_webmaster_ai_cli_mcp.render import REDACTED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com"),
        ("https://example.com/", "https://example.com"),
        ("http://example.com", "http://example.com"),
        ("  https://example.com  ", "https://example.com"),
    ],
)
def test_site_normalisation(raw: str, expected: str) -> None:
    assert normalise_site(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://",
        "http://",
        "ftp://example.com",
        "FTP://example.com",
        "mailto:a@b.example",
        "[abc",
        "https://example.com?a=1",
        "https://a b.example",
        "https://-bad.example",
        "https://a_example",
        "https://a.example/shop/../admin",
        "https://a.example/shop/%2e%2e/admin",
    ],
)
def test_unusable_site_urls_are_rejected(raw: str) -> None:
    from bing_webmaster_ai_cli_mcp.errors import InvalidRequest

    with pytest.raises(InvalidRequest):
        normalise_site(raw)


def test_uppercase_scheme_is_normalised_not_prefixed() -> None:
    assert normalise_site("HTTPS://Example.com") == "https://example.com"


SITE_READS = [
    (sites.site_roles, "GetSiteRoles", ()),
    (sites.site_moves, "GetSiteMoves", ()),
    (traffic.query_stats, "GetQueryStats", ()),
    (traffic.query_traffic_stats, "GetQueryTrafficStats", ("shoes",)),
    (traffic.query_page_stats, "GetQueryPageStats", ("shoes",)),
    (
        traffic.query_page_detail_stats,
        "GetQueryPageDetailStats",
        ("shoes", "https://a.example/p"),
    ),
    (traffic.page_stats, "GetPageStats", ()),
    (traffic.page_query_stats, "GetPageQueryStats", ("https://a.example/p",)),
    (traffic.rank_and_traffic_stats, "GetRankAndTrafficStats", ()),
    (crawl.url_info, "GetUrlInfo", ("https://a.example/p",)),
    (crawl.url_traffic_info, "GetUrlTrafficInfo", ("https://a.example/p",)),
    (crawl.children_url_traffic_info, "GetChildrenUrlTrafficInfo", ("https://a.example/d",)),
    (crawl.crawl_stats, "GetCrawlStats", ()),
    (crawl.crawl_issues, "GetCrawlIssues", ()),
    (crawl.crawl_settings, "GetCrawlSettings", ()),
    (crawl.fetched_urls, "GetFetchedUrls", ()),
    (crawl.fetched_url_details, "GetFetchedUrlDetails", ("https://a.example/p",)),
    (submission.url_submission_quota, "GetUrlSubmissionQuota", ()),
    (submission.content_submission_quota, "GetContentSubmissionQuota", ()),
    (sitemaps.feeds, "GetFeeds", ()),
    (sitemaps.feed_details, "GetFeedDetails", ("https://a.example/sitemap.xml",)),
    (blocking.blocked_urls, "GetBlockedUrls", ()),
    (blocking.page_preview_blocks, "GetActivePagePreviewBlocks", ()),
    (blocking.deep_link_blocks, "GetDeepLinkBlocks", ()),
    (params.query_parameters, "GetQueryParameters", ()),
    (geo.country_region_settings, "GetCountryRegionSettings", ()),
    (links.link_counts, "GetLinkCounts", ()),
    (links.url_links, "GetUrlLinks", ("https://a.example/p",)),
    (links.connected_pages, "GetConnectedPages", ()),
]


@pytest.mark.parametrize(("function", "method", "args"), SITE_READS)
async def test_site_scoped_reads_route_exactly(
    tmp_path, function, method: str, args: tuple
) -> None:
    transport = bing_transport({method: []})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        await function(client, "a.example/", *args)
    request = transport.calls[0]
    assert request.url.path.endswith(f"/{method}")
    if request.method == "GET":
        assert request.url.params["siteUrl"] == "https://a.example"
    else:
        assert b'"siteUrl":"https://a.example"' in request.content


async def test_get_user_sites_has_no_site_parameter(tmp_path) -> None:
    transport = bing_transport({"GetUserSites": []})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        assert await sites.list_sites(client) == []
    assert "siteUrl" not in transport.calls[0].url.params


async def test_children_url_info_is_a_post_with_verified_default_filters(tmp_path) -> None:
    transport = bing_transport({"GetChildrenUrlInfo": []})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        await crawl.children_url_info(client, "a.example", "https://a.example/d")
    request = transport.calls[0]
    assert request.method == "POST"
    assert b'"filterProperties"' in request.content
    assert all(f'"{name}":0'.encode() in request.content for name in crawl.FILTER_FIELDS)


async def test_exact_special_parameter_names(tmp_path) -> None:
    routes = {"GetSiteRoles": [], "GetUrlLinks": []}
    transport = bing_transport(routes)
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        await sites.site_roles(client, "a.example", include_all_subdomains=True)
        await links.url_links(client, "a.example", "https://a.example/p", page=2)
    assert transport.calls[0].url.params["includeAllSubdomains"] == "true"
    assert transport.calls[1].url.params["link"] == "https://a.example/p"
    assert transport.calls[1].url.params["page"] == "2"


@pytest.mark.parametrize(
    ("function", "method", "args"),
    [
        (keywords.keyword, "GetKeyword", (date(2026, 8, 1), date(2026, 8, 25))),
        (keywords.keyword_stats, "GetKeywordStats", ()),
        (
            keywords.related_keywords,
            "GetRelatedKeywords",
            (date(2026, 8, 1), date(2026, 8, 25)),
        ),
    ],
)
async def test_keyword_reads_are_standalone(tmp_path, function, method: str, args: tuple) -> None:
    transport = bing_transport({method: []})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        await function(client, "shoes", "US", "en-US", *args)
    request = transport.calls[0]
    assert "siteUrl" not in request.url.params
    assert request.url.params["q"] == "shoes"


async def test_read_results_are_sanitized(tmp_path) -> None:
    transport = bing_transport({"GetCrawlIssues": [{"Url": "x\u202eevil", "Message": "m\x00"}]})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result: dict[str, Any] = await crawl.crawl_issues(client, "a.example")
    row = result["issues"][0]
    assert row["Url"] == {"value": "xevil", "untrusted": True}
    assert row["Message"] == {"value": "m", "untrusted": True}


@pytest.mark.parametrize(
    ("issues", "expected"),
    [
        (0, ["none"]),
        (1, ["redirect_301"]),
        (2, ["redirect_302"]),
        (4, ["http_4xx"]),
        (8, ["http_5xx"]),
        (16, ["blocked_by_robots_txt"]),
        (32, ["contains_malware"]),
        (64, ["important_url_blocked_by_robots_txt"]),
        (128, ["dns_errors"]),
        (256, ["timeout_errors"]),
        (12, ["http_4xx", "http_5xx"]),
        ("4", ["http_4xx"]),
        ("Code4xx, BlockedByRobotsTxt", ["http_4xx", "blocked_by_robots_txt"]),
    ],
)
def test_documented_issue_flags_map_to_categories(issues: Any, expected: list[str]) -> None:
    categories, unknown = crawl.categorise_issue(issues)
    assert sorted(categories) == sorted(expected)
    assert unknown == 0


@pytest.mark.parametrize("issues", [None, "unheard-of", -1, True, {"a": 1}])
def test_an_unreadable_issue_field_becomes_other_and_is_never_dropped(issues: Any) -> None:
    assert crawl.categorise_issue(issues) == (["other"], 0)


@pytest.mark.parametrize(
    ("issues", "http_code", "expected"),
    [
        (4, 404, ["http_4xx", "http_404"]),
        (4, 403, ["http_4xx", "http_403"]),
        (4, 410, ["http_4xx"]),
        (4, "404", ["http_4xx"]),
        (4, None, ["http_4xx"]),
        (4, True, ["http_4xx"]),
        (4 | 16, 404, ["http_4xx", "blocked_by_robots_txt", "http_404"]),
        # No Code4xx flag, so the status code refines nothing: Bing did not call it a 4xx.
        (8, 404, ["http_5xx"]),
        (0, 404, ["none"]),
    ],
)
def test_a_4xx_row_is_split_into_404_and_403_by_its_own_http_code(
    issues: Any, http_code: Any, expected: list[str]
) -> None:
    categories, unknown = crawl.categorise_issue(issues, http_code)
    assert sorted(categories) == sorted(expected)
    assert unknown == 0


def test_the_404_and_403_categories_are_a_subset_of_http_4xx_not_a_replacement() -> None:
    rows = [
        {"Url": "https://a.example/a", "HttpCode": 404, "Issues": 4},
        {"Url": "https://a.example/b", "HttpCode": 404, "Issues": 4},
        {"Url": "https://a.example/c", "HttpCode": 403, "Issues": 4},
        {"Url": "https://a.example/d", "HttpCode": 410, "Issues": 4},
    ]
    summary = crawl.summarise_crawl_issues(rows)
    assert summary["categories"] == {"http_403": 1, "http_404": 2, "http_4xx": 4}
    assert summary["issues"][0]["categories"] == ["http_4xx", "http_404"]
    assert summary["issues"][3]["categories"] == ["http_4xx"]


def test_an_undocumented_bit_is_kept_as_other_alongside_the_known_ones() -> None:
    categories, unknown = crawl.categorise_issue(4 | 1024)
    assert categories == ["http_4xx", "other"]
    assert unknown == 1024


def test_crawl_issue_summary_counts_categories_and_http_codes_without_losing_raw_fields() -> None:
    rows = [
        {"Url": "https://a.example/a", "HttpCode": 404, "Issues": 4, "InLinks": 3},
        {"Url": "https://a.example/b", "HttpCode": 403, "Issues": 4},
        {"Url": "https://a.example/c", "HttpCode": 200, "Issues": 16},
        {"Url": "https://a.example/d", "HttpCode": 0, "Issues": 128 | 2048},
    ]
    summary = crawl.summarise_crawl_issues(rows)
    assert summary["total"] == 4
    assert summary["categories"] == {
        "blocked_by_robots_txt": 1,
        "dns_errors": 1,
        "http_403": 1,
        "http_404": 1,
        "http_4xx": 2,
        "other": 1,
    }
    assert summary["http_codes"] == {"0": 1, "200": 1, "403": 1, "404": 1}
    assert summary["issues"][0]["InLinks"] == 3
    assert summary["issues"][0]["Issues"] == 4
    assert summary["issues"][0]["Url"] == "https://a.example/a"
    assert summary["issues"][3]["unknown_issue_bits"] == 2048


def test_a_site_with_no_crawl_issues_summarises_to_zero() -> None:
    assert crawl.summarise_crawl_issues([]) == {
        "total": 0,
        "categories": {},
        "http_codes": {},
        "issues": [],
    }


def test_an_unexpected_crawl_issue_payload_is_returned_rather_than_discarded() -> None:
    assert crawl.summarise_crawl_issues({"d": "surprise"})["issues"] == {"d": "surprise"}


def test_site_url_with_an_unparsable_port_is_rejected() -> None:
    from bing_webmaster_ai_cli_mcp.errors import InvalidRequest

    with pytest.raises(InvalidRequest):
        normalise_site("https://example.com:notaport")


def test_ipv6_site_keeps_its_brackets() -> None:
    assert normalise_site("https://[2001:db8::1]/") == "https://[2001:db8::1]"
    assert normalise_site("https://[2001:db8::1]:8443/p") == "https://[2001:db8::1]:8443/p"


def test_bare_host_with_a_port_is_not_read_as_a_scheme() -> None:
    assert normalise_site("example.com:8443") == "https://example.com:8443"


SITE_WITH_SECRETS = {
    "Url": "https://a.example/",
    "IsVerified": True,
    "AuthenticationCode": "auth-secret",
    "DnsVerificationCode": "dns-secret",
}


async def test_listing_sites_never_returns_verification_secrets_by_default(tmp_path) -> None:
    """Enumerating sites does not need the proofs that let anyone claim them."""
    transport = bing_transport({"GetUserSites": [dict(SITE_WITH_SECRETS)]})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await sites.list_sites(client)
    serialised = json.dumps(result)
    assert "auth-secret" not in serialised
    assert "dns-secret" not in serialised
    assert result[0]["AuthenticationCode"] == REDACTED
    assert result[0]["DnsVerificationCode"] == REDACTED
    assert result[0]["IsVerified"] is True


async def test_an_operator_who_asks_by_name_still_gets_the_codes(tmp_path) -> None:
    transport = bing_transport({"GetUserSites": [dict(SITE_WITH_SECRETS)]})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await sites.list_sites(client, reveal_secrets=True)
    assert result[0]["AuthenticationCode"] == "auth-secret"
    assert result[0]["DnsVerificationCode"] == "dns-secret"


async def test_showing_one_site_redacts_it_too(tmp_path) -> None:
    transport = bing_transport({"GetUserSites": [dict(SITE_WITH_SECRETS)]})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await sites.show_site(client, "a.example")
    assert result is not None
    assert result["AuthenticationCode"] == REDACTED


async def test_a_delegation_code_on_a_site_role_is_redacted(tmp_path) -> None:
    rows = [{"Email": "a@b.example", "DelegatedCode": "delegation-secret", "Role": 0}]
    transport = bing_transport({"GetSiteRoles": rows})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await sites.site_roles(client, "a.example")
    assert "delegation-secret" not in json.dumps(result)
    assert result[0]["DelegatedCode"] == REDACTED
    assert result[0]["Email"] == "a@b.example"


async def test_redaction_covers_every_read_not_only_the_two_that_carry_codes(tmp_path) -> None:
    """The boundary is the fetch helper, so a method that starts leaking is covered."""
    transport = bing_transport({"GetCrawlSettings": {"AuthenticationCode": "surprise"}})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await crawl.crawl_settings(client, "a.example")
    assert result == {"AuthenticationCode": REDACTED}


async def test_url_info_says_when_bing_reported_no_http_status(tmp_path) -> None:
    """HttpStatus 0 is 'not reported'; IsPage true must not read as a working page."""
    row = {"Url": "https://a.example/gone", "HttpStatus": 0, "IsPage": True}
    transport = bing_transport({"GetUrlInfo": row})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await crawl.url_info(client, "a.example", "https://a.example/gone")
    assert result["http_status_reported"] is False
    assert "not reported" in result["http_status_note"]
    assert result["HttpStatus"] == 0
    assert result["IsPage"] is True


@pytest.mark.parametrize("status", [200, 404, 500])
async def test_url_info_marks_a_status_bing_did_report(tmp_path, status: int) -> None:
    transport = bing_transport({"GetUrlInfo": {"HttpStatus": status}})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await crawl.url_info(client, "a.example", "https://a.example/p")
    assert result["http_status_reported"] is True
    assert "http_status_note" not in result


async def test_a_row_without_an_http_status_field_is_left_alone(tmp_path) -> None:
    transport = bing_transport({"GetUrlInfo": {"IsPage": True}})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await crawl.url_info(client, "a.example", "https://a.example/p")
    assert result == {"IsPage": True}


async def test_every_child_url_row_is_labelled(tmp_path) -> None:
    rows = [{"HttpStatus": 0, "IsPage": True}, {"HttpStatus": 200, "IsPage": True}]
    transport = bing_transport({"GetChildrenUrlInfo": rows})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await crawl.children_url_info(client, "a.example", "https://a.example/d")
    assert [row["http_status_reported"] for row in result] == [False, True]


@pytest.mark.parametrize("status", [True, "0", None])
def test_an_unreadable_http_status_is_not_reported_either(status: Any) -> None:
    annotated = crawl.annotate_http_status({"HttpStatus": status})
    assert annotated["http_status_reported"] is False
    assert annotated["HttpStatus"] == status
