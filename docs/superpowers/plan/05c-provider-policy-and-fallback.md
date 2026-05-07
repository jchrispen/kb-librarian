# Phase 05c - Provider Policy and Fallback

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Pending

## Goal

Add an explicit, deterministic provider-selection policy with bounded fallback behavior and clear diagnostics.

## Depends On

- Phase 05a and 05b complete.
- Existing provider route and retry foundations.

## Model and Effort Recommendation

- Recommended model: `gpt-5.5`
- Reasoning effort: `xhigh`
- Why: provider-selection and fallback semantics become control-plane logic across all provider-backed operations and require careful invariants.

## Deliverables

- Provider policy config for default provider and per-operation overrides.
- Optional bounded fallback chain configuration.
- Deterministic provider selection evaluator used by all provider-backed operations.
- Doctor and CLI diagnostics for invalid/unsafe policy configuration.
- Focused tests for policy and fallback semantics.

## Implementation Tasks

1. Define provider policy schema.
   - Add config keys for default provider and per-operation override behavior.
   - Add explicit fallback list format with bounded attempt semantics.
   - Reject cycles, duplicate consecutive providers, and invalid provider references.

2. Implement selection evaluator.
   - Resolve provider in this order: operation override, global default, existing route fallback behavior if configured.
   - Apply fallback chain only for eligible failure classes.
   - Keep policy transparent: no hidden implicit provider switching.

3. Integrate with retry and execution.
   - Ensure fallback decisions compose with existing retry policy without runaway loops.
   - Preserve operation IDs and error attribution per attempted provider.
   - Keep recovery semantics for ingest unchanged.

4. Add diagnostics.
   - Doctor checks for invalid policy graphs and unreachable provider configurations.
   - Error output includes attempted provider sequence and stop reason.

5. Add tests.
   - Unit tests for policy parsing/validation and selection outcomes.
   - Integration tests for fallback-triggering and non-triggering error classes.
   - CLI smoke tests for explicit policy behavior and doctor findings.

## Public Interfaces

- Config gains provider policy and fallback sections.
- Existing CLI commands unchanged; behavior controlled by config.

## Data/State Changes

- Config schema expansion for policy/fallback.
- Error logs include provider attempt traces for policy-controlled execution.

## Test Plan

- Extend config/provider/retry tests with policy and fallback matrices.
- Add doctor tests for invalid policy shape and unresolved provider names.

## Acceptance Criteria

- Provider selection is deterministic and explainable from config.
- Fallback behavior is bounded, explicit, and test-covered.
- Failure reporting includes attempted providers and terminal reason.
- Existing workflows remain stable when policy is not configured.

## Out of Scope

- Dynamic runtime scoring of providers.
- Cost-based automatic provider optimization.
- Embedding retrieval implementation.
