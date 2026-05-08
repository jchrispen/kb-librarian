# Phase 03d - Hierarchical Topics and Tree Views

Status: Complete
Completed: 2026-05-06

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Support nested topic organization without breaking existing retrieval behavior. At the end of this milestone, the KB can store slash-delimited topics in nested directories, generate hierarchy-aware topic artifacts, and display the structure through `kb topics --tree`.

## Depends On

- Phases 01 and 02 must be complete.
- Reuse note storage, topic index generation, path normalization, and retrieval code from earlier milestones.

## Deliverables

- Slash-delimited hierarchical topic support.
- Nested `topics/` storage and generated topic artifacts.
- `kb topics --tree` with hierarchy-aware counts.
- Compatibility with existing flat topics.

## Implementation Tasks

1. Add hierarchical topic parsing and storage.
   - Support slash-delimited topics such as `agent-systems/retrieval`.
   - Store nested topic notes under matching nested directories beneath `topics/`.
   - Preserve existing flat topics as top-level topics.

2. Generate hierarchy-aware topic artifacts.
   - Create `scope.txt` and `INDEX.md` for each topic directory when missing.
   - Keep nested topic index generation deterministic.
   - Ensure parent and child topics remain inspectable as files, not just as internal metadata.

3. Preserve retrieval compatibility.
   - Update note discovery, indexing, and retrieval to handle nested topics.
   - Keep existing retrieval commands working for both flat and nested topics.
   - Avoid changing note IDs solely because a topic is nested.

4. Implement `kb topics --tree`.
   - Show hierarchy, note counts, and stale, orphan, or review counts when that data is available.
   - Keep output concise and readable for both shallow and deep hierarchies.
   - Preserve a non-tree topics view if the existing CLI already exposes one.

5. Add migration-safe behavior.
   - Ensure existing flat-topic KBs continue to work unchanged.
   - Do not move notes automatically just because hierarchical topic support now exists.
   - Keep path normalization and conflict detection explicit.

6. Add tests.
   - Unit-test hierarchical topic parsing, nested path normalization, index generation, and tree rendering.
   - CLI smoke-test `kb topics --tree` and retrieval commands against mixed flat and nested topics.

## Public Interfaces

- `kb topics [--tree]`
- Existing retrieval and indexing commands, now with hierarchical topic support

## Data/State Changes

- Supports nested directories under `topics/`.
- Generates `scope.txt` and `INDEX.md` files for nested topic directories.
- Updates note discovery and generated index state to understand hierarchy.
- Does not reorganize existing topics automatically.

## Test Plan

- Unit tests for hierarchy-aware storage and rendering.
- CLI smoke tests using temporary KB directories with flat and nested topics.
- Manual check: add or ingest notes into nested topics, run `kb reindex`, and inspect `kb topics --tree` plus retrieval behavior.

## Acceptance Criteria

- Slash-delimited topics map cleanly to nested topic directories.
- Retrieval commands continue to work for both flat and nested topics.
- `kb topics --tree` displays hierarchy and counts clearly.
- Existing flat-topic KBs keep working without migration.

## Completion Record

Completed on 2026-05-06.

Implemented files:

- `src/kb_librarian/storage.py`
- `src/kb_librarian/indexing.py`
- `src/kb_librarian/cli.py`
- `tests/test_storage.py`
- `tests/test_indexing.py`
- `tests/test_cli.py`
- `README.md`
- `docs/command-reference.md`
- `docs/user-guide.md`

Verification:

- `pytest -q tests/test_storage.py tests/test_indexing.py tests/test_cli.py` - passed, 30 tests.
- `pytest -q` - passed, 101 tests.

Behavior delivered:

- Slash-delimited topics now normalize to nested `topics/<parent>/<child>/...` directories while preserving flat-topic compatibility.
- `kb reindex` now discovers nested topic directories recursively and ensures `scope.txt` plus generated topic indexes exist for parent and child topic directories.
- Added `kb topics` and `kb topics --tree` for flat and hierarchical topic inspection, including note counts and stale/orphan/review counts when review state is present.
- Retrieval and indexing paths continue to work with mixed flat and nested topics without changing note IDs.

## Out of Scope

- Mutating topic reorganization commands.
- Compaction application.
- Doctor and hygiene queue generation beyond any existing count surfaces.
