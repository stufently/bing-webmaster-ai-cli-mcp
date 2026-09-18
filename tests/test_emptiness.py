from __future__ import annotations

from typing import Any

import pytest

from bing_webmaster_ai_cli_mcp.emptiness import (
    EMPTY_RESPONSE_NOTE,
    ROWS,
    SINGLE_RECORD,
    empty_response_report,
    read_shape,
    returned_no_rows,
)
from bing_webmaster_ai_cli_mcp.ops import crawl


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"result": []},
        # GetLinkCounts for a site whose GetCrawlStats reported 1700 inbound links.
        {"Links": [], "TotalPages": 0},
        # The bing_crawl_issues summary for a site with 4 crawl errors in GetCrawlStats.
        {"total": 0, "categories": {}, "http_codes": {}, "issues": []},
    ],
)
def test_a_response_carrying_no_rows_is_recognised_as_silence(payload: Any) -> None:
    assert returned_no_rows(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        [{"Url": "https://a.example"}],
        [None],
        [0],
        {"Links": [{"Url": "https://a.example"}], "TotalPages": 0},
        {"total": 1, "categories": {"http_404": 1}, "http_codes": {}, "issues": [{"a": 1}]},
    ],
)
def test_a_response_carrying_rows_is_a_measurement(payload: Any) -> None:
    assert returned_no_rows(payload) is False


@pytest.mark.parametrize(
    "payload",
    [
        # A single record has no rows to be empty of, and its zeros are real readings.
        {"HttpStatus": 0, "IsPage": True},
        {"DailyQuota": 0, "MonthlyQuota": 0},
        {},
        None,
        0,
        "",
    ],
)
def test_a_record_without_row_collections_is_never_called_empty(payload: Any) -> None:
    assert returned_no_rows(payload) is False
    assert empty_response_report(payload) is None


def test_the_report_states_that_nothing_was_measured() -> None:
    report = empty_response_report([])
    assert report == {"rows_returned": 0, "measured": False, "note": EMPTY_RESPONSE_NOTE}


def test_the_note_forbids_reading_silence_as_a_clean_bill_of_health() -> None:
    note = EMPTY_RESPONSE_NOTE.casefold()
    assert "not a measurement" in note
    assert "no problems found" in note


# The live GetCrawlSettings shape: one record whose CrawlRate is the hourly-rate array.
CRAWL_SETTINGS = {"CrawlBoostAvailable": True, "CrawlBoostEnabled": False, "CrawlRate": []}


def test_a_single_record_with_an_empty_array_field_is_not_silence() -> None:
    """CrawlRate is a field of the one record Bing returned, not a list of findings."""
    assert returned_no_rows(CRAWL_SETTINGS, SINGLE_RECORD) is False
    assert empty_response_report(CRAWL_SETTINGS, SINGLE_RECORD) is None


def test_the_same_payload_shape_is_silence_for_a_read_that_carries_rows() -> None:
    """The shape cannot decide it: only the operation knows which of the two it is."""
    assert returned_no_rows({"Links": [], "TotalPages": 0}, ROWS) is True


def test_crawl_settings_declares_itself_a_single_record() -> None:
    assert read_shape(crawl.crawl_settings) == SINGLE_RECORD
    assert read_shape(crawl.crawl_stats) == ROWS


def test_an_undeclared_read_still_labels_its_silence() -> None:
    """The fallback warns; the coverage test is what refuses an undeclared read."""

    async def undeclared() -> None:  # pragma: no cover - never called
        return None

    assert read_shape(undeclared) == ROWS
