# Phase 02 - Daily-Use Ergonomics Implementation Plan

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make the Phase 1 tool comfortable in normal agent sessions. Agents should be able to retrieve, explore, cite, and log KB use through stable commands, while human review stays actionable and short.

This phase assumes Phase 1 is complete and should preserve all existing note files, indexes, and review queues.

## Deliverables

- Stable review item IDs stored in `review/review-items.json` and rendered into markdown queue files.
- Actionable review commands: `list`, `explain`, `accept`, `reject`, and `defer`.
- `kb explore` for broader ideation and adjacent concept retrieval.
- Usage logging, `kb log-use`, and a simple `kb usage` report.
- Search-miss logging that turns repeated poor retrieval into reviewable items.
- Agent preamble installation through `kb init --hooks` or an equivalent explicit install path.
- Citation block generation for agent-facing retrieval commands.
- Better ingest reports with clear counts, created notes, review items, duplicates, errors, and archived raw files.

## Implementation Tasks

1. Add a durable review item model.
   - Store machine-readable state in `review/review-items.json`.
   - Each item must include `id`, `queue`, `status`, `priority`, `title`, `created`, `updated`, `target_notes`, `proposed_action`, `payload`, and `history`.
   - Use deterministic IDs with queue prefix and date, for example `merge-2026-05-04-001`.
   - Import existing Phase 1 markdown-only review entries into `review-items.json` idempotently.
   - Render markdown queue files from `review-items.json` so humans can inspect them without treating markdown as the state database.

2. Implement actionable review commands.
   - `kb review list` prints high-priority items first and caps display at `review.max_review_items_per_run`.
   - `kb review explain <item-id>` prints the full item, involved note paths, proposed change, and why it exists.
   - `kb review accept <item-id>` applies the queued action when the action is phase-2 supported.
   - `kb review reject <item-id>` marks the item rejected, records a history entry, and moves rendered detail under `review/rejected/`.
   - `kb review defer <item-id> --days <n>` marks the item deferred until the target date and hides it from default review output.
   - Supported accept actions in this phase: classification selection, source append, merge-proposal body append only when explicitly approved, dispute acknowledgement, and search-miss resolution notes.

3. Tighten review behavior for a 5-minute workflow.
   - Add priority ordering: disputes, unsafe merges, classification blockers, repeated search misses, duplicates, then unsupported files.
   - Show a suggested number of items to handle now, not the full backlog.
   - Ensure all review commands are idempotent and give a clear message when an item is already accepted, rejected, or deferred.

4. Implement `kb explore`.
   - Accept `kb explore "<problem>" [--budget <tokens>] [--json]`.
   - Retrieve broader candidates than `kb context` by expanding through tags, retrieval phrases, note types, related notes, and backlinks.
   - Prefer high recall while still excluding archived, superseded, and clearly irrelevant notes.
   - Synthesize output with spec sections: directly relevant concepts, adjacent patterns, tensions/tradeoffs, possible analogies, anti-patterns to avoid, open questions, and source notes.
   - Keep `kb explore` separate from `kb context`; do not make one command a flag-only alias for the other.

5. Add usage logging.
   - Write `.kb/usage.log` as line-delimited JSON.
   - Log retrieval command, timestamp, query/task, mode, budget, returned note IDs, result count, and whether synthesis succeeded.
   - Do not log full note bodies.
   - Add `kb log-use <id> [--task "<task>"]` to record that an agent actually used or cited a note.
   - Add `kb usage [--since <duration>] [--note <id>]` to summarize retrievals, logged uses, and suspect flags.

6. Add search-miss logging.
   - Write `.kb/search-misses.log` as line-delimited JSON for zero-result, low-score, or explicitly reported poor-result searches.
   - Record command, query, filters, timestamp, result count, top score when available, and optional task.
   - Promote repeated or high-value misses into `review/search-misses.md` and `review/review-items.json`.
   - Do not create notes automatically from search misses in this phase.

7. Install the agent preamble.
   - Generate `PREAMBLE.md` using the revised text from spec section 8.2.
   - `kb init --hooks` should create or refresh the KB preamble and print explicit instructions for including it in agent sessions.
   - Do not write into external agent configuration files unless the user passes an explicit destination path supported by the implementation.
   - The preamble must direct agents to prefer `kb context`, use `kb explore` for ideation, cite sources, and call `kb log-use` when practical.

8. Add citation block generation.
   - Add `--with-citations` to `kb context`, `kb explore`, and `kb search`.
   - Include a citation block by default for `kb context` and `kb explore`; keep `kb search` concise unless the flag is passed.
   - Citation entries must include note ID, relative path, confidence, status, and title.
   - JSON output must include the same citation data as structured fields.

9. Improve ingest reports.
   - At the end of `kb ingest`, print counts for processed files, created notes, source appends, merge proposals, disputes, classification items, skipped candidates, duplicates, unsupported files, and errors.
   - Include note IDs and review item IDs in the report.
   - Support `--json` for ingest reports if the CLI already has JSON output infrastructure; otherwise keep this as a phase-2 stretch inside the same command.
   - Keep `--quiet` quiet except for errors.

10. Add tests for normal-session ergonomics.
    - Unit-test review ID allocation, review state transitions, defer date handling, usage log parsing, citation formatting, and search-miss promotion.
    - Mock-provider-test `kb explore` synthesis and broad retrieval selection.
    - CLI smoke-test review accept/reject/defer flows in a temporary KB.

## Public Interfaces

- `kb review list`
- `kb review explain <item-id>`
- `kb review accept <item-id>`
- `kb review reject <item-id>`
- `kb review defer <item-id> --days <days>`
- `kb explore "<problem>" [--budget <tokens>] [--json]`
- `kb log-use <id> [--task "<task>"]`
- `kb usage [--since <duration>] [--note <id>]`
- `kb context "<task>" [--mode <mode>] [--budget <tokens>] [--json] [--with-citations]`
- `kb search "<query>" [--topic <topic>] [--type <knowledge-type>] [--budget <tokens>] [--json] [--with-citations]`
- `kb ingest [<file>] [--force] [--quiet] [--json]`
- `kb init [--data-dir <path>] [--hooks]`

## Data/State Changes

- Adds `review/review-items.json` as the durable review state file.
- Renders review markdown queues from review state, including `pending-classification.md`, `pending-merge.md`, `disputes.md`, `search-misses.md`, and `rejected/`.
- Writes `.kb/usage.log` and `.kb/search-misses.log`.
- Updates `.kb/stats.json` with aggregated retrieval and usage counters.
- Refreshes `PREAMBLE.md`.
- May update notes only through explicit accepted review actions or direct source append behavior already allowed by Phase 1.

## Test Plan

- Unit tests for review item ID stability, review transitions, review markdown rendering, usage aggregation, search-miss thresholds, citation formatting, and preamble rendering.
- Mock-provider tests for `kb explore` broad retrieval and synthesis.
- CLI smoke tests using temporary KB directories for `review list`, `review explain`, `review accept`, `review reject`, `review defer`, `explore`, `log-use`, `usage`, citation flags, and improved ingest reports.
- Manual acceptance scenario:
  1. Start with a Phase 1 KB containing 20-50 notes and several pending review entries.
  2. Run `kb context` and `kb explore` from a realistic agent task.
  3. Include the CLI-provided citation block in an agent response.
  4. Run `kb log-use` for used notes.
  5. Handle three review items with `kb review explain` and `kb review accept/reject/defer`.
  6. Confirm the review session stays under roughly 5 minutes.

## Acceptance Criteria

- Review items have stable IDs that survive reindexing, rerendering, and repeated CLI calls.
- Review commands can explain and resolve supported item types without manual file editing.
- `kb explore` returns useful adjacent concepts and source notes distinct from high-precision `kb context`.
- Retrieval commands produce citation-ready source blocks.
- Usage and search-miss logs are written without storing full note bodies.
- Repeated missed searches become reviewable items.
- Ingest reports clearly state what happened and which note/review IDs need attention.
- A normal agent session can retrieve context, explore ideas, cite notes, log use, and keep review bounded.

## Out of Scope

- Compaction execution, stale-note flagging, orphan flagging, low-utility queues, `kb doctor`, INDEX pagination, and topic reorganization.
- Concurrent ingest lockfiles, resume, provider retry/backoff, golden corpus harness, PDF/HTML parsing, session-start hooks beyond preamble installation, cron templates, and auto-commit.
- Embeddings, additional providers, MCP wrapper, TUI/web review, privacy/redaction tooling, and multi-machine conflict helpers.
