# Phase 03 - Long-Term Hygiene Implementation Plan

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Prevent the KB from becoming stale, fragmented, redundant, or hard to trust. This phase adds review-gated cleanup, diagnostics, topic reorganization, and health signals while preserving the file artifact and the Phase 1-2 agent workflow.

This phase assumes Phase 1 and Phase 2 are complete.

## Deliverables

- Compaction detection and review-gated compaction proposal flow.
- Stale-note flagging based on note type, staleness risk, age, status, and confidence.
- Orphan flagging based on backlinks, related references, usage, and topic membership.
- Low-utility queue based on retrieval/use/correction signals.
- `kb doctor` for artifact health checks and optional self-test.
- Paginated top-level and topic indexes for large topics.
- Hierarchical topics with `kb topics --tree`.
- Reorganization commands: `promote`, `split`, `merge`, and `rename`.

## Implementation Tasks

1. Add compaction detection.
   - During `kb reindex --scan-clusters`, identify candidate duplicate or overlapping clusters using title similarity, retrieval phrase overlap, tag overlap, backlink proximity, repeated `adds_nuance` proposals, and repeated context output overlap.
   - Respect `review.duplicate_cluster_threshold` and `review.compaction_cooldown_days`.
   - Create review items in `review/pending-compaction.md` and `review/review-items.json`; do not rewrite notes during detection.
   - Include source note IDs, reason for the cluster, suggested canonical note title, and estimated risk.

2. Implement `kb compact <topic-or-cluster>`.
   - Accept either a topic name or an existing compaction review item/cluster ID.
   - Retrieve the relevant notes and ask the provider to draft a canonical note plus a source-note disposition plan.
   - The proposal must include the new note body, frontmatter, source notes, citations, supersession/deletion recommendations, and a human-readable diff summary.
   - Store the proposal as a pending compaction review item.
   - Do not apply the proposal until `kb review accept <item-id>`.

3. Apply accepted compaction safely.
   - Require a clean worktree for compaction unless `--force` is explicitly provided on the accepting command.
   - Write the canonical note atomically.
   - For heavily used notes, mark old notes `status: superseded` and add `superseded_by`.
   - For low-use duplicate notes, allow archive/delete only when the accepted review item explicitly says so.
   - Update backlinks, topic indexes, FTS, stats, and review history after applying.
   - Never silently discard provenance; accepted canonical notes must keep source references.

4. Add stale-note flagging.
   - Evaluate staleness during `kb reindex --scan-clusters`, `kb doctor`, and any explicit stale scan command added by the implementation.
   - Use `review.stale_after_days`, note `updated`, `knowledge_type`, `staleness_risk`, confidence, status, and source quality.
   - Facts with medium/high staleness risk should be prioritized over durable heuristics and decisions.
   - Create review items in `review/stale.md` without rewriting note bodies.
   - If a stale item is accepted, update status/review metadata only according to the accepted action.

5. Add orphan flagging.
   - Generate backlinks from note bodies, `related` sections, explicit note IDs, tags, topics, and source references.
   - Flag notes that have no backlinks or related references, no recent usage, and no clear topic neighborhood after `review.orphan_after_days`.
   - Create review items in `review/orphans.md` with suggested actions: add links, move topic, merge, archive, or leave alone.
   - Do not auto-archive orphan notes.

6. Add the low-utility queue.
   - Use `.kb/usage.log`, `.kb/stats.json`, `kb log-use`, search misses, suspect flags, and user corrections.
   - Flag notes that are repeatedly retrieved but rarely logged as used, repeatedly cause corrections, or frequently appear in low-score contexts.
   - Create review items in `review/low-utility.md` with evidence and suggested actions.
   - Keep low-utility scoring explainable; include counts and dates in each item.

7. Implement `kb flag-suspect`.
   - Accept `kb flag-suspect <id> "<reason>"`.
   - Append a usage/stat event and create or update a low-utility or dispute review item depending on the reason.
   - Do not change note body text.
   - If the note is already disputed or low-utility, add the new reason to the existing active review item.

8. Implement `kb doctor`.
   - Check required directories, config validity, note schema validity, duplicate IDs, broken note links, missing topic indexes, FTS freshness, stale generated artifacts, unreadable review state, raw ingest errors, and provider config presence.
   - `kb doctor --self-test` should run a tiny deterministic local fixture using the mock provider and a temporary data directory.
   - Print severity levels: `ok`, `warn`, and `error`.
   - Return nonzero when errors are found.

9. Add INDEX pagination.
   - Keep top-level `INDEX.md` thin and deterministic.
   - Paginate topic indexes when note count exceeds a configured threshold.
   - Include page navigation links and stable sort order.
   - Ensure `kb search`, `kb context`, and `kb explore` do not depend on reading paginated markdown indexes.

10. Add hierarchical topics.
    - Support slash-delimited topics such as `agent-systems/retrieval`.
    - Store nested topic notes under matching nested directories beneath `topics/`.
    - Generate `scope.txt` and `INDEX.md` for each topic directory when missing.
    - Preserve existing flat topics as top-level topics.
    - Implement `kb topics --tree` to show hierarchy, note counts, and stale/orphan/review counts.

11. Implement topic reorganization commands.
    - `kb topic promote <topic...> --under <parent>` moves existing topics under a parent topic after validating there are no path conflicts.
    - `kb topic rename <old> <new>` renames one topic, updates note frontmatter, moves note files, and regenerates indexes.
    - `kb topic split <topic> --into <new...>` creates a review proposal with suggested note-to-topic mapping; applying requires `kb review accept`.
    - `kb topic merge <a> <b> --as <name>` creates a review proposal with target topic name, path changes, and affected notes; applying requires `kb review accept`.
    - All mutating topic operations require a clean worktree unless explicitly forced.
    - All operations must update note paths, note frontmatter, indexes, backlinks, and review history consistently.

12. Add safety tests for hygiene mutations.
    - Unit-test compaction clustering, stale scoring, orphan detection, low-utility scoring, doctor checks, pagination, and topic path normalization.
    - Mock-provider-test compaction draft generation and split/merge topic proposals.
    - CLI smoke-test accepted and rejected compaction/topic proposals in a temporary git repo.

## Public Interfaces

- `kb compact <topic-or-cluster>`
- `kb reindex [--scan-clusters] [--all]`
- `kb doctor [--self-test]`
- `kb topics [--tree]`
- `kb topic promote <topic...> --under <parent>`
- `kb topic split <topic> --into <new...>`
- `kb topic merge <a> <b> --as <name>`
- `kb topic rename <old> <new>`
- `kb flag-suspect <id> "<reason>"`
- Existing `kb review explain|accept|reject|defer` commands for hygiene review items.

## Data/State Changes

- Adds or fully activates review queues: `pending-compaction.md`, `stale.md`, `orphans.md`, and `low-utility.md`.
- Extends `review/review-items.json` with compaction, stale, orphan, low-utility, and topic-reorganization item payloads.
- Updates `.kb/backlinks.json` with richer link and relationship data.
- Updates `.kb/stats.json` with retrieval/use/correction aggregates used by hygiene scoring.
- May add `superseded_by` and review metadata to note frontmatter after accepted compaction.
- May move note files and update note `topic` fields during accepted topic reorganization.
- Regenerates indexes after every accepted hygiene mutation.

## Test Plan

- Unit tests for deterministic compaction cluster detection, staleness rules, orphan rules, low-utility thresholds, doctor validation, pagination boundaries, hierarchical topic parsing, and topic move planning.
- Mock-provider tests for compaction proposal generation, split-topic mapping proposals, merge-topic proposals, and explanation text for review items.
- CLI smoke tests in temporary KBs and temporary git repos for `compact`, `review accept` on compaction, `doctor`, `topics --tree`, topic rename, topic promote, topic split, topic merge, stale queue creation, orphan queue creation, and low-utility queue creation.
- Manual acceptance scenario:
  1. Start with a Phase 2 KB containing duplicate/overlapping notes, stale facts, isolated notes, and usage logs.
  2. Run `kb reindex --scan-clusters`.
  3. Run `kb doctor`.
  4. Review pending compaction, stale, orphan, and low-utility items.
  5. Accept one safe compaction and one topic reorganization.
  6. Confirm `kb context`, `kb explore`, and `kb search` still retrieve the affected knowledge with valid citations.

## Acceptance Criteria

- The KB can identify duplicate clusters and create compaction proposals without applying them automatically.
- Accepted compaction preserves provenance, updates indexes, and does not break retrieval.
- Stale, orphan, and low-utility queues explain why each item needs attention.
- `kb doctor` catches broken artifacts and returns nonzero for errors.
- Large topic indexes paginate deterministically.
- Hierarchical topics work with retrieval commands and `kb topics --tree`.
- Topic reorganization commands update paths, frontmatter, indexes, and review history safely.
- Hygiene operations are review-gated where judgment or destructive mutation is involved.

## Out of Scope

- Concurrent ingest lockfiles, interrupted-operation resume, provider retry/backoff, golden corpus harness, PDF/HTML parsing, session-start hooks, cron templates, and auto-commit.
- Embeddings, additional providers, local providers, MCP wrapper, TUI/web review, privacy/redaction tooling, and multi-machine conflict helpers.
