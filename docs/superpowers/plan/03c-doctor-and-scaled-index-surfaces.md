# Phase 03c - Doctor and Scaled Index Surfaces

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make artifact health inspectable and keep published indexes usable as the KB grows. At the end of this milestone, `kb doctor` can detect broken or stale local state, and large top-level or topic indexes paginate deterministically without affecting retrieval behavior.

## Depends On

- Phases 01 and 02 must be complete.
- Phase 03b may enrich doctor findings, but `kb doctor` should still be useful if hygiene queues are not fully implemented yet.
- Reuse config validation, note validation, index generation, and mock-provider infrastructure from earlier milestones.

## Deliverables

- `kb doctor [--self-test]` with severity-based health checks.
- Deterministic pagination for large `INDEX.md` and topic indexes.
- Clear separation between human-facing paginated indexes and retrieval/indexing internals.

## Implementation Tasks

1. Implement `kb doctor` health checks.
   - Check required directories, config validity, note schema validity, duplicate IDs, broken note links, missing topic indexes, FTS freshness, stale generated artifacts, unreadable review state, raw ingest errors, and provider config presence.
   - Print severities `ok`, `warn`, and `error`.
   - Return nonzero when errors are found.

2. Add `kb doctor --self-test`.
   - Run a tiny deterministic local fixture using the mock provider and a temporary data directory.
   - Keep the self-test fast and offline.
   - Report whether core flows needed for local confidence are functioning.

3. Harden doctor output.
   - Group findings by subsystem so failures are easy to scan.
   - Keep messages actionable and explicit about the affected files or artifacts.
   - Avoid noisy warnings for generated artifacts that can be trivially rebuilt unless they affect trust or retrieval.

4. Add INDEX pagination.
   - Keep top-level `INDEX.md` thin and deterministic.
   - Paginate topic indexes when note count exceeds a configured threshold.
   - Include page navigation links, stable sort order, and predictable page boundaries.

5. Preserve retrieval independence from markdown indexes.
   - Ensure `kb search`, `kb context`, and `kb explore` do not depend on reading paginated markdown indexes.
   - Keep pagination a published-artifact readability feature, not a retrieval dependency.

6. Add tests.
   - Unit-test doctor finding generation, exit-code behavior, self-test result shape, pagination thresholds, navigation rendering, and deterministic ordering.
   - CLI smoke-test `kb doctor` and pagination behavior in temporary KB directories.

## Public Interfaces

- `kb doctor [--self-test]`
- `kb reindex [--scan-clusters] [--all]`

Published markdown indexes also change shape once pagination activates.

## Data/State Changes

- Regenerates top-level and topic `INDEX.md` outputs with pagination when needed.
- May add page files or page-linked generated artifacts beneath topic directories if the implementation needs them.
- Reads and validates `.kb/` state, review state, and note files; it should not mutate canonical notes.

## Test Plan

- Unit tests for doctor findings and index pagination.
- CLI smoke tests in temporary KB directories.
- Manual check: create a KB with deliberate validation issues and a large topic, then confirm `kb doctor` and paginated indexes behave as intended.

## Acceptance Criteria

- `kb doctor` catches broken or stale artifact state and returns nonzero for errors.
- `kb doctor --self-test` runs offline and verifies a minimal deterministic local path.
- Large topic indexes paginate deterministically with usable navigation.
- Retrieval commands remain independent of paginated markdown indexes.

## Out of Scope

- Topic hierarchy and reorganization commands.
- Applying compaction or hygiene review actions.
- Auto-repair behavior beyond any explicit regeneration already owned by existing commands.
