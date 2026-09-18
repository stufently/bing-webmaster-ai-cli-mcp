"""The only module that executes a mutating Bing API request."""

from __future__ import annotations

from typing import Any

import httpx

from .audit import AuditLog
from .client import BingClient
from .config import Settings
from .errors import BingWebmasterError, PlanUnknownOutcome
from .limits import RateLimiter
from .ops import indexnow
from .plans import PlanStore, create_write_plan
from .render import redact, secret_values
from .writes import prepare_write


async def apply_plan(
    plan_id: str,
    *,
    settings: Settings,
    store: PlanStore,
    client: BingClient | None,
    audit: AuditLog,
    limiter: RateLimiter,
    indexnow_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    with store.applying(plan_id) as lease:
        plan = lease.plan
        prepared = prepare_write(plan.operation, plan.args)
        if prepared.method != "IndexNow" and client is None:
            raise RuntimeError("a Bing client is required to apply this plan")
        # Re-checked here, not just at plan creation: the denylist may have grown since,
        # and a plan can sit pending for a whole BING_WM_PLAN_TTL_SECONDS.
        try:
            settings.check_site_allowed(plan.site_url)
        except BingWebmasterError as exc:
            audit.record("plan_apply_denied", plan_id=plan_id, error=exc.to_dict())
            raise

        # Reserved before the request, not counted after it: a ceiling that refuses once
        # the write has already been sent would leave the plan pending and invite a retry.
        reserved_day = limiter.consume(plan.site_url, prepared.cost)
        try:
            if prepared.method == "IndexNow":
                # Fetching the key file sends no submission, so a failure or an interrupt
                # here is an ordinary failure and stays outside the unknown-outcome zone.
                async with httpx.AsyncClient(
                    transport=indexnow_transport, timeout=30.0
                ) as preflight:
                    await indexnow.verify_key_file(
                        preflight,
                        prepared.body["host"],
                        prepared.body["key"],
                        prepared.body["keyLocation"],
                    )
            audit.record(
                "plan_apply_attempted",
                plan_id=plan_id,
                operation=plan.operation,
                site=plan.site_url,
            )
        except BingWebmasterError as exc:
            limiter.release(plan.site_url, prepared.cost, day=reserved_day)
            audit.record("plan_apply_failed", plan_id=plan_id, error=exc.to_dict())
            raise
        except BaseException:
            limiter.release(plan.site_url, prepared.cost, day=reserved_day)
            raise

        try:
            if prepared.method == "IndexNow":
                async with httpx.AsyncClient(
                    transport=indexnow_transport, timeout=30.0
                ) as indexnow_http:
                    result = await indexnow.submit(
                        indexnow_http,
                        prepared.body["host"],
                        prepared.body["key"],
                        prepared.body["urlList"],
                        prepared.body["keyLocation"],
                    )
            else:
                result = await client.call(
                    prepared.method,
                    body=prepared.body,
                    mutating=True,
                )
        except PlanUnknownOutcome as exc:
            try:
                store.mark_unknown(plan_id)
            except BaseException:
                lease.preserve_lock()
                raise
            audit.record("plan_apply_unknown", plan_id=plan_id, error=exc.to_dict())
            raise
        except BingWebmasterError as exc:
            # Everything reaching here failed before or during dispatch: a lost or
            # ambiguous response is a PlanUnknownOutcome and was handled above.
            limiter.release(plan.site_url, prepared.cost, day=reserved_day)
            audit.record("plan_apply_failed", plan_id=plan_id, error=exc.to_dict())
            raise
        except BaseException as exc:
            # Ctrl-C or any other interruption once dispatch has begun: the lock is
            # released by the context manager, so a plan left pending would invite a
            # retry of a write that may already have been sent.
            try:
                store.mark_unknown(plan_id)
            except BaseException:
                lease.preserve_lock()
                raise
            audit.record(
                "plan_apply_unknown",
                plan_id=plan_id,
                error={"code": "INTERRUPTED", "type": type(exc).__name__},
            )
            raise

        try:
            store.mark_applied(plan_id)
        except BaseException:
            lease.preserve_lock()
            raise
        audit.record("plan_apply_succeeded", plan_id=plan_id)
        return {
            "applied": True,
            "plan_id": plan_id,
            "operation": plan.operation,
            # A write leaves by this door, and reads leave by ``ops._common.fetch``.
            # Both are redacted, so neither is the way a code Bing echoed back reaches a
            # transcript. The literals we sent are hidden too, not only the named fields.
            "result": redact(result, secret_values(prepared.body)),
        }


async def execute_write(
    operation: str,
    args: dict[str, Any],
    *,
    settings: Settings,
    client: BingClient | None,
    audit: AuditLog,
    limiter: RateLimiter,
    indexnow_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Record a plan and apply it in the same call.

    The one-step path is not a second write implementation: it still creates the durable
    plan record and then goes through ``apply_plan``, so the denylist, Bing's own
    submission quota, the local daily ceiling, one-shot application and the audit trail
    are exactly the ones the reviewed path uses. What it drops is the human between the
    two steps, which is what ``BING_WM_ALLOW_WRITES`` decides.
    """
    settings.check_writes_allowed()
    store = PlanStore(settings.state_dir, settings.plan_ttl_seconds)
    plan = await create_write_plan(operation, args, settings=settings, client=client)
    try:
        return await apply_plan(
            plan.plan_id,
            settings=settings,
            store=store,
            client=client,
            audit=audit,
            limiter=limiter,
            indexnow_transport=indexnow_transport,
        )
    except BingWebmasterError as exc:
        _close_out(store, plan.plan_id, audit)
        # Whatever went wrong, the caller has to be able to find the record: for an
        # unknown outcome the plan id is the only way to tell which write to check
        # against Bing.
        exc.details = {**(exc.details or {}), "plan_id": plan.plan_id}
        raise
    except BaseException:
        _close_out(store, plan.plan_id, audit)
        raise


def _close_out(store: PlanStore, plan_id: str, audit: AuditLog) -> None:
    """Refuse a one-step plan whose write never happened.

    Nobody is coming back to it. Left pending it would stay applicable by hand for a
    whole TTL, which would send a change no human ever reviewed. A plan the apply
    boundary already made terminal - applied, or an unknown outcome - keeps exactly the
    state it recorded, and a failure to clean up never masks the original error.
    """
    try:
        if store.get(plan_id).state != "pending":
            return
        store.reject(plan_id)
        audit.record("plan_discarded", plan_id=plan_id, reason="one_step_write_failed")
    except BingWebmasterError:
        return
    except OSError:
        return
