# Owner-only launch tasks

The implementation is complete in the checkout. These steps require the repository
owner's external accounts and are intentionally not automated from a developer machine.

## Recently completed

- [x] 2026-08-30 — `BING_WM_ALLOW_WRITES` write-path switch shipped; the local
  `bing-webmaster-mcp:local` image (pre-rename tag) is rebuilt and serves the direct write tools to the
  Claude and Codex MCP entries. Each client picks them up on its next restart.

## Open follow-up

- [ ] Idempotency key for one-step writes. Raised in the 2026-08-30 Codex review: with
  `BING_WM_ALLOW_WRITES=true`, a retry after a lost response records a new plan and
  sends the write again, because the one-shot guarantee is per plan. A client-supplied
  key with a durable mapping to a plan and its result would close it. Deliberately
  deferred and recorded in `docs/product-boundaries.md`.
- [ ] OAuth2 would take the credential out of the URL. Checked on 2026-09-02: Microsoft
  documents no header form for the API key — every example on
  [Getting started](https://learn.microsoft.com/en-us/bingwebmaster/getting-started) puts
  it in the query string, and the one documented header, `Authorization: Bearer` on
  [the OAuth2 page](https://learn.microsoft.com/en-us/bingwebmaster/oauth2), carries an
  OAuth2 access token rather than a key. So the key stays in the query string and is
  redacted at the exit boundary instead. Implementing OAuth2 — already designed for
  behind `AuthProvider` — would remove the exposure at the source rather than covering
  it. Open question for the owner: is that flow's client registration and user consent
  worth it for a single-operator install?

## First PyPI release

- [ ] On PyPI, register a pending Trusted Publisher for project
  `bing-webmaster-ai-cli-mcp`: owner `stufently`, repository `bing-webmaster-ai-cli-mcp`, workflow
  `release.yml`, environment `pypi`.
- [ ] In GitHub, create the protected deployment environment `pypi`.
- [ ] Confirm the version in `pyproject.toml`, commit it, and push the matching `v*`
  tag. The release workflow rejects a mismatched tag.
- [ ] From a clean machine, run
  `docker run --rm python:3.14-slim sh -c "pip install bing-webmaster-ai-cli-mcp && bing-wm --help"`.

## Repository and discovery

- [ ] Set the GitHub description to the exact pitch used in README, `pyproject.toml`,
  and `llms.txt`.
- [ ] Review the GitHub topics listed in the implementation plan.
- [ ] Upload the prepared `docs/assets/social-preview.png` under Settings → General →
  Social preview.
  ⚠️ The image still reads `bing-webmaster-mcp` (drawn before the 2026-09-18 rename);
  regenerate it with the new name before uploading.
- [ ] Publish the server to `registry.modelcontextprotocol.io` with `mcp-publisher`
  after the PyPI release, using the same verified GitHub identity.
- [ ] Submit the released server to the community catalogues listed in the launch plan.

## Tool-dependent follow-up

- [ ] Re-run the Antigravity review over `0700dc5..7e5595f` when that reviewer is
  available. The 2026-08-25 Codex re-review found and fixed scheme-specific default-port
  validation; Antigravity is not available in the current environment.
