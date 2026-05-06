# Phase 03b - Hygiene Signal Queues

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Surface knowledge that is aging badly, isolated, or not helping agents much. At the end of this milestone, stale, orphan, and low-utility review queues exist with explainable evidence, and users can flag suspect notes without rewriting note bodies.

## Depends On

- Phases 01 and 02 must be complete.
- Reuse durable review items, backlinks, usage logs, search-miss logs, and stats from earlier phases.

## Deliverables

- Stale-note review items rendered into `review/stale.md`.
- Orphan review items rendered into `review/orphans.md`.
- Low-utility review items rendered into `review/low-utility.md`.
- `kb flag-suspect <id> "<reason>"`.
- Explainable hygiene scoring and evidence in durable review state.

## Implementation Tasks

1. Add stale-note flagging.
   - Evaluate staleness during `kb reindex --scan-clusters`, `kb doctor`, and any explicit stale scan path the implementation adds.
   - Use `review.stale_after_days`, note `updated`, `knowledge_type`, `staleness_risk`, confidence, status, and source quality.
   - Prioritize medium or high-risk facts over durable heuristics and decisions.

2. Create stale review items.
   - Write stale candidates to `review/review-items.json` and render them into `review/stale.md`.
   - Include the reason each item is stale, the evidence date windows, and the suggested action.
   - Do not rewrite note bodies automatically.

3. Add orphan flagging.
   - Generate or reuse richer backlinks from note bodies, `related` sections, explicit note IDs, tags, topics, and source references.
   - Flag notes with no backlinks or related references, no recent usage, and no clear topic neighborhood after `review.orphan_after_days`.
   - Suggest actions such as add links, move topic, merge, archive, or leave alone.

4. Add low-utility scoring.
   - Use `.kb/usage.log`, `.kb/stats.json`, `kb log-use`, search misses, suspect flags, and user corrections.
   - Flag notes that are repeatedly retrieved but rarely logged as used, repeatedly cause corrections, or frequently appear in weak retrieval contexts.
   - Keep scoring explainable by storing counts, dates, and reasons in the review payload.

5. Implement `kb flag-suspect`.
   - Accept `kb flag-suspect <id> "<reason>"`.
   - Append a usage or stats event and create or update an active low-utility or dispute-adjacent review item depending on the reason.
   - If the note is already represented by an active hygiene item, add the new reason rather than duplicating the queue entry.

6. Keep hygiene queues bounded and idempotent.
   - Prevent duplicate stale, orphan, or low-utility items for the same unresolved condition.
   - Reopen or refresh existing items only when the evidence materially changes.
   - Preserve enough history for later explain and accept flows.

7. Add tests.
   - Unit-test stale scoring, orphan detection, low-utility thresholds, suspect-flag deduplication, and rendered queue ordering.
   - CLI smoke-test queue generation and `kb flag-suspect` in temporary KB directories.

## Public Interfaces

- `kb flag-suspect <id> "<reason>"`
- `kb reindex [--scan-clusters] [--all]`
- Existing `kb review` commands for stale, orphan, and low-utility items

## Data/State Changes

- Adds or activates rendered review queues: `review/stale.md`, `review/orphans.md`, and `review/low-utility.md`.
- Extends `review/review-items.json` with stale, orphan, and low-utility payloads.
- Updates `.kb/backlinks.json` and `.kb/stats.json` if needed for hygiene scoring.
- Appends suspect or correction signals to existing usage-related state.
- Does not modify note bodies automatically.

## Test Plan

- Unit tests for hygiene scoring and item lifecycle behavior.
- CLI smoke tests using temporary KB directories with seeded usage and backlink scenarios.
- Manual check: generate each queue type and verify the evidence is understandable from `kb review explain` output.

## Acceptance Criteria

- Stale, orphan, and low-utility queues are populated with explainable evidence.
- `kb flag-suspect` records the issue and updates review state without rewriting notes.
- Hygiene queue generation remains bounded and idempotent.
- Review items clearly distinguish why a note needs attention.

## Out of Scope

- `kb doctor`.
- Compaction proposal drafting or application.
- Topic hierarchy and topic reorganization commands.
