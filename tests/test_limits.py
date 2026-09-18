from __future__ import annotations

import pytest

from bing_webmaster_ai_cli_mcp.errors import QuotaExceeded
from bing_webmaster_ai_cli_mcp.limits import RateLimiter


def test_configured_consumption_persists(tmp_path) -> None:
    limiter = RateLimiter(tmp_path, max_per_day=5)
    limiter.consume("site", 4)
    RateLimiter(tmp_path, max_per_day=5).consume("site", 1)
    with pytest.raises(QuotaExceeded):
        RateLimiter(tmp_path, max_per_day=5).consume("site", 1)


def test_no_local_ceiling_is_not_a_hardcoded_quota(tmp_path) -> None:
    limiter = RateLimiter(tmp_path, max_per_day=None)
    limiter.consume("site", 1_000_000)
    limiter.consume("site", 1_000_000)


def test_counters_roll_over_on_new_utc_day(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.limits._today", lambda: "2026-08-25")
    RateLimiter(tmp_path, max_per_day=1).consume("site")
    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.limits._today", lambda: "2026-08-26")
    RateLimiter(tmp_path, max_per_day=1).consume("site")


def test_release_credits_the_day_the_reservation_was_taken_on(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.limits._today", lambda: "2026-08-25")
    limiter = RateLimiter(tmp_path, max_per_day=1)
    day = limiter.consume("site")

    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.limits._today", lambda: "2026-08-26")
    limiter.release("site", day=day)
    # The new day must still have its whole ceiling: the release belonged to the old one.
    limiter.consume("site")
    with pytest.raises(QuotaExceeded):
        limiter.consume("site")

    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.limits._today", lambda: "2026-08-25")
    limiter.consume("site", 1)


def test_release_never_drops_below_zero(tmp_path) -> None:
    limiter = RateLimiter(tmp_path, max_per_day=2)
    limiter.release("site", 5)
    limiter.consume("site", 2)
    with pytest.raises(QuotaExceeded):
        limiter.consume("site", 1)
