# Phase 03 - Long-Term Hygiene

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Prevent the KB from becoming stale, fragmented, redundant, or hard to trust. This phase adds review-gated cleanup, diagnostics, topic reorganization, and health signals while preserving the file artifact and the Phase 1-2 agent workflow.

Phase 3 assumes Phase 1 and Phase 2 are complete.

## Milestone Status

| Milestone | Status | Notes |
|---|---|---|
| [03a - Compaction Detection and Proposal Flow](03a-compaction-detection-and-proposal-flow.md) | Complete | Added duplicate-cluster detection, `kb compact`, and durable review proposals without rewriting notes automatically. |
| [03b - Hygiene Signal Queues](03b-hygiene-signal-queues.md) | Complete | Added stale/orphan/low-utility hygiene queues, explainable evidence payloads, and `kb flag-suspect`. |
| [03c - Doctor and Scaled Index Surfaces](03c-doctor-and-scaled-index-surfaces.md) | Complete | Added `kb doctor`, offline self-test, and deterministic pagination for large indexes. |
| [03d - Hierarchical Topics and Tree Views](03d-hierarchical-topics-and-tree-views.md) | Complete | Added nested topics, generated nested topic artifacts, and `kb topics --tree` with hierarchy-aware counts. |
| [03e - Review-Gated Hygiene Mutations](03e-review-gated-hygiene-mutations.md) | Next | Applies accepted compaction and topic-reorganization changes safely under review control. |

## Milestone Order

1. [03a - Compaction Detection and Proposal Flow](03a-compaction-detection-and-proposal-flow.md) - complete
   - Identifies overlapping note clusters and turns them into explicit reviewable compaction proposals.
2. [03b - Hygiene Signal Queues](03b-hygiene-signal-queues.md) - complete
   - Surfaces stale, orphaned, and low-utility knowledge with explainable evidence and bounded review items.
3. [03c - Doctor and Scaled Index Surfaces](03c-doctor-and-scaled-index-surfaces.md) - complete
   - Adds health checks and keeps large published indexes inspectable without affecting retrieval behavior.
4. [03d - Hierarchical Topics and Tree Views](03d-hierarchical-topics-and-tree-views.md) - complete
   - Introduces nested topic structure and hierarchy-aware inspection while preserving retrieval compatibility.
5. [03e - Review-Gated Hygiene Mutations](03e-review-gated-hygiene-mutations.md) - next
   - Safely applies accepted compaction and topic reorganization under clean-worktree and history-preserving rules.

Each milestone should leave the CLI runnable and covered by focused tests. Later milestones may adjust earlier code, but they should preserve accepted Phase 1-2 behavior and the public interfaces already shipped in earlier Phase 3 milestones.

## Phase Deliverables

- Compaction detection and review-gated compaction proposal flow.
- Stale-note flagging based on note type, staleness risk, age, status, and confidence.
- Orphan flagging based on backlinks, related references, usage, and topic membership.
- Low-utility queue based on retrieval/use/correction signals.
- `kb doctor` for artifact health checks and optional self-test.
- Paginated top-level and topic indexes for large topics.
- Hierarchical topics with `kb topics --tree`.
- Reorganization commands: `promote`, `split`, `merge`, and `rename`.

## Final Acceptance Criteria

- The KB can identify duplicate clusters and create compaction proposals without applying them automatically.
- Accepted compaction preserves provenance, updates indexes, and does not break retrieval.
- Stale, orphan, and low-utility queues explain why each item needs attention.
- `kb doctor` catches broken artifacts and returns nonzero for errors.
- Large topic indexes paginate deterministically.
- Hierarchical topics work with retrieval commands and `kb topics --tree`.
- Topic reorganization commands update paths, frontmatter, indexes, and review history safely.
- Hygiene operations are review-gated where judgment or destructive mutation is involved.

## Phase Test Strategy

- Unit tests for deterministic compaction clustering, staleness/orphan/utility scoring, doctor validation, pagination boundaries, hierarchical topic parsing, and topic move planning.
- Mock-provider tests for compaction proposal generation and topic split/merge proposal drafting.
- CLI smoke tests in temporary KBs and temporary git repos for Phase 3 commands and accepted hygiene mutations.
- Manual acceptance with a Phase 2 KB containing duplicate or overlapping notes, stale facts, isolated notes, and usage logs.

## Out of Scope

- Concurrent ingest lockfiles, interrupted-operation resume, provider retry/backoff, golden corpus harness, PDF/HTML parsing, session-start hooks, cron templates, and auto-commit.
- Embeddings, additional providers, local providers, MCP wrapper, TUI/web review, privacy/redaction tooling, and multi-machine conflict helpers.
