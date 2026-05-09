# Phase 03e - Review-Gated Hygiene Mutations

Status: Complete
Completed: 2026-05-07

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

## Goal

Apply accepted structural hygiene changes safely. At the end of this milestone, accepted compaction proposals and topic reorganization commands can mutate notes, paths, and indexes under explicit review control while preserving provenance, worktree safety, and retrieval integrity.

## Depends On

- Phases 03a through 03d should be complete.
- Reuse durable review items, note-writing helpers, clean-worktree checks, retrieval/index regeneration, and hierarchical topic support from earlier milestones.

## Deliverables

- Safe application of accepted compaction proposals.
- Topic reorganization commands: `promote`, `rename`, `split`, and `merge`.
- Review-gated proposal flow for topic split and topic merge.
- Consistent path, frontmatter, backlink, index, and review-history updates after accepted mutations.

## Implementation Tasks

1. Apply accepted compaction safely.
   - Require a clean worktree for compaction unless `--force` is explicitly provided on the accepting command.
   - Write the canonical note atomically.
   - For heavily used notes, mark old notes `status: superseded` and add `superseded_by`.
   - For low-use duplicate notes, allow archive or delete only when the accepted review item explicitly says so.
   - Update backlinks, topic indexes, FTS, stats, and review history after applying.
   - Never silently discard provenance; accepted canonical notes must keep source references.

2. Implement direct topic mutations.
   - `kb topic promote <topic...> --under <parent>` moves existing topics under a parent topic after validating there are no path conflicts.
   - `kb topic rename <old> <new>` renames one topic, updates note frontmatter, moves note files, and regenerates indexes.
   - Require a clean worktree for mutating topic operations unless explicitly forced.

3. Add review-gated topic split and merge proposals.
   - `kb topic split <topic> --into <new...>` creates a review proposal with suggested note-to-topic mapping.
   - `kb topic merge <a> <b> --as <name>` creates a review proposal with target topic name, path changes, and affected notes.
   - Applying split or merge proposals must require `kb review accept <item-id>`.

4. Apply accepted topic proposals safely.
   - Update note paths, note frontmatter, indexes, backlinks, and review history consistently.
   - Validate there are no path conflicts or orphaned generated artifacts after the move.
   - Keep accepted proposal history readable for later audit.

5. Preserve retrieval integrity after mutations.
   - Reindex after each accepted compaction or topic change.
   - Ensure `kb search`, `kb context`, `kb explore`, and `kb get` can still retrieve moved or superseded knowledge appropriately.
   - Keep user-facing paths and citations coherent after path changes.

6. Add tests.
   - Unit-test compaction application, supersession metadata, topic path conflict detection, note move planning, and accepted split or merge application.
   - Mock-provider-test split-topic mapping proposals and merge-topic proposals.
   - CLI smoke-test accepted and rejected compaction or topic proposals in a temporary git repo.

## Public Interfaces

- `kb review accept <item-id>` for compaction and topic-proposal items
- `kb topic promote <topic...> --under <parent>`
- `kb topic split <topic> --into <new...>`
- `kb topic merge <a> <b> --as <name>`
- `kb topic rename <old> <new>`

## Data/State Changes

- May add `superseded_by` and review metadata to note frontmatter after accepted compaction.
- May move note files and update note `topic` fields during accepted topic reorganization.
- Updates backlinks, indexes, retrieval state, and review history after accepted mutations.
- May archive or delete duplicate notes only when an accepted review item explicitly authorizes it.

## Test Plan

- Unit tests for safe mutation planning and application.
- Mock-provider tests for topic proposal generation.
- CLI smoke tests in temporary KB directories and temporary git repos.
- Manual check: accept one safe compaction and one topic reorganization, then confirm retrieval and citations still work.

## Acceptance Criteria

- Accepted compaction preserves provenance and does not break retrieval.
- Topic reorganization commands update paths, frontmatter, indexes, and review history safely.
- Split and merge remain review-gated rather than direct destructive operations.
- Hygiene mutations respect clean-worktree safety unless explicitly forced.

## Completion Record

Completed on 2026-05-07.

Implemented files:

- `src/kb_librarian/mutations.py`
- `src/kb_librarian/compaction.py`
- `src/kb_librarian/topic_mutations.py`
- `src/kb_librarian/review.py`
- `src/kb_librarian/cli.py`
- `src/kb_librarian/init.py`
- `src/kb_librarian/paths.py`
- `tests/test_compaction.py`
- `tests/test_topic_mutations.py`
- `tests/test_cli.py`
- `README.md`
- `docs/command-reference.md`
- `docs/user-guide.md`

Verification:

- `pytest -q tests/test_compaction.py tests/test_topic_mutations.py tests/test_review.py` - passed, 23 tests.
- `pytest -q tests/test_cli.py::test_kb_review_accept_compaction_proposal_smoke_in_git_repo tests/test_cli.py::test_kb_topic_split_reject_smoke` - passed, 2 tests.
- `pytest -q` - passed, 110 tests.

Behavior delivered:

- `kb review accept <compaction-item>` now applies compaction proposals by creating a canonical note, preserving source-note references, superseding or archiving source notes according to dispositions, and rebuilding indexes.
- Mutating hygiene operations require a clean git worktree when the KB is inside git unless `--force` is supplied.
- Added `kb topic rename` and `kb topic promote` for direct topic moves with note path/frontmatter updates and index regeneration.
- Added `kb topic split` and `kb topic merge` as review-gated `topic` proposals rendered in `review/pending-topic.md`; accepted proposals move notes through `kb review accept`.

## Out of Scope

- New Phase 4 robustness work such as resume, retry, or auto-commit.
- Embedding-based restructuring or external UI workflows.
