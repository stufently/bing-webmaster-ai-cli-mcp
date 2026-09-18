from __future__ import annotations

from typing import Any

from ..client import BingClient
from ._common import bool_param, fetch, normalise_site, row_read, single_record

# ``reveal_secrets`` is a parameter of these operations and deliberately not of any MCP
# tool: the MCP schemas do not offer it, and unknown tool arguments are refused, so a
# model cannot ask for a verification code however it is prompted. Only an operator
# typing the CLI flag can, which is the person who needs one - to put the proof on the
# site during onboarding.


@row_read
async def list_sites(client: BingClient, reveal_secrets: bool = False) -> list[dict[str, Any]]:
    return await fetch(client, "GetUserSites", reveal_secrets=reveal_secrets)


@row_read
async def site_roles(
    client: BingClient,
    site_url: str,
    include_all_subdomains: bool = False,
    reveal_secrets: bool = False,
) -> list[dict[str, Any]]:
    return await fetch(
        client,
        "GetSiteRoles",
        {
            "siteUrl": normalise_site(site_url),
            "includeAllSubdomains": bool_param(include_all_subdomains),
        },
        reveal_secrets=reveal_secrets,
    )


@row_read
async def site_moves(client: BingClient, site_url: str) -> list[dict[str, Any]]:
    return await fetch(client, "GetSiteMoves", {"siteUrl": normalise_site(site_url)})


@single_record
async def show_site(
    client: BingClient, site_url: str, reveal_secrets: bool = False
) -> dict[str, Any] | None:
    wanted = normalise_site(site_url).casefold()
    for site in await list_sites(client, reveal_secrets=reveal_secrets):
        url = site.get("Url")
        if isinstance(url, dict):
            url = url.get("value")
        if isinstance(url, str) and url.rstrip("/").casefold() == wanted:
            return site
    return None
