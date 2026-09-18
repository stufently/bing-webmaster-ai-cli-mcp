# Changelog

## Unreleased

- Rename the project to `bing-webmaster-ai-cli-mcp`, matching `telegram-ai-cli-mcp` and
  `zabbix-ai-cli-mcp`: it ships a real CLI as well as an MCP server, and the old name
  only said the second. The GitHub repository, the distribution, the import package
  (`bing_webmaster_ai_cli_mcp`), the MCP server name, the registry name in
  `server.json` and the console scripts `bing-webmaster-ai-cli-mcp` and
  `bing-webmaster-ai-cli-mcp-http` all follow. Unchanged on purpose: the `bing-wm`
  command and the `BING_WM_*` environment variables. Nothing was published under the
  old name, so no alias is kept. The default state directory moves to
  `~/.local/state/bing-webmaster-ai-cli-mcp`, but an install that only has the old
  `~/.local/state/bing-webmaster-mcp` keeps using it, so plans, the audit log and the
  daily write counters are not reset by an upgrade.

- Cover the API key itself at the same exit boundary. Microsoft documents the key only as
  a query-string parameter, so every URL this client builds carries a live credential —
  and the redaction that already scrubbed error text derived its literals from the
  request *body*, where the key never is. Nothing here prints a URL, and `httpx`'s own
  exception messages do not contain one either (measured: `ConnectError`, `ReadTimeout`,
  `RemoteProtocolError` and `TooManyRedirects` all carry no URL). The leak is narrower
  and real: a message that came from *underneath* — a proxy or transport naming the
  address it could not reach — or Bing quoting our request back in its error text. Either
  became the error message, the MCP reply, the terminal output and the audit entry
  verbatim. The credential is now hidden in all of them, under its own marker
  `[redacted: API credential]` rather than the verification-secret one, and the literal
  comes from the auth provider that applied it, so an OAuth2 token added later is covered
  by implementing one method. An error carrying no credential still reads exactly as
  before. Three details the first pass got wrong and a review caught: the literals are
  read on every call rather than once at construction, so a provider that refreshes a
  token stays covered; each credential is also matched in the form httpx writes into a
  URL, because a key holding `/` or a space reaches the wire as `%2F` or `+` and an exact
  search for the value we hold would miss it; and a lost write no longer chains the raw
  `httpx` exception as its cause, since `to_dict` cannot cover a `__cause__` that any
  formatted traceback would print. What failed is carried scrubbed in the error's
  `details.cause` instead, where the audit trail records it too.
- Close the two ways a verification secret still left the process after it was redacted
  in reads. A plan keeps the arguments it will send, so `bing_plan_show`, `bing_plan_list`
  and `bing-wm plan show|list` were handing back the `authentication_code` an
  `add_site_roles` plan carries, and the `bing-wm plan apply` prompt printed it. All of
  them now use a public serialization that redacts every secret in a plan's arguments,
  nested ones included; `--reveal-verification-codes` shows them at the CLI, and no MCP
  tool can ask. The record on disk still holds the real value, because a plan that lost
  its code could never be applied.
- Make the redaction an exit boundary rather than one point on the read path. A write's
  result and, more importantly, Bing's own error text now pass through it: if Bing
  quotes the `authenticationCode` it rejected, that message reached the caller, the MCP
  error and the audit trail verbatim. The literals to hide are derived from the request
  body itself, so a write that starts carrying a new secret is covered without anybody
  remembering. A field name is matched with its underscores removed and its case folded,
  so `AuthenticationCode`, `authenticationCode` and `authentication_code` are one secret
  and not three lists to keep in step.
- Stop reporting a single record as silence. The empty-read label judged any empty
  list-valued field structurally, so `bing_crawl_settings` — whose `CrawlRate` is an
  hourly-rate array inside one record — was labelled `empty_response` when Bing had
  answered in full. Each read now declares whether it carries rows, and single-record
  reads are exempt from the structural test entirely. A row read that returns nothing is
  labelled exactly as before. A test refuses a read that declares nothing.
- Redact the verification secrets Bing returns beside a site. `GetUserSites` sends
  `AuthenticationCode` and `DnsVerificationCode` and `GetSiteRoles` sends
  `DelegatedCode`; these are ownership proofs, and listing 74 sites was putting 148 of
  them into a caller's transcript, logs and reports for a read that does not need one.
  They now come back as `[redacted: verification secret]` from every read, in the MCP
  server and the CLI alike, because the redaction sits in the shared fetch helper rather
  than in the two operations known to carry a code today. A field Bing left `null` stays
  `null`. The only way to see one is `bing-wm sites list|show|roles
  --reveal-verification-codes`: no MCP tool takes that argument and unknown tool
  arguments are refused, so no model, and no text a model read, can ask for a code.
- Label a read that returned nothing, so silence cannot be reported as a clean bill of
  health. Several older endpoints answer some accounts empty while another endpoint
  reports plenty for the same site: `bing_link_counts`, `bing_crawl_issues` and
  `bing_fetched_urls` all came back empty for a site whose `bing_crawl_stats` reported
  `InLinks: 1700` and `CrawlErrors: 4` in the same minute. Such a result now carries
  `empty_response: {rows_returned: 0, measured: false, note: …}` beside `result` over
  MCP, and the same note on stderr at the CLI. `result` keeps the shape Bing's response
  had. A single record such as `bing_url_info` or a quota is never called empty — a zero
  in it is a reading, not a silence — and which reads those are is declared per
  operation, see the entry above.
- Say when Bing reports no HTTP status for a URL. `GetUrlInfo` returns `HttpStatus: 0`
  with `IsPage: true` for URLs that answer 404 today, which reads as a healthy page.
  `bing_url_info` and `bing_children_url_info` now add `http_status_reported` to every
  row, with an `http_status_note` when it is false, and keep the raw `HttpStatus`.

- Split a crawl issue Bing flagged `Code4xx` into `http_404` or `http_403` using the
  `HttpCode` on the same row, beside the broad `http_4xx` rather than instead of it, so
  a caller counting 4xx rows keeps counting all of them. Microsoft's flags enum has no
  404 or 403 member, so this comes from the status code Bing itself reported and from
  nothing else: without the `Code4xx` flag, or with any other 4xx code, no refinement is
  added. Bing still reports no `noindex` anywhere in this API, and no category invents
  one.

- Add `SKILL.md`: the task recipes an agent needs to drive this server — onboarding a
  new site, a recurring crawl and traffic audit, when IndexNow replaces `submit_url` —
  together with the rules it must not work around.
- Categorise `bing_crawl_issues` / `bing-wm crawl issues` by Microsoft's
  `UrlWithCrawlIssues.CrawlIssues` flags, with a count per category and per raw
  `HttpCode`. Every field Bing returned is kept; an undocumented flag lands in `other`
  and reports its bits rather than disappearing. The read now returns
  `{total, categories, http_codes, issues}` instead of a bare list.
- Add `bing_indexnow_key_plan` and options on `bing-wm indexnow key`, so generating an
  IndexNow key, working out where its key file belongs and checking whether it is
  already served is available to an agent and not only at the CLI. It sends nothing to
  Bing or IndexNow, needs no API key and records no plan, so it is not a write.
- Refuse an IndexNow key-file check whose host resolves to a non-public address, and
  ignore proxy environment variables on the MCP path. `foo.localhost` and
  `127.0.0.1.nip.io` are well-formed multi-label names, and the check is the one request
  an MCP client can make the server send to a host it named.
- Let a tool declare `openWorldHint` itself, so `bing_indexnow_key_plan` can admit that
  it reaches an arbitrary host while the Bing reads stay closed-world.
- Record `UrlWithCrawlIssues`, `UrlInfo`, `QueryStats` and `RankAndTrafficStats` in
  `docs/api-surface.md` with their Microsoft sources. Bing returns no CTR field and no
  robots-directive or content-changed property; the docs now say so.
- Add `BING_WM_ALLOW_WRITES` (default `true`) to choose the write path. With it on,
  the MCP server advertises one-step `bing_<operation>` tools and the CLI gains
  `bing-wm write`, `submit-url`, `submit-urls`, `submit-sitemap`, `block-url` and
  `indexnow submit`, all of which reach Bing on the first call. Setting it to
  `false` restores the previous behaviour: only `bing_plan_<operation>` tools, and
  a human applies the plan with `bing-wm plan apply`. A one-step write still
  records a plan and goes through the same apply boundary, so the denylist, Bing's
  quota check, the local daily ceiling, one-shot application and the audit trail
  are unchanged. No MCP tool accepts a plan ID in either mode, so none can apply or
  reject a plan recorded for review.
- Refuse a one-step write's plan when the write itself fails, so a plan nobody reviewed
  cannot be applied by hand later, and name the plan in the error so an unknown outcome
  can be traced.
- Report `POLICY_DENIED` rather than `AUTH_FAILED` for a write attempted while
  `BING_WM_ALLOW_WRITES=false` and no API key is set.
- Implement all 59 supported Bing Webmaster Tools API methods.
- Add the CLI and the MCP stdio server with no apply capability over MCP.
- Put all Bing mutations and IndexNow submission behind durable, expiring plans.
- Add auditing, unknown-outcome protection, site policy, and local limits.
- Add protocol-correct IndexNow key verification and batch validation.
- Add an optional bearer-gated, loopback-only Streamable HTTP transport.
- Sanitize and label attacker-influenced response fields.
- Record a plan's outcome even when its TTL elapses while the request is in flight.
- Treat an HTTP 5xx on a mutating request as an unknown outcome instead of a
  retryable failure, so a retry cannot duplicate a write Bing may have processed.
- Match `BING_WM_DENIED_SITES` on the parsed host and re-check it when a plan is
  applied, not only when it is created.
- Show the prepared request body in the `plan apply` confirmation prompt.
- Validate URL ownership inside country-region and site-move payloads.
- Parse the scheme before validating a site URL, and reject hostnames that are not
  hostnames in IndexNow instead of raising a bare `ValueError`.
- Report a non-JSON list variable as `INVALID_REQUEST` rather than a traceback.
- Enforce argument types in the MCP server instead of trusting the client's schema.
- Close SQLite connections in the local write limiter, and reserve the local daily
  ceiling before a write instead of counting it afterwards, releasing the reservation
  only when the request provably never reached Bing.
- Treat a 2xx a mutating call cannot decode as an unknown outcome rather than a
  failure that would leave the plan pending.
- Keep `plan reject` on the expiry check and refuse it while an apply holds the lock.
- Parse host and port through one guarded splitter, so a bad port or bracket is an
  `INVALID_REQUEST` everywhere including IndexNow key locations and URL batches.
- Strip bidi and other format characters from the apply confirmation prompt, and
  show every element of a batch rather than a truncated head.
- Bind a released reservation to the day it was taken on, so a request spanning a UTC
  midnight cannot raise the new day's ceiling.
- Report an out-of-range tick date as a malformed response, and treat any failure to
  read a write's response as an unknown outcome.
- Refuse a denylist entry that names no host, and detect the scheme by parsing so an
  entry like `a.example/shop//eu` still matches.
- Record a cancelled write as an unknown outcome instead of leaving the plan pending.
- Compare host and non-default port, not only the hostname, when checking that a URL
  belongs to the site, and keep the brackets on an IPv6 authority. The scheme stays out
  of it: a legacy `http://` property still submits its live `https://` URLs.
- Keep the IndexNow key-file check out of the unknown-outcome zone, and release the
  reservation if the audit write itself fails.
- Reject a tick date whose timezone minutes are 60 or more.
- Recover locks left by dead apply processes through an audited `bing-wm plan unlock`
  command, marking unfinished plans as unknown instead of making them retryable.
- Compare explicit ports against the default for their own scheme when validating that
  a submitted URL belongs to a site.
- Make the repository test command import the local package in a clean checkout.
- Keep IndexNow 5xx plans retryable because the protocol directs callers to resubmit a
  valid request after a non-success response; no retry is automatic.
- Preserve the apply lock if a terminal plan outcome cannot be written after dispatch,
  and make plan and audit writes resilient to partial operating-system writes.
- Reject invalid hostnames and ambiguous literal or repeatedly encoded dot segments
  before site ownership, denylist, and IndexNow subpath checks.
- Require an exact, bounded IndexNow key-file response without redirects, IP literals,
  single-label hosts, alternate ports, query strings, or fragments.
- Enforce Microsoft's 10 MB uncompressed `SubmitContent` limit across the combined
  decoded content fields rather than independently for each field.
- Return `INVALID_REQUEST` for an unknown MCP tool and run lint, tests, and the MCP smoke
  handshake before Trusted Publishing can upload a tagged release.
