# Phase 02b - Actionable Review Workflow

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-06

## Goal

Make review actionable instead of read-only. At the end of this milestone, a human can inspect one review item, accept supported actions, reject invalid work, or defer items for later without editing markdown files by hand.

## Depends On

- Phase 02a must be complete.
- Reuse durable review items, rendered queue files, existing note mutation helpers, and Phase 1 integration safety rules.

## Deliverables

- `kb review explain <item-id>`.
- `kb review accept <item-id>` for Phase 2-supported actions.
- `kb review reject <item-id>`.
- `kb review defer <item-id> --days <n>`.
- Priority ordering and idempotent state transitions sized for a roughly 5-minute review session.

## Implementation Tasks

1. Define review state transitions.
   - Support pending, accepted, rejected, and deferred states.
   - Record transition timestamps and actor/action notes in `history`.
   - Add `defer_until` handling for deferred items.
   - Make repeated transition commands idempotent with clear output.

2. Implement `kb review explain`.
   - Print the full item, involved note paths, proposed action, source material, and why the item exists.
   - Include enough detail to decide without opening raw JSON unless the user wants to.
   - Return clear errors for unknown IDs.

3. Implement supported accept actions.
   - Support classification selection, source append, merge-proposal body append only when explicitly approved through the review item, dispute acknowledgement, and search-miss resolution notes.
   - Use existing safe note-writing helpers where possible.
   - Keep all note mutations explicit, validated, and logged in item history.
   - Reject unsupported action types with a clear message instead of attempting partial application.

4. Implement reject behavior.
   - Mark the item rejected.
   - Record a rejection history entry.
   - Move rendered detail for rejected items under `review/rejected/` while preserving durable state.

5. Implement defer behavior.
   - Accept `--days <n>` and compute the target date.
   - Hide deferred items from default `kb review` and `kb review list` output until due.
   - Preserve visibility through explicit explain or non-default listing if implemented in the current CLI shape.

6. Tighten bounded review workflow behavior.
   - Order pending items by priority: disputes, unsafe merges, classification blockers, repeated search misses, duplicates, then unsupported files.
   - Show a suggested number of items to handle now rather than dumping the full backlog.
   - Keep rendered markdown queues synchronized after every transition.

7. Add tests.
   - Unit-test transition rules, defer date handling, accept application for each supported action family, rejection archiving, and idempotent repeated commands.
   - CLI smoke-test explain/accept/reject/defer flows in temporary KB directories.

## Public Interfaces

- `kb review explain <item-id>`
- `kb review accept <item-id>`
- `kb review reject <item-id>`
- `kb review defer <item-id> --days <days>`

## Data/State Changes

- Updates `review/review-items.json` state and history.
- Regenerates rendered review queue files and `review/rejected/` details.
- May update notes only through explicit supported accept actions.

## Test Plan

- Unit tests for state transitions, item application, and due-date filtering.
- CLI smoke tests for the full review command set.
- Manual check: resolve several different item types and confirm review output stays bounded and accurate.

## Acceptance Criteria

- Review items can be explained and resolved without manual file edits.
- Supported accept actions update notes or review state safely and idempotently.
- Rejected and deferred items are tracked durably and no longer clutter default pending output.
- Review ordering and output keep a normal session under roughly 5 minutes.

## Completion Record

Implemented files:

- `src/kb_librarian/review.py`
- `src/kb_librarian/cli.py`
- `tests/test_review.py`
- `tests/test_cli.py`

Verification:

- `python3 -m pytest tests/test_review.py tests/test_cli.py::test_kb_review_lists_bounded_items tests/test_cli.py::test_kb_review_actionable_workflow_smoke` - passed, 12 tests.
- `python3 -m pytest` - passed, 60 tests.

## Out of Scope

- `kb explore`.
- Usage logging, search-miss logging, and citation block generation.
- Compaction or any Phase 3 review families beyond those already introduced.
