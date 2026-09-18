"""Thin Click facade over the shared operations and plan/apply boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any

import click
import httpx

from .apply import apply_plan, execute_write
from .audit import AuditLog
from .client import BingClient
from .config import Settings
from .emptiness import EMPTY_RESPONSE_NOTE, read_shape, returned_no_rows
from .errors import BingWebmasterError, InvalidRequest
from .limits import RateLimiter
from .ops import (
    blocking,
    crawl,
    geo,
    indexnow,
    keywords,
    links,
    params,
    sitemaps,
    sites,
    submission,
    traffic,
)
from .plans import Plan, PlanStore, create_write_plan
from .render import redact_secrets, sanitize_text
from .writes import WRITE_OPS, prepare_write


def _load_settings(*, require_api_key: bool = True) -> Settings:
    return Settings.load(require_api_key=require_api_key)


def _transport() -> Any:
    return None


def run_async(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


def guarded(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except BingWebmasterError as exc:
            click.echo(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False), err=True)
            raise click.exceptions.Exit(1) from exc

    return wrapper


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def emit(result: Any, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False, default=_json_default))
        return
    if isinstance(result, list):
        _emit_table(result)
    elif isinstance(result, dict):
        _emit_table([result])
    else:
        click.echo(str(result))


def _display_value(value: Any) -> str:
    if isinstance(value, dict) and value.get("untrusted") is True:
        return f"⚠ {value.get('value', '')}"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    return _json_default(value)


def _emit_table(rows: list[Any]) -> None:
    if not rows:
        click.echo("(no results)")
        return
    if not all(isinstance(row, dict) for row in rows):
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False, default=_json_default))
        return
    columns = list(dict.fromkeys(key for row in rows for key in row))
    rendered = [[_display_value(row.get(column, "")) for column in columns] for row in rows]
    widths = [
        min(60, max(len(column), *(len(values[index]) for values in rendered)))
        for index, column in enumerate(columns)
    ]
    click.echo(
        "  ".join(
            column[: widths[index]].ljust(widths[index]) for index, column in enumerate(columns)
        )
    )
    click.echo("  ".join("-" * width for width in widths))
    for values in rendered:
        click.echo(
            "  ".join(
                values[index][: widths[index]].ljust(widths[index]) for index in range(len(columns))
            )
        )


async def _read(function: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    settings = _load_settings()
    async with BingClient(settings, transport=_transport()) as client:
        result = await function(client, *args)
    # On stderr, so it reaches a person reading the terminal without getting into the
    # JSON a script is parsing. Only reads are labelled: a write that returns nothing
    # returns nothing because it had nothing to say.
    if returned_no_rows(result, read_shape(function)):
        click.echo(f"note: {EMPTY_RESPONSE_NOTE}", err=True)
    return result


def _limit(result: Any, limit: int | None) -> Any:
    return result[:limit] if limit is not None and isinstance(result, list) else result


async def _create_plan(operation: str, args: dict[str, Any]) -> Plan:
    require_api_key = operation != "indexnow_submit"
    settings = _load_settings(require_api_key=require_api_key)
    if operation == "indexnow_submit":
        return await create_write_plan(operation, args, settings=settings, client=None)
    async with BingClient(settings, transport=_transport()) as client:
        return await create_write_plan(operation, args, settings=settings, client=client)


async def _write_now(operation: str, args: dict[str, Any]) -> dict[str, Any]:
    """Create and apply a plan in one command.

    The plan record, the audit trail and every limit still apply; what BING_WM_ALLOW_WRITES
    removes is the separate human approval between the two steps.
    """
    # Policy first: a disabled write path must say so even when no API key is set.
    _load_settings(require_api_key=False).check_writes_allowed()
    settings = _load_settings(require_api_key=operation != "indexnow_submit")
    audit = AuditLog(settings.state_dir)
    limiter = RateLimiter(settings.state_dir, max_per_day=settings.max_writes_per_day)
    if operation == "indexnow_submit":
        return await execute_write(
            operation,
            args,
            settings=settings,
            client=None,
            audit=audit,
            limiter=limiter,
            indexnow_transport=_transport(),
        )
    async with BingClient(settings, transport=_transport()) as client:
        return await execute_write(
            operation,
            args,
            settings=settings,
            client=client,
            audit=audit,
            limiter=limiter,
            indexnow_transport=_transport(),
        )


def _plan_payload(plan: Plan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "summary": plan.summary,
        "state": plan.state,
        "expires_at": plan.expires_at,
        "apply_with": f"bing-wm plan apply {plan.plan_id}",
    }


_REVIEW_STRING_LIMIT = 200


def _review(plan: Plan, reveal_secrets: bool = False) -> str:
    """Render what the plan actually sends.

    This prompt is the security boundary for an agent-authored change, so it must show
    the prepared request, not only the one-line summary: for the complex-object writes
    the summary names the site and nothing about the payload. The verification secrets
    inside that payload are redacted, because approving a delegation needs the email and
    the site, not the credential - and this text is read aloud, screenshotted and pasted
    into tickets. ``--reveal-verification-codes`` prints them for the operator who is
    checking exactly that.
    """
    prepared = prepare_write(plan.operation, plan.args)
    payload = prepared.body if reveal_secrets else redact_secrets(prepared.body)
    body = json.dumps(_abbreviate(payload), indent=2, ensure_ascii=False, sort_keys=True)
    return f"{plan.summary}\n\n{prepared.method} request body:\n{body}"


def _abbreviate(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _abbreviate(item) for key, item in value.items()}
    if isinstance(value, list):
        # Every element is shown: a batch the operator cannot see is a batch they cannot
        # approve, and the summary above already states how many there are.
        return [_abbreviate(item) for item in value]
    if isinstance(value, str):
        # Bidi and other format characters would let a payload lie about itself in the
        # terminal, and this prompt is what the operator approves. Only the shown head is
        # sanitized: a payload can be megabytes, and the stated length is the real one.
        if len(value) > _REVIEW_STRING_LIMIT:
            head = sanitize_text(value[:_REVIEW_STRING_LIMIT])
            return f"{head}... ({len(value)} characters)"
        return sanitize_text(value)
    return value


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter("use YYYY-MM-DD") from exc


def json_option(function: Callable[..., Any]) -> Callable[..., Any]:
    return click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON.")(
        function
    )


@click.group()
@click.version_option()
def main() -> None:
    """Inspect and safely change Bing Webmaster Tools data."""


@main.group("sites")
def sites_group() -> None:
    """Sites, verification and delegated roles."""


# Verification codes are ownership proofs, so they are redacted everywhere and this flag
# is the only way to see one. It exists at the CLI and nowhere else: the operator putting
# the proof on the site needs it, and no MCP tool - hence no model, and no text a model
# read - can ask for it.
reveal_option = click.option(
    "--reveal-verification-codes",
    "reveal_secrets",
    is_flag=True,
    help="Print the verification/delegation secrets instead of redacting them. "
    "Treat the output as credentials: anyone holding one can claim the site.",
)


@sites_group.command("list")
@reveal_option
@json_option
@guarded
def sites_list(reveal_secrets: bool, as_json: bool) -> None:
    emit(run_async(_read(sites.list_sites, reveal_secrets)), as_json)


@sites_group.command("show")
@click.argument("site")
@reveal_option
@json_option
@guarded
def sites_show(site: str, reveal_secrets: bool, as_json: bool) -> None:
    emit(run_async(_read(sites.show_site, site, reveal_secrets)), as_json)


@sites_group.command("roles")
@click.argument("site")
@click.option("--include-all-subdomains", is_flag=True)
@reveal_option
@json_option
@guarded
def sites_roles(
    site: str, include_all_subdomains: bool, reveal_secrets: bool, as_json: bool
) -> None:
    emit(run_async(_read(sites.site_roles, site, include_all_subdomains, reveal_secrets)), as_json)


@sites_group.command("moves")
@click.argument("site")
@json_option
@guarded
def sites_moves(site: str, as_json: bool) -> None:
    emit(run_async(_read(sites.site_moves, site)), as_json)


@main.group("traffic")
def traffic_group() -> None:
    """Queries, pages, clicks, impressions and rank."""


def _traffic_list(function: Callable[..., Awaitable[Any]], site: str, limit: int | None) -> Any:
    return _limit(run_async(_read(function, site)), limit)


@traffic_group.command("queries")
@click.argument("site")
@click.option("--limit", type=click.IntRange(min=1))
@json_option
@guarded
def traffic_queries(site: str, limit: int | None, as_json: bool) -> None:
    emit(_traffic_list(traffic.query_stats, site, limit), as_json)


@traffic_group.command("pages")
@click.argument("site")
@click.option("--limit", type=click.IntRange(min=1))
@json_option
@guarded
def traffic_pages(site: str, limit: int | None, as_json: bool) -> None:
    emit(_traffic_list(traffic.page_stats, site, limit), as_json)


@traffic_group.command("query")
@click.argument("site")
@click.argument("query")
@json_option
@guarded
def traffic_query(site: str, query: str, as_json: bool) -> None:
    emit(run_async(_read(traffic.query_traffic_stats, site, query)), as_json)


@traffic_group.command("query-pages")
@click.argument("site")
@click.argument("query")
@json_option
@guarded
def traffic_query_pages(site: str, query: str, as_json: bool) -> None:
    emit(run_async(_read(traffic.query_page_stats, site, query)), as_json)


@traffic_group.command("query-page")
@click.argument("site")
@click.argument("query")
@click.argument("page")
@json_option
@guarded
def traffic_query_page(site: str, query: str, page: str, as_json: bool) -> None:
    emit(run_async(_read(traffic.query_page_detail_stats, site, query, page)), as_json)


@traffic_group.command("page")
@click.argument("site")
@click.argument("page")
@json_option
@guarded
def traffic_page(site: str, page: str, as_json: bool) -> None:
    emit(run_async(_read(traffic.page_query_stats, site, page)), as_json)


@traffic_group.command("rank")
@click.argument("site")
@json_option
@guarded
def traffic_rank(site: str, as_json: bool) -> None:
    emit(run_async(_read(traffic.rank_and_traffic_stats, site)), as_json)


@main.group("index")
def index_group() -> None:
    """Index and URL traffic details."""


@index_group.command("url")
@click.argument("site")
@click.argument("url")
@json_option
@guarded
def index_url(site: str, url: str, as_json: bool) -> None:
    emit(run_async(_read(crawl.url_info, site, url)), as_json)


@index_group.command("url-traffic")
@click.argument("site")
@click.argument("url")
@json_option
@guarded
def index_url_traffic(site: str, url: str, as_json: bool) -> None:
    emit(run_async(_read(crawl.url_traffic_info, site, url)), as_json)


@index_group.command("children")
@click.argument("site")
@click.argument("url")
@click.option("--page", type=click.IntRange(min=0), default=0, show_default=True)
@json_option
@guarded
def index_children(site: str, url: str, page: int, as_json: bool) -> None:
    emit(run_async(_read(crawl.children_url_info, site, url, page)), as_json)


@index_group.command("children-traffic")
@click.argument("site")
@click.argument("url")
@click.option("--page", type=click.IntRange(min=0), default=0, show_default=True)
@json_option
@guarded
def index_children_traffic(site: str, url: str, page: int, as_json: bool) -> None:
    emit(run_async(_read(crawl.children_url_traffic_info, site, url, page)), as_json)


@main.group("crawl")
def crawl_group() -> None:
    """Crawl statistics, issues, settings and fetched URLs."""


def _register_site_read(
    group: click.Group, command_name: str, function: Callable[..., Any]
) -> None:
    @group.command(command_name)
    @click.argument("site")
    @json_option
    @guarded
    def command(site: str, as_json: bool) -> None:
        emit(run_async(_read(function, site)), as_json)


for _name, _function in {
    "stats": crawl.crawl_stats,
    "settings": crawl.crawl_settings,
    "fetched": crawl.fetched_urls,
}.items():
    _register_site_read(crawl_group, _name, _function)


@crawl_group.command("issues")
@click.argument("site")
@json_option
@guarded
def crawl_issues(site: str, as_json: bool) -> None:
    """Crawl issues counted by category, with every raw field Bing returned."""
    result = run_async(_read(crawl.crawl_issues, site))
    if as_json:
        emit(result, True)
        return
    click.echo(f"{result['total']} URLs with crawl issues")
    for category, count in result["categories"].items():
        click.echo(f"  {category}: {count}")
    for code, count in result["http_codes"].items():
        click.echo(f"  HTTP {code}: {count}")
    emit(result["issues"], False)


@crawl_group.command("fetched-details")
@click.argument("site")
@click.argument("url")
@json_option
@guarded
def crawl_fetched_details(site: str, url: str, as_json: bool) -> None:
    emit(run_async(_read(crawl.fetched_url_details, site, url)), as_json)


@main.group("links")
def links_group() -> None:
    """Inbound link counts and details."""


@links_group.command("counts")
@click.argument("site")
@click.option("--page", type=click.IntRange(min=0, max=32767), default=0)
@json_option
@guarded
def links_counts(site: str, page: int, as_json: bool) -> None:
    emit(run_async(_read(links.link_counts, site, page)), as_json)


@links_group.command("url")
@click.argument("site")
@click.argument("url")
@click.option("--page", type=click.IntRange(min=0, max=32767), default=0)
@json_option
@guarded
def links_url(site: str, url: str, page: int, as_json: bool) -> None:
    emit(run_async(_read(links.url_links, site, url, page)), as_json)


@links_group.command("connected")
@click.argument("site")
@json_option
@guarded
def links_connected(site: str, as_json: bool) -> None:
    emit(run_async(_read(links.connected_pages, site)), as_json)


@main.group("keywords")
def keywords_group() -> None:
    """Standalone Bing keyword research."""


def keyword_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--language", required=True)(function)
    return click.option("--country", required=True)(function)


def date_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--end", required=True, callback=lambda _c, _p, value: _date(value))(
        function
    )
    return click.option("--start", required=True, callback=lambda _c, _p, value: _date(value))(
        function
    )


@keywords_group.command("get")
@click.argument("keyword")
@keyword_options
@date_options
@json_option
@guarded
def keywords_get(
    keyword: str, country: str, language: str, start: date, end: date, as_json: bool
) -> None:
    emit(run_async(_read(keywords.keyword, keyword, country, language, start, end)), as_json)


@keywords_group.command("stats")
@click.argument("keyword")
@keyword_options
@json_option
@guarded
def keywords_stats(keyword: str, country: str, language: str, as_json: bool) -> None:
    emit(run_async(_read(keywords.keyword_stats, keyword, country, language)), as_json)


@keywords_group.command("related")
@click.argument("keyword")
@keyword_options
@date_options
@json_option
@guarded
def keywords_related(
    keyword: str, country: str, language: str, start: date, end: date, as_json: bool
) -> None:
    emit(
        run_async(_read(keywords.related_keywords, keyword, country, language, start, end)),
        as_json,
    )


@main.group("sitemaps")
def sitemaps_group() -> None:
    """Submitted sitemaps and feeds."""


@sitemaps_group.command("list")
@click.argument("site")
@json_option
@guarded
def sitemaps_list(site: str, as_json: bool) -> None:
    emit(run_async(_read(sitemaps.feeds, site)), as_json)


@sitemaps_group.command("details")
@click.argument("site")
@click.argument("feed_url")
@json_option
@guarded
def sitemaps_details(site: str, feed_url: str, as_json: bool) -> None:
    emit(run_async(_read(sitemaps.feed_details, site, feed_url)), as_json)


@main.command("quota")
@click.argument("site")
@json_option
@guarded
def quota(site: str, as_json: bool) -> None:
    emit(run_async(_read(submission.url_submission_quota, site)), as_json)


@main.group("blocking")
def blocking_group() -> None:
    """Current URL, page-preview and deep-link blocks."""


for _name, _function in {
    "urls": blocking.blocked_urls,
    "page-previews": blocking.page_preview_blocks,
    "deep-links": blocking.deep_link_blocks,
}.items():
    _register_site_read(blocking_group, _name, _function)


@main.command("query-parameters")
@click.argument("site")
@json_option
@guarded
def query_parameters(site: str, as_json: bool) -> None:
    emit(run_async(_read(params.query_parameters, site)), as_json)


@main.command("geo-settings")
@click.argument("site")
@json_option
@guarded
def geo_settings(site: str, as_json: bool) -> None:
    emit(run_async(_read(geo.country_region_settings, site)), as_json)


@main.command("content-quota")
@click.argument("site")
@json_option
@guarded
def content_quota(site: str, as_json: bool) -> None:
    emit(run_async(_read(submission.content_submission_quota, site)), as_json)


@main.group("indexnow")
def indexnow_group() -> None:
    """IndexNow key generation and submission."""


@indexnow_group.command("key")
@click.argument("host")
@click.option("--key", help="Inspect an existing key instead of generating a new one.")
@click.option("--key-location", help="Exact key-file URL when the key lives under a subpath.")
@click.option(
    "--check/--no-check",
    "check",
    default=None,
    help="Fetch the key file to see whether it is served. On by default with --key.",
)
@json_option
@guarded
def indexnow_key(
    host: str, key: str | None, key_location: str | None, check: bool | None, as_json: bool
) -> None:
    """Prepare IndexNow key material. Sends nothing to Bing or IndexNow.

    A generated key is printed once and stored nowhere: save it and serve the key file.
    """
    emit(run_async(_key_plan(host, key, key_location, check)), as_json)


async def _key_plan(
    host: str, key: str | None, key_location: str | None, check: bool | None
) -> dict[str, Any]:
    async with httpx.AsyncClient(transport=_transport(), timeout=30.0) as http:
        return await indexnow.key_plan(
            http,
            host,
            key=key,
            key_location=key_location,
            check_key_file=check,
        )


@indexnow_group.command("submit")
@click.argument("host")
@click.option("--file", "path", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--key", required=True)
@click.option("--key-location")
@json_option
@guarded
def indexnow_submit(
    host: str, path: Path, key: str, key_location: str | None, as_json: bool
) -> None:
    """Submit a URL batch to IndexNow now. Needs BING_WM_ALLOW_WRITES."""
    args: dict[str, Any] = {"host": host, "key": key, "url_list": _read_lines(path)}
    if key_location:
        args["key_location"] = key_location
    emit(run_async(_write_now("indexnow_submit", args)), as_json)


def _read_lines(path: Path) -> list[str]:
    values = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if not values:
        raise InvalidRequest(f"{path} contains no URLs")
    return values


def _args_object(args_json: str) -> dict[str, Any]:
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise InvalidRequest(f"--args-json is not valid JSON: {exc}") from exc
    if not isinstance(args, dict):
        raise InvalidRequest("--args-json must contain an object")
    return args


@main.command("write")
@click.argument("operation", type=click.Choice(sorted(WRITE_OPS)))
@click.option("--args-json", required=True, help="Operation arguments as a JSON object.")
@json_option
@guarded
def write(operation: str, args_json: str, as_json: bool) -> None:
    """Send any supported write to Bing now. Needs BING_WM_ALLOW_WRITES."""
    emit(run_async(_write_now(operation, _args_object(args_json))), as_json)


@main.command("submit-url")
@click.argument("site")
@click.argument("url")
@json_option
@guarded
def submit_url(site: str, url: str, as_json: bool) -> None:
    """Submit one URL to Bing now. Needs BING_WM_ALLOW_WRITES."""
    emit(run_async(_write_now("submit_url", {"site_url": site, "url": url})), as_json)


@main.command("submit-urls")
@click.argument("site")
@click.option("--file", "path", required=True, type=click.Path(path_type=Path, exists=True))
@json_option
@guarded
def submit_urls(site: str, path: Path, as_json: bool) -> None:
    """Submit a newline-delimited file of URLs now. Needs BING_WM_ALLOW_WRITES."""
    args = {"site_url": site, "url_list": _read_lines(path)}
    emit(run_async(_write_now("submit_url_batch", args)), as_json)


@main.command("submit-sitemap")
@click.argument("site")
@click.argument("feed_url")
@json_option
@guarded
def submit_sitemap(site: str, feed_url: str, as_json: bool) -> None:
    """Submit a sitemap now. Needs BING_WM_ALLOW_WRITES."""
    emit(run_async(_write_now("submit_feed", {"site_url": site, "feed_url": feed_url})), as_json)


@main.command("block-url")
@click.argument("site")
@click.argument("url")
@click.option("--entity-type", required=True, type=int)
@click.option("--request-type", required=True, type=int)
@json_option
@guarded
def block_url(site: str, url: str, entity_type: int, request_type: int, as_json: bool) -> None:
    """Block a URL or directory now. Needs BING_WM_ALLOW_WRITES."""
    args = {
        "site_url": site,
        "blocked_url": {"Url": url, "EntityType": entity_type, "RequestType": request_type},
    }
    emit(run_async(_write_now("add_blocked_url", args)), as_json)


@main.group("plan")
def plan_group() -> None:
    """Record, inspect and explicitly apply writes."""


@plan_group.command("create")
@click.argument("operation", type=click.Choice(sorted(WRITE_OPS)))
@click.option("--args-json", required=True, help="Operation arguments as a JSON object.")
@json_option
@guarded
def plan_create(operation: str, args_json: str, as_json: bool) -> None:
    emit(_plan_payload(run_async(_create_plan(operation, _args_object(args_json)))), as_json)


@plan_group.command("submit-url")
@click.argument("site")
@click.argument("url")
@json_option
@guarded
def plan_submit_url(site: str, url: str, as_json: bool) -> None:
    plan = run_async(_create_plan("submit_url", {"site_url": site, "url": url}))
    emit(_plan_payload(plan), as_json)


@plan_group.command("submit-urls")
@click.argument("site")
@click.option("--file", "path", required=True, type=click.Path(path_type=Path, exists=True))
@json_option
@guarded
def plan_submit_urls(site: str, path: Path, as_json: bool) -> None:
    plan = run_async(
        _create_plan("submit_url_batch", {"site_url": site, "url_list": _read_lines(path)})
    )
    emit(_plan_payload(plan), as_json)


@plan_group.command("submit-sitemap")
@click.argument("site")
@click.argument("feed_url")
@json_option
@guarded
def plan_submit_sitemap(site: str, feed_url: str, as_json: bool) -> None:
    plan = run_async(_create_plan("submit_feed", {"site_url": site, "feed_url": feed_url}))
    emit(_plan_payload(plan), as_json)


@plan_group.command("block-url")
@click.argument("site")
@click.argument("url")
@click.option("--entity-type", required=True, type=int)
@click.option("--request-type", required=True, type=int)
@json_option
@guarded
def plan_block_url(site: str, url: str, entity_type: int, request_type: int, as_json: bool) -> None:
    args = {
        "site_url": site,
        "blocked_url": {"Url": url, "EntityType": entity_type, "RequestType": request_type},
    }
    emit(_plan_payload(run_async(_create_plan("add_blocked_url", args))), as_json)


@plan_group.command("indexnow")
@click.argument("host")
@click.option("--file", "path", required=True, type=click.Path(path_type=Path, exists=True))
@click.option("--key", required=True)
@click.option("--key-location")
@json_option
@guarded
def plan_indexnow(host: str, path: Path, key: str, key_location: str | None, as_json: bool) -> None:
    args = {"host": host, "key": key, "url_list": _read_lines(path)}
    if key_location:
        args["key_location"] = key_location
    emit(_plan_payload(run_async(_create_plan("indexnow_submit", args))), as_json)


@plan_group.command("list")
@reveal_option
@json_option
@guarded
def plan_list(reveal_secrets: bool, as_json: bool) -> None:
    settings = _load_settings(require_api_key=False)
    plans = PlanStore(settings.state_dir, settings.plan_ttl_seconds).list()
    emit([plan.public_dump(reveal_secrets=reveal_secrets) for plan in plans], as_json)


@plan_group.command("show")
@click.argument("plan_id")
@reveal_option
@json_option
@guarded
def plan_show(plan_id: str, reveal_secrets: bool, as_json: bool) -> None:
    settings = _load_settings(require_api_key=False)
    plan = PlanStore(settings.state_dir, settings.plan_ttl_seconds).get(plan_id)
    emit(plan.public_dump(reveal_secrets=reveal_secrets), as_json)


@plan_group.command("reject")
@click.argument("plan_id")
@json_option
@guarded
def plan_reject(plan_id: str, as_json: bool) -> None:
    settings = _load_settings(require_api_key=False)
    plan = PlanStore(settings.state_dir, settings.plan_ttl_seconds).reject(plan_id)
    AuditLog(settings.state_dir).record("plan_rejected", plan_id=plan_id, operation=plan.operation)
    emit({"plan_id": plan_id, "state": "rejected"}, as_json)


@plan_group.command("unlock")
@click.argument("plan_id")
@click.option("--yes", is_flag=True, help="Skip the human confirmation prompt.")
@json_option
@guarded
def plan_unlock(plan_id: str, yes: bool, as_json: bool) -> None:
    """Recover an apply lock after its process died."""
    settings = _load_settings(require_api_key=False)
    if not yes:
        click.confirm(
            "Recover this lock? An unfinished plan becomes unknown_outcome and cannot be applied",
            abort=True,
        )
    plan, owner_pid = PlanStore(settings.state_dir, settings.plan_ttl_seconds).recover_lock(
        plan_id, AuditLog(settings.state_dir)
    )
    emit(
        {"plan_id": plan_id, "state": plan.state, "recovered_owner_pid": owner_pid},
        as_json,
    )


@plan_group.command("apply")
@click.argument("plan_id")
@click.option("--yes", is_flag=True, help="Skip the human confirmation prompt.")
@reveal_option
@json_option
@guarded
def plan_apply(plan_id: str, yes: bool, reveal_secrets: bool, as_json: bool) -> None:
    settings = _load_settings(require_api_key=False)
    store = PlanStore(settings.state_dir, settings.plan_ttl_seconds)
    plan = store.ensure_pending(plan_id)
    if not yes:
        click.confirm(f"{_review(plan, reveal_secrets)}\nApply this plan?", abort=True)

    async def execute() -> dict[str, Any]:
        limiter = RateLimiter(
            settings.state_dir,
            max_per_day=settings.max_writes_per_day,
        )
        if plan.operation == "indexnow_submit":
            return await apply_plan(
                plan_id,
                settings=settings,
                store=store,
                client=None,
                audit=AuditLog(settings.state_dir),
                limiter=limiter,
            )
        authenticated = _load_settings()
        async with BingClient(authenticated, transport=_transport()) as client:
            return await apply_plan(
                plan_id,
                settings=settings,
                store=store,
                client=client,
                audit=AuditLog(settings.state_dir),
                limiter=limiter,
            )

    emit(run_async(execute()), as_json)


if __name__ == "__main__":  # pragma: no cover
    main()
