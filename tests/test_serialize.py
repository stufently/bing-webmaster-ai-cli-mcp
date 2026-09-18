from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from bing_webmaster_ai_cli_mcp._serialize import (
    decode,
    encode_bing_datetime,
    parse_bing_datetime,
    unwrap,
)
from bing_webmaster_ai_cli_mcp.errors import MalformedResponse


def test_unwrap_returns_d_and_rejects_other_shapes() -> None:
    assert unwrap({"d": [1]}) == [1]
    with pytest.raises(MalformedResponse):
        unwrap({"ErrorCode": 1, "Message": "bad"})


@pytest.mark.parametrize(
    ("raw", "offset"),
    [
        ("/Date(1316156400000-0700)/", timedelta(hours=-7)),
        ("/Date(1316156400000+0530)/", timedelta(hours=5, minutes=30)),
        ("/Date(1316156400000)/", timedelta(0)),
    ],
)
def test_tick_date_offsets(raw: str, offset: timedelta) -> None:
    parsed = parse_bing_datetime(raw)
    assert parsed.utcoffset() == offset
    assert parsed.timestamp() == 1316156400.0


def test_escaped_slashes_are_accepted() -> None:
    assert parse_bing_datetime("\\/Date(0)\\/") == datetime(1970, 1, 1, tzinfo=UTC)


def test_malformed_date_raises() -> None:
    with pytest.raises(MalformedResponse):
        parse_bing_datetime("2011-09-16T00:00:00Z")


def test_decode_walks_and_drops_type_markers() -> None:
    assert decode({"__type": "X", "When": "/Date(0)/", "Rows": [{"__type": "Y"}]}) == {
        "When": datetime(1970, 1, 1, tzinfo=UTC),
        "Rows": [{}],
    }


def test_encode_datetime_uses_aspnet_ticks() -> None:
    value = datetime(1970, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    assert encode_bing_datetime(value) == "/Date(-7200000+0200)/"


def test_out_of_range_tick_date_is_a_malformed_response() -> None:
    with pytest.raises(MalformedResponse):
        parse_bing_datetime("/Date(99999999999999999)/")
    with pytest.raises(MalformedResponse):
        parse_bing_datetime("/Date(0+9900)/")


def test_timezone_minutes_must_be_below_sixty() -> None:
    with pytest.raises(MalformedResponse):
        parse_bing_datetime("/Date(0+0060)/")
