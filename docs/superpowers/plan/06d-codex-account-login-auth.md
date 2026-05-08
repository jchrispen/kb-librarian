# Phase 06d - Codex Account-Login Auth

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Add an explicit Codex CLI delegation path for Codex routes so users can opt into ChatGPT account access, instead of requiring `OPENAI_API_KEY`.

## Depends On

- Phase 06a complete.
- Existing Codex-compatible provider behavior from Phase 05b.
- Preferably Phase 06c complete so vendor CLI delegation patterns are proven on one cloud provider first.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4` or stronger
- Reasoning effort: `high`
- Why: Codex-compatible routing already supports alternate base URLs, so account-login auth must integrate without making precedence or endpoint behavior ambiguous.

## Deliverables

- Codex credential-source option for Codex CLI delegation.
- Supported setup path using `codex login` browser OAuth or `codex login --device-auth` for headless flows.
- Provider adapter that calls `codex exec` with structured-output constraints instead of reading Codex credential files directly.
- Clear interaction rules between `base_url`, direct API-key auth, and Codex CLI delegation.
- Docs that explain supported and unsupported endpoint/auth combinations.

## Target Mechanism

- Primary mechanism: invoke the installed `codex` CLI in non-interactive mode (`codex exec`) and let Codex manage ChatGPT OAuth through its supported credential system.
- Local login setup: users run `codex login` and choose ChatGPT sign-in.
- Headless setup: users run `codex login --device-auth` when device-code auth is enabled, or seed `~/.codex/auth.json` through a trusted secret channel only as documented by Codex for headless/CI use.
- Health check: prefer non-inference Codex diagnostics if available; otherwise check installed CLI/cache shape and offer an explicit opt-in live probe.
- Non-target: parsing `~/.codex/auth.json`, reading OS keyrings, scraping browser cookies, or sending ChatGPT access tokens directly to the Responses API unless OpenAI documents that as a stable third-party contract.

## Implementation Tasks

1. Implement the supported login path.
   - Require an installed `codex` CLI at a configurable command path.
   - Document `codex login` and `codex login --device-auth` as the supported setup flows.
   - Avoid undocumented browser-session scraping or credential-cache parsing.

2. Implement Codex CLI delegation.
   - Add a provider adapter that invokes `codex exec` with prompts matching the existing provider protocol.
   - Use `--output-schema` for structured outputs where possible.
   - Run with read-only/no-approval settings suitable for pure completion behavior and avoid giving Codex file-editing authority.
   - Keep current bearer-token API-key behavior as the default direct HTTP path.

3. Define precedence and compatibility rules.
   - Treat ChatGPT account delegation as valid only for Codex CLI's built-in OpenAI/Codex account path.
   - Keep arbitrary `base_url` compatible endpoints on API-key/direct HTTP auth unless Codex documents account-auth forwarding for that endpoint type.
   - Keep config validation and doctor findings explicit.

4. Add diagnostics and docs.
   - Extend `kb doctor` with Codex CLI delegation checks.
   - Update docs for setup, limitations, and troubleshooting.

5. Add tests.
   - Add credential-resolution and endpoint-compatibility tests.
   - Add guarded smoke coverage only where credentials can be tested safely.

## Public Interfaces

- Codex provider config gains an opt-in `vendor_cli` credential-source mode for Codex CLI delegation.
- Existing route names and CLI commands remain unchanged.

## Data/State Changes

- Config schema expands with Codex auth-source options.
- Diagnostics report auth-source and endpoint compatibility status in non-secret form.

## Test Plan

- `tests/test_config.py` for Codex auth-source and endpoint compatibility validation.
- `tests/test_providers.py` for Codex CLI delegation and subprocess error handling.
- `tests/test_doctor.py` for missing/expired/unsupported-combination findings.

## Acceptance Criteria

- Users can explicitly choose Codex CLI delegation for ChatGPT account-backed Codex routes.
- Existing `OPENAI_API_KEY` flows and compatible `base_url` flows continue to work unchanged.
- Unsupported auth-and-endpoint combinations fail early and clearly.

## Out of Scope

- Automatic translation of ChatGPT account auth into arbitrary third-party OpenAI-compatible endpoints.
- Direct parsing of Codex credential stores.
- Direct Responses API calls using ChatGPT access tokens unless OpenAI documents that as stable for third-party clients.
- Hidden fallback across unrelated credential sources.
