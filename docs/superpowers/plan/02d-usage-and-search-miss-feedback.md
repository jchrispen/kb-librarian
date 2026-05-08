# Phase 02d - Usage and Search-Miss Feedback

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-06

## Goal

Let the KB learn from use without rewriting content automatically. At the end of this milestone, retrieval and explicit note usage are logged, repeated failed lookups become reviewable search-miss items, and a human can inspect lightweight usage summaries.

## Depends On

- Phase 01e must be complete.
- Phase 02a is required if repeated misses are promoted into durable review items.
- Reuse retrieval command surfaces and bounded review behavior from earlier milestones.

## Deliverables

- `.kb/usage.log` line-delimited JSON.
- `kb log-use <id> [--task "<task>"]`.
- `kb usage [--since <duration>] [--note <id>]`.
- `.kb/search-misses.log` line-delimited JSON.
- Promotion of repeated or high-value misses into `review/review-items.json` and rendered `review/search-misses.md`.

## Implementation Tasks

1. Add retrieval usage logging.
   - Log retrieval command, timestamp, query/task, mode, budget, returned note IDs, result count, and whether synthesis succeeded.
   - Write line-delimited JSON to `.kb/usage.log`.
   - Do not log full note bodies.

2. Implement `kb log-use`.
   - Record that a note was actually used or cited by an agent.
   - Accept an optional `--task` summary.
   - Validate note IDs and keep logging append-only.

3. Implement `kb usage` summaries.
   - Support `--since <duration>` and `--note <id>`.
   - Summarize retrieval counts, logged uses, and suspect flags if they already exist in the current codebase.
   - Keep the report concise and operational rather than analytical.

4. Add search-miss logging.
   - Record zero-result, low-score, or explicitly reported poor-result searches in `.kb/search-misses.log`.
   - Store command, query, filters, timestamp, result count, top score when available, and optional task.
   - Keep the log lightweight and body-free.

5. Promote repeated misses into review.
   - Define thresholds for repeated or high-value misses.
   - Create durable review items for promoted misses.
   - Render promoted misses into `review/search-misses.md`.
   - Do not create notes automatically from search misses in this phase.

6. Update aggregate counters.
   - Refresh `.kb/stats.json` with retrieval and usage aggregates if that file is already used for summary state.
   - Keep aggregates rebuildable from logs where practical.

7. Add tests.
   - Unit-test log record schemas, duration filtering, note-specific summaries, miss-threshold promotion, and durable review-item creation for misses.
   - CLI smoke-test retrieval logging, `log-use`, `usage`, and search-miss promotion in temporary KB directories.

## Public Interfaces

- `kb log-use <id> [--task "<task>"]`
- `kb usage [--since <duration>] [--note <id>]`

Retrieval commands should also gain logging side effects during normal operation.

## Data/State Changes

- Writes `.kb/usage.log` and `.kb/search-misses.log`.
- May update `.kb/stats.json` with aggregated counters.
- Writes durable review items and rendered `review/search-misses.md` for promoted misses.
- Does not modify canonical notes automatically.

## Test Plan

- Unit tests for logging, summarization, and promotion thresholds.
- CLI smoke tests using temporary KB directories and representative retrieval scenarios.
- Manual check: run successful and failed retrievals, then confirm `kb usage` and review surfaces reflect the expected signals.

## Acceptance Criteria

- Retrieval and explicit note-use events are logged without storing full note bodies.
- `kb usage` gives useful lightweight summaries.
- Repeated or important misses become reviewable items.
- Search-miss promotion does not create notes automatically.

## Completion Record

Implemented files:

- `src/kb_librarian/usage.py`
- `src/kb_librarian/cli.py`
- `src/kb_librarian/context.py`
- `src/kb_librarian/indexing.py`
- `src/kb_librarian/paths.py`
- `src/kb_librarian/review.py`
- `tests/test_usage.py`
- `tests/test_cli.py`
- `docs/command-reference.md`
- `docs/user-guide.md`
- `docs/configuration.md`

Verification:

- `pytest tests/test_usage.py -q` - passed, 4 tests.
- `pytest tests/test_cli.py -q` - passed, 13 tests.
- `pytest tests/test_usage.py tests/test_cli.py tests/test_context.py tests/test_review.py -q` - passed, 33 tests.
- `pytest -q` - passed, 71 tests.

## Out of Scope

- `kb explore` implementation.
- Preamble installation and ingest report improvements.
- Compaction, low-utility queues, or other Phase 3 hygiene work.
