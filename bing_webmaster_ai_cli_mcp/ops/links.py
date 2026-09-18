from __future__ import annotations

from typing import Any

from ..client import BingClient
from ._common import fetch, normalise_site, row_read


@row_read
async def link_counts(client: BingClient, site_url: str, page: int = 0) -> list[dict[str, Any]]:
    return await fetch(client, "GetLinkCounts", {"siteUrl": normalise_site(site_url), "page": page})


@row_read
async def url_links(
    client: BingClient, site_url: str, url: str, page: int = 0
) -> list[dict[str, Any]]:
    return await fetch(
        client,
        "GetUrlLinks",
        {"siteUrl": normalise_site(site_url), "link": url, "page": page},
    )


@row_read
async def connected_pages(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetConnectedPages", {"siteUrl": normalise_site(site_url)})
