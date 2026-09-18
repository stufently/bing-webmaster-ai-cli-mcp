"""Serialization for Bing's JSON endpoint and its ASP.NET date representation."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .errors import MalformedResponse

_TICKS = re.compile(r"^\\?/Date\((?P<ms>-?\d+)(?:(?P<sign>[+-])(?P<hh>\d{2})(?P<mm>\d{2}))?\)\\?/$")


def unwrap(payload: Any) -> Any:
    if not isinstance(payload, dict) or "d" not in payload:
        raise MalformedResponse(
            "response body has no 'd' envelope",
            suggestion="inspect the status and upstream response contract",
            details={"keys": sorted(payload) if isinstance(payload, dict) else None},
        )
    return payload["d"]


def parse_bing_datetime(value: str) -> datetime:
    match = _TICKS.fullmatch(value)
    if match is None:
        raise MalformedResponse(
            f"not an ASP.NET tick date: {value!r}",
            suggestion="Bing JSON dates must use /Date(milliseconds±hhmm)/",
        )
    try:
        moment = datetime.fromtimestamp(int(match["ms"]) / 1000, tz=UTC)
        if match["sign"] is None:
            return moment
        if int(match["mm"]) >= 60:
            raise ValueError("timezone minutes must be below 60")
        offset = timedelta(hours=int(match["hh"]), minutes=int(match["mm"]))
        if match["sign"] == "-":
            offset = -offset
        return moment.astimezone(timezone(offset))
    except (OSError, OverflowError, ValueError) as exc:
        # A tick count or an offset outside what datetime can represent.
        raise MalformedResponse(f"tick date is out of range: {value!r}") from exc


def encode_bing_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Bing datetimes must be timezone-aware")
    milliseconds = int(value.timestamp() * 1000)
    offset = value.utcoffset() or timedelta(0)
    sign = "+" if offset >= timedelta(0) else "-"
    total_minutes = abs(int(offset.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"/Date({milliseconds}{sign}{hours:02d}{minutes:02d})/"


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items() if key != "__type"}
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, str) and _TICKS.fullmatch(value):
        return parse_bing_datetime(value)
    return value
