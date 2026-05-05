# Phase 01d - Integration and Basic Review

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make ingest trust-preserving. At the end of this milestone, extracted candidates are compared with existing notes, integration verdicts are applied safely, and basic review surfaces classification, merge, dispute, duplicate, and unsupported-file work.

## Depends On

- Phases 01a, 01b, and 01c must be complete.
- Reuse provider protocol, lexical lookup, note storage, ingest reports, and review queue files from earlier milestones.

## Deliverables

- Candidate matching across topic, parent topic, tags, retrieval phrases, and global lexical index.
- Provider-backed integration verdicts: `identical`, `adds_nuance`, `contradicts`, and `unrelated`.
- Source append behavior for identical candidates.
- Merge proposal queue for adds-nuance candidates.
- Dispute handling for contradictory candidates.
- Basic `kb review` and `kb review list`.

## Implementation Tasks

1. Implement candidate match retrieval.
   - For each candidate, retrieve likely matches from assigned topic, parent topic when available, tags, retrieval phrases, title/body lexical matches, and global note IDs.
   - Include cross-topic matches; contradictions and duplicates are not restricted to a single topic.
   - Cap match count to keep provider prompts bounded.
   - Include enough metadata and excerpts for the provider to judge similarity.

2. Implement integration verdict calls.
   - Use the provider protocol's integration method.
   - Ask for one verdict per candidate: `identical`, `adds_nuance`, `contradicts`, or `unrelated`.
   - Validate structured JSON responses before applying any change.
   - Treat unknown or malformed verdicts as review-needed errors, not as create-new-note.

3. Apply `unrelated` as create-new-note.
   - Create a new note using the candidate's topic, type, body, retrieval phrases, tags, confidence, and source metadata.
   - Validate before writing.
   - Reindex after successful writes.

4. Apply `identical` safely.
   - Append the new source to the existing note frontmatter.
   - Do not rewrite the existing note body.
   - Avoid duplicate source entries for the same raw file hash or source ref.
   - Update `updated` only if the project chooses to treat source append as a note metadata update; keep this behavior documented and tested.

5. Queue `adds_nuance` proposals.
   - Write a review entry to `review/pending-merge.md`.
   - Include candidate title/body, target note IDs, source refs, provider rationale, and suggested action.
   - Do not merge or rewrite existing note bodies in Phase 1.
   - Make duplicate merge proposals idempotent for the same candidate/source/target set.

6. Apply `contradicts` safely.
   - Mark involved notes `status: disputed`.
   - Add dispute metadata to frontmatter without deleting existing metadata.
   - Write a review entry to `review/disputes.md` with candidate text, target note IDs, source refs, and provider rationale.
   - Do not synthesize a replacement note automatically.

7. Improve ingest reports for integration outcomes.
   - Add counts for created notes, source appends, merge proposals, disputes, classification items, skipped candidates, duplicates, unsupported files, and errors.
   - Include note IDs and review file paths.
   - Preserve `--quiet` behavior.

8. Implement basic `kb review`.
   - `kb review` and `kb review list` print counts and excerpts for pending classification, merge, dispute, duplicate, and unsupported-file items.
   - Review output can be markdown-file-backed in Phase 1.
   - Stable review item IDs and actionable accept/reject/defer commands belong to Phase 2.
   - Keep output bounded by `review.max_review_items_per_run`.

9. Add tests.
   - Unit-test candidate matching, verdict validation, source append idempotency, merge queue rendering, dispute metadata updates, and review output bounds.
   - Mock-provider-test each integration verdict.
   - CLI smoke-test ingest flows that create, append sources, queue merges, queue disputes, and print review summaries.

## Public Interfaces

- `kb ingest [<file>] [--force] [--quiet]`, now with full Phase 1 integration handling.
- `kb review [list]`

## Data/State Changes

- May create new notes under `topics/<topic>/<id>.md`.
- May update existing note frontmatter for source appends, dispute status, and dispute metadata.
- Writes `review/pending-merge.md` and `review/disputes.md`.
- Uses `review/pending-classification.md` from Phase 01c.
- Records duplicate and unsupported-file issues in review output or supporting state.
- Regenerates indexes after note creations or frontmatter changes.

## Test Plan

- Unit tests for deterministic matching, verdict application, review queue rendering, and safe frontmatter updates.
- Mock-provider tests for `identical`, `adds_nuance`, `contradicts`, and `unrelated`.
- CLI smoke tests using temporary KBs with seeded notes and scripted provider responses.
- Manual check: ingest material that duplicates, extends, contradicts, and does not match existing notes; confirm each outcome is handled as specified.

## Acceptance Criteria

- Ingest compares candidates against likely existing notes across topics.
- `identical` appends sources without rewriting bodies.
- `adds_nuance` creates merge review entries without applying body changes.
- `contradicts` marks notes disputed and creates dispute review entries.
- `unrelated` creates validated new notes.
- `kb review` gives a bounded summary of Phase 1 review work.
- Integration changes keep lexical indexes current.

## Out of Scope

- Stable review item IDs and actionable review accept/reject/defer commands.
- Compaction, stale-note flagging, orphan flagging, low-utility queues, and `kb doctor`.
- `kb context` synthesis.
- Retry/backoff, ingest lockfiles, and resume.
