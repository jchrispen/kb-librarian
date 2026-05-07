# Phase 05b - Codex Provider Support

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Add a Codex-compatible cloud provider adapter so users can route provider-backed operations to it through config only.

## Depends On

- Phase 05a complete.
- Existing provider abstraction and retry policy.

## Model and Effort Recommendation

- Recommended model: `gpt-5.5`
- Reasoning effort: `high`
- Why: provider API integration and error normalization are high-risk for regressions without strong cross-file reasoning and tests.

## Deliverables

- Codex-compatible provider adapter implementation.
- Config seam for codex provider credentials/settings.
- Operation route support for codex provider across extract/classify/integrate/synthesize/compact.
- Error mapping and retry integration for codex provider responses.
- Focused tests for codex route behavior.

## Implementation Tasks

1. Implement codex provider adapter.
   - Add an adapter that satisfies the existing provider protocol.
   - Normalize response parsing for all provider-backed operations.
   - Map auth/rate-limit/transient/server failures into current retry/error pathways.

2. Extend provider config validation.
   - Add required codex provider settings and credential references.
   - Ensure missing or invalid credential settings fail fast with actionable errors.
   - Preserve existing provider config behavior for current adapters.

3. Wire operation routes.
   - Allow each provider-backed operation to target codex provider via config.
   - Keep command-line interfaces and payload surfaces stable.

4. Add diagnostics.
   - Add doctor checks for codex credential presence and route misconfiguration.
   - Ensure CLI errors identify provider name, failing operation, and fix path.

5. Add tests.
   - Unit tests for codex config validation and adapter error handling.
   - Mocked provider tests for route behavior across ingest/context/explore/compaction.
   - CLI smoke coverage for codex-routed operations and failure surfaces.

## Public Interfaces

- Provider config gains codex provider section.
- Existing operation route config supports provider=`codex`.
- CLI command contract remains unchanged.

## Data/State Changes

- Config schema and docs updated for codex settings.
- Error logs may include codex-specific diagnostics.

## Test Plan

- Extend config/provider tests for codex setup and failures.
- Extend CLI/flow tests to verify route switching and retry behavior under codex routing.

## Acceptance Criteria

- Provider-backed operations can all run via codex provider by config.
- Credentials/config failures are explicit and actionable.
- Retry/error behavior is consistent with existing provider resilience semantics.
- Existing local/default provider behavior does not regress.

## Out of Scope

- Provider fallback policy.
- Embedding retrieval.
- MCP wrapper.
