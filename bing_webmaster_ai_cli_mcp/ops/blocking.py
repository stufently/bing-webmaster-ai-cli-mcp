from __future__ import annotations

from typing import Any

from ..client import BingClient
from ._common import fetch, normalise_site, row_read


@row_read
async def blocked_urls(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetBlockedUrls", {"siteUrl": normalise_site(site_url)})


@row_read
async def page_preview_blocks(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetActivePagePreviewBlocks", {"siteUrl": normalise_site(site_url)})


@row_read
async def deep_link_blocks(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetDeepLinkBlocks", {"siteUrl": normalise_site(site_url)})
