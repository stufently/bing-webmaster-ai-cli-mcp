"""Public error taxonomy.

The ``code`` strings are a wire contract. Adding a code is compatible; renaming one is not.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Caller mistakes
    INVALID_REQUEST = "INVALID_REQUEST"
    # Credentials and ownership
    AUTH_FAILED = "AUTH_FAILED"
    SITE_NOT_VERIFIED = "SITE_NOT_VERIFIED"
    # Budget
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    # Upstream
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    # Plans
    PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PLAN_ALREADY_APPLIED = "PLAN_ALREADY_APPLIED"
    PLAN_UNKNOWN_OUTCOME = "PLAN_UNKNOWN_OUTCOME"
    # Policy
    POLICY_DENIED = "POLICY_DENIED"
    # Catch-all
    INTERNAL = "INTERNAL"


class BingWebmasterError(Exception):
    """Base for stable, serializable errors. Base instances are not retryable."""

    code: ErrorCode = ErrorCode.INTERNAL
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.retry_after = retry_after
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.suggestion is not None:
            payload["suggestion"] = self.suggestion
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        if self.details is not None:
            payload["details"] = self.details
        return payload


class InvalidRequest(BingWebmasterError):
    """Arguments are invalid; an unchanged retry will fail again."""

    code = ErrorCode.INVALID_REQUEST


class AuthFailed(BingWebmasterError):
    """Credentials are absent or rejected; retrying without new credentials cannot help."""

    code = ErrorCode.AUTH_FAILED


class SiteNotVerified(BingWebmasterError):
    """The account does not own the target; retries cannot establish ownership."""

    code = ErrorCode.SITE_NOT_VERIFIED


class QuotaExceeded(BingWebmasterError):
    """The current quota is exhausted; retrying does not create more quota."""

    code = ErrorCode.QUOTA_EXCEEDED


class RateLimited(BingWebmasterError):
    """The caller should retry a safe read after the indicated delay."""

    code = ErrorCode.RATE_LIMITED
    retryable = True


class UpstreamUnavailable(BingWebmasterError):
    """A connection or server failure is assumed transient for safe reads."""

    code = ErrorCode.UPSTREAM_UNAVAILABLE
    retryable = True


class MalformedResponse(BingWebmasterError):
    """The documented response contract was broken; retrying will not fix the parser."""

    code = ErrorCode.MALFORMED_RESPONSE


class PlanNotFound(BingWebmasterError):
    """No such recorded intent exists; retrying the identifier cannot create it."""

    code = ErrorCode.PLAN_NOT_FOUND


class PlanExpired(BingWebmasterError):
    """Stale intent must be planned and reviewed again, never retried automatically."""

    code = ErrorCode.PLAN_EXPIRED


class PlanAlreadyApplied(BingWebmasterError):
    """The one-shot plan has completed; retrying could duplicate the write."""

    code = ErrorCode.PLAN_ALREADY_APPLIED


class PlanUnknownOutcome(BingWebmasterError):
    """The request left but its response was lost; retrying could duplicate the write."""

    code = ErrorCode.PLAN_UNKNOWN_OUTCOME


class PolicyDenied(BingWebmasterError):
    """Local policy forbids the operation; retries cannot override policy."""

    code = ErrorCode.POLICY_DENIED


class InternalError(BingWebmasterError):
    """An unexpected local bug occurred; automatic retries risk repeating it."""

    code = ErrorCode.INTERNAL
