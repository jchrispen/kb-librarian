# Phase 03a - Compaction Detection and Proposal Flow

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Identify fragmented or overlapping knowledge and turn it into explicit reviewable compaction work without mutating notes automatically. At the end of this milestone, `kb reindex --scan-clusters` and `kb compact` can create durable compaction review items with clear evidence and provider-drafted canonical proposals.

## Depends On

- Phases 01 and 02 must be complete.
- Reuse stable review items, retrieval metadata, backlinks, provider synthesis infrastructure, and clean note-reading paths from earlier phases.

## Deliverables

- Duplicate or overlap cluster detection during `kb reindex --scan-clusters`.
- Compaction review items in `review/review-items.json` and rendered `review/pending-compaction.md`.
- `kb compact <topic-or-cluster>` for proposal drafting.
- Provider-drafted canonical note proposals with disposition plans and diff summaries.

## Implementation Tasks

1. Add compaction cluster detection.
   - During `kb reindex --scan-clusters`, identify candidate duplicate or overlapping clusters using title similarity, retrieval phrase overlap, tag overlap, backlink proximity, repeated `adds_nuance` proposals, and repeated context output overlap.
   - Respect `review.duplicate_cluster_threshold` and `review.compaction_cooldown_days`.
   - Keep clustering deterministic for unchanged notes and review state.

2. Create durable compaction review items.
   - Write cluster detections to `review/review-items.json` and render them into `review/pending-compaction.md`.
   - Include source note IDs, reason for the cluster, suggested canonical note title, estimated risk, and enough evidence to justify review.
   - Keep repeated scans idempotent when the underlying cluster has not materially changed.

3. Implement `kb compact <topic-or-cluster>`.
   - Accept either a topic name or an existing compaction review item or cluster ID.
   - Resolve the note set to compact and validate that the target is unambiguous.
   - Return clear errors when the requested topic or cluster has insufficient material.

4. Draft compaction proposals.
   - Ask the provider to draft a canonical note plus a source-note disposition plan.
   - Require the proposal to include frontmatter, body, source notes, citations, supersession or deletion recommendations, and a human-readable diff summary.
   - Store the draft as a pending compaction review item rather than applying it.

5. Keep compaction review bounded.
   - Show compaction items through existing review commands with stable IDs and priorities.
   - Avoid flooding the queue with near-duplicate proposals for the same cluster.
   - Preserve provenance and rationale in review history.

6. Add tests.
   - Unit-test cluster detection, cooldown behavior, idempotent item creation, target resolution for `kb compact`, and proposal payload validation.
   - Mock-provider-test canonical draft generation and diff-summary shape.
   - CLI smoke-test `kb reindex --scan-clusters` and `kb compact` in temporary KB directories.

## Public Interfaces

- `kb reindex [--scan-clusters] [--all]`
- `kb compact <topic-or-cluster>`

Existing Phase 2 review commands should surface compaction items once they exist.

## Data/State Changes

- Adds or activates `review/pending-compaction.md` as a rendered queue.
- Extends `review/review-items.json` with compaction cluster and compaction proposal payloads.
- May update generated relationship or scan metadata if the implementation needs durable cluster fingerprints.
- Does not rewrite canonical notes yet.

## Test Plan

- Unit tests for deterministic clustering and review item generation.
- Mock-provider tests for proposal drafting.
- CLI smoke tests using temporary KB directories with overlapping notes.
- Manual check: scan a KB with overlapping notes, inspect the pending compaction queue, and draft one proposal with `kb compact`.

## Acceptance Criteria

- `kb reindex --scan-clusters` identifies duplicate or overlapping clusters deterministically.
- Compaction review items are durable, stable, and rendered for human inspection.
- `kb compact` can draft a canonical proposal without mutating notes.
- Compaction proposals preserve enough provenance and evidence for later acceptance.

## Out of Scope

- Applying compaction proposals to notes.
- Topic reorganization commands.
- Stale, orphan, and low-utility queues beyond any shared review plumbing.
