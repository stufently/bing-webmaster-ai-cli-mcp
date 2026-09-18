from __future__ import annotations

import json
import struct
import tomllib
from pathlib import Path

from bing_webmaster_ai_cli_mcp.mcp_server import TOOL_SPECS, tool_names
from bing_webmaster_ai_cli_mcp.ops.crawl import CRAWL_ISSUE_CATEGORIES
from bing_webmaster_ai_cli_mcp.writes import WRITE_OPS

ROOT = Path(__file__).resolve().parents[1]
PITCH = (
    "bing-webmaster-ai-cli-mcp gives an AI agent read access to what Bing knows about your sites "
    "— traffic, indexing, crawl issues, inbound links, keywords — and a write path you "
    "choose: direct by default, or reviewed plan-and-apply."
)


def test_pitch_is_consistent() -> None:
    for path in (ROOT / "README.md", ROOT / "llms.txt", ROOT / "pyproject.toml"):
        assert PITCH in path.read_text()


def test_readme_states_the_write_switch_and_indexnow_google_fact() -> None:
    text = (ROOT / "README.md").read_text().casefold()
    assert "bing_wm_allow_writes" in text
    assert "prompt injection" in text
    assert "google" in text and "does not participate" in text


def test_every_mcp_tool_and_write_is_documented() -> None:
    operations = (ROOT / "docs" / "operations.md").read_text()
    for allow_writes in (True, False):
        assert not [name for name in tool_names(allow_writes) if name not in operations]
    assert not [name for name in WRITE_OPS if name not in operations]


def test_every_crawl_issue_category_is_documented_where_a_reader_will_look() -> None:
    """A category nobody can look up is a number without a meaning."""
    texts = {
        "docs/operations.md": (ROOT / "docs" / "operations.md").read_text(),
        "SKILL.md": (ROOT / "SKILL.md").read_text(),
        "the bing_crawl_issues tool description": TOOL_SPECS["bing_crawl_issues"].description,
    }
    for category in CRAWL_ISSUE_CATEGORIES:
        for where, text in texts.items():
            assert category in text, f"{category} is missing from {where}"


def test_product_boundaries_record_every_deliberate_exclusion() -> None:
    text = (ROOT / "docs" / "product-boundaries.md").read_text()
    for term in (
        "SOAP",
        "POX",
        "Bing Search",
        "Google Search Console",
        "GetDeepLinkAlgoUrls",
        "GetDeepLink",
        "UpdateDeepLink",
        "OAuth2",
        "scheduler",
        "noindex",
        "verification secret",
        "HttpStatus",
    ):
        assert term in text
    assert "2026-08-25" in text


def test_configuration_names_every_setting() -> None:
    text = (ROOT / "docs" / "configuration.md").read_text()
    for name in (
        "BING_WM_API_KEY",
        "BING_WM_BASE_URL",
        "BING_WM_CALLS_PER_SECOND",
        "BING_WM_MAX_ATTEMPTS",
        "BING_WM_PLAN_TTL_SECONDS",
        "BING_WM_STATE_DIR",
        "BING_WM_ALLOW_WRITES",
        "BING_WM_DENIED_SITES",
        "BING_WM_MAX_WRITES_PER_DAY",
        "BING_WM_HTTP_HOST",
        "BING_WM_HTTP_PORT",
        "BING_WM_HTTP_BEARER_TOKEN",
    ):
        assert name in text


def test_supporting_metadata_exists() -> None:
    assert "bing-webmaster-ai-cli-mcp" in (ROOT / "CITATION.cff").read_text()
    assert "## Unreleased" in (ROOT / "CHANGELOG.md").read_text()
    assert len((ROOT / "llms.txt").read_text().splitlines()) >= 8


def test_registry_metadata_matches_the_package_and_readme_marker() -> None:
    registry = json.loads((ROOT / "server.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert registry["name"] == "io.github.stufently/bing-webmaster-ai-cli-mcp"
    assert registry["version"] == project["version"]
    assert registry["packages"][0]["identifier"] == project["name"]
    assert registry["packages"][0]["version"] == project["version"]
    assert f"mcp-name: {registry['name']} -->" in (ROOT / "README.md").read_text()


def test_social_preview_has_githubs_required_dimensions() -> None:
    data = (ROOT / "docs" / "assets" / "social-preview.png").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (1280, 640)


def test_the_empty_response_label_is_documented_where_a_reader_will_look() -> None:
    for path in ("docs/operations.md", "SKILL.md"):
        text = (ROOT / path).read_text()
        assert "empty_response" in text, path
        assert "http_status_reported" in text, path


def test_the_reveal_flag_and_the_redaction_are_documented() -> None:
    operations = (ROOT / "docs" / "operations.md").read_text()
    assert "--reveal-verification-codes" in operations
    assert "AuthenticationCode" in operations
    assert "DnsVerificationCode" in operations
    assert "DelegatedCode" in operations


def test_the_plan_and_error_exits_are_documented_as_redacted() -> None:
    operations = (ROOT / "docs" / "operations.md").read_text()
    assert "public_dump" in operations
    assert "_map_error" in operations
    skill = (ROOT / "SKILL.md").read_text()
    assert "bing_plan_show" in skill


def test_the_declared_read_shape_is_documented() -> None:
    operations = (ROOT / "docs" / "operations.md").read_text()
    assert "@row_read" in operations
    assert "@single_record" in operations


def test_the_two_new_boundaries_are_recorded_with_their_date() -> None:
    boundaries = (ROOT / "docs" / "product-boundaries.md").read_text()
    assert "a plan keeps its secret on disk and hides it in every view" in boundaries
    assert "a read's shape is declared, never inferred" in boundaries
