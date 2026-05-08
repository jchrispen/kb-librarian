# Phase 06a - Provider Backend and Credential Source Seams

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Introduce minimal backend, credential-source, and delegation seams so additional local providers and vendor-CLI-backed account auth can be added without duplicating route logic or scattering auth policy across adapters.

## Depends On

- Phase 05 complete.
- Existing provider routing, retry policy, diagnostics, and privacy controls.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4` or stronger
- Reasoning effort: `high`
- Why: this milestone changes central provider construction and config validation, so a small design mistake would ripple across all provider-backed commands.

## Deliverables

- Config/schema support for explicit credential-source selection per provider.
- A provider execution mode distinction between direct HTTP adapters and vendor CLI delegation adapters.
- Provider-construction seam that separates transport backend from credential acquisition.
- Backward-compatible defaults that preserve current API-key behavior when new config is absent.
- Shared diagnostic helpers for reporting active backend and active credential source.

## Target Credential-Source Vocabulary

- `api_key_env`: current direct HTTP behavior using an environment variable such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
- `vendor_cli`: account-auth delegation through a supported installed CLI, initially `claude` for Anthropic and `codex` for Codex.
- `token_env`: explicit token environment variable consumed by a vendor CLI when documented, initially `CLAUDE_CODE_OAUTH_TOKEN` for Claude Code CLI delegation.
- `command`: future-safe helper that prints a bearer token to stdout for proxy/gateway use, modeled after existing vendor helper patterns but not used for subscription account caches unless officially supported.

## Implementation Tasks

1. Define credential-source vocabulary.
   - Add a constrained config shape for provider credential sources such as `api_key_env`, `vendor_cli`, `token_env`, and `command`.
   - Keep defaults identical to current behavior when users do not opt in.
   - Reject ambiguous or conflicting auth configuration with actionable errors.

2. Define backend vocabulary.
   - Separate provider kind from backend transport where needed so local backends can evolve beyond one hard-coded adapter.
   - Preserve existing route names unless there is a concrete migration need.

3. Refactor provider construction.
   - Centralize credential resolution and provider instantiation.
   - Add a safe adapter boundary for providers that are subprocess delegates rather than direct HTTP clients.
   - Ensure retry, privacy gating, and fallback logic still operate on the same provider protocol.

4. Add diagnostics.
   - Extend doctor output to show configured backend kind and credential-source kind without revealing secrets.
   - Ensure error messages indicate whether the failure is transport, configuration, or credential acquisition.

5. Add tests.
   - Add config, provider, and doctor coverage for default compatibility and new validation paths.

## Public Interfaces

- Provider config gains explicit credential-source fields or sub-mappings.
- Provider config can identify direct HTTP versus vendor CLI delegation without changing operation route names.
- Existing operation route names remain stable.
- CLI command interfaces remain unchanged.

## Data/State Changes

- Config schema expands to describe backend and credential-source selection.
- Diagnostics may report backend/auth-source metadata in non-secret form.

## Test Plan

- `tests/test_config.py` for credential-source and backend validation.
- `tests/test_providers.py` for provider-construction and precedence behavior.
- `tests/test_doctor.py` for backend/auth-source reporting and failure classification.

## Acceptance Criteria

- Existing Anthropic, Codex, and local Ollama configurations continue to work unchanged.
- New backend/auth seams exist without requiring any Phase 6 backend to be implemented yet.
- Misconfigured backend/auth combinations fail early with clear guidance.
- Vendor CLI delegation is represented as an explicit execution mode, not as hidden API-key emulation.

## Out of Scope

- Shipping new local backends.
- Shipping vendor CLI account-auth flows.
