# Phase 05 - Provider Expansion and Local-First Operation

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Expand provider support beyond the current default routes, with first-class local-provider operation and explicit Codex-compatible provider support. Keep behavior predictable through conservative provider selection policy and clear diagnostics.

Phase 5 assumes Phases 1-4 are complete.

## Milestone Status

| Milestone | Status | Notes |
|---|---|---|
| [05a - Local Provider Core](05a-local-provider-core.md) | Pending | Add a local provider adapter and route all provider-backed operations through it when configured. |
| [05b - Codex Provider Support](05b-codex-provider-support.md) | Pending | Add Codex-compatible provider adapter and operation routing support without CLI contract changes. |
| [05c - Provider Policy and Fallback](05c-provider-policy-and-fallback.md) | Pending | Add explicit provider selection policy and bounded fallback behavior with clear diagnostics. |
| [05d - Embedding Retrieval Seams](05d-embedding-retrieval-seams.md) | Pending | Add retrieval/config/index seams to enable future embedding retrieval without shipping it yet. |

## Milestone Order

1. [05a - Local Provider Core](05a-local-provider-core.md)
   - Priority: local-first execution for ingest/retrieval/compaction operations.
2. [05b - Codex Provider Support](05b-codex-provider-support.md)
   - Priority: cloud-provider expansion to Codex-compatible routes.
3. [05c - Provider Policy and Fallback](05c-provider-policy-and-fallback.md)
   - Priority: deterministic provider selection, fallback behavior, and doctor coverage.
4. [05d - Embedding Retrieval Seams](05d-embedding-retrieval-seams.md)
   - Priority: forward-compatibility seams for later embedding retrieval work.

Each milestone is a standalone, one-agent executable increment and should keep the CLI runnable with focused tests.

## Phase Deliverables

- Local-provider adapter support integrated with existing provider protocol.
- Codex-compatible provider adapter support integrated with existing provider protocol.
- Config and validation updates for multi-provider routing and conservative fallback policy.
- Diagnostic coverage in `kb doctor` for provider misconfiguration and unavailable local backends.
- Retrieval/config/index seams for deferred embedding retrieval work (no production embedding retrieval yet).

## Final Acceptance Criteria

- Users can run provider-backed flows on a local provider through config-only changes.
- Users can run provider-backed flows on a Codex-compatible provider through config-only changes.
- Provider selection is deterministic, explicit, and observable in error/reporting surfaces.
- Fallback behavior is bounded and does not silently mask configuration problems.
- Embedding-retrieval prep introduces no regression to existing lexical retrieval behavior.

## Phase Test Strategy

- Unit tests for provider config parsing, route selection, fallback semantics, and error classification.
- Mock-provider tests for ingest/context/explore/compaction behavior across local and codex routes.
- CLI smoke tests that switch provider routes via config and verify expected outcomes.
- Doctor tests that verify provider diagnostics for missing credentials, unavailable local backends, and invalid fallback configs.

## Out of Scope

- MCP wrapper work (explicitly deferred).
- TUI/web review interface.
- Full embedding retrieval implementation.
- Expanded privacy/redaction tooling.
- Multi-machine conflict helpers.
