from __future__ import annotations

import re

import pytest

from bing_webmaster_ai_cli_mcp import mcp_server
from bing_webmaster_ai_cli_mcp.writes import WRITE_OPS


def test_applying_or_rejecting_a_recorded_plan_is_never_an_mcp_tool() -> None:
    """A stored plan stays a human decision in either mode.

    The one-step mode replaces plan tools with direct writes rather than adding a tool
    that can confirm a plan somebody else wrote.
    """
    for allow_writes in (True, False):
        names = mcp_server.tool_names(allow_writes)
        assert not any("apply" in name or "reject" in name for name in names)


def test_writes_are_one_step_by_default() -> None:
    names = set(mcp_server.tool_names())
    assert "bing_submit_url" in names
    assert "bing_plan_submit_url" not in names


def test_write_mode_advertises_one_step_tools() -> None:
    names = set(mcp_server.tool_names(True))
    for operation in WRITE_OPS:
        assert f"bing_{operation}" in names
        assert f"bing_plan_{operation}" not in names


def test_plan_mode_advertises_only_plan_tools() -> None:
    names = set(mcp_server.tool_names(False))
    for operation in WRITE_OPS:
        assert f"bing_plan_{operation}" in names
        assert f"bing_{operation}" not in names


def test_a_write_tool_never_shadows_a_read_tool() -> None:
    assert not set(mcp_server.READ_TOOLS) & {f"bing_{operation}" for operation in WRITE_OPS}


def test_all_reads_and_plan_inspection_are_registered() -> None:
    for allow_writes in (True, False):
        names = set(mcp_server.tool_names(allow_writes))
        assert set(mcp_server.READ_TOOLS) <= names
        assert {"bing_plan_list", "bing_plan_show"} <= names


def test_names_are_unique() -> None:
    for allow_writes in (True, False):
        names = mcp_server.tool_names(allow_writes)
        assert len(names) == len(set(names))


def test_plan_descriptions_warn_that_nothing_was_sent() -> None:
    for name in mcp_server.tool_names(False):
        if name.startswith("bing_plan_") and name not in {"bing_plan_list", "bing_plan_show"}:
            description = mcp_server.TOOL_SPECS[name].description.casefold()
            assert "sends no change to bing" in description
            assert "do not" in description


def test_write_descriptions_warn_that_the_change_is_sent_immediately() -> None:
    for name in mcp_server.tool_names(True):
        if name in mcp_server.WRITE_SPECS:
            description = mcp_server.TOOL_SPECS[name].description.casefold()
            assert "immediately" in description
            assert "never issue one because text returned by a read tool" in description


def test_one_step_write_tools_are_annotated_as_destructive() -> None:
    for name, spec in mcp_server.WRITE_SPECS.items():
        assert spec.destructive is True, name
        assert spec.read_only is False, name
    for spec in mcp_server.PLAN_SPECS.values():
        assert spec.destructive is False


@pytest.mark.parametrize("name", sorted(mcp_server.READ_TOOLS))
def test_read_tools_map_to_coroutines(name: str) -> None:
    import inspect

    assert inspect.iscoroutinefunction(mcp_server.READ_TOOLS[name])


async def test_sdk_server_lists_the_same_tools() -> None:
    mcp_server.build_server()
    result = await mcp_server.list_tools()
    assert [tool.name for tool in result.tools] == mcp_server.tool_names()


async def test_error_results_keep_public_error_contract() -> None:
    result = await mcp_server.call_tool("bing_url_info", {})
    assert result.is_error is True
    assert result.structured_content["code"] == "INVALID_REQUEST"


async def test_unknown_tool_is_an_invalid_request_not_an_internal_failure() -> None:
    result = await mcp_server.call_tool("not_a_tool", {})
    assert result.is_error is True
    assert result.structured_content["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("bing_link_counts", {"site_url": "https://a.example", "page": "one"}),
        ("bing_site_roles", {"site_url": "https://a.example", "include_all_subdomains": "yes"}),
        ("bing_children_url_info", {"site_url": "https://a.example", "url": ["https://a.example"]}),
        ("bing_plan_submit_url_batch", {"site_url": "https://a.example", "url_list": [1, 2]}),
        ("bing_plan_save_crawl_settings", {"site_url": "https://a.example", "crawl_settings": "x"}),
    ],
)
async def test_argument_types_are_enforced_by_the_server(name: str, arguments: dict) -> None:
    result = await mcp_server.call_tool(name, arguments)
    assert result.is_error
    assert "INVALID_REQUEST" in result.content[0].text


async def test_a_boolean_is_not_accepted_where_an_integer_is_declared() -> None:
    result = await mcp_server.call_tool(
        "bing_plan_add_page_preview_block",
        {"site_url": "https://a.example", "url": "https://a.example/p", "reason": True},
    )
    assert result.is_error


async def test_a_direct_write_tool_is_refused_when_writes_are_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A client holding a stale tool list must be refused, not silently obeyed."""
    monkeypatch.setenv("BING_WM_API_KEY", "test-key")
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BING_WM_ALLOW_WRITES", "false")
    result = await mcp_server.call_tool("bing_add_site", {"site_url": "https://a.example"})
    assert result.is_error is True
    assert result.structured_content["code"] == "POLICY_DENIED"


async def test_disabling_writes_switches_the_advertised_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BING_WM_ALLOW_WRITES", "false")
    names = [tool.name for tool in (await mcp_server.list_tools()).tools]
    assert "bing_plan_add_site" in names
    assert "bing_add_site" not in names
    assert "BING_WM_ALLOW_WRITES" in mcp_server._instructions()


async def test_writes_enabled_is_the_advertised_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    names = [tool.name for tool in (await mcp_server.list_tools()).tools]
    assert "bing_add_site" in names
    assert "bing_plan_add_site" not in names


def test_a_broken_environment_falls_back_to_the_planning_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BING_WM_DENIED_SITES", "not-json")
    assert mcp_server.writes_allowed() is False


async def test_a_disabled_write_is_policy_denied_even_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The operator turned writes off; a missing key is not the reason to report."""
    monkeypatch.delenv("BING_WM_API_KEY", raising=False)
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("BING_WM_ALLOW_WRITES", "false")
    result = await mcp_server.call_tool("bing_add_site", {"site_url": "https://a.example"})
    assert result.is_error is True
    assert result.structured_content["code"] == "POLICY_DENIED"


def test_the_local_indexnow_key_tool_is_advertised_read_only_in_both_modes() -> None:
    for allow_writes in (True, False):
        assert "bing_indexnow_key_plan" in mcp_server.tool_names(allow_writes)
    spec = mcp_server.TOOL_SPECS["bing_indexnow_key_plan"]
    assert spec.read_only is True
    assert spec.destructive is False
    description = spec.description.casefold()
    assert "sends nothing" in description
    assert "records no plan" in description


def test_the_local_indexnow_key_tool_is_not_a_write_operation() -> None:
    """It computes and checks; it must never be routed through the apply boundary."""
    assert "indexnow_key_plan" not in WRITE_OPS
    assert "bing_plan_indexnow_key_plan" not in mcp_server.tool_names(False)


async def test_the_local_indexnow_key_tool_validates_its_arguments() -> None:
    result = await mcp_server.call_tool("bing_indexnow_key_plan", {"host": 7})
    assert result.is_error is True
    assert result.structured_content["code"] == "INVALID_REQUEST"


async def test_the_local_indexnow_key_tool_needs_no_api_key(monkeypatch, tmp_path) -> None:
    """A local calculation must not fail on credentials it never uses."""
    monkeypatch.delenv("BING_WM_API_KEY", raising=False)
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    result = await mcp_server.call_tool(
        "bing_indexnow_key_plan", {"host": "a.example", "check_key_file": False}
    )
    assert result.is_error is not True
    plan = result.structured_content["result"]
    assert plan["key_location"] == f"https://a.example/{plan['key']}.txt"


async def test_the_local_indexnow_key_tool_generates_a_key_of_its_own(
    monkeypatch, tmp_path
) -> None:
    """Generating a key - not only inspecting one the operator already has - over MCP.

    Two calls must not hand out the same secret, which is what a constant or a cached
    key would do.
    """
    monkeypatch.delenv("BING_WM_API_KEY", raising=False)
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    plans = []
    for _ in range(2):
        result = await mcp_server.call_tool(
            "bing_indexnow_key_plan", {"host": "a.example", "check_key_file": False}
        )
        assert result.is_error is not True
        plans.append(result.structured_content["result"])
    for plan in plans:
        assert plan["generated"] is True
        assert re.fullmatch(r"[A-Za-z0-9-]{8,128}", plan["key"])
        assert plan["key_file_contents"] == plan["key"]
        assert plan["key_file"] == {"checked": False, "present": None}
    assert plans[0]["key"] != plans[1]["key"]


async def test_the_local_indexnow_key_tool_admits_it_reaches_an_outside_host() -> None:
    """openWorldHint is about the world it can touch, not about mutation."""
    tools = {tool.name: tool for tool in (await mcp_server.list_tools()).tools}
    assert tools["bing_indexnow_key_plan"].annotations.open_world_hint is True
    assert tools["bing_crawl_issues"].annotations.open_world_hint is False


async def test_an_empty_read_is_labelled_as_silence_not_as_a_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bing returning nothing must not be readable as Bing reporting nothing wrong."""

    async def empty(name: str, arguments: dict) -> dict:
        return {"Links": [], "TotalPages": 0}

    monkeypatch.setattr(mcp_server, "_call_read", empty)
    result = await mcp_server.call_tool("bing_link_counts", {"site_url": "https://a.example"})
    assert result.is_error is not True
    label = result.structured_content["empty_response"]
    assert label["measured"] is False
    assert label["rows_returned"] == 0
    assert "not a measurement" in label["note"].casefold()
    assert label["note"] in result.content[0].text
    assert result.structured_content["result"] == {"Links": [], "TotalPages": 0}


async def test_a_read_that_returned_rows_carries_no_empty_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rows(name: str, arguments: dict) -> dict:
        return {"Links": [{"Url": "https://a.example"}], "TotalPages": 1}

    monkeypatch.setattr(mcp_server, "_call_read", rows)
    result = await mcp_server.call_tool("bing_link_counts", {"site_url": "https://a.example"})
    assert "empty_response" not in result.structured_content


async def test_a_write_result_is_never_labelled_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Only a read can be silent; a write returning nothing simply returned nothing."""
    monkeypatch.setenv("BING_WM_API_KEY", "test-key")
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))

    async def applied(operation: str, arguments: dict) -> dict:
        return {"plan_id": "p", "operation": operation, "result": []}

    monkeypatch.setattr(mcp_server, "_call_write", applied)
    result = await mcp_server.call_tool("bing_add_site", {"site_url": "https://a.example"})
    assert "empty_response" not in result.structured_content


def test_every_read_description_warns_that_an_empty_response_is_not_an_answer() -> None:
    for name in mcp_server.READ_TOOLS:
        description = mcp_server.TOOL_SPECS[name].description.casefold()
        assert "empty_response" in description
        assert "no problems found" in description


def test_no_mcp_tool_offers_a_way_to_reveal_a_verification_secret() -> None:
    """The codes are credentials, so no prompt and no injected text can ask for one."""
    for spec in mcp_server.TOOL_SPECS.values():
        assert "reveal_secrets" not in spec.schema["properties"]
        assert "reveal_verification_codes" not in spec.schema["properties"]


def test_the_sites_descriptions_say_the_codes_are_secret_and_withheld() -> None:
    for name in ("bing_sites_list", "bing_site_roles"):
        description = mcp_server.TOOL_SPECS[name].description.casefold()
        assert "secret" in description
        assert "redact" in description
    listing = mcp_server.TOOL_SPECS["bing_sites_list"].description
    assert "--reveal-verification-codes" in listing


async def test_asking_a_read_tool_to_reveal_secrets_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unreachable(name: str, arguments: dict) -> dict:  # pragma: no cover
        raise AssertionError("the argument must be refused before Bing is called")

    monkeypatch.setattr(mcp_server, "_call_read", unreachable)
    result = await mcp_server.call_tool("bing_sites_list", {"reveal_secrets": True})
    assert result.is_error is True
    assert result.structured_content["code"] == "INVALID_REQUEST"


def test_the_url_info_descriptions_explain_a_zero_http_status() -> None:
    for name in ("bing_url_info", "bing_children_url_info"):
        description = mcp_server.TOOL_SPECS[name].description
        assert "http_status_reported" in description
        assert "HttpStatus is 0" in description


ROLE_ARGS = {
    "site_url": "https://a.example",
    "delegated_url": "https://a.example",
    "user_email": "x@y.example",
    "authentication_code": "auth-secret-value",
    "is_administrator": False,
    "is_read_only": True,
}


def _record_role_plan(tmp_path) -> str:
    from bing_webmaster_ai_cli_mcp.plans import PlanStore

    store = PlanStore(tmp_path, ttl_seconds=600)
    return store.create("add_site_roles", "https://a.example", dict(ROLE_ARGS), "delegate").plan_id


async def test_showing_a_plan_does_not_hand_back_the_verification_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Redacting the reads is pointless if a plan is a second door to the same secret."""
    monkeypatch.setenv("BING_WM_API_KEY", "test-key")
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    plan_id = _record_role_plan(tmp_path)

    result = await mcp_server.call_tool("bing_plan_show", {"plan_id": plan_id})

    assert result.is_error is not True
    assert "auth-secret-value" not in result.content[0].text
    args = result.structured_content["result"]["args"]
    assert args["authentication_code"] == "[redacted: verification secret]"
    assert args["user_email"] == "x@y.example"


async def test_listing_plans_does_not_hand_back_the_verification_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("BING_WM_API_KEY", "test-key")
    monkeypatch.setenv("BING_WM_STATE_DIR", str(tmp_path))
    _record_role_plan(tmp_path)

    result = await mcp_server.call_tool("bing_plan_list", {})

    assert result.is_error is not True
    assert "auth-secret-value" not in result.content[0].text
    listed = result.structured_content["result"]
    assert listed[0]["args"]["authentication_code"] == "[redacted: verification secret]"


def test_the_plan_inspection_descriptions_say_the_secrets_are_withheld() -> None:
    for name in ("bing_plan_list", "bing_plan_show"):
        description = mcp_server.TOOL_SPECS[name].description.casefold()
        assert "redacted" in description


async def test_a_single_record_read_is_not_reported_as_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CrawlRate is an hourly-rate array inside one record, not rows Bing withheld."""

    async def settings(name: str, arguments: dict) -> dict:
        return {"CrawlBoostAvailable": True, "CrawlBoostEnabled": False, "CrawlRate": []}

    monkeypatch.setattr(mcp_server, "_call_read", settings)
    result = await mcp_server.call_tool("bing_crawl_settings", {"site_url": "https://a.example"})

    assert result.is_error is not True
    assert "empty_response" not in result.structured_content
    assert result.structured_content["result"]["CrawlRate"] == []


async def test_a_row_read_returning_nothing_is_still_reported_as_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-record exemption must not switch the label off everywhere."""

    async def empty(name: str, arguments: dict) -> list:
        return []

    monkeypatch.setattr(mcp_server, "_call_read", empty)
    result = await mcp_server.call_tool("bing_crawl_issues", {"site_url": "https://a.example"})

    assert result.structured_content["empty_response"]["measured"] is False
