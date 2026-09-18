# bing-webmaster-ai-cli-mcp

<!-- mcp-name: io.github.stufently/bing-webmaster-ai-cli-mcp -->

bing-webmaster-ai-cli-mcp gives an AI agent read access to what Bing knows about your sites — traffic, indexing, crawl issues, inbound links, keywords — and a write path you choose: direct by default, or reviewed plan-and-apply.

It ships a Python 3.12+ CLI and MCP server for the JSON Bing Webmaster Tools API,
plus protocol-correct IndexNow submission. SOAP and POX are deliberately absent.

## Choosing a write path

Read tools always execute immediately. For everything that changes Bing state, one
setting picks between two paths:

```console
export BING_WM_ALLOW_WRITES=true    # default: writes go straight through
export BING_WM_ALLOW_WRITES=false   # writes are planned and applied by a human
```

**If you are not sure, set it to `false`.** The default is convenience; `false` is the
safe answer, and here is the reason. An agent using this server also reads pages, anchor
text, crawl issues and search queries written by strangers. A confirmation an agent can
send is a confirmation prompt injection can send, so as long as the agent can write, a
poisoned string in someone else's anchor text can reach your Bing account. With
`false`, nothing an agent does changes Bing until you have read the plan and run
`bing-wm plan apply` yourself.

### Direct writes (default)

The MCP server advertises one-step `bing_<operation>` tools, and the CLI accepts:

```console
$ bing-wm submit-url example.com https://example.com/new-page --json
{"applied": true, "plan_id": "…", "operation": "submit_url", "result": null}
```

A direct write is not idempotent: retrying one records a new plan and sends the change
again. If a response is lost, read `audit.jsonl` or `bing-wm plan list` before repeating
the call.

### Reviewed writes (`BING_WM_ALLOW_WRITES=false`)

The server advertises `bing_plan_<operation>` instead. Those tools send nothing:

```console
$ bing-wm plan submit-url example.com https://example.com/new-page --json
{
  "plan_id": "…",
  "summary": "submit url https://example.com/new-page for https://example.com",
  "apply_with": "bing-wm plan apply …"
}
$ bing-wm plan apply PLAN_ID
Apply this plan? [y/N]: y
```

No MCP tool takes a plan ID, in either mode: a plan recorded for review is applied or
rejected only at the CLI, by you. A direct write applies the plan it creates in the same
call and never touches one somebody else recorded. The plan path stays available while
direct writes are on, so an agent can still propose a change for review.

### What both paths share

A direct write is the same code with the human step removed: it records the same durable
plan and goes through the same apply boundary. A readable plan record, an append-only
audit trail, one-shot application, expiry, Bing's own submission quota, a site denylist
(`BING_WM_DENIED_SITES`) and restart-persistent local limits
(`BING_WM_MAX_WRITES_PER_DAY`) apply to both.

If the applying process is killed and leaves a lock behind, the command
`bing-wm plan unlock PLAN_ID` verifies that the recorded PID is gone before recovering
it. An unfinished plan becomes `unknown_outcome` and cannot be applied again; the
recovery is audited.

## Install

```console
python -m pip install bing-webmaster-ai-cli-mcp
```

The supported matrix is Python 3.12, 3.13, and 3.14. Development and images use the
exact versions in `constraints.txt`; published dependencies remain compatible floors.

Create an API key in Bing Webmaster Tools under Settings → API Access and provide it
through the environment:

```console
export BING_WM_API_KEY='…'
bing-wm sites list
```

Never put a real key in the repository. See [configuration](docs/configuration.md) for
all settings.

## MCP client configuration

Run the stdio server with `bing-webmaster-ai-cli-mcp`. Point an MCP client at that executable
and pass `BING_WM_API_KEY` through its protected environment configuration. The server
exposes 34 Bing read tools, one local read-only tool (`bing_indexnow_key_plan`), plan
inspection, and one write tool per supported operation —
direct `bing_<operation>` tools by default, or `bing_plan_<operation>` tools when
`BING_WM_ALLOW_WRITES=false`. It exposes no plan application or rejection tool. Restart
the server after changing the setting so the client refreshes its tool list.

An optional Streamable HTTP entry point is also available:

```console
export BING_WM_HTTP_BEARER_TOKEN='a-random-token-of-at-least-32-characters'
bing-webmaster-ai-cli-mcp-http
```

It refuses non-loopback bind addresses and unauthenticated requests.

## Common questions

### Which crawl issues does my site have?

```console
bing-wm crawl issues example.com --json
```

The result carries Bing's rows unchanged plus a category breakdown built from
Microsoft's `UrlWithCrawlIssues.CrawlIssues` flags — redirects, 4xx, 5xx, robots.txt
blocks, malware, DNS and timeout errors — with a count per category, a count per raw
`HttpCode`, and an `other` bucket so nothing Bing sends is dropped. A 4xx row is split
further into `http_404` or `http_403` from its own `HttpCode`, alongside the broad
`http_4xx` rather than instead of it. Bing has no `noindex` crawl-issue flag, so there
is no such category and this project does not invent one.

### How do I check if Bing has indexed my page?

```console
bing-wm index url example.com https://example.com/page
```

### How do I submit URLs to Bing from the command line?

With writes enabled, from one URL or a newline-delimited file:

```console
bing-wm submit-url example.com https://example.com/page
bing-wm submit-urls example.com --file PATH
```

With `BING_WM_ALLOW_WRITES=false`, create a plan, review it, then apply it:

```console
bing-wm plan submit-url example.com https://example.com/page
bing-wm plan submit-urls example.com --file PATH
bing-wm plan show PLAN_ID
bing-wm plan apply PLAN_ID
```

The planner calls `GetUrlSubmissionQuota`; no quota number is hardcoded. Bing's method
documentation also limits one `SubmitUrlBatch` call to 500 URLs.

### Does IndexNow work with Google?

Google does not participate in IndexNow. The live IndexNow registry currently lists
Bing, Yandex, Seznam, Naver, Yep, Internet Archive, and Amazonbot. Submission through
`api.indexnow.org` fans out to participating engines.

Generate a key, host the displayed UTF-8 key file, then submit:

```console
bing-wm indexnow key example.com
bing-wm indexnow key example.com --key KEY   # is the key file live yet?
bing-wm indexnow submit example.com --file PATH --key KEY
```

`indexnow key` — and the matching `bing_indexnow_key_plan` MCP tool — only computes and
checks. It talks to neither Bing nor `api.indexnow.org`, so it is not a write and needs
no plan. The generated key is printed once and stored nowhere: save it, serve it at the
printed URL, then submit.

Or, with `BING_WM_ALLOW_WRITES=false`:

```console
bing-wm plan indexnow example.com --file PATH --key KEY
bing-wm plan apply PLAN_ID
```

The submission checks the exact key-file URL without following redirects before it sends
the batch. IndexNow hosts must use multi-label DNS names rather than IP literals or
single-label names, and the key file must
contain only the key. Batches above 10,000 URLs, ambiguous dot-segment paths, and URLs
outside the authorized host or key subpath are rejected locally. There is intentionally
no unverified 2,048-character URL cap. IndexNow's protocol tells callers to resubmit a
valid request after a non-success response, so an HTTP 5xx leaves the plan pending; the
CLI never retries it automatically.

## Output safety

Anchor text, crawl-issue URLs, titles, messages, and query strings may be controlled by
strangers. The shared operation layer removes control and bidirectional-formatting
characters, truncates extreme values, and returns these fields as
`{"value": "…", "untrusted": true}`. Consumers must treat them as data, never as
instructions.

Verification secrets never leave the machine by accident. `GetUserSites` returns
`AuthenticationCode` and `DnsVerificationCode` beside every site and `GetSiteRoles`
returns `DelegatedCode`; whoever holds one can claim the site in another Bing account.
All three are replaced with `[redacted: verification secret]` in every response, and the
only way to see one is `--reveal-verification-codes` typed by an operator on
`bing-wm sites list|show|roles` or `bing-wm plan show|list|apply`. No MCP tool takes that
argument, so no model — and no text a model read — can ask for a code.

Redaction is an exit boundary, not a step on the read path. The same filter covers a
write's result, the arguments a plan records — an `add_site_roles` plan holds the
`authentication_code` it will send, and showing the plan does not show the code — and
Bing's own error text, in case Bing quotes back the code it rejected. The literals to
hide come from the request body, so the cover does not depend on anyone predicting which
field a secret will next arrive in. The plan record on disk keeps the real value; a plan
that lost its code could not be applied.

The API key is covered at the same boundary, under its own marker
`[redacted: API credential]`. Microsoft documents the key only as a query-string
parameter, so every request URL carries a live credential — and a proxy naming the
address it could not reach, or Bing quoting the request back in an error, would otherwise
put that URL into the error message, the terminal and the audit trail. An error carrying
no credential reads exactly as it did before.

An empty answer is never presented as a measurement. Some of the older endpoints return
an empty collection for accounts that demonstrably have data: `bing_link_counts`,
`bing_crawl_issues` and `bing_fetched_urls` all came back empty for a site whose
`bing_crawl_stats` reported 1700 inbound links in the same minute. A read that returned
no rows carries `empty_response: {rows_returned: 0, measured: false, note: …}` beside
`result` over MCP, and prints the note on stderr at the CLI, so "Bing returned nothing"
cannot be reported as "no problems found". Reads that answer with a single record —
`bing_url_info`, `bing_crawl_settings`, the quotas, `bing_keyword` — are never labelled:
an array inside one record, such as `CrawlRate`, is a field of that record
and not a row Bing withheld. `bing_url_info` labels the same trap in one field:
`HttpStatus: 0` means Bing reports no status, not `200`, and the row says so with
`http_status_reported`.

## Coverage and references

- [Agent skill: how to drive this server](SKILL.md)
- [CLI commands, MCP tools, and write operations](docs/operations.md)
- [Complete 62-method Microsoft interface transcription](docs/api-surface.md)
- [Product boundaries](docs/product-boundaries.md)
- [Configuration](docs/configuration.md)

The Microsoft interface contains 62 methods: 59 supported here and three obsolete
deep-link methods deliberately excluded. Keyword research is standalone because its
official signatures contain no site parameter. Content submission is exposed because
it remains present in Microsoft's current interface; account-side eligibility can only
be confirmed by Bing for a particular account.

## Licence

MIT.
