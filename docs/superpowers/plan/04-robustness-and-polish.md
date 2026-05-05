# Phase 04 - Robustness and Polish Implementation Plan

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make the MVP resilient enough for ongoing use. Interrupted operations should recover cleanly, transient provider failures should not corrupt state, representative corpus checks should catch retrieval regressions, and optional automation should be available without changing the default local-first behavior.

This phase assumes Phases 1-3 are complete.

## Deliverables

- Concurrent ingest lockfile with stale-lock handling and clear user-facing errors.
- Resume behavior for interrupted ingest operations.
- Retry/backoff for transient provider failures.
- Golden corpus harness for extraction and retrieval regression checks.
- PDF and HTML parsing without OCR or browser automation.
- Session-start hook templates and cron templates that are off by default.
- Optional auto-commit policy controlled by config and disabled by default.
- Polish for atomic writes, error reporting, and recovery diagnostics where needed by the above work.

## Implementation Tasks

1. Add an ingest lockfile.
   - Acquire `.kb/ingest.lock` before `kb ingest` mutates raw files, notes, review queues, or generated indexes.
   - Store PID, hostname, command, started timestamp, data directory, and current raw file in the lock.
   - If another active lock exists, exit with a clear message and do not process anything.
   - If the lock is stale, report it and allow recovery through an explicit `--force` or resume flow.
   - Always release the lock on normal completion.

2. Add ingest checkpoint and resume state.
   - Record operation checkpoints in `.kb/state.json`.
   - Track current raw file, hash, parse status, extracted candidate IDs, integration decisions, files written, archive target, and last completed stage.
   - Make writes idempotent so retrying or resuming does not duplicate notes, sources, review items, or archived raw files.
   - Add `kb ingest --resume` to continue the last interrupted ingest.
   - Add clear diagnostics when resume is impossible and manual cleanup is required.

3. Make ingest and index writes atomic.
   - Write note files, review state, generated indexes, and state files through temporary files followed by atomic rename.
   - Ensure partial FTS/index generation cannot replace a good existing index.
   - Preserve previous generated indexes if a rebuild fails.
   - Log failures to `.kb/errors.log` with operation, file, stage, and exception summary.

4. Add retry/backoff for provider failures.
   - Wrap provider calls with configurable max attempts, base delay, max delay, and jitter.
   - Retry transient failures such as timeouts, rate limits, and 5xx responses.
   - Do not retry deterministic prompt/schema validation failures without changing input.
   - Record retry counts and final failures in `.kb/errors.log`.
   - Ensure provider failure during ingest leaves enough checkpoint state for `kb ingest --resume`.

5. Add a golden corpus harness.
   - Create a small representative corpus under the test tree, not in the user's default KB.
   - Include markdown and txt inputs, expected note counts, expected note types, expected retrieval phrases, expected source IDs for sample queries, and expected citation presence.
   - Run with the mock provider for deterministic CI tests.
   - Allow an optional real-provider golden run that is skipped unless credentials and an explicit opt-in flag are present.
   - Measure regressions around the core question: whether `kb context` helps an agent with fewer tokens.

6. Add PDF parsing.
   - Support `.pdf` files in `kb ingest`.
   - Extract text with a maintained local parser library.
   - Preserve page numbers in source metadata when available.
   - Do not implement image OCR in this phase.
   - If text extraction is empty or fails, leave the file in `raw/`, log the error, and surface a review item.

7. Add HTML parsing.
   - Support `.html` and `.htm` files in `kb ingest`.
   - Extract visible text, headings, title, links, and source URL metadata when present.
   - Ignore scripts, styles, navigation boilerplate, and hidden elements where the parser can identify them.
   - Preserve enough structure for extraction prompts to distinguish headings from body text.
   - Do not fetch remote pages; parse local files only.

8. Add session-start hook templates.
   - Generate optional templates under the KB data directory, for example `.kb/hooks/`.
   - Include a session-start template that runs a bounded health/retrieval check suitable for agent startup.
   - Keep hooks disabled by default through `hooks.session_start_ingest: false`.
   - `kb init --hooks` may create templates and print installation instructions, but must not silently install into external tools.

9. Add cron templates.
   - Generate optional cron/system scheduler templates under `.kb/hooks/` or another documented generated-template directory.
   - Include examples for periodic `kb ingest`, `kb reindex --scan-clusters`, and `kb doctor`.
   - Templates must use explicit data-directory arguments and conservative logging.
   - Do not register cron jobs automatically.

10. Add optional auto-commit policy.
    - Keep `git.auto_commit: false` as the default.
    - When enabled, commit note/review changes after successful ingest, accepted review mutation, compaction, or topic reorganization.
    - Do not commit generated index-only changes unless the config explicitly opts in.
    - Refuse auto-commit when the worktree has unrelated changes unless the config explicitly allows it.
    - Use clear commit messages that identify operation type and affected note/review IDs.

11. Polish recovery and diagnostics.
    - Add consistent operation IDs to ingest, review mutations, compaction, and topic reorganization logs.
    - Make error output tell the user whether to rerun, resume, run doctor, or inspect a review item.
    - Ensure `kb doctor` reports stale locks, interrupted operations, failed parser dependencies, and auto-commit configuration issues.
    - Keep defaults conservative: no automation, no auto-commit, no external hook installation.

12. Add robustness tests.
    - Unit-test lock acquisition/release, stale-lock detection, checkpoint serialization, idempotent resume, retry classification, parser failure handling, and auto-commit policy decisions.
    - CLI smoke-test interrupted ingest recovery using temporary KB directories.
    - Golden-corpus-test representative extraction and retrieval behavior.
    - Parser-test PDF and HTML samples with deterministic expected text snippets and source metadata.

## Public Interfaces

- `kb ingest [<file>] [--force] [--quiet] [--json] [--resume]`
- `kb init [--data-dir <path>] [--hooks]`
- `kb doctor [--self-test]`
- Existing retrieval and review commands, now with stronger recovery guarantees.
- Config additions for provider retry/backoff, parser behavior, hook templates, and optional git auto-commit.

No Phase 4 interface should enable automation by default.

## Data/State Changes

- Adds `.kb/ingest.lock`.
- Extends `.kb/state.json` with resumable operation checkpoints.
- Extends `.kb/errors.log` with retry, parser, lock, resume, and auto-commit diagnostics.
- Adds optional generated hook and cron templates under a documented `.kb/` subdirectory.
- Adds optional golden corpus fixtures under the repository test tree.
- May add parser source metadata to note `sources`.
- May create git commits only when auto-commit is explicitly enabled.

## Test Plan

- Unit tests for lockfile semantics, stale-lock handling, checkpoint/resume idempotency, atomic write helpers, retry/backoff classification, PDF/HTML parser output, and auto-commit policy.
- Mock-provider tests for retries, permanent failures, resume after provider failure, and golden corpus extraction/retrieval.
- CLI smoke tests using temporary KB directories for concurrent ingest refusal, stale-lock recovery, interrupted ingest resume, parser failures, PDF ingest, HTML ingest, hook template generation, and disabled-by-default automation.
- Manual acceptance scenario:
  1. Start an ingest, interrupt it after extraction or integration, then run `kb ingest --resume`.
  2. Simulate a transient provider failure and confirm retry/backoff occurs without duplicate notes.
  3. Ingest one PDF and one local HTML file.
  4. Run the golden corpus harness.
  5. Generate hook and cron templates.
  6. Enable auto-commit in a temporary git-backed KB and confirm only intended successful operations are committed.

## Acceptance Criteria

- Concurrent ingest attempts do not corrupt raw files, notes, review state, or indexes.
- Interrupted ingest can resume without duplicate notes, duplicate review items, or lost raw files.
- Transient provider failures retry with backoff and leave recoverable state on final failure.
- Golden corpus checks catch extraction or retrieval regressions.
- PDF and local HTML files can be ingested, with failures surfaced cleanly when parsing is impossible.
- Hook and cron templates are available but not automatically installed or enabled.
- Auto-commit remains off by default and only commits intended changes when explicitly configured.
- `kb doctor` reports recovery problems clearly.

## Out of Scope

- Phase 5+ backlog: additional LLM providers, local provider support, embedding-based retrieval, MCP wrapper, TUI/web review interface, privacy/redaction tooling, and multi-machine conflict helpers.
- Image OCR for PDFs.
- Remote web fetching for HTML ingestion.
- Autonomous knowledge mutation without review.
