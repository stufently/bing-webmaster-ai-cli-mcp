# bing-webmaster-ai-cli-mcp — implementation spec

Status: implemented for 0.1.0; release pending. Written 2026-08-25.

This document is the contract for the first implementation. It is written to be
handed to a coding agent: every section states what to build, and the facts it
rests on are marked **verified** (fetched from a primary source on 2026-08-25) or
**unverified** (must be checked before the code depends on it).

---

## 1. What this is

An MCP server and CLI over the **Bing Webmaster Tools API**, plus first-class
**IndexNow** submission.

One sentence for the README and for anything that quotes it: *bing-webmaster-ai-cli-mcp
gives an AI agent read access to what Bing knows about your sites — traffic,
indexing, crawl issues, inbound links, keywords — and a write path you choose:
direct by default, or reviewed plan-and-apply.*

### Why it exists

Bing is the retrieval layer behind ChatGPT search and Copilot, so Bing Webmaster
Tools is where the ground truth about AI-search visibility lives. The API is free
and requires only a key from the Bing Webmaster Tools UI.

**The classic Bing Search API is dead** and is not part of this project: Microsoft
announced its retirement on 2025-08-11 and instances were deactivated through
2026-08. Its replacement, "Grounding with Bing Search", is an agent product inside
Azure AI Foundry that returns a generated answer rather than results, at a
materially higher price. Nothing here talks to it. *(verified)*

### Differentiation

Two servers already exist in this space. Neither is a reason not to build; both
define what this one has to do better.

| | `isiahw1/mcp-server-bing-webmaster` | `saurabhsharma2u/search-console-mcp` |
|---|---|---|
| Stars | 22 | 276 |
| Scope | Bing only, ~50 tools (README-confirmed, source not read) | Google-first combine: GSC + Bing + GA4 + AdSense |
| Bing coverage | close to the full method surface | **19 methods, source-verified** — a real subset |
| Writes | direct-execute MCP tools | direct-execute MCP tools |
| IndexNow | not mentioned | an `index_now` option, implementation unverified |

The three openings, in priority order:

1. **A review gate available on every mutating call.** Neither competitor has
   one. `BING_WM_ALLOW_WRITES=false` routes every write through plan-and-apply
   (§6). It is off by default — an operator who wants the gate opts into it —
   but the gate itself remains the main differentiator.
2. **First-class, protocol-correct IndexNow** (§8) — batch, key-file handling,
   fan-out to all participating engines, honest response-code mapping.
3. **Complete Bing coverage in a focused server**, rather than Bing as an
   afterthought in a Google-first tool. Methods `search-console-mcp` is verified
   *not* to implement: `VerifySite`, site roles, `GetQueryPageDetailStats`,
   `GetUrlTrafficInfo`, crawl settings, content submission, `GetKeyword`,
   `AddConnectedPage`, `GetUrlLinks`, blocked URLs, deep-link blocks, query
   parameters, geo-targeting, page-preview blocks, site moves, children-URL info.

---

## 2. Non-goals

Write these into `docs/product-boundaries.md` on the first commit, with dates.
Absence is a decision, not a gap.

- **No SOAP, no POX/XML.** Both retire 2026-08-31. Build only against
  `.../api.svc/json/`. *(verified)*
- **No Bing Search / SERP scraping.** No API exists and scraping is a different
  project with different legal exposure.
- **No Google Search Console, GA4, or AdSense.** That is `search-console-mcp`'s
  territory and a combine is not the differentiator.
- **No `apply` tool over MCP.** See §6 — this is a security boundary, not an
  omission.
- **The two deep-link methods Microsoft marks `Obsolete`** (`GetDeepLinkAlgoUrls`,
  `GetDeepLink`, `UpdateDeepLink`) are not exposed. The non-obsolete deep-link
  *block* methods are.
- **No stored analytics, no scheduler, no dashboards.** Read on demand, hand the
  data to the agent.

---

## 3. Verified facts the implementation depends on

### Transport

| Fact | Value |
|---|---|
| Base URL | `https://ssl.bing.com/webmaster/api.svc/json/{Method}` |
| Auth (simple) | query parameter `apikey=<key>`, from Bing Webmaster Tools → Settings → API Access |
| Auth (OAuth2) | authorization-code flow; authorize `https://www.bing.com/webmasters/oauth/authorize`, token `https://www.bing.com/webmasters/oauth/token`, header `Authorization: Bearer <token>`, scopes `Webmaster.read` and `Webmaster.manage`, auth code lives 5 minutes, refresh token is long-lived |
| Verbs | GET (params in query string) and POST (params in a JSON body) are both accepted for every method |
| Success envelope | `{"d": ...}` — always unwrap `d`. Objects carry an informational `__type` that is ignored |
| Error envelope | `{"ErrorCode": <int>, "Message": "<string>"}` with HTTP 400, and **no** `d` wrapper |

All of the above: *verified* against `learn.microsoft.com/bingwebmaster/api-protocols`,
`.../oauth2`, and the SOAP/POX retirement notice.

### Two traps to handle explicitly

**Dates are not ISO 8601.** The JSON endpoint returns ASP.NET tick format —
`"\/Date(1316156400000-0700)\/"`. Write a deserializer, test it against a value
with a negative offset, a positive offset, and no offset. Anything that assumes
`datetime.fromisoformat` will fail at runtime and pass a unit test that was
written from the same wrong assumption. *(verified)*

**No JSON schema is published.** The `.NET IWebmasterApi` interface reference is
the closest thing to one. Parameter names and order map onto the JSON endpoints.

### Auth mode decision

Ship `apikey` first — it is what both competitors and the one existing Python
client use, and it needs no redirect URI. Design `auth.py` so OAuth2 slots in
behind the same interface, and implement it only when someone asks. Record that
choice in `docs/product-boundaries.md`.

---

## 4. Method surface

The full table lives in `docs/api-surface.md` — copy it there as the first
implementation step from the research notes below.

Groups, with counts to build toward:

| Group | Methods | Read | Write |
|---|---|---|---|
| Site management | 9 | `GetUserSites`, `GetSiteRoles`, `GetSiteMoves` | `AddSite`, `RemoveSite`, `VerifySite`, `AddSiteRoles`, `RemoveSiteRole`, `SubmitSiteMove` |
| Traffic and queries | 7 | `GetQueryStats`, `GetQueryTrafficStats`, `GetQueryPageStats`, `GetQueryPageDetailStats`, `GetPageStats`, `GetPageQueryStats`, `GetRankAndTrafficStats` | — |
| Index and crawl | 11 | `GetUrlInfo`, `GetUrlTrafficInfo`, `GetChildrenUrlInfo`, `GetChildrenUrlTrafficInfo`, `GetCrawlStats`, `GetCrawlIssues`, `GetCrawlSettings`, `GetFetchedUrls`, `GetFetchedUrlDetails` | `SaveCrawlSettings`, `FetchUrl` |
| URL submission | 3 | `GetUrlSubmissionQuota` | `SubmitUrl`, `SubmitUrlBatch` |
| Content submission (beta) | 2 | `GetContentSubmissionQuota` | `SubmitContent` |
| Sitemaps / feeds | 4 | `GetFeeds`, `GetFeedDetails` | `SubmitFeed`, `RemoveFeed` |
| URL blocking | 3 | `GetBlockedUrls` | `AddBlockedUrl`, `RemoveBlockedUrl` |
| Query parameters | 4 | `GetQueryParameters` | `AddQueryParameter`, `RemoveQueryParameter`, `EnableDisableQueryParameter` |
| Geo-targeting | 3 | `GetCountryRegionSettings` | `AddCountryRegionSettings`, `RemoveCountryRegionSettings` |
| Page-preview blocks | 3 | `GetActivePagePreviewBlocks` | `AddPagePreviewBlock`, `RemovePagePreviewBlock` |
| Deep-link blocks | 3 | `GetDeepLinkBlocks` | `AddDeepLinkBlock`, `RemoveDeepLinkBlock` |
| Inbound links | 4 | `GetLinkCounts`, `GetUrlLinks`, `GetConnectedPages` | `AddConnectedPage` |
| Keyword research | 3 | `GetKeyword`, `GetKeywordStats`, `GetRelatedKeywords` | — |

> **Resolved 2026-08-25 from the primary source.** `IWebmasterApi` lists 62 methods:
> 59 supported methods in the grouped table above plus the three deliberately excluded
> obsolete deep-link methods. The earlier report of 57 was incorrect. The complete
> transcription, exact parameter names and method-level `WebGet`/`WebInvoke` verbs are
> recorded in `docs/api-surface.md`.
>
> Complex parameter types (`BlockedUrl`, `CrawlSettings`, `FilterProperties`,
> `SiteMoveSettings`, `CountryRegionSettings`, `BlockReason`, `DeepLinkWeight`,
> `SiteRoles`) each have their own reference page. Fetch each one while
> implementing its method. Do not infer field names.

Read/write classification above is derived from the verb (`Get*`/`Fetch*` read;
`Add*`/`Remove*`/`Submit*`/`Save*`/`Verify*`/`Update*`/`EnableDisable*` write) and
is not labelled as such by Microsoft. `FetchUrl` is classified **write** despite
its name: it asks Bing to crawl something and consumes quota.

---

## 5. Architecture

Mirror `telegram-ai-cli`, which is the proven template in this family. CLI and MCP
must never diverge in behaviour, so both call one shared ops layer.

```
bing_webmaster_ai_cli_mcp/
  cli.py              Click entry point, thin, dispatches into ops/
  config.py           settings via pydantic-settings, env-first
  auth.py             apikey now, OAuth2 behind the same interface later
  client.py           thin httpx JSON client: d-unwrap, tick-dates, error mapping,
                      client-side throttle, retry with backoff
  errors.py           error taxonomy, public JSON contract
  plans.py            plan records: create, list, show, expire
  apply.py            execution of an approved plan
  audit.py            append-only log of every attempted and completed write
  limits.py           rate limits that survive a restart
  render.py           output sanitizer (see below)
  ops/
    sites.py  traffic.py  crawl.py  submission.py  sitemaps.py
    blocking.py  params.py  geo.py  links.py  keywords.py  indexnow.py
    _common.py  _serialize.py
  mcp_server.py       stdio MCP server, calls ops/
  http_server.py      loopback-only, bearer-gated (optional, ship after stdio)
```

`render.py` is not optional. Crawl issues, inbound-link anchor text and query
strings are attacker-influenced: anyone can link to your site with any anchor
text, and any bot can put anything in a URL that ends up in a crawl-issue report.
That text passes through this tool into an agent's context and onto a terminal.
Sanitize control characters and mark the fields as untrusted before they are
rendered or returned. `telegram-ai-cli`'s `render.py` and its untrusted-field
marking are the reference implementation.

### Error taxonomy (`errors.py`)

Copy the shape from `telegram-ai-cli/telegram_ai_cli/errors.py`:

- one `ErrorCode(StrEnum)`, flat namespace, grouped by comment headers;
- one base `BingWebmasterError(message, *, suggestion=None, retry_after=None, details=None)`
  with class-level `code` and `retryable: bool`;
- `.to_dict()` → `{"code", "message", "retryable", "suggestion"?, "retry_after"?, "details"?}`;
- every subclass carries a docstring saying *why* it is or is not retryable.

Minimum set:

| Code | HTTP / cause | Retryable |
|---|---|---|
| `INVALID_REQUEST` | 400 with `ErrorCode`/`Message` body | no |
| `AUTH_FAILED` | bad or missing api key, expired token | no |
| `SITE_NOT_VERIFIED` | operating on a site the account does not own | no |
| `QUOTA_EXCEEDED` | submission quota exhausted | no — waiting does not help until reset |
| `RATE_LIMITED` | 429 | yes, with `retry_after` |
| `UPSTREAM_UNAVAILABLE` | 5xx, connection failure | yes |
| `MALFORMED_RESPONSE` | missing `d`, undecodable tick date | no |
| `PLAN_UNKNOWN_OUTCOME` | request sent, response lost | **no** — a retry is how one submission becomes two |
| `INTERNAL` | catch-all | no |

Treat codes as a public contract: renaming one is a breaking change.

---

## 6. The write boundary — `BING_WM_ALLOW_WRITES`

Every write in §4, plus IndexNow submission, takes one of two paths. A single
boolean setting picks which, and which MCP tools are advertised.

**`true` (default) — direct.** One `bing_<operation>` MCP tool per write, and
`bing-wm write` plus its shortcuts on the CLI. The change reaches Bing on the
first call, with no separate approval.

**`false` — reviewed.** The write is split in two:

1. `bing_plan_<operation>` validates arguments, resolves the target site, checks
   quota where relevant, records the intent, and returns `plan_id` + a
   human-readable `summary`. **No change is sent** — planning a quota-aware write
   does read Bing's quota, and nothing else reaches Bing.
2. `bing-wm plan apply <plan_id>` executes it — CLI only, with a confirmation
   prompt unless `--yes`.

A direct write is not a second write implementation. It records the same durable
plan and goes through the same apply boundary; the setting removes the human
between the two steps and nothing else. Every control below holds on both paths.

**No MCP tool accepts a plan ID, in either mode**, so none can apply or reject a
plan recorded for review. A direct write applies the plan it creates within the
same call. Enabling direct writes replaces the planning tools rather than adding a
tool that can confirm a plan somebody else wrote. State the reasoning in the README: a
confirmation an agent can send over MCP is a confirmation that prompt injection
can send — which is also why the README tells an unsure operator to choose
`false`, and why the planning path stays available while direct writes are on.

Be honest about the limit, the way `telegram-ai-cli` is: an MCP client that also
has a shell (Claude Code, Codex) can run `plan apply` itself. The design does not
prevent that. What the reviewed path buys is that the write leaves a plan record,
an audit entry and a rendered summary a human can read, instead of happening
invisibly inside a tool call. Compensating controls on both paths: full audit log
of attempt and outcome, restart-persistent rate limits, and a config denylist of
sites that can never be mutated.

Plans expire — default 15 minutes, configurable. An expired plan cannot be
applied. Applying twice must fail on the second attempt, not submit twice. A
direct write creates and applies its plan in the same call, so its TTL never
matters.

`allow_writes` is re-read where the write executes, not only where a tool is
advertised: an MCP client may hold a tool list from before the setting changed,
and that call must be refused with `POLICY_DENIED`.

---

## 7. Surface: CLI and MCP tools

Console script: `bing-wm`. Every command prints JSON with `--json`, human tables
otherwise.

```
bing-wm sites list
bing-wm sites show <site>
bing-wm sites roles <site>
bing-wm traffic queries <site> [--limit N]
bing-wm traffic pages <site> [--limit N]
bing-wm traffic query <site> <query>
bing-wm traffic page <site> <page>
bing-wm traffic rank <site>
bing-wm index url <site> <url>
bing-wm index children <site> <url> [--page N]
bing-wm crawl stats <site>
bing-wm crawl issues <site>
bing-wm crawl settings <site>
bing-wm links counts <site> [--page N]
bing-wm links url <site> <url> [--page N]
bing-wm keywords get <keyword> --country US --language en-US --start … --end …
bing-wm keywords related <keyword> …
bing-wm sitemaps list <site>
bing-wm quota <site>

bing-wm write <operation> --args-json '{…}'
bing-wm submit-url <site> <url>
bing-wm submit-urls <site> --file urls.txt
bing-wm submit-sitemap <site> <feed-url>
bing-wm block-url <site> <url> …
bing-wm indexnow submit <host> --file urls.txt --key <key>

bing-wm plan submit-url <site> <url>
bing-wm plan submit-urls <site> --file urls.txt
bing-wm plan submit-sitemap <site> <feed-url>
bing-wm plan block-url <site> <url> …
bing-wm plan indexnow <host> --file urls.txt
bing-wm plan list | show <id> | apply <id> [--yes] | reject <id>
```

The one-step commands above the blank line require `BING_WM_ALLOW_WRITES`; the
plan commands work in both modes.

MCP tools: `bing_sites_list`, `bing_site_roles`, `bing_traffic_queries`,
`bing_traffic_pages`, `bing_traffic_query`, `bing_traffic_page`,
`bing_traffic_rank`, `bing_url_info`, `bing_children_url_info`,
`bing_crawl_stats`, `bing_crawl_issues`, `bing_crawl_settings`,
`bing_link_counts`, `bing_url_links`, `bing_connected_pages`,
`bing_keyword`, `bing_keyword_stats`, `bing_related_keywords`,
`bing_sitemaps`, `bing_sitemap_details`, `bing_blocked_urls`,
`bing_query_parameters`, `bing_geo_settings`, `bing_page_preview_blocks`,
`bing_deep_link_blocks`, `bing_site_moves`, `bing_fetched_urls`,
`bing_submission_quota`, `bing_content_submission_quota`,
plus `bing_indexnow_key_plan`, which computes IndexNow key material locally
and reaches neither Bing nor `api.indexnow.org`,
plus one write tool per operation — `bing_<operation>` when
`BING_WM_ALLOW_WRITES` is on, `bing_plan_<operation>` when it is off — plus
`bing_plan_list` and `bing_plan_show`. No `bing_plan_apply` in either mode.

Tool descriptions are part of the prompt the model reads. Write them as
instructions to the model, not as API docs, and say what a tool must **not** be
used for where that matters.

---

## 8. IndexNow

A separate protocol, not part of the Bing API, and worth doing properly because
neither competitor confirms it. All *verified* from `indexnow.org` and the live
`searchengines.json`.

- **Key**: any string of 8–128 chars from `a-zA-Z0-9-`. No derivation required; a
  random hex string is fine. The tool should be able to generate one.
- **Key hosting**: `https://<host>/<key>.txt`, UTF-8, containing exactly the key,
  publicly readable, no auth. A key at a subpath only authorizes URLs under that
  subpath, and then `keyLocation` must be passed.
- **Single URL**: `GET https://api.indexnow.org/indexnow?url=<url>&key=<key>[&keyLocation=<url>]`
- **Batch**: `POST https://api.indexnow.org/indexnow`,
  `Content-Type: application/json; charset=utf-8`, body
  `{"host", "key", "keyLocation", "urlList"}`. **Max 10 000 URLs**; over that the
  request may fail or return 422.
- **Participating engines (7, live from `searchengines.json`)**: Bing, Yandex,
  Seznam, Naver, Yep, Internet Archive, Amazonbot. `api.indexnow.org` fans out to
  all of them; each also accepts direct submission at its own `/indexnow` path.
- **Google does not participate** and never adopted it. Say so in the README —
  users will assume otherwise.
- Response codes: 200 received, 202 accepted (typical on first use of a key), 400
  bad request, 403 key invalid or key file unreachable, 422 URLs not on the host /
  key mismatch / batch too large, 429 rate limited.

Before submitting, verify the key file is actually reachable and contains the key,
and fail with a clear message if not — 403 from a fan-out endpoint is otherwise
very hard to diagnose. A per-URL length cap of 2048 chars is cited by secondary
sources only: **unverified**, do not hardcode it without checking.

---

## 9. Quotas

**Never hardcode quota numbers.** `GetUrlSubmissionQuota` returns
`{"DailyQuota": int, "MonthlyQuota": int}` per site; call it before any submission
batch and refuse to plan a batch larger than the remaining daily quota. *(verified)*

The widely repeated figures — 10 000 URLs/day, 100/day for new sites, midnight-UTC
reset, 500 per batch — are secondary-source folklore. The 10 000/day figure traces
to a 2019 Bing blog post about the *manual* submission tool. **Unverified**; the
API is the authority.

No QPS rate limit for the Webmaster API is documented anywhere. Apply a
conservative client-side throttle (5 calls/sec is what the one existing Python
client chose) and back off on 429, honouring `Retry-After` when present.

---

## 10. Dependencies and packaging

Checked 2026-08-25: Python 3.14 is the newest maintained branch; 3.12 and 3.13 are
supported until 2028 and 2029. `mcp` is at 2.1.0, `httpx` at 0.28.1.

- `requires-python = ">=3.12"` — this is a **floor**, not a pin. CI matrix 3.12,
  3.13, 3.14.
- Dependency **floors** in `pyproject.toml` (`httpx>=0.28,<1`, `pydantic>=2.11,<3`,
  `pydantic-settings`, `click>=8.2,<9`, `mcp>=2.1,<3`), exact pins in
  `constraints.txt`, which is what CI and the Docker image install. Copy the
  comment from `telegram-ai-cli/pyproject.toml` explaining why.
- Console script `bing-wm = "bing_webmaster_ai_cli_mcp.cli:main"`.
- Ruff: `line-length = 100`, `target-version = "py312"`,
  `select = ["E","F","I","UP","B","SIM","S"]`, `tests/*` exempt from `S101`.
- MIT, matching the rest of the family.

### Do not depend on `bing-webmaster-tools`

The one existing Python client (`bing-webmaster-tools` 1.2.0, merj, MIT, 20 stars)
last saw a push on 2025-04-26 — about sixteen months ago — and is apikey-only. It
independently corroborates the base URL and auth style, which is useful. Read its
source for parameter shapes; do not take the dependency. A stale client is exactly
what will not have absorbed the SOAP/POX retirement or the tick-date handling.

---

## 11. Tests

`tests/` mirrors modules 1:1. `asyncio_mode = "auto"`, `testpaths = ["tests"]`,
`pythonpath = [".", "tests"]` so the checkout and `fakes` import without requiring an
editable install, and
`filterwarnings = ["error::DeprecationWarning:bing_webmaster_ai_cli_mcp.*"]`.

A shared `tests/fakes.py` provides a fake transport. **No test may reach the
network.** Add a test that fails if a real HTTP call is attempted.

Required cases, beyond per-op coverage:

- tick-date parsing: positive offset, negative offset, no offset, and a malformed
  value that must raise `MALFORMED_RESPONSE` rather than return `None`;
- `d`-unwrapping, including an error body that has no `d`;
- error mapping for 400 / 401 / 429 / 500;
- a plan cannot be applied twice;
- an expired plan cannot be applied;
- a batch larger than the remaining quota is refused at plan time;
- `bing_plan_apply` does **not** exist in the MCP tool list — assert on the
  registered tool names, so adding one later fails the suite;
- IndexNow: batch over 10 000 is refused locally; an unreachable key file is
  refused before submission;
- untrusted fields (anchor text, crawl-issue URLs, query strings) survive a
  round-trip through `render.py` with control characters neutralised.

## 12. CI

Copy `telegram-ai-cli/.github/workflows/ci.yml`. Third-party actions pinned to
**commit SHA**, not tag, with a comment recording the verification date. Jobs:

- `lint` — ruff, same `make lint` target a developer runs;
- `test` — matrix 3.12/3.13/3.14, `pip install -c constraints.txt ".[test]"`;
- `smoke` — a real MCP stdio `initialize` handshake, not an import check;
- `container` — builds the image and asserts it does not run as root.

`concurrency` cancels superseded runs; `permissions: contents: read`.

Release: PyPI Trusted Publishing on a `v*` tag, environment `pypi`, no token in
the repo. The account-side steps (pending publisher, GitHub environment) are the
owner's and are listed in `TASKS.md`.

---

## 13. Build order

Each step ends with tests passing and a commit.

0. **Reconcile the method table** (§4 warning). Write `docs/api-surface.md`.
1. `errors.py` + `client.py`: transport, `d`-unwrap, tick dates, error mapping,
   throttle, retry. Tests first — this is where the traps are.
2. `config.py` + `auth.py` (apikey), `ops/sites.py`, and `bing-wm sites list`.
   First end-to-end slice.
3. Read ops by group: traffic → crawl → links → keywords → sitemaps → blocking →
   params → geo → page-preview → site moves. One group per commit.
4. `plans.py`, `apply.py`, `audit.py`, `limits.py` + the first write
   (`submit-url`). Get the boundary right before adding writes in bulk.
5. Remaining writes.
6. `ops/indexnow.py` including key generation and key-file verification.
7. `mcp_server.py` over stdio; wire every read tool and every `plan_*` tool.
   Assert no apply tool.
8. `README.md`, `llms.txt`, `docs/product-boundaries.md`, `docs/operations.md`,
   `CITATION.cff`.
9. CI, `constraints.txt`, Dockerfile, release workflow.
10. `http_server.py` — loopback, bearer-gated. Last, and only if wanted.

---

## 14. Resolved research questions

Re-fetched from Microsoft Learn on 2026-08-25 and recorded in
`docs/api-surface.md`:

1. The interface has 62 methods: 59 supported plus three obsolete exclusions.
2. Every page exposes `WebGet` or `WebInvoke(Method="POST")`; the exact verbs are
   transcribed. `GetChildrenUrlInfo` is the unusual POST read.
3. Exact complex-type property names are transcribed from their type pages.
4. Keyword methods are standalone: their signatures contain no site parameter.
5. Microsoft's `SubmitUrlBatch` remarks document a 500-URL per-call maximum.
6. `GetContentSubmissionQuota` returns `DailyQuota` and `MonthlyQuota` as `Int64`.
7. The current interface still exposes content submission without a beta label.
   Per-account eligibility cannot be checked without an enrolled account, so Bing's
   response remains authoritative.
