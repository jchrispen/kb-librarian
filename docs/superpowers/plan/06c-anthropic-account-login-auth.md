# Phase 06c - Anthropic Account-Login Auth

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Add an explicit Claude Code CLI delegation path for Anthropic routes so users can opt into Claude subscription account access, instead of requiring `ANTHROPIC_API_KEY`.

## Depends On

- Phase 06a complete.
- Existing Anthropic provider behavior from Phase 05.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4` or stronger
- Reasoning effort: `high`
- Why: vendor CLI account auth must be implemented conservatively, with attention to supported flows, secret handling, expiry behavior, and user-facing diagnostics.

## Deliverables

- Anthropic credential-source option for Claude Code CLI delegation.
- Supported setup path using `claude auth login` for local subscription OAuth and `claude setup-token` plus `CLAUDE_CODE_OAUTH_TOKEN` for scriptable subscription auth.
- Provider adapter that calls `claude -p` with structured-output constraints instead of reading Claude Code credential files directly.
- Credential refresh/expiry detection with actionable errors.
- Docs that explain when to use direct API keys vs Claude Code CLI delegation.

## Target Mechanism

- Primary mechanism: invoke the installed `claude` CLI in non-interactive print mode (`claude -p`) and let Claude Code manage subscription OAuth through its supported credential system.
- Local login setup: users run `claude auth login` and complete the browser or code flow supported by Claude Code.
- Scriptable setup: users may generate a long-lived subscription OAuth token with `claude setup-token` and expose it to the `claude` subprocess as `CLAUDE_CODE_OAUTH_TOKEN`.
- Health check: use `claude auth status` when available, then fall back to a guarded live probe only when the user opts in.
- Non-target: parsing `~/.claude/.credentials.json`, reading OS keychains, scraping browser cookies, or sending subscription OAuth tokens directly to the Messages API unless Anthropic documents that as a stable third-party contract.

## Implementation Tasks

1. Implement the supported login path.
   - Require an installed `claude` CLI at a configurable command path.
   - Document `claude auth login` and `claude setup-token` as the supported setup flows.
   - Do not rely on brittle browser cookie scraping or credential-cache parsing.

2. Implement Anthropic CLI delegation.
   - Add a provider adapter that invokes `claude -p` with prompts matching the existing provider protocol.
   - Use structured JSON output where Claude Code supports `--output-format json` and `--json-schema`.
   - Disable model-initiated tools or run with an explicit no-tool posture for pure completion behavior.
   - Keep API-key auth as the default and safest path unless users explicitly opt in.

3. Handle expiry and missing state.
   - Detect missing `claude` binary, missing login state, expired login state, and unsupported CLI versions distinctly.
   - Surface remediation steps that match the supported login flow.

4. Add diagnostics and docs.
   - Extend `kb doctor` for Anthropic CLI delegation checks.
   - Update user docs with setup, limitations, and security expectations.

5. Add tests.
   - Add unit tests for delegation setup and failure classification.
   - Add guarded smoke coverage for the Claude Code CLI path if it can be tested safely and reproducibly.

## Public Interfaces

- Anthropic provider config gains an opt-in `vendor_cli` credential-source mode for Claude Code CLI delegation.
- Existing Anthropic route names and CLI commands remain unchanged.

## Data/State Changes

- Config schema expands with Anthropic auth-source options.
- Diagnostics may report login-state health without revealing credential contents.

## Test Plan

- `tests/test_config.py` for Anthropic auth-source validation.
- `tests/test_providers.py` for Claude Code CLI delegation and subprocess error handling.
- `tests/test_doctor.py` for missing/expired login findings.

## Acceptance Criteria

- Users can explicitly route Anthropic-backed commands through Claude Code CLI delegation.
- Existing `ANTHROPIC_API_KEY` flows continue to work unchanged.
- Claude Code CLI delegation failures are differentiated from transport or model failures.

## Out of Scope

- Unsupported unofficial browser-session scraping.
- Direct parsing of Claude Code credential stores.
- Direct Messages API calls using Claude subscription OAuth tokens unless Anthropic documents that as stable for third-party clients.
- Silent fallback from failed login auth to some other credential source unless explicitly configured.
