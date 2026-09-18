"""Durable one-shot plan records. Creating a plan never sends a mutation."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, computed_field

from .audit import AuditLog
from .client import BingClient
from .config import Settings
from .errors import (
    InvalidRequest,
    MalformedResponse,
    PlanAlreadyApplied,
    PlanExpired,
    PlanNotFound,
    QuotaExceeded,
)
from .ops._common import normalise_site
from .render import redact_secrets
from .writes import prepare_write

_PLAN_ID = re.compile(r"^[0-9a-f]{32}$")
_LOCK_OWNER = re.compile(r"^pid=([1-9][0-9]*)(?:\ntoken=([0-9a-f]{32}))?\n?$")


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("write returned zero bytes")
        remaining = remaining[written:]


@dataclass
class ApplyLease:
    """A locked plan and whether recovery must retain its lock."""

    plan: Plan
    _retain_lock: bool = False

    def preserve_lock(self) -> None:
        self._retain_lock = True


def _same_lock(path: Path, original: os.stat_result, contents: str) -> bool:
    try:
        current = path.lstat()
        current_contents = path.read_text()
    except (OSError, UnicodeError):
        return False
    return (current.st_dev, current.st_ino) == (
        original.st_dev,
        original.st_ino,
    ) and current_contents == contents


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Plan(BaseModel):
    plan_id: str
    operation: str
    site_url: str
    args: dict[str, Any]
    summary: str
    created_at: datetime
    expires_at: datetime
    applied_at: datetime | None = None
    rejected_at: datetime | None = None
    unknown_outcome_at: datetime | None = None

    @computed_field
    @property
    def state(self) -> str:
        if self.applied_at is not None:
            return "applied"
        if self.rejected_at is not None:
            return "rejected"
        if self.unknown_outcome_at is not None:
            return "unknown_outcome"
        if self.is_expired():
            return "expired"
        return "pending"

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def public_dump(self, *, reveal_secrets: bool = False) -> dict[str, Any]:
        """The plan as it may be shown, with the secrets in its arguments hidden.

        The record on disk keeps the real values - the plan has to be appliable, and
        ``add_site_roles`` cannot be sent without its ``authentication_code``. What
        changes is that showing a plan is no longer a way to read one back: this is the
        only serialization ``bing_plan_show``, ``bing_plan_list`` and the CLI use, and
        only the CLI can pass ``reveal_secrets``, from an explicit operator flag.
        """
        data = self.model_dump(mode="json")
        if not reveal_secrets:
            data["args"] = redact_secrets(data["args"])
        return data


class PlanStore:
    def __init__(self, state_dir: Path, ttl_seconds: int) -> None:
        self._directory = Path(state_dir) / "plans"
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory.chmod(0o700)
        self._ttl = ttl_seconds

    def _path(self, plan_id: str) -> Path:
        if _PLAN_ID.fullmatch(plan_id) is None:
            raise PlanNotFound(f"no plan {plan_id!r}")
        return self._directory / f"{plan_id}.json"

    def _lock_path(self, plan_id: str) -> Path:
        return self._path(plan_id).with_suffix(".lock")

    def create(self, operation: str, site_url: str, args: dict[str, Any], summary: str) -> Plan:
        now = datetime.now(UTC)
        plan = Plan(
            plan_id=secrets.token_hex(16),
            operation=operation,
            site_url=site_url,
            args=args,
            summary=summary,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl),
        )
        self._write(plan)
        return plan

    def _write(self, plan: Plan) -> None:
        path = self._path(plan.plan_id)
        temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_all(descriptor, plan.model_dump_json(indent=2).encode())
            os.fsync(descriptor)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def get(self, plan_id: str) -> Plan:
        path = self._path(plan_id)
        if not path.exists():
            raise PlanNotFound(f"no plan {plan_id}", suggestion="run `bing-wm plan list`")
        return Plan.model_validate_json(path.read_text())

    def list(self) -> list[Plan]:
        return sorted(
            (Plan.model_validate_json(path.read_text()) for path in self._directory.glob("*.json")),
            key=lambda plan: plan.created_at,
            reverse=True,
        )

    def ensure_pending(self, plan_id: str) -> Plan:
        plan = self.get(plan_id)
        if plan.applied_at or plan.rejected_at or plan.unknown_outcome_at:
            raise PlanAlreadyApplied(f"plan {plan_id} is already {plan.state}")
        if plan.is_expired():
            raise PlanExpired(
                f"plan {plan_id} expired at {plan.expires_at.isoformat()}",
                suggestion="create a fresh plan and review its current arguments",
            )
        return plan

    def _record_outcome(self, plan_id: str, field: str) -> Plan:
        """Write a terminal state.

        Deliberately not routed through ``ensure_pending``: expiry is checked before a
        mutation is sent, never after. A plan whose TTL elapses while its request is in
        flight must still record what happened to it, or the operator is told "expired"
        about a write that already reached Bing.
        """
        plan = self.get(plan_id)
        if plan.applied_at or plan.rejected_at or plan.unknown_outcome_at:
            raise PlanAlreadyApplied(f"plan {plan_id} is already {plan.state}")
        updated = plan.model_copy(update={field: datetime.now(UTC)})
        self._write(updated)
        return updated

    def mark_applied(self, plan_id: str) -> Plan:
        return self._record_outcome(plan_id, "applied_at")

    def mark_unknown(self, plan_id: str) -> Plan:
        return self._record_outcome(plan_id, "unknown_outcome_at")

    def reject(self, plan_id: str) -> Plan:
        """Refuse a plan.

        Unlike the two apply outcomes this is a fresh decision, not the record of a
        request already sent, so it keeps the expiry check. It takes the same exclusive
        lock for the whole transition: testing for the lock and then writing would let
        an apply start in between and lose its own outcome.
        """
        with self._locked(plan_id, "wait for it to finish"):
            self.ensure_pending(plan_id)
            return self._record_outcome(plan_id, "rejected_at")

    def set_expiry(self, plan_id: str, when: datetime) -> None:
        self._write(self.get(plan_id).model_copy(update={"expires_at": when}))

    def recover_lock(self, plan_id: str, audit: AuditLog) -> tuple[Plan, int]:
        """Remove a lock whose owning process is gone without enabling a duplicate write.

        A dead process may have exited before or after dispatch. An unfinished plan is
        therefore made terminal with ``unknown_outcome`` before its lock is removed.
        Already-terminal plans keep their recorded outcome.
        """
        plan = self.get(plan_id)
        path = self._lock_path(plan_id)
        try:
            contents = path.read_text()
            identity = path.lstat()
        except FileNotFoundError as exc:
            raise InvalidRequest(f"plan {plan_id} has no apply lock to recover") from exc
        except (OSError, UnicodeError) as exc:
            raise InvalidRequest(f"plan {plan_id} has a malformed apply lock") from exc
        owner = _LOCK_OWNER.fullmatch(contents)
        if owner is None:
            raise InvalidRequest(
                f"plan {plan_id} has a malformed apply lock",
                suggestion="inspect the lock file and running processes before changing it",
            )
        owner_pid = int(owner.group(1))
        if _pid_is_alive(owner_pid):
            raise PlanAlreadyApplied(
                f"plan {plan_id} is locked by live process {owner_pid}; wait for it to finish"
            )

        previous_state = plan.state
        if not (plan.applied_at or plan.rejected_at or plan.unknown_outcome_at):
            plan = self.mark_unknown(plan_id)
        audit.record(
            "plan_lock_recovery_started",
            plan_id=plan_id,
            owner_pid=owner_pid,
            previous_state=previous_state,
            state=plan.state,
        )
        try:
            if not _same_lock(path, identity, contents):
                raise InvalidRequest(f"plan {plan_id} apply lock changed during recovery")
            path.unlink()
        except FileNotFoundError as exc:
            raise InvalidRequest(f"plan {plan_id} apply lock was already removed") from exc
        except OSError as exc:
            raise InvalidRequest(f"plan {plan_id} apply lock could not be removed") from exc
        audit.record(
            "plan_lock_recovered",
            plan_id=plan_id,
            owner_pid=owner_pid,
            previous_state=previous_state,
            state=plan.state,
        )
        return plan, owner_pid

    @contextmanager
    def _locked(self, plan_id: str, advice: str) -> Iterator[ApplyLease]:
        path = self._lock_path(plan_id)
        lease: ApplyLease | None = None
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise PlanAlreadyApplied(
                f"plan {plan_id} is locked by an apply attempt; {advice}"
            ) from exc
        identity = os.fstat(descriptor)
        contents = f"pid={os.getpid()}\ntoken={secrets.token_hex(16)}\n"
        try:
            try:
                _write_all(descriptor, contents.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            lease = ApplyLease(self.get(plan_id))
            yield lease
        finally:
            if (lease is None or not lease._retain_lock) and _same_lock(path, identity, contents):
                path.unlink()

    @contextmanager
    def applying(self, plan_id: str) -> Iterator[ApplyLease]:
        with self._locked(plan_id, "its outcome may be unknown") as lease:
            lease.plan = self.ensure_pending(plan_id)
            yield lease


async def create_write_plan(
    operation: str,
    args: dict[str, Any],
    *,
    settings: Settings,
    client: BingClient | None,
) -> Plan:
    prepared = prepare_write(operation, args)
    if operation == "indexnow_submit":
        site_url = f"https://{prepared.body['host']}"
    else:
        site_url = normalise_site(str(args.get("site_url", "")))
    settings.check_site_allowed(site_url)

    if prepared.quota_method is not None:
        if client is None:
            raise RuntimeError("a Bing client is required for quota-aware plans")
        quota = await client.call(prepared.quota_method, {"siteUrl": site_url})
        if not isinstance(quota, dict) or not isinstance(quota.get("DailyQuota"), int):
            raise MalformedResponse(
                f"{prepared.quota_method}: DailyQuota is missing or not an integer"
            )
        remaining = quota["DailyQuota"]
        if prepared.cost > remaining:
            raise QuotaExceeded(
                f"{prepared.cost} submissions requested; Bing reports {remaining} left today",
                suggestion="reduce the plan or wait for Bing's quota to reset",
                details={"requested": prepared.cost, "daily_quota": remaining},
            )

    store = PlanStore(settings.state_dir, settings.plan_ttl_seconds)
    plan = store.create(operation, site_url, args, prepared.summary)
    AuditLog(settings.state_dir).record(
        "plan_created", plan_id=plan.plan_id, operation=operation, site=site_url
    )
    return plan
