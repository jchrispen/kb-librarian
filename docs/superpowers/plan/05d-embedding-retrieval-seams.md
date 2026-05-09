# Phase 05d - Embedding Retrieval Seams

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-07

## Goal

Prepare retrieval and indexing interfaces for future embedding-based retrieval without enabling production embedding retrieval in this milestone.

## Depends On

- Phase 05c complete.
- Existing lexical indexing and retrieval command behavior.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4`
- Reasoning effort: `medium`
- Why: this is interface and config seam work with constrained behavior change and lower algorithmic risk.

## Deliverables

- Config validation and docs for embedding-related toggles/paths/settings.
- Retrieval/index abstraction seams to host future embedding index and hybrid ranking.
- Doctor/reporting surface that reflects embedding seam status.
- Focused non-regression tests ensuring lexical behavior remains the default.

## Implementation Tasks

1. Add config seam hardening.
   - Validate embedding-related config keys and types.
   - Keep embedding retrieval disabled by default.
   - Ensure misconfiguration fails with actionable messages.

2. Add retrieval/index interfaces.
   - Introduce narrow interfaces for candidate sources/ranker composition.
   - Keep lexical index as the active implementation.
   - Ensure no CLI behavior change unless future embedding implementation is added.

3. Add doctor/reporting visibility.
   - Report whether embedding seam is configured/enabled/unsupported in current build.
   - Keep diagnostics explicit that embeddings are deferred.

4. Add tests.
   - Unit tests for config seam validation and interface wiring.
   - Retrieval non-regression tests proving lexical behavior remains unchanged.

## Public Interfaces

- Config docs and validation updated for embedding seam keys.
- No new required CLI flags.

## Data/State Changes

- Optional config keys for future embedding index settings.
- No required new index artifact in this milestone.

## Test Plan

- Extend `tests/test_config.py` and retrieval/index tests for seam behavior.
- Run full suite to ensure no retrieval regressions.

## Acceptance Criteria

- Embedding-related config is validated and documented.
- Retrieval code has stable seams for future embedding integration.
- Lexical retrieval remains the default and regression-free.
- Doctor clearly reports seam status without implying full embedding support.

## Out of Scope

- Embedding generation.
- Vector index build/query implementation.
- Hybrid scoring rollout.

## Completion Record

Completed on 2026-05-07.

Implemented files:

- `src/kb_librarian/config.py`
- `src/kb_librarian/retrieval.py`
- `src/kb_librarian/context.py`
- `src/kb_librarian/cli.py`
- `src/kb_librarian/doctor.py`
- `docs/configuration.md`
- `tests/test_config.py`
- `tests/test_indexing.py`
- `tests/test_doctor.py`

Verification:

- `pytest tests/test_config.py tests/test_indexing.py tests/test_context.py tests/test_doctor.py` - 50 passed
- `pytest` - 193 passed, 3 skipped
