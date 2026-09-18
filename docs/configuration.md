# Configuration

Settings come from environment variables. Secrets use `SecretStr` and are never written
to plans or audit entries.

| Variable | Default | Effect |
|---|---|---|
| `BING_WM_API_KEY` | required for Bing calls | API key; not required for IndexNow-only planning |
| `BING_WM_BASE_URL` | `https://ssl.bing.com/webmaster/api.svc/json` | JSON API base |
| `BING_WM_CALLS_PER_SECOND` | `5` | local throttle, not a claimed Bing quota |
| `BING_WM_MAX_ATTEMPTS` | `3` | attempts for retryable safe reads; writes never auto-retry |
| `BING_WM_PLAN_TTL_SECONDS` | `900` | seconds before a plan expires |
| `BING_WM_STATE_DIR` | `~/.local/state/bing-webmaster-ai-cli-mcp` (the pre-rename `~/.local/state/bing-webmaster-mcp` is used while only it exists) | plans, audit log, and local counters |
| `BING_WM_ALLOW_WRITES` | `true` | `false` removes every one-step write and leaves only the reviewed plan path |
| `BING_WM_DENIED_SITES` | `[]` | JSON list of sites that may never be mutated |
| `BING_WM_MAX_WRITES_PER_DAY` | unset | optional operator local ceiling |
| `BING_WM_HTTP_HOST` | `127.0.0.1` | literal loopback bind address |
| `BING_WM_HTTP_PORT` | `8765` | optional HTTP port |
| `BING_WM_HTTP_BEARER_TOKEN` | unset | required for HTTP; at least 32 characters |

`BING_WM_ALLOW_WRITES` is the switch between the two write paths. On, the MCP server
advertises one-step `bing_<operation>` tools and the CLI accepts `bing-wm write` and its
shortcuts; the change reaches Bing on the first call. Off, the server advertises
`bing_plan_<operation>` instead, which sends nothing, and the change reaches Bing only
when a human runs `bing-wm plan apply`. Set it to `false` if an agent using this server
also reads pages, anchor text, or crawl issues written by strangers: a confirmation an
agent can send is a confirmation prompt injection can send. Everything else — the
denylist, Bing's own quota check, `BING_WM_MAX_WRITES_PER_DAY`, one-shot application and
the audit trail — applies to both paths.

Denylist matching is on the parsed host, so `http://`, an explicit `:443`, a trailing
dot, and a change of case all match the same entry. Entries may be written as a URL or
as a bare hostname. Matching is exact per host: a denied `example.com` does not cover
`www.example.com`, which Bing treats as a separate site. An entry without a path denies
every path on that host; an entry with a path denies that path and everything under it.
The denylist is re-checked when a plan is applied, not only when it is created.
Ambiguous paths containing literal or percent-encoded dot segments are invalid rather
than being compared as raw strings, so `/shop/../admin` cannot bypass an `/admin` entry.

A variable that holds a list must be JSON, for example
`BING_WM_DENIED_SITES='["https://locked.example"]'`. A bare value is reported as an
`INVALID_REQUEST` naming the field rather than as a traceback.
