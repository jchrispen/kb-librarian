---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Account Auth Delegation: Subprocess Delegation, No Credential Parsing

## Context

Users with Anthropic or OpenAI subscriptions can access model capacity without managing API keys if the vendor's CLI is installed and logged in. kb-librarian needed a way to use this capacity without storing, parsing, or transmitting vendor credentials itself.

## Decision

For Anthropic: delegate via `claude -p` (Claude Code CLI subprocess), authenticated through `claude auth login` OAuth or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. For Codex/OpenAI: delegate via `codex exec`, authenticated through `codex login` browser OAuth or `codex login --device-auth` device-code flow. In both cases, kb-librarian never parses credential-cache files, OS keyrings, or browser cookies. Delegation always runs completion-only (no model-initiated tool calls), with a bounded timeout and an explicit working directory. API-key auth remains the default when vendor CLIs are not configured.

## Alternatives considered

- **Parse `~/.claude/.credentials.json` or `~/.codex/auth.json` directly** — rejected: these are internal cache files with no stability contract; format changes would silently break auth.
- **Embedded OAuth web server** — rejected: adds port management and browser redirect handling that is out of scope for a CLI tool.
- **Browser session scraping** — rejected: fragile, security-sensitive, no stable interface.
- **Direct Messages API calls using subscription OAuth tokens** — rejected: Anthropic has not published a stable third-party contract for using account OAuth tokens against the Messages API.

## Consequences

- Auth can fail at the subprocess level for reasons not visible to kb-librarian (expired login, wrong CLI version, missing binary).
- `kb doctor` must distinguish at least four auth failure modes as separate findings: missing binary, missing login state, expired login state, and transport errors. These require different remediation steps and must not be collapsed into a generic error.
- Subprocess argv and env must be audited separately from log output — token values can appear in either and require targeted masking by `privacy.py`.
- Silent fallback from expired vendor CLI login to API key is forbidden unless explicitly configured by the user.
