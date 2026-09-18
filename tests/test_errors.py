from __future__ import annotations

import pytest

from bing_webmaster_ai_cli_mcp.errors import (
    BingWebmasterError,
    ErrorCode,
    PlanUnknownOutcome,
    QuotaExceeded,
    RateLimited,
)


def test_to_dict_is_the_wire_contract() -> None:
    assert RateLimited("slow down", retry_after=30).to_dict() == {
        "code": "RATE_LIMITED",
        "message": "slow down",
        "retryable": True,
        "retry_after": 30,
    }


def test_optional_fields_are_omitted_when_absent() -> None:
    assert set(QuotaExceeded("no quota left").to_dict()) == {"code", "message", "retryable"}


def test_details_and_suggestion_round_trip() -> None:
    error = QuotaExceeded("none", suggestion="wait", details={"daily": 0})
    assert error.to_dict()["suggestion"] == "wait"
    assert error.to_dict()["details"] == {"daily": 0}


def test_unknown_outcome_and_quota_are_not_retryable() -> None:
    assert PlanUnknownOutcome("x").retryable is False
    assert QuotaExceeded("x").retryable is False


def test_every_error_code_has_a_class() -> None:
    subclasses = {subclass.code for subclass in _all_subclasses(BingWebmasterError)}
    assert subclasses == set(ErrorCode)


def _all_subclasses(cls: type[BingWebmasterError]):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _all_subclasses(subclass)


def test_errors_are_catchable_by_the_base() -> None:
    with pytest.raises(BingWebmasterError):
        raise RateLimited("x")
