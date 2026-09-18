from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fakes import bing_transport, fake_settings
from test_writes import sample_args

from bing_webmaster_ai_cli_mcp.apply import apply_plan, execute_write
from bing_webmaster_ai_cli_mcp.audit import AuditLog
from bing_webmaster_ai_cli_mcp.client import BingClient
from bing_webmaster_ai_cli_mcp.errors import (
    InvalidRequest,
    PlanAlreadyApplied,
    PlanExpired,
    PlanUnknownOutcome,
    PolicyDenied,
    QuotaExceeded,
    UpstreamUnavailable,
)
from bing_webmaster_ai_cli_mcp.limits import RateLimiter
from bing_webmaster_ai_cli_mcp.plans import PlanStore, create_write_plan
from bing_webmaster_ai_cli_mcp.render import REDACTED
from bing_webmaster_ai_cli_mcp.writes import WRITE_OPS


async def test_creating_plan_sends_nothing_and_checks_live_quota(tmp_path) -> None:
    transport = bing_transport({"GetUrlSubmissionQuota": {"DailyQuota": 1, "MonthlyQuota": 5}})
    settings = fake_settings(tmp_path)
    async with BingClient(settings, transport=transport) as client:
        plan = await create_write_plan(
            "submit_url", sample_args("submit_url"), settings=settings, client=client
        )
    assert [request.url.path.rsplit("/", 1)[-1] for request in transport.calls] == [
        "GetUrlSubmissionQuota"
    ]
    assert plan.state == "pending"


async def test_batch_larger_than_remaining_quota_is_refused(tmp_path) -> None:
    from bing_webmaster_ai_cli_mcp.errors import QuotaExceeded

    args = {
        "site_url": "https://a.example",
        "url_list": ["https://a.example/1", "https://a.example/2"],
    }
    transport = bing_transport({"GetUrlSubmissionQuota": {"DailyQuota": 1}})
    settings = fake_settings(tmp_path)
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(QuotaExceeded):
            await create_write_plan("submit_url_batch", args, settings=settings, client=client)


async def test_apply_is_one_shot_and_audited(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, settings.plan_ttl_seconds)
    args = sample_args("add_site")
    plan = store.create("add_site", args["site_url"], args, "add site")
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        result = await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=AuditLog(tmp_path),
            limiter=RateLimiter(tmp_path, max_per_day=None),
        )
        with pytest.raises(PlanAlreadyApplied):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert result["applied"] is True
    assert len(transport.calls) == 1
    assert [entry["event"] for entry in AuditLog(tmp_path).entries()] == [
        "plan_apply_attempted",
        "plan_apply_succeeded",
    ]


async def test_expired_plan_never_reaches_upstream(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 1)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    store.set_expiry(plan.plan_id, datetime.now(UTC) - timedelta(seconds=1))
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PlanExpired):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert transport.calls == []


async def test_lost_write_response_marks_unknown_and_blocks_retry(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lost", request=request)

    async with BingClient(settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PlanUnknownOutcome):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert store.get(plan.plan_id).state == "unknown_outcome"
    with pytest.raises(PlanAlreadyApplied):
        store.ensure_pending(plan.plan_id)


async def test_indexnow_plan_verifies_key_then_submits(tmp_path) -> None:
    store = PlanStore(tmp_path, 900)
    args = sample_args("indexnow_submit")
    prepared = WRITE_OPS["indexnow_submit"].prepare(args)
    plan = store.create("indexnow_submit", "https://a.example", args, prepared.summary)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(200, text=args["key"], request=request)
        return httpx.Response(200, request=request)

    result = await apply_plan(
        plan.plan_id,
        settings=fake_settings(tmp_path),
        store=store,
        client=None,
        audit=AuditLog(tmp_path),
        limiter=RateLimiter(tmp_path, max_per_day=None),
        indexnow_transport=httpx.MockTransport(handler),
    )
    assert result["applied"] is True
    assert calls == [prepared.body["keyLocation"], "https://api.indexnow.org/indexnow"]


async def test_indexnow_5xx_stays_retryable_as_the_protocol_directs(tmp_path) -> None:
    store = PlanStore(tmp_path, 900)
    args = sample_args("indexnow_submit")
    prepared = WRITE_OPS["indexnow_submit"].prepare(args)
    plan = store.create("indexnow_submit", "https://a.example", args, prepared.summary)
    limiter = RateLimiter(tmp_path, max_per_day=1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=args["key"], request=request)
        return httpx.Response(503, request=request)

    with pytest.raises(UpstreamUnavailable):
        await apply_plan(
            plan.plan_id,
            settings=fake_settings(tmp_path),
            store=store,
            client=None,
            audit=AuditLog(tmp_path),
            limiter=limiter,
            indexnow_transport=httpx.MockTransport(handler),
        )

    assert store.get(plan.plan_id).state == "pending"
    limiter.consume("https://a.example", 1)


@pytest.mark.parametrize("name", sorted(name for name in WRITE_OPS if name != "indexnow_submit"))
async def test_every_write_applies_as_post(tmp_path, name: str) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    args = sample_args(name)
    prepared = WRITE_OPS[name].prepare(args)
    plan = store.create(name, args["site_url"], args, prepared.summary)
    transport = bing_transport({prepared.method: None})
    async with BingClient(settings, transport=transport) as client:
        await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=AuditLog(tmp_path),
            limiter=RateLimiter(tmp_path, max_per_day=None),
        )
    assert transport.calls[0].method == "POST"


async def test_server_error_on_a_write_marks_the_plan_unknown(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"Message": "boom"}, request=request)
    )
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PlanUnknownOutcome):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert store.get(plan.plan_id).state == "unknown_outcome"


async def test_write_that_lands_after_the_ttl_still_records_its_outcome(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")

    def handler(request: httpx.Request) -> httpx.Response:
        store.set_expiry(plan.plan_id, datetime.now(UTC) - timedelta(seconds=1))
        return httpx.Response(200, json={"d": None}, request=request)

    async with BingClient(settings, transport=httpx.MockTransport(handler)) as client:
        result = await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=AuditLog(tmp_path),
            limiter=RateLimiter(tmp_path, max_per_day=None),
        )
    assert result["applied"] is True
    assert store.get(plan.plan_id).state == "applied"
    assert [entry["event"] for entry in AuditLog(tmp_path).entries()][-1] == "plan_apply_succeeded"


async def test_denylist_added_after_planning_blocks_the_apply(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    denied = fake_settings(tmp_path, denied_sites=("a.example",))
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PolicyDenied):
            await apply_plan(
                plan.plan_id,
                settings=denied,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert transport.calls == []
    assert store.get(plan.plan_id).state == "pending"
    assert [entry["event"] for entry in AuditLog(tmp_path).entries()] == ["plan_apply_denied"]


async def test_undecodable_response_to_a_write_is_an_unknown_outcome(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>gateway</html>", request=request)
    )
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PlanUnknownOutcome):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert store.get(plan.plan_id).state == "unknown_outcome"


async def test_local_ceiling_is_reserved_before_the_write(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    limiter = RateLimiter(tmp_path, max_per_day=1)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=AuditLog(tmp_path),
            limiter=limiter,
        )
        second = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
        with pytest.raises(QuotaExceeded):
            await apply_plan(
                second.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=limiter,
            )
    assert len(transport.calls) == 1


async def test_a_refused_write_gives_its_reservation_back(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    limiter = RateLimiter(tmp_path, max_per_day=1)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"Message": "no"}, request=request)
    )
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(InvalidRequest):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=limiter,
            )
    limiter.consume("https://a.example", 1)


async def test_out_of_range_date_in_a_write_response_is_an_unknown_outcome(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"d": {"When": "/Date(99999999999999999)/"}}, request=request
        )
    )
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PlanUnknownOutcome):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert store.get(plan.plan_id).state == "unknown_outcome"


async def test_reject_cannot_run_while_an_apply_holds_the_lock(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    rejected_during_apply = []

    def handler(request: httpx.Request) -> httpx.Response:
        with pytest.raises(PlanAlreadyApplied):
            store.reject(plan.plan_id)
        rejected_during_apply.append(True)
        return httpx.Response(200, json={"d": None}, request=request)

    async with BingClient(settings, transport=httpx.MockTransport(handler)) as client:
        await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=AuditLog(tmp_path),
            limiter=RateLimiter(tmp_path, max_per_day=None),
        )
    assert rejected_during_apply == [True]
    assert store.get(plan.plan_id).state == "applied"


async def test_cancelled_write_is_recorded_as_unknown(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with BingClient(settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    assert store.get(plan.plan_id).state == "unknown_outcome"
    assert not list((tmp_path / "plans").glob("*.lock"))


async def test_interrupt_during_the_indexnow_preflight_leaves_the_plan_retryable(
    tmp_path,
) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    args = sample_args("indexnow_submit")
    prepared = WRITE_OPS["indexnow_submit"].prepare(args)
    plan = store.create("indexnow_submit", "https://a.example", args, prepared.summary)
    limiter = RateLimiter(tmp_path, max_per_day=5)

    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=None,
            audit=AuditLog(tmp_path),
            limiter=limiter,
            indexnow_transport=httpx.MockTransport(handler),
        )
    # Nothing was submitted, so the plan stays usable and the reservation is given back.
    assert store.get(plan.plan_id).state == "pending"
    limiter.consume("https://a.example", 5)


async def test_a_failed_audit_write_gives_the_reservation_back(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    store = PlanStore(tmp_path, 900)
    limiter = RateLimiter(tmp_path, max_per_day=1)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")

    class BrokenAudit(AuditLog):
        def record(self, event: str, **fields: object) -> None:
            raise OSError("audit log is unwritable")

    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(OSError):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=BrokenAudit(tmp_path),
                limiter=limiter,
            )
    assert transport.calls == []
    limiter.consume("https://a.example", 1)


async def test_terminal_state_write_failure_keeps_the_apply_lock(tmp_path) -> None:
    settings = fake_settings(tmp_path)

    class BrokenOutcomeStore(PlanStore):
        def mark_applied(self, plan_id: str):
            raise OSError("disk full after the upstream write succeeded")

    store = BrokenOutcomeStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = bing_transport({"AddSite": None})

    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(OSError, match="disk full"):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )

    assert len(transport.calls) == 1
    assert store.get(plan.plan_id).state == "pending"
    assert (tmp_path / "plans" / f"{plan.plan_id}.lock").exists()


async def test_unknown_outcome_write_failure_keeps_the_apply_lock(tmp_path) -> None:
    settings = fake_settings(tmp_path)

    class BrokenOutcomeStore(PlanStore):
        def mark_unknown(self, plan_id: str):
            raise OSError("disk full while recording the unknown outcome")

    store = BrokenOutcomeStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lost response", request=request)

    async with BingClient(settings, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OSError, match="disk full"):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )

    assert store.get(plan.plan_id).state == "pending"
    assert (tmp_path / "plans" / f"{plan.plan_id}.lock").exists()


def _write_context(tmp_path, settings):
    return AuditLog(settings.state_dir), RateLimiter(
        settings.state_dir, max_per_day=settings.max_writes_per_day
    )


async def test_one_step_write_sends_the_change_and_records_an_applied_plan(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    audit, limiter = _write_context(tmp_path, settings)
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        result = await execute_write(
            "add_site",
            sample_args("add_site"),
            settings=settings,
            client=client,
            audit=audit,
            limiter=limiter,
        )
    assert result["applied"] is True
    assert [request.url.path.rsplit("/", 1)[-1] for request in transport.calls] == ["AddSite"]
    stored = PlanStore(tmp_path, settings.plan_ttl_seconds).get(result["plan_id"])
    assert stored.state == "applied"
    events = [entry["event"] for entry in audit.entries()]
    assert events == ["plan_created", "plan_apply_attempted", "plan_apply_succeeded"]


async def test_one_step_write_is_refused_when_writes_are_disabled(tmp_path) -> None:
    settings = fake_settings(tmp_path, allow_writes=False)
    audit, limiter = _write_context(tmp_path, settings)
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PolicyDenied):
            await execute_write(
                "add_site",
                sample_args("add_site"),
                settings=settings,
                client=client,
                audit=audit,
                limiter=limiter,
            )
    assert transport.calls == []
    assert not list((tmp_path / "plans").glob("*.json"))


async def test_one_step_write_still_honours_the_denylist(tmp_path) -> None:
    settings = fake_settings(tmp_path, denied_sites=("a.example",))
    audit, limiter = _write_context(tmp_path, settings)
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PolicyDenied):
            await execute_write(
                "add_site",
                sample_args("add_site"),
                settings=settings,
                client=client,
                audit=audit,
                limiter=limiter,
            )
    assert transport.calls == []


async def test_one_step_write_still_honours_the_local_daily_ceiling(tmp_path) -> None:
    settings = fake_settings(tmp_path, max_writes_per_day=1)
    audit, limiter = _write_context(tmp_path, settings)
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        await execute_write(
            "add_site",
            sample_args("add_site"),
            settings=settings,
            client=client,
            audit=audit,
            limiter=limiter,
        )
        with pytest.raises(QuotaExceeded):
            await execute_write(
                "add_site",
                sample_args("add_site"),
                settings=settings,
                client=client,
                audit=audit,
                limiter=limiter,
            )
    assert [request.url.path.rsplit("/", 1)[-1] for request in transport.calls] == ["AddSite"]


async def test_a_failed_one_step_write_leaves_no_applicable_plan(tmp_path) -> None:
    """Nobody reviews a one-step plan, so a failed one must not stay applicable."""
    settings = fake_settings(tmp_path, max_writes_per_day=1)
    audit, limiter = _write_context(tmp_path, settings)
    limiter.consume("https://a.example", 1)
    transport = bing_transport({"AddSite": None})
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(QuotaExceeded) as caught:
            await execute_write(
                "add_site",
                sample_args("add_site"),
                settings=settings,
                client=client,
                audit=audit,
                limiter=limiter,
            )
    assert transport.calls == []
    plan_id = caught.value.to_dict()["details"]["plan_id"]
    store = PlanStore(tmp_path, settings.plan_ttl_seconds)
    assert store.get(plan_id).state == "rejected"
    assert "plan_discarded" in [entry["event"] for entry in audit.entries()]


async def test_an_unknown_outcome_keeps_its_state_and_names_its_plan(tmp_path) -> None:
    settings = fake_settings(tmp_path)
    audit, limiter = _write_context(tmp_path, settings)
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"Message": "down"}))
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(PlanUnknownOutcome) as caught:
            await execute_write(
                "add_site",
                sample_args("add_site"),
                settings=settings,
                client=client,
                audit=audit,
                limiter=limiter,
            )
    plan_id = caught.value.to_dict()["details"]["plan_id"]
    assert PlanStore(tmp_path, settings.plan_ttl_seconds).get(plan_id).state == "unknown_outcome"


ROLE_CODE = "auth-secret-value"
ROLE_ARGS = {
    "site_url": "https://a.example",
    "delegated_url": "https://a.example",
    "user_email": "x@y.example",
    "authentication_code": ROLE_CODE,
    "is_administrator": False,
    "is_read_only": True,
}


async def test_a_write_result_echoing_the_code_leaves_redacted(tmp_path) -> None:
    """The write result is an exit too, and nothing redacted it before this change."""
    transport = bing_transport(
        {
            "AddSiteRoles": {
                "AuthenticationCode": ROLE_CODE,
                "Message": f"accepted {ROLE_CODE}",
            }
        }
    )
    settings = fake_settings(tmp_path)
    async with BingClient(settings, transport=transport) as client:
        outcome = await execute_write(
            "add_site_roles",
            dict(ROLE_ARGS),
            settings=settings,
            client=client,
            audit=AuditLog(settings.state_dir),
            limiter=RateLimiter(settings.state_dir, max_per_day=10),
        )

    assert outcome["applied"] is True
    assert ROLE_CODE not in json.dumps(outcome, default=str)
    assert outcome["result"]["AuthenticationCode"] == REDACTED
    assert outcome["result"]["Message"] == f"accepted {REDACTED}"


async def test_the_audit_trail_never_receives_the_code_from_a_failed_write(tmp_path) -> None:
    """A rejection quoting the code would otherwise be written to disk verbatim."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400, json={"Message": f"code {ROLE_CODE} rejected"}, request=request
        )
    )
    settings = fake_settings(tmp_path)
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(InvalidRequest):
            await execute_write(
                "add_site_roles",
                dict(ROLE_ARGS),
                settings=settings,
                client=client,
                audit=AuditLog(settings.state_dir),
                limiter=RateLimiter(settings.state_dir, max_per_day=10),
            )

    written = (settings.state_dir / "audit.jsonl").read_text()
    assert ROLE_CODE not in written
    assert REDACTED in written


async def test_the_audit_trail_never_records_the_api_key(tmp_path) -> None:
    """The key lives in the query string, so an upstream quoting our URL would log it."""
    fake_key = "FAKE-KEY-DO-NOT-USE-0123456789"
    settings = fake_settings(tmp_path, api_key=fake_key)
    store = PlanStore(tmp_path, 900)
    plan = store.create("add_site", "https://a.example", sample_args("add_site"), "x")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400, json={"Message": f"bad request for {request.url}"}, request=request
        )
    )
    async with BingClient(settings, transport=transport) as client:
        with pytest.raises(InvalidRequest):
            await apply_plan(
                plan.plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(tmp_path),
                limiter=RateLimiter(tmp_path, max_per_day=None),
            )
    entries = AuditLog(tmp_path).entries()
    assert [entry["event"] for entry in entries] == ["plan_apply_attempted", "plan_apply_failed"]
    assert fake_key not in json.dumps(entries)
