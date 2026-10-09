# Security

## Reporting a vulnerability

Please report it privately, not in a public issue. Open the
[Security tab](https://github.com/stufently/bing-webmaster-ai-cli-mcp/security) of this
repository and use **Report a vulnerability**
([direct link](https://github.com/stufently/bing-webmaster-ai-cli-mcp/security/advisories/new)).
If that button is not available, open an issue asking for a private channel — without any
details of the problem.

## What the tool touches

- **`BING_WM_API_KEY`** is read from the environment and sent only to the Bing Webmaster JSON
  API (`https://ssl.bing.com/webmaster/api.svc/json`, or `BING_WM_BASE_URL`). Microsoft documents
  the key only as a query-string parameter, so it travels as `apikey=` in every request URL.
  The key is replaced with `[redacted: API credential]` in errors, output and the audit trail,
  and is never written to plans or audit entries.
- **IndexNow** submissions go to `api.indexnow.org`. Before sending a batch the tool fetches the
  key file on your own site, without following redirects. `bing-wm indexnow key` and the
  `bing_indexnow_key_plan` tool talk to neither Bing nor IndexNow; a generated key is printed
  once and stored nowhere.
- **Local state** lives in `BING_WM_STATE_DIR` (default
  `~/.local/state/bing-webmaster-ai-cli-mcp`): plans, the append-only `audit.jsonl` and local
  counters. The `plans/` subdirectory is kept at `0700` and the plan, audit and counter files
  are created `0600`; the state directory itself may be created with your umask, so tighten
  it if other users share the machine. A plan on disk keeps the real
  arguments it will send — including an `authentication_code` for `add_site_roles` — so treat
  the directory as sensitive.
- **Site verification secrets** (`AuthenticationCode`, `DnsVerificationCode`, `DelegatedCode`)
  are redacted in every response. Only an operator can reveal them, with
  `--reveal-verification-codes` on the CLI; no MCP tool takes that flag.
- **Strings written by strangers** — anchor text, crawl-issue URLs, titles, queries — come back
  as `{"value": "…", "untrusted": true}` with control and bidirectional characters stripped.

See [Output safety](README.md#output-safety) and [configuration](docs/configuration.md) for
details.

## Least privilege

- **`BING_WM_ALLOW_WRITES=false`** if the agent also reads text from the web. The MCP server then
  offers only `bing_plan_<operation>` tools, which change nothing at Bing (planning a URL or
  content submission still reads its quota from Bing); a change reaches Bing only when you run
  `bing-wm plan apply` yourself. No MCP tool can apply or reject a plan in either mode.
- **`BING_WM_DENIED_SITES`** lists sites that may never be changed; it is checked again when a
  plan is applied.
- **`BING_WM_MAX_WRITES_PER_DAY`** sets a local ceiling on writes that survives restarts.
- Pass the key through the client's environment settings, never in a committed file.

## HTTP transport

`bing-webmaster-ai-cli-mcp-http` binds only to loopback (default `127.0.0.1:8765`), refuses
non-loopback addresses, and requires `BING_WM_HTTP_BEARER_TOKEN` (at least 32 characters) on
every request.
