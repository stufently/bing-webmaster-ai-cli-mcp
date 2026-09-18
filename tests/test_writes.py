from __future__ import annotations

import base64

import pytest

from bing_webmaster_ai_cli_mcp.errors import InvalidRequest
from bing_webmaster_ai_cli_mcp.writes import WRITE_OPS, prepare_write

SITE = "https://a.example"

EXPECTED = {
    "add_blocked_url",
    "add_connected_page",
    "add_country_region_settings",
    "add_deep_link_block",
    "add_page_preview_block",
    "add_query_parameter",
    "add_site",
    "add_site_roles",
    "enable_disable_query_parameter",
    "fetch_url",
    "indexnow_submit",
    "remove_blocked_url",
    "remove_country_region_settings",
    "remove_deep_link_block",
    "remove_feed",
    "remove_page_preview_block",
    "remove_query_parameter",
    "remove_site",
    "remove_site_role",
    "save_crawl_settings",
    "submit_content",
    "submit_feed",
    "submit_site_move",
    "submit_url",
    "submit_url_batch",
    "verify_site",
}


def sample_args(name: str) -> dict:
    encoded = base64.b64encode(b"HTTP/1.1 200 OK\r\n\r\nhello").decode()
    extras = {
        "add_blocked_url": {
            "blocked_url": {"Url": f"{SITE}/secret", "EntityType": 0, "RequestType": 1}
        },
        "add_connected_page": {"master_url": "https://social.example/profile"},
        "add_country_region_settings": {
            "settings": {"TwoLetterIsoCountryCode": "TH", "Type": 0, "Url": SITE}
        },
        "add_deep_link_block": {
            "market": "en-US",
            "search_url": f"{SITE}/p",
            "deep_link_url": f"{SITE}/q",
        },
        "add_page_preview_block": {"url": f"{SITE}/p", "reason": 0},
        "add_query_parameter": {"query_parameter": "utm_source"},
        "add_site": {},
        "add_site_roles": {
            "delegated_url": SITE,
            "user_email": "a@b.example",
            "authentication_code": "abc",
            "is_administrator": False,
            "is_read_only": True,
        },
        "enable_disable_query_parameter": {"query_parameter": "utm_source", "is_enabled": True},
        "fetch_url": {"url": f"{SITE}/p"},
        "indexnow_submit": {
            "host": "a.example",
            "key": "0123456789abcdef0123456789abcdef",
            "url_list": [f"{SITE}/p"],
        },
        "remove_blocked_url": {
            "blocked_url": {"Url": f"{SITE}/secret", "EntityType": 0, "RequestType": 0}
        },
        "remove_country_region_settings": {
            "settings": {"TwoLetterIsoCountryCode": "TH", "Type": 0, "Url": SITE}
        },
        "remove_deep_link_block": {
            "market": "en-US",
            "search_url": f"{SITE}/p",
            "deep_link_url": f"{SITE}/q",
        },
        "remove_feed": {"feed_url": f"{SITE}/sitemap.xml"},
        "remove_page_preview_block": {"url": f"{SITE}/p"},
        "remove_query_parameter": {"query_parameter": "utm_source"},
        "remove_site": {},
        "remove_site_role": {"site_role": {"Email": "a@b.example", "Site": SITE, "Role": 2}},
        "save_crawl_settings": {
            "crawl_settings": {"CrawlBoostEnabled": False, "CrawlRate": [1] * 24}
        },
        "submit_content": {
            "url": f"{SITE}/p",
            "http_message": encoded,
            "structured_data": "",
            "dynamic_serving": 0,
        },
        "submit_feed": {"feed_url": f"{SITE}/sitemap.xml"},
        "submit_site_move": {
            "settings": {
                "MoveScope": 0,
                "MoveType": 0,
                "SourceUrl": SITE,
                "TargetUrl": "https://b.example",
            }
        },
        "submit_url": {"url": f"{SITE}/p"},
        "submit_url_batch": {"url_list": [f"{SITE}/p"]},
        "verify_site": {},
    }
    if name == "indexnow_submit":
        return extras[name]
    return {"site_url": SITE, **extras[name]}


def test_registry_is_the_complete_supported_write_surface() -> None:
    assert set(WRITE_OPS) == EXPECTED
    assert all(operation.http == "POST" for operation in WRITE_OPS.values())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_write_prepares_verified_camel_case_body(name: str) -> None:
    prepared = prepare_write(name, sample_args(name))
    if name != "indexnow_submit":
        assert prepared.body["siteUrl"] == SITE
    assert prepared.summary
    assert prepared.cost >= 1


def test_batch_cap_is_verified_and_enforced() -> None:
    args = {"site_url": SITE, "url_list": [f"{SITE}/{number}" for number in range(501)]}
    with pytest.raises(InvalidRequest, match="500"):
        prepare_write("submit_url_batch", args)


def test_content_must_be_base64_and_dynamic_serving_in_range() -> None:
    args = sample_args("submit_content")
    args["http_message"] = "not base64!"
    with pytest.raises(InvalidRequest, match="base64"):
        prepare_write("submit_content", args)
    args = sample_args("submit_content")
    args["dynamic_serving"] = 6
    with pytest.raises(InvalidRequest, match="dynamic_serving"):
        prepare_write("submit_content", args)


def test_content_limit_applies_to_the_whole_uncompressed_request() -> None:
    args = sample_args("submit_content")
    args["http_message"] = base64.b64encode(b"x" * (6 * 1024 * 1024)).decode()
    args["structured_data"] = base64.b64encode(b"x" * (5 * 1024 * 1024)).decode()

    with pytest.raises(InvalidRequest, match="10 MB"):
        prepare_write("submit_content", args)


def test_complex_types_reject_invented_fields() -> None:
    args = sample_args("save_crawl_settings")
    args["crawl_settings"]["AjaxEnabled"] = True
    with pytest.raises(InvalidRequest, match="AjaxEnabled"):
        prepare_write("save_crawl_settings", args)


def test_country_region_url_must_belong_to_the_site() -> None:
    args = {
        "site_url": SITE,
        "settings": {"TwoLetterIsoCountryCode": "TH", "Type": 0, "Url": "https://evil.example/x"},
    }
    with pytest.raises(InvalidRequest):
        prepare_write("add_country_region_settings", args)


def test_site_move_source_must_belong_to_the_site() -> None:
    args = {
        "site_url": SITE,
        "settings": {
            "MoveScope": 0,
            "MoveType": 0,
            "SourceUrl": "https://evil.example",
            "TargetUrl": "https://b.example",
        },
    }
    with pytest.raises(InvalidRequest):
        prepare_write("submit_site_move", args)


def test_site_move_target_may_be_another_site_but_must_be_a_url() -> None:
    args = sample_args("submit_site_move")
    prepared = prepare_write("submit_site_move", args)
    assert prepared.body["settings"]["TargetUrl"] == "https://b.example"
    args["settings"] = {**args["settings"], "TargetUrl": "not-a-url"}
    with pytest.raises(InvalidRequest):
        prepare_write("submit_site_move", args)


@pytest.mark.parametrize("url", ["ftp://a.example/secret", "//a.example/secret", "a.example/x"])
def test_urls_inside_complex_objects_must_be_absolute_http(url: str) -> None:
    args = {
        "site_url": SITE,
        "blocked_url": {"Url": url, "EntityType": 0, "RequestType": 1},
    }
    with pytest.raises(InvalidRequest):
        prepare_write("add_blocked_url", args)


def test_delegated_url_must_be_an_absolute_url() -> None:
    args = {**sample_args("add_site_roles"), "site_url": SITE, "delegated_url": "a.example"}
    with pytest.raises(InvalidRequest):
        prepare_write("add_site_roles", args)


def test_delegated_url_may_name_the_subdomain_in_microsofts_example() -> None:
    args = {
        **sample_args("add_site_roles"),
        "site_url": "http://example.com",
        "delegated_url": "http://host1.example.com",
    }
    prepared = prepare_write("add_site_roles", args)
    assert prepared.body["delegatedUrl"] == "http://host1.example.com"


def test_url_with_an_unparsable_port_inside_a_complex_object_is_rejected() -> None:
    args = {
        "site_url": SITE,
        "settings": {
            "MoveScope": 0,
            "MoveType": 0,
            "SourceUrl": SITE,
            "TargetUrl": "https://b.example:notaport",
        },
    }
    with pytest.raises(InvalidRequest):
        prepare_write("submit_site_move", args)


def test_a_url_on_another_port_does_not_belong_to_the_site() -> None:
    with pytest.raises(InvalidRequest):
        prepare_write("submit_url", {"site_url": SITE, "url": "https://a.example:8443/p"})
    with pytest.raises(InvalidRequest):
        prepare_write(
            "submit_url",
            {"site_url": "https://a.example:8443", "url": "https://a.example/p"},
        )


def test_a_legacy_http_site_may_submit_its_https_urls() -> None:
    prepared = prepare_write(
        "submit_url", {"site_url": "http://a.example", "url": "https://a.example/p"}
    )
    assert prepared.body["url"] == "https://a.example/p"


def test_a_site_on_an_explicit_port_keeps_its_own_urls() -> None:
    site = "https://a.example:8443"
    prepared = prepare_write("submit_url", {"site_url": site, "url": f"{site}/p"})
    assert prepared.body["url"] == f"{site}/p"


@pytest.mark.parametrize(
    "url",
    [
        "https://a.example/shop/../admin",
        "https://a.example/shop/%2e%2e/admin",
        "https://a.example/shop%2f..%2fadmin",
        "https://a.example/shop/%252e%252e/admin",
    ],
)
def test_ambiguous_paths_cannot_bypass_registered_site_scope(url: str) -> None:
    with pytest.raises(InvalidRequest, match="dot segments"):
        prepare_write("submit_url", {"site_url": "https://a.example/shop", "url": url})


@pytest.mark.parametrize(
    ("site", "url"),
    [
        ("http://a.example:443", "http://a.example/p"),
        ("https://a.example:80", "https://a.example/p"),
    ],
)
def test_a_port_that_is_non_default_for_its_scheme_identifies_another_site(
    site: str, url: str
) -> None:
    with pytest.raises(InvalidRequest):
        prepare_write("submit_url", {"site_url": site, "url": url})
