from __future__ import annotations

import json

from click.testing import CliRunner
from fakes import bing_transport, fake_settings

from bing_webmaster_ai_cli_mcp import cli


def test_sites_list_prints_json(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUserSites": [{"Url": "https://a.example"}]})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["sites", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"Url": {"value": "https://a.example", "untrusted": True}}]


def test_human_list_is_a_table(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUserSites": [{"Url": "https://a.example", "IsVerified": True}]})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["sites", "list"])
    assert result.exit_code == 0
    assert "Url" in result.output
    assert "IsVerified" in result.output


def test_public_errors_are_json_and_nonzero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({}))
    result = CliRunner().invoke(cli.main, ["sites", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "INVALID_REQUEST"


def test_plan_submit_url_checks_quota_and_records_only(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUrlSubmissionQuota": {"DailyQuota": 1}})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(
        cli.main,
        ["plan", "submit-url", "a.example", "https://a.example/p", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["plan_id"]
    assert [request.url.path.rsplit("/", 1)[-1] for request in transport.calls] == [
        "GetUrlSubmissionQuota"
    ]


def test_apply_prompts_and_can_be_aborted(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({"AddSite": None}))
    plan = cli.run_async(cli._create_plan("add_site", {"site_url": "https://a.example"}))
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output


def test_all_required_command_groups_exist() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    for group in (
        "sites",
        "traffic",
        "index",
        "crawl",
        "links",
        "keywords",
        "sitemaps",
        "plan",
        "indexnow",
    ):
        assert group in result.output


def test_apply_prompt_shows_the_prepared_request_body(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({}))
    plan = cli.run_async(
        cli._create_plan(
            "save_crawl_settings",
            {
                "site_url": "https://a.example",
                "crawl_settings": {"CrawlBoostEnabled": False, "CrawlRate": [7] * 24},
            },
        )
    )
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    assert "SaveCrawlSettings request body" in result.output
    assert "CrawlBoostEnabled" in result.output
    assert "crawlSettings" in result.output


def test_apply_prompt_shows_every_url_in_a_batch(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(
        cli, "_transport", lambda: bing_transport({"GetUrlSubmissionQuota": {"DailyQuota": 500}})
    )
    urls = [f"https://a.example/{index}" for index in range(60)]
    plan = cli.run_async(
        cli._create_plan("submit_url_batch", {"site_url": "https://a.example", "url_list": urls})
    )
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    for url in urls:
        assert url in result.output


def test_apply_prompt_states_the_size_of_an_elided_value(tmp_path, monkeypatch) -> None:
    import base64

    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(
        cli,
        "_transport",
        lambda: bing_transport({"GetContentSubmissionQuota": {"DailyQuota": 10}}),
    )
    encoded = base64.b64encode(b"HTTP/1.1 200 OK\r\n\r\n" + b"x" * 5000).decode()
    plan = cli.run_async(
        cli._create_plan(
            "submit_content",
            {
                "site_url": "https://a.example",
                "url": "https://a.example/p",
                "http_message": encoded,
                "structured_data": "",
                "dynamic_serving": 0,
            },
        )
    )
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    assert f"({len(encoded)} characters)" in result.output


def test_apply_prompt_strips_bidi_characters_from_the_payload(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({}))
    plan = cli.run_async(
        cli._create_plan(
            "add_query_parameter",
            {"site_url": "https://a.example", "query_parameter": "utm‮source"},
        )
    )
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    assert "‮" not in result.output
    assert "utmsource" in result.output


def test_plan_unlock_recovers_a_dead_apply_without_making_it_retryable(
    tmp_path, monkeypatch
) -> None:
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.plans._pid_is_alive", lambda _pid: False)
    store = cli.PlanStore(tmp_path, settings.plan_ttl_seconds)
    plan = store.create("add_site", "https://a.example", {"site_url": "https://a.example"}, "x")
    (tmp_path / "plans" / f"{plan.plan_id}.lock").write_text("pid=12345\n")

    result = CliRunner().invoke(cli.main, ["plan", "unlock", plan.plan_id, "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["state"] == "unknown_outcome"
    assert store.get(plan.plan_id).state == "unknown_outcome"


def test_submit_url_sends_immediately(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUrlSubmissionQuota": {"DailyQuota": 5}, "SubmitUrl": None})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(
        cli.main, ["submit-url", "a.example", "https://a.example/p", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["operation"] == "submit_url"
    assert [request.url.path.rsplit("/", 1)[-1] for request in transport.calls] == [
        "GetUrlSubmissionQuota",
        "SubmitUrl",
    ]


def test_one_step_commands_are_refused_when_writes_are_disabled(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path, allow_writes=False)
    transport = bing_transport({"AddSite": None})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    args = ["write", "add_site", "--args-json", '{"site_url": "https://a.example"}', "--json"]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "POLICY_DENIED"
    assert transport.calls == []


def test_planning_still_works_while_writes_are_disabled(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path, allow_writes=False)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({"AddSite": None}))
    payload = '{"site_url": "https://a.example"}'
    args = ["plan", "create", "add_site", "--args-json", payload, "--json"]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["plan_id"]


def test_generic_write_rejects_a_non_object_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({}))
    result = CliRunner().invoke(cli.main, ["write", "add_site", "--args-json", "[]", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "INVALID_REQUEST"


def test_a_disabled_write_is_policy_denied_even_without_an_api_key(tmp_path, monkeypatch) -> None:
    settings = fake_settings(tmp_path, allow_writes=False, api_key=None)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({}))
    result = CliRunner().invoke(
        cli.main, ["submit-url", "a.example", "https://a.example/p", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "POLICY_DENIED"


def test_crawl_issues_prints_counts_above_the_table(tmp_path, monkeypatch) -> None:
    rows = [
        {"Url": "https://a.example/a", "HttpCode": 404, "Issues": 4},
        {"Url": "https://a.example/b", "HttpCode": 403, "Issues": 4},
    ]
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({"GetCrawlIssues": rows}))
    result = CliRunner().invoke(cli.main, ["crawl", "issues", "a.example"])
    assert result.exit_code == 0, result.output
    assert "2 URLs with crawl issues" in result.output
    assert "http_4xx: 2" in result.output
    assert "http_404: 1" in result.output
    assert "http_403: 1" in result.output
    assert "HTTP 404: 1" in result.output
    assert "categories" in result.output


def test_crawl_issues_json_keeps_the_raw_rows(tmp_path, monkeypatch) -> None:
    rows = [{"Url": "https://a.example/a", "HttpCode": 500, "Issues": 8, "InLinks": 2}]
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({"GetCrawlIssues": rows}))
    result = CliRunner().invoke(cli.main, ["crawl", "issues", "a.example", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["categories"] == {"http_5xx": 1}
    assert payload["issues"][0]["InLinks"] == 2
    assert payload["issues"][0]["categories"] == ["http_5xx"]


def test_indexnow_key_generates_without_reaching_the_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    result = CliRunner().invoke(cli.main, ["indexnow", "key", "a.example", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["key_location"] == f"https://a.example/{payload['key']}.txt"
    assert payload["key_file"] == {"checked": False, "present": None}


def test_indexnow_key_checks_an_existing_key_file(tmp_path, monkeypatch) -> None:
    import httpx

    from bing_webmaster_ai_cli_mcp.ops import indexnow

    async def resolve(host: str) -> set[str]:
        return {"93.184.216.34"}

    key = "0123456789abcdef0123456789abcdef"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=key))
    monkeypatch.setattr(indexnow, "_resolve", resolve)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["indexnow", "key", "a.example", "--key", key, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["key_file"] == {"checked": True, "present": True}


SITE_WITH_SECRETS = {
    "Url": "https://a.example",
    "IsVerified": True,
    "AuthenticationCode": "auth-secret",
    "DnsVerificationCode": "dns-secret",
}


def test_sites_list_redacts_verification_codes(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUserSites": [dict(SITE_WITH_SECRETS)]})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["sites", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert "auth-secret" not in result.output
    assert json.loads(result.stdout)[0]["AuthenticationCode"] == "[redacted: verification secret]"


def test_the_operator_can_reveal_them_with_an_explicit_flag(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUserSites": [dict(SITE_WITH_SECRETS)]})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(
        cli.main, ["sites", "list", "--reveal-verification-codes", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["AuthenticationCode"] == "auth-secret"


def test_an_empty_read_prints_the_silence_note_without_spoiling_the_json(
    tmp_path, monkeypatch
) -> None:
    transport = bing_transport({"GetLinkCounts": {"Links": [], "TotalPages": 0}})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["links", "counts", "a.example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"Links": [], "TotalPages": 0}
    assert "not a measurement" in result.stderr.casefold()


def test_a_read_that_returned_rows_prints_no_note(tmp_path, monkeypatch) -> None:
    rows = {"Links": [{"Url": "https://a"}], "TotalPages": 1}
    transport = bing_transport({"GetLinkCounts": rows})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["links", "counts", "a.example", "--json"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_url_info_labels_a_status_bing_did_not_report(tmp_path, monkeypatch) -> None:
    transport = bing_transport({"GetUrlInfo": {"HttpStatus": 0, "IsPage": True}})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    command = ["index", "url", "a.example", "https://a.example/p", "--json"]
    result = CliRunner().invoke(cli.main, command)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["http_status_reported"] is False


ROLE_CODE = "auth-secret-value"
ROLE_ARGS = {
    "site_url": "https://a.example",
    "delegated_url": "https://a.example",
    "user_email": "x@y.example",
    "authentication_code": ROLE_CODE,
    "is_administrator": False,
    "is_read_only": True,
}


def _role_plan(tmp_path, monkeypatch):
    settings = fake_settings(tmp_path)
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli, "_transport", lambda: bing_transport({"AddSiteRoles": None}))
    return cli.run_async(cli._create_plan("add_site_roles", dict(ROLE_ARGS)))


def test_plan_show_redacts_the_verification_code(tmp_path, monkeypatch) -> None:
    plan = _role_plan(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli.main, ["plan", "show", plan.plan_id, "--json"])
    assert result.exit_code == 0, result.output
    assert ROLE_CODE not in result.output
    args = json.loads(result.stdout)["args"]
    assert args["authentication_code"] == "[redacted: verification secret]"


def test_plan_list_redacts_the_verification_code(tmp_path, monkeypatch) -> None:
    _role_plan(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli.main, ["plan", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert ROLE_CODE not in result.output


def test_the_operator_can_reveal_a_plan_secret_with_the_same_flag(tmp_path, monkeypatch) -> None:
    plan = _role_plan(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        cli.main, ["plan", "show", plan.plan_id, "--reveal-verification-codes", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["args"]["authentication_code"] == ROLE_CODE


def test_the_apply_prompt_redacts_the_code_it_is_about_to_send(tmp_path, monkeypatch) -> None:
    """The approval prompt is read aloud and pasted into tickets; the email is enough."""
    plan = _role_plan(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli.main, ["plan", "apply", plan.plan_id], input="n\n")
    assert result.exit_code == 1
    assert ROLE_CODE not in result.output
    assert "[redacted: verification secret]" in result.output
    assert "x@y.example" in result.output


def test_the_apply_prompt_can_still_show_the_code_on_request(tmp_path, monkeypatch) -> None:
    plan = _role_plan(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        cli.main,
        ["plan", "apply", plan.plan_id, "--reveal-verification-codes"],
        input="n\n",
    )
    assert result.exit_code == 1
    assert ROLE_CODE in result.output


def test_a_single_record_read_prints_no_silence_note(tmp_path, monkeypatch) -> None:
    """GetCrawlSettings answers one record; its empty CrawlRate is not a missing row."""
    settings = {"CrawlBoostAvailable": True, "CrawlBoostEnabled": False, "CrawlRate": []}
    transport = bing_transport({"GetCrawlSettings": settings})
    monkeypatch.setattr(cli, "_load_settings", lambda **kwargs: fake_settings(tmp_path))
    monkeypatch.setattr(cli, "_transport", lambda: transport)
    result = CliRunner().invoke(cli.main, ["crawl", "settings", "a.example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == settings
    assert "not a measurement" not in result.stderr.casefold()
