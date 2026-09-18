from __future__ import annotations

from datetime import date
from typing import Any

from ..client import BingClient
from ._common import fetch, row_read, single_record


def _base(keyword: str, country: str, language: str) -> dict[str, str]:
    return {"q": keyword, "country": country, "language": language}


@single_record
async def keyword(
    client: BingClient,
    keyword: str,
    country: str,
    language: str,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    request = _base(keyword, country, language)
    request.update({"startDate": start_date.isoformat(), "endDate": end_date.isoformat()})
    return await fetch(client, "GetKeyword", request)


@row_read
async def keyword_stats(
    client: BingClient, keyword: str, country: str, language: str
) -> list[dict[str, Any]]:
    return await fetch(client, "GetKeywordStats", _base(keyword, country, language))


@row_read
async def related_keywords(
    client: BingClient,
    keyword: str,
    country: str,
    language: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    request = _base(keyword, country, language)
    request.update({"startDate": start_date.isoformat(), "endDate": end_date.isoformat()})
    return await fetch(client, "GetRelatedKeywords", request)
