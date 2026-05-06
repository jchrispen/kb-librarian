# Phase 02a - Durable Review State and Queue Rendering

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Replace Phase 1's markdown-only review state with a durable machine-readable review database while keeping markdown review files as generated inspection surfaces. At the end of this milestone, review items have stable IDs, survive rerenders, and can be listed consistently without manual parsing of queue markdown.

## Depends On

- Phase 01a through 01e must be complete.
- Reuse the existing Phase 1 review queue semantics for classification, merge, dispute, duplicate, unsupported-file, and future search-miss item families.

## Deliverables

- Durable review item model stored in `review/review-items.json`.
- Deterministic review item ID allocation.
- Idempotent import of existing Phase 1 markdown review entries into machine-readable state.
- Rendered markdown queue files generated from review state.
- Stable `kb review` and `kb review list` output sourced from review state rather than ad hoc markdown parsing.

## Implementation Tasks

1. Define the review item schema.
   - Store review items in `review/review-items.json`.
   - Each item must include `id`, `queue`, `status`, `priority`, `title`, `created`, `updated`, `target_notes`, `proposed_action`, `payload`, and `history`.
   - Add any minimal supporting fields needed for deterministic rendering, such as `defer_until` or `source_refs`, only if they are clearly required now.
   - Keep the format append-friendly and explicit enough for later review actions.

2. Implement deterministic review ID allocation.
   - Use queue-prefixed IDs with date and counter, for example `merge-2026-05-04-001`.
   - Keep allocation deterministic for repeated imports and rerenders.
   - Prevent duplicate IDs even when multiple items land in the same queue on the same day.

3. Import Phase 1 review surfaces into durable state.
   - Parse existing `pending-classification.md`, `pending-merge.md`, and `disputes.md` entries.
   - Create matching review items in `review-items.json`.
   - Make import idempotent so rerunning it does not duplicate items.
   - Preserve enough source material in `payload` and `history` to support later explain and accept flows.

4. Render markdown queues from review state.
   - Generate human-readable queue files from `review-items.json`.
   - Keep `pending-classification.md`, `pending-merge.md`, and `disputes.md` as rendered views, not the source of truth.
   - Add rendered support for `search-misses.md` even if Phase 02d is the first milestone that populates it.
   - Make queue rendering deterministic when review state is unchanged.

5. Update review listing behavior.
   - Make `kb review` and `kb review list` read from `review-items.json`.
   - Show stable IDs in review output.
   - Preserve bounded output using `review.max_review_items_per_run`.
   - Keep already-resolved items hidden from default pending output.

6. Add safe migration and bootstrap handling.
   - If `review/review-items.json` is missing, create it from current review surfaces or initialize it empty.
   - Do not discard existing markdown review content until it has been represented in durable state.
   - Keep behavior safe for existing Phase 1 KB directories.

7. Add tests.
   - Unit-test schema validation, ID allocation, import idempotency, queue rendering, and pending-item ordering.
   - CLI smoke-test `kb review` and `kb review list` against a temporary Phase 1-style KB upgraded into durable review state.

## Public Interfaces

- `kb review`
- `kb review list`
- Durable review storage at `review/review-items.json`

## Data/State Changes

- Adds `review/review-items.json` as the durable review state file.
- Regenerates markdown review queues from durable review state.
- Does not apply review actions yet and should not rewrite canonical note bodies.

## Test Plan

- Unit tests for deterministic state migration and queue rendering.
- CLI smoke tests using temporary KB directories with preexisting Phase 1 review files.
- Manual check: rerun review rendering twice and confirm item IDs and queue markdown stay stable.

## Acceptance Criteria

- Review items receive stable IDs and persist in `review/review-items.json`.
- Existing Phase 1 review entries are imported without duplication.
- Queue markdown is rendered from durable state and remains human-inspectable.
- `kb review` and `kb review list` show stable IDs and bounded pending output.

## Out of Scope

- `kb review explain`, `accept`, `reject`, and `defer`.
- Applying any queued action to notes or review state beyond migration/rendering.
- Search-miss generation, usage logging, citation blocks, and `kb explore`.
