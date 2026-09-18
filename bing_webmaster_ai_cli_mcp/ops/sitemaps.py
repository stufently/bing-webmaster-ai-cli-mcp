from __future__ import annotations

from typing import Any

from ..client import BingClient
from ._common import fetch, normalise_site, row_read


@row_read
async def feeds(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetFeeds", {"siteUrl": normalise_site(site_url)})


@row_read
async def feed_details(client: BingClient, site_url: str, feed_url: str) -> list[dict[str, Any]]:
    return await fetch(
        client,
        "GetFeedDetails",
        {"siteUrl": normalise_site(site_url), "feedUrl": feed_url},
    )
