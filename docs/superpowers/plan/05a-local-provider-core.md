# Phase 05a - Local Provider Core

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Add first-class local provider support so provider-backed operations can run without cloud credentials when configured.

## Depends On

- Phase 04 complete.
- Existing provider abstraction, retry policy, ingest/context/explore/compaction flows, and config validation.

## Model and Effort Recommendation

- Recommended model: `gpt-5.5`
- Reasoning effort: `high`
- Why: this milestone touches cross-cutting adapter and config behavior across many command paths and requires regression-safe integration tests.

## Deliverables

- Local provider adapter implementation using the existing provider protocol.
- Config seam for local provider settings.
- Operation route support for local provider across extract/classify/integrate/synthesize/compact.
- Clear local-backend diagnostics when unavailable or misconfigured.
- Focused tests for local provider route behavior.

## Implementation Tasks

1. Implement local provider adapter.
   - Add a provider implementation for one local backend (single backend only in this milestone).
   - Preserve the existing provider protocol and structured response contract.
   - Map backend transport and response errors into existing provider error types where possible.

2. Extend provider config parsing.
   - Add config shape and validation for the local provider section.
   - Keep defaults conservative: local provider opt-in only, no implicit route switching.
   - Ensure malformed local config yields actionable errors.

3. Wire operation routes.
   - Allow each provider-backed operation route to choose local provider by config.
   - Keep CLI command interfaces unchanged.
   - Ensure retry behavior integrates cleanly with local provider errors.

4. Add diagnostics.
   - Add doctor findings for missing/unreachable local backend.
   - Ensure error logs indicate operation, provider, and next action.

5. Add tests.
   - Unit tests for local config validation and adapter error mapping.
   - Mocked local-provider tests for ingest/context/explore/compaction route selection.
   - CLI smoke tests using local-provider configuration fixtures.

## Public Interfaces

- Provider config gains a local provider section.
- Existing operation routes can select local provider values.
- Existing CLI commands remain unchanged.

## Data/State Changes

- Config schema is extended with local provider fields.
- Error diagnostics may include local backend connectivity failures.

## Test Plan

- `tests/test_config.py` additions for local provider shape and validation.
- `tests/test_providers.py` additions for local adapter and route behavior.
- CLI and workflow tests covering local route execution and failure reporting.

## Acceptance Criteria

- All provider-backed operations can be routed to local provider by config.
- No cloud credentials are required when all routes use local provider.
- Local backend failures are reported clearly through CLI and doctor.
- Existing non-local provider behavior remains unchanged unless config switches routes.

## Out of Scope

- Multi-local-backend abstraction.
- Provider fallback chains.
- Embedding retrieval logic.
