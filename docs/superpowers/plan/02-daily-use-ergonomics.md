# Phase 02 - Daily-Use Ergonomics

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make the Phase 1 tool comfortable in normal agent sessions. Agents should be able to retrieve, explore, cite, and log KB use through stable commands, while human review stays actionable and short.

Phase 2 assumes Phase 1 is complete and should preserve all existing note files, indexes, and review queues.

## Milestone Status

| Milestone | Status | Notes |
|---|---|---|
| [02a - Durable Review State and Queue Rendering](02a-durable-review-state-and-queue-rendering.md) | Complete | Introduced `review/review-items.json`, stable IDs, idempotent Phase 1 review import, and rendered markdown queues backed by durable state. |
| [02b - Actionable Review Workflow](02b-actionable-review-workflow.md) | Complete | Added explain/accept/reject/defer commands, state transitions with defer-until visibility, and actionable review mutation flows with tests. |
| [02c - Explore and Citation Surfaces](02c-explore-and-citation-surfaces.md) | Complete | Added `kb explore`, broad-recall exploration synthesis, default context/explore citation blocks, optional search citation blocks, and structured citation JSON fields. |
| [02d - Usage and Search-Miss Feedback](02d-usage-and-search-miss-feedback.md) | Next | Adds usage logging, `kb log-use`, `kb usage`, search-miss logging, and miss promotion into review. |
| [02e - Preamble Install, Ingest Reports, and Session Proof](02e-preamble-ingest-reports-and-session-proof.md) | Pending | Adds `kb init --hooks`, revised `PREAMBLE.md`, ingest report polish, and end-to-end normal-session verification. |

## Milestone Order

1. [02a - Durable Review State and Queue Rendering](02a-durable-review-state-and-queue-rendering.md) - complete
   - Creates the machine-readable review state model with stable IDs and keeps markdown review files as rendered inspection surfaces.
2. [02b - Actionable Review Workflow](02b-actionable-review-workflow.md) - complete
   - Makes review items explainable and resolvable without manual file edits while keeping the workflow bounded.
3. [02c - Explore and Citation Surfaces](02c-explore-and-citation-surfaces.md) - complete
   - Adds broad-recall ideation retrieval and citation-ready output for agent-facing commands.
4. [02d - Usage and Search-Miss Feedback](02d-usage-and-search-miss-feedback.md) - next
   - Records retrieval/use signals and turns repeated misses into reviewable improvement work.
5. [02e - Preamble Install, Ingest Reports, and Session Proof](02e-preamble-ingest-reports-and-session-proof.md) - pending
   - Finishes normal-session ergonomics with installable agent guidance, better ingest summaries, and a phase-level proof run.

Each milestone should leave the CLI runnable and covered by focused tests. Later milestones may adjust earlier code, but they should preserve accepted Phase 1 behavior and the public interfaces already shipped in earlier Phase 2 milestones.

## Phase Deliverables

- Stable review item IDs stored in `review/review-items.json` and rendered into markdown queue files.
- Actionable review commands: `list`, `explain`, `accept`, `reject`, and `defer`.
- `kb explore` for broader ideation and adjacent concept retrieval.
- Usage logging, `kb log-use`, and a simple `kb usage` report.
- Search-miss logging that turns repeated poor retrieval into reviewable items.
- Agent preamble installation through `kb init --hooks` or an equivalent explicit install path.
- Citation block generation for agent-facing retrieval commands.
- Better ingest reports with clear counts, created notes, review items, duplicates, errors, and archived raw files.

## Final Acceptance Criteria

- Review items have stable IDs that survive reindexing, rerendering, and repeated CLI calls.
- Review commands can explain and resolve supported item types without manual file editing.
- `kb explore` returns useful adjacent concepts and source notes distinct from high-precision `kb context`.
- Retrieval commands produce citation-ready source blocks.
- Usage and search-miss logs are written without storing full note bodies.
- Repeated missed searches become reviewable items.
- `kb init --hooks` refreshes `PREAMBLE.md` with the revised Phase 2 guidance and prints explicit usage instructions.
- Ingest reports clearly state what happened and which note/review IDs need attention.
- A normal agent session can retrieve context, explore ideas, cite notes, log use, and keep review bounded to roughly 5 minutes.

## Phase Test Strategy

- Unit tests for review item state, transition rules, citation formatting, usage aggregation, search-miss thresholds, preamble rendering, and ingest reporting.
- Mock-provider tests for `kb explore` retrieval breadth and synthesis output.
- CLI smoke tests using temporary KB directories for Phase 2 commands and upgraded output paths.
- Manual acceptance with a Phase 1 KB containing 20-50 notes and several review items.

## Out of Scope

- Compaction, stale-note flagging, orphan flagging, low-utility queues, `kb doctor`, INDEX pagination, and topic reorganization.
- Concurrent ingest lockfiles, resume, provider retry/backoff, golden corpus harness, PDF/HTML parsing, session-start automation beyond explicit preamble installation, cron templates, and auto-commit.
- Embeddings, additional providers, MCP wrapper, TUI/web review, privacy/redaction tooling, and multi-machine conflict helpers.
