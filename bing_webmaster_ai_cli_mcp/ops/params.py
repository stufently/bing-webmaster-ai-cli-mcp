from __future__ import annotations

from typing import Any

from ..client import BingClient
from ._common import fetch, normalise_site, row_read


@row_read
async def query_parameters(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetQueryParameters", {"siteUrl": normalise_site(site_url)})
