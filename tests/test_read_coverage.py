from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "api-surface.md"
ROW = re.compile(r"^\|\s*`(?P<method>\w+)`\s*\|.*\|\s*(?P<rw>[RW])\s*\|")
MODULES = (
    "sites",
    "traffic",
    "crawl",
    "submission",
    "sitemaps",
    "blocking",
    "params",
    "geo",
    "links",
    "keywords",
)
EXCLUDED = {"GetDeepLinkAlgoUrls", "GetDeepLink"}


def test_every_supported_read_appears_in_exactly_one_domain_module() -> None:
    sources = {
        name: (ROOT / "bing_webmaster_ai_cli_mcp" / "ops" / f"{name}.py").read_text()
        for name in MODULES
    }
    missing: list[str] = []
    duplicated: list[str] = []
    for line in DOC.read_text().splitlines():
        match = ROW.match(line)
        if match is None or match["rw"] != "R" or match["method"] in EXCLUDED:
            continue
        count = sum(f'"{match["method"]}"' in source for source in sources.values())
        if count == 0:
            missing.append(match["method"])
        elif count > 1:
            duplicated.append(match["method"])
    assert not missing
    assert not duplicated


# Reads whose answer is one record, so an empty array inside it is a field of that
# record and not a row Bing failed to send. Written out here rather than derived, so
# adding a read means deciding which of the two it is instead of inheriting a default.
SINGLE_RECORD_READS = {
    "bing_url_info",
    "bing_url_traffic_info",
    "bing_crawl_settings",
    "bing_fetched_url_details",
    "bing_submission_quota",
    "bing_content_submission_quota",
    "bing_keyword",
}


def test_every_read_tool_declares_whether_it_carries_rows() -> None:
    """An undeclared read would fall back to the label and cry silence over a record."""
    from bing_webmaster_ai_cli_mcp.emptiness import SHAPE_ATTRIBUTE
    from bing_webmaster_ai_cli_mcp.mcp_server import READ_TOOLS

    undeclared = [
        name for name, function in READ_TOOLS.items() if not hasattr(function, SHAPE_ATTRIBUTE)
    ]
    assert undeclared == []


def test_the_single_record_reads_are_exactly_the_ones_listed() -> None:
    from bing_webmaster_ai_cli_mcp.emptiness import SINGLE_RECORD, read_shape
    from bing_webmaster_ai_cli_mcp.mcp_server import READ_TOOLS

    declared = {
        name for name, function in READ_TOOLS.items() if read_shape(function) == SINGLE_RECORD
    }
    assert declared == SINGLE_RECORD_READS


def test_the_cli_only_read_declares_its_shape_too() -> None:
    from bing_webmaster_ai_cli_mcp.emptiness import SINGLE_RECORD, read_shape
    from bing_webmaster_ai_cli_mcp.ops import sites

    assert read_shape(sites.show_site) == SINGLE_RECORD
