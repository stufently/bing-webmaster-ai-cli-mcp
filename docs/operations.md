# Operations

CLI and MCP are thin facades over the same operation and planning modules. CLI commands
accept `--json`; lists render as human-readable tables otherwise.

## CLI

- `bing-wm sites list|show|roles|moves`; `list`, `show` and `roles` also take
  `--reveal-verification-codes`, which prints the ownership proofs that are otherwise
  redacted (see [Verification secrets are redacted](#verification-secrets-are-redacted))
- `bing-wm traffic queries|query|query-pages|query-page|pages|page|rank`
- `bing-wm index url|url-traffic|children|children-traffic`
- `bing-wm crawl stats|issues|settings|fetched|fetched-details`
- `bing-wm links counts|url|connected`
- `bing-wm keywords get|stats|related`
- `bing-wm sitemaps list|details`
- `bing-wm quota`, `content-quota`, `blocking`, `query-parameters`, `geo-settings`
- `bing-wm indexnow key HOST [--key KEY] [--key-location URL] [--check/--no-check]`
  generates or inspects an IndexNow key. It sends nothing to Bing or IndexNow and
  stores nothing: a generated key is printed once. `--check` fetches the key file on
  your own host to see whether it is already served, and is on by default with
  `--key` (a key generated here cannot be published yet).

One-step writes, available while `BING_WM_ALLOW_WRITES` is on (the default). They send
the change immediately and print the applied plan:

- `bing-wm submit-url|submit-urls|submit-sitemap|block-url`
- `bing-wm indexnow submit`
- `bing-wm write OPERATION --args-json OBJECT` covers every write below.

The reviewed path stays available in both modes and is the only path when
`BING_WM_ALLOW_WRITES=false`:

- `bing-wm plan submit-url|submit-urls|submit-sitemap|block-url|indexnow`
- `bing-wm plan create OPERATION --args-json OBJECT` covers every write below.
- `bing-wm plan list|show|reject|apply|unlock`; apply and unlock prompt unless `--yes`
  is supplied. `list`, `show` and `apply` take `--reveal-verification-codes`, which
  prints the ownership proofs a plan's arguments carry (see
  [Verification secrets are redacted](#verification-secrets-are-redacted)).

## MCP read tools

- `bing_sites_list`, `bing_site_roles`, `bing_site_moves`
- `bing_traffic_queries`, `bing_traffic_query`, `bing_query_page_stats`,
  `bing_query_page_detail_stats`, `bing_traffic_pages`, `bing_traffic_page`,
  `bing_traffic_rank`
- `bing_url_info`, `bing_url_traffic_info`, `bing_children_url_info`,
  `bing_children_url_traffic_info`
- `bing_crawl_stats`, `bing_crawl_issues`, `bing_crawl_settings`,
  `bing_fetched_urls`, `bing_fetched_url_details`
- `bing_submission_quota`, `bing_content_submission_quota`
- `bing_sitemaps`, `bing_sitemap_details`
- `bing_blocked_urls`, `bing_page_preview_blocks`, `bing_deep_link_blocks`
- `bing_query_parameters`, `bing_geo_settings`
- `bing_link_counts`, `bing_url_links`, `bing_connected_pages`
- `bing_keyword`, `bing_keyword_stats`, `bing_related_keywords`

### An empty read is not a zero

Several of the older endpoints answer some accounts with an empty collection while a
different endpoint reports plenty for the same site at the same moment: on a live
account `bing_link_counts`, `bing_crawl_issues` and `bing_fetched_urls` all came back
empty for a site whose `bing_crawl_stats` reported `InLinks: 1700` and `CrawlErrors: 4`
in the same minute. Nothing in the payload distinguishes "Bing has nothing to report"
from "Bing reported nothing", so the difference is labelled rather than guessed:

- **MCP** — the result gains a sibling key beside `result`:
  `"empty_response": {"rows_returned": 0, "measured": false, "note": "…"}`. `result`
  keeps exactly the shape Bing's response had. The key is present only on read tools and
  only when the response carried row collections and every one of them was empty.
- **CLI** — the same note goes to stderr, so a `--json` pipe stays parseable.

Report it as "Bing returned nothing", never as "no problems found".

Which reads can be labelled is declared per operation, not inferred from the payload.
`GetCrawlSettings` answers one record whose `CrawlRate` is an hourly-rate array, and
`GetLinkCounts` answers a container whose `Links` array is the rows; both are "a mapping
with an empty list in it", and only the second is silence. Each read op therefore carries
`@row_read` or `@single_record` from `ops/_common.py`, and single-record reads —
`bing_url_info`, `bing_url_traffic_info`, `bing_crawl_settings`,
`bing_fetched_url_details`, `bing_keyword`, `bing_submission_quota`,
`bing_content_submission_quota`, and the CLI-only `sites show` — are exempt from the
structural test entirely. A test in `tests/test_read_coverage.py` fails if a read
declares neither, and a second test pins the single-record list, so adding a read means
deciding which it is. An undeclared read falls back to `@row_read`: the fallback labels,
because silence is the thing being warned about.

### Verification secrets are redacted

`GetUserSites` returns `AuthenticationCode` and `DnsVerificationCode` beside every site,
and `GetSiteRoles` returns `DelegatedCode`. These are ownership proofs — whoever holds
one can claim the site in another Bing account — and no read, audit or report needs
them. They are replaced with `[redacted: verification secret]` in every response, from
every method, in both the MCP server and the CLI. A field Bing left `null` stays `null`
rather than acquiring a marker that would imply a code exists.

The only way to see one is `--reveal-verification-codes`, typed by an operator who is
about to publish the proof on the site. It exists on `bing-wm sites list|show|roles` and
on `bing-wm plan show|list|apply`. No MCP tool takes that flag and unknown tool arguments
are refused, so no model — and no text a model read — can ask for a code.

Redaction is the exit boundary, not a step on the read path. Everything leaving the
process passes through `render.redact`:

- **Reads** — `ops/_common.fetch`, so a method that starts returning a code tomorrow is
  covered without anybody remembering to cover it.
- **Plans** — `Plan.public_dump()`, used by `bing_plan_show`, `bing_plan_list` and the
  CLI. An `add_site_roles` plan records the `authentication_code` it will send, so
  showing the plan would otherwise be a second door to the same secret. The plan file on
  disk keeps the real value, because a plan that lost its code could not be applied. The
  `bing-wm plan apply` confirmation prompt redacts the prepared body for the same reason:
  approving a delegation turns on the email and the site, not the credential.
- **Write results and upstream errors** — `apply.apply_plan` and `client._map_error`.
  Bing's own message about a rejected write can quote the code it rejected, and that text
  reaches the MCP error, the terminal and the audit trail.

Two spellings of the same fact are matched. A field name is compared with its underscores
removed and its case folded, so `AuthenticationCode` (response), `authenticationCode`
(request body) and `authentication_code` (plan arguments) are one secret rather than
three lists to keep in step. Separately, the literal values a request body carries under
those names are collected and searched for in any free text the same call produces — that
is what catches a code Bing quoted back inside a message that named no field. A value
shorter than four characters is not searched for: replacing every occurrence of a
two-character string would shred a message without hiding a real credential.

### The API key is redacted too, under its own marker

Microsoft documents the API key only as a query-string parameter (`?apikey=…`), so every
URL this client builds carries a live credential. Nothing in this project prints a URL,
and `httpx` does not put one in its own exception messages — measured against
`ConnectError`, `ReadTimeout`, `RemoteProtocolError` and `TooManyRedirects`, none of
which quotes the address. The exposure is narrower than "any network error":

- a message that came from **underneath** — a proxy, a TLS layer or a custom transport
  naming the address it could not reach — and was formatted into the error text; or
- **Bing's own error text**, if it quotes the request back.

Both paths ended in the same four places: the MCP error, the CLI output, `exc.to_dict()`
and the audit trail. The key is now hidden in all of them, in both `httpx` branches of
`client.call`, in `client._map_error` and in the `details` an error carries.

Three things this needs to get right, each pinned by a test:

- **The wire form, not just the literal.** The key is a query parameter, so httpx encodes
  it: one holding `/` or a space arrives as `%2F` or `+`, and a search for the value we
  hold would walk past it. Both forms are matched, and the encoded one is asked of httpx
  rather than guessed a second time here.
- **Read on every call, after the credential is applied.** Reading it once when the
  client is built would cover only the value it started with; a provider that refreshes a
  token would go uncovered exactly when it rotates.
- **No chained cause on a lost write.** `PlanUnknownOutcome` used to be raised `from` the
  `httpx` exception. Its own message never quoted a URL, but `__cause__` did, and a
  formatted traceback prints what `to_dict` cannot reach. The failure is now carried
  scrubbed in `details.cause`, so the operator still learns what went wrong — in the
  audit trail, where it is more use than in a traceback nobody kept.

The marker is `[redacted: API credential]`, deliberately not the verification-secret one:
a reader who finds "verification secret" where an API key leaked would misjudge both what
escaped and what has to be rotated. The literal comes from `AuthProvider.secrets()` —
the component that applied the credential is the only one that knows it, so a provider
added later covers its own token by implementing that method, and no second list has to
be kept in step. An error that carried no credential is unchanged, character for
character; the cover must not turn ordinary failures into a wall of markers, and a test
pins that.

### `HttpStatus: 0` means Bing reported no status

`GetUrlInfo` returns `HttpStatus: 0` for a URL it holds crawl history for but no status
on; it is not `200` and not a status code. `bing_url_info` and `bing_children_url_info`
therefore add `http_status_reported` to every row, plus an `http_status_note` when it is
`false`. The raw `HttpStatus` is kept. `IsPage: true` with `http_status_reported: false`
means Bing once saw a page there, not that the URL works now — this has been observed on
a URL that returns 404 today.

`bing_crawl_issues` returns `{total, categories, http_codes, issues}`. Each row in
`issues` keeps every field Bing sent and gains a lower-case `categories` list, plus
`unknown_issue_bits` when Bing sets a flag Microsoft has not documented. Categories
come from Microsoft's `UrlWithCrawlIssues.CrawlIssues` flags enum — `redirect_301`,
`redirect_302`, `http_4xx`, `http_5xx`, `blocked_by_robots_txt`, `contains_malware`,
`important_url_blocked_by_robots_txt`, `dns_errors`, `timeout_errors` — plus `none`
for a row with no flags and `other` for anything unrecognised. Microsoft's enum stops
at `Code4xx`, so a row carrying that flag is split by its own `HttpCode` field into
`http_404` or `http_403`; those two are a subset of `http_4xx`, which still counts
every 4xx row, and any other 4xx code is left to `http_codes`. Bing has no `noindex`
crawl-issue flag, so there is no such category. `http_codes` counts the raw
`HttpCode` field on every row.

## MCP local read-only tools

- `bing_indexnow_key_plan` — generate or inspect IndexNow key material for a host:
  the key, the exact key-file URL, the bytes that file must contain, the URL prefix
  the key authorizes, and whether the file is already served. It reaches neither Bing
  nor `api.indexnow.org`, needs no API key, consumes no quota and records no plan, so
  there is nothing to apply. Its only network access is an optional unauthenticated
  GET of the key file on the host given, the same request the submission preflight
  makes; that check is skipped by default for a key the tool just generated. Before
  fetching, the host is resolved and refused if any address is not globally routable,
  and the MCP path ignores proxy environment variables — a syntactically valid name like
  `foo.localhost` or `127.0.0.1.nip.io` must not turn this tool into a way to reach the
  operator's own network. The key is not stored: it exists only in the response.

## MCP write tools

`BING_WM_ALLOW_WRITES` decides which of the two sets below is advertised. Both are
always dispatchable, so a client holding a stale tool list gets the operation's real
answer — a `POLICY_DENIED` for a disabled write — rather than "unknown tool".

## MCP one-step write tools (`BING_WM_ALLOW_WRITES=true`, the default)

Each sends the change to Bing immediately and returns the applied plan ID, the operation
and Bing's result. They are annotated `readOnlyHint: false`, `destructiveHint: true`.

- `bing_add_blocked_url`
- `bing_add_connected_page`
- `bing_add_country_region_settings`
- `bing_add_deep_link_block`
- `bing_add_page_preview_block`
- `bing_add_query_parameter`
- `bing_add_site`
- `bing_add_site_roles`
- `bing_enable_disable_query_parameter`
- `bing_fetch_url`
- `bing_indexnow_submit`
- `bing_remove_blocked_url`
- `bing_remove_country_region_settings`
- `bing_remove_deep_link_block`
- `bing_remove_feed`
- `bing_remove_page_preview_block`
- `bing_remove_query_parameter`
- `bing_remove_site`
- `bing_remove_site_role`
- `bing_save_crawl_settings`
- `bing_submit_content`
- `bing_submit_feed`
- `bing_submit_site_move`
- `bing_submit_url`
- `bing_submit_url_batch`
- `bing_verify_site`

## MCP planning tools (`BING_WM_ALLOW_WRITES=false`)

Each returns a plan ID, summary, expiry, and CLI apply command. No change is sent to
Bing; planning a quota-aware write does read Bing's quota.

- `bing_plan_add_blocked_url`
- `bing_plan_add_connected_page`
- `bing_plan_add_country_region_settings`
- `bing_plan_add_deep_link_block`
- `bing_plan_add_page_preview_block`
- `bing_plan_add_query_parameter`
- `bing_plan_add_site`
- `bing_plan_add_site_roles`
- `bing_plan_enable_disable_query_parameter`
- `bing_plan_fetch_url`
- `bing_plan_indexnow_submit`
- `bing_plan_remove_blocked_url`
- `bing_plan_remove_country_region_settings`
- `bing_plan_remove_deep_link_block`
- `bing_plan_remove_feed`
- `bing_plan_remove_page_preview_block`
- `bing_plan_remove_query_parameter`
- `bing_plan_remove_site`
- `bing_plan_remove_site_role`
- `bing_plan_save_crawl_settings`
- `bing_plan_submit_content`
- `bing_plan_submit_feed`
- `bing_plan_submit_site_move`
- `bing_plan_submit_url`
- `bing_plan_submit_url_batch`
- `bing_plan_verify_site`
- `bing_plan_list`, `bing_plan_show`

There is no `bing_plan_apply` or `bing_plan_reject` tool in either mode, and no MCP tool
accepts a plan ID: a plan recorded for review is applied or refused by a human at the
CLI. A direct write applies the plan it creates in the same call. Enabling one-step
writes replaces the planning tools with direct ones rather than adding a tool that can
confirm a plan somebody else wrote.

A direct write is not idempotent. Retrying one creates a new plan and sends the change
again; the one-shot guarantee is per plan, and there is no client-supplied idempotency
key. Before repeating a call whose response was lost, read `audit.jsonl` or `bing_plan_list`.

## Write operation names

`bing-wm write` and `bing-wm plan create` both accept `add_blocked_url`, `add_connected_page`,
`add_country_region_settings`, `add_deep_link_block`, `add_page_preview_block`,
`add_query_parameter`, `add_site`, `add_site_roles`,
`enable_disable_query_parameter`, `fetch_url`, `indexnow_submit`,
`remove_blocked_url`, `remove_country_region_settings`, `remove_deep_link_block`,
`remove_feed`, `remove_page_preview_block`, `remove_query_parameter`, `remove_site`,
`remove_site_role`, `save_crawl_settings`, `submit_content`, `submit_feed`,
`submit_site_move`, `submit_url`, `submit_url_batch`, and `verify_site`.

Complex values use the exact property names in [the API surface](api-surface.md).
Enum-valued fields use numeric values; invented enum names are not accepted.

`add_site_roles.delegated_url` must be an absolute HTTP(S) URL but is deliberately not
restricted to `site_url`'s host. Microsoft's JSON example delegates `example.com` to
`host1.example.com`, so a cross-host value is part of the documented operation shape.

## State, audit, and recovery

A one-step write is not a second write implementation: it records the same durable plan
and then goes through the same apply boundary, so the denylist, Bing's own submission
quota, the local daily ceiling, one-shot application and the audit trail are identical.
The only thing `BING_WM_ALLOW_WRITES` removes is the human between the two steps.

Plan files are mode `0600` under a mode `0700` directory. Plans expire after 15 minutes
by default and move to terminal `applied`, `rejected`, or `unknown_outcome` states.
Applying acquires an exclusive lock before the request. A process crash after a request
therefore leaves a lock instead of allowing an accidental duplicate.

If recording `applied` or `unknown_outcome` fails after dispatch, the apply process also
keeps its lock. This deliberately converts a local disk failure into the same audited
operator-recovery path as a process crash instead of leaving a retryable `pending` plan.

`bing-wm plan apply` prints the prepared request body before asking for confirmation,
because for the complex-object writes the one-line summary names only the site. A write
whose outcome cannot be known — a lost response, an HTTP 5xx from Bing, or a 2xx whose
body cannot be read — becomes `unknown_outcome` and is never retried automatically. A
plan whose TTL elapses while its request is in flight still records what happened to it.
An IndexNow 5xx is the exception and leaves the plan pending as a retryable failure:
the protocol directs callers to resubmit a valid request after a non-success response.
The CLI does not retry automatically, and repeated submissions can consume crawl quota.

`audit.jsonl` records creation, attempts, failures, denials, unknown outcomes, and
successes without storing the API key or content bodies. `limits.sqlite3` stores
configured local counters transactionally; a plan reserves its cost before the request
and the reservation is released only if the request never reached Bing. For an unknown
outcome, inspect Bing and the audit log; do not create a replacement blindly.

A SIGKILL or power loss can leave `<plan_id>.lock` after its process has died. Run
`bing-wm plan unlock <plan_id>` to recover it. The command refuses a live PID, a
malformed lock, or a plan with no lock. Before removing a dead process's lock it marks
any unfinished plan `unknown_outcome`, so recovery cannot enable an accidental second
write; an already-terminal plan keeps its state. Both the start and completion of the
recovery are appended to `audit.jsonl`.

Site-scoped and IndexNow URL paths reject literal or repeatedly percent-encoded dot
segments before ownership and denylist comparisons. IndexNow key-file preflight does not
follow redirects, accepts only the submitted multi-label DNS host on the default HTTPS port,
and requires the response body to contain exactly the key.

## Releases

A `vX.Y.Z` tag must match the version in `pyproject.toml`. `mcpb.yml` builds the
Desktop Extension and attaches it to the GitHub Release, creating the release if
needed. The PyPI job in `release.yml` is skipped unless the repository variable
`PYPI_PUBLISH` is `true`. PyPI publishing is enabled by setting `PYPI_PUBLISH=true`
after the PyPI Trusted Publisher is configured for this repository and the `pypi`
environment.
