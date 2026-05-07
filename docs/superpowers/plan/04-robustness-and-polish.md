# Phase 04 - Robustness and Polish

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make the MVP resilient enough for ongoing use. Interrupted operations should recover cleanly, transient provider failures should not corrupt state, representative corpus checks should catch retrieval regressions, and optional automation should be available without changing the default local-first behavior.

Phase 4 assumes Phases 1-3 are complete.

## Milestone Status

| Milestone | Status | Notes |
|---|---|---|
| [04a - Ingest Locking and Atomic Recovery](04a-ingest-locking-and-atomic-recovery.md) | Complete | Added ingest lockfiles, resumable checkpoints, atomic writes, doctor recovery diagnostics, and tests. |
| [04b - Provider Retry and Golden Corpus Harness](04b-provider-retry-and-golden-corpus-harness.md) | Complete | Added configurable retry/backoff, provider failure diagnostics, deterministic golden corpus harness, and opt-in live-provider smoke checks. |
| [04c - PDF and HTML Ingest Parsing](04c-pdf-and-html-ingest-parsing.md) | Complete | Added local PDF and HTML parsing with parser failure review surfacing and source metadata. |
| [04d - Hook and Scheduler Templates](04d-hook-and-scheduler-templates.md) | Next | Adds optional session-start and scheduled-run templates without installing automation by default. |
| [04e - Optional Auto-Commit and Final Diagnostics](04e-optional-auto-commit-and-final-diagnostics.md) | Pending | Adds guarded auto-commit policy and phase-level diagnostic polish across recovery flows. |

## Milestone Order

1. [04a - Ingest Locking and Atomic Recovery](04a-ingest-locking-and-atomic-recovery.md) - complete
   - Prevents concurrent ingest corruption and makes interrupted operations recoverable.
2. [04b - Provider Retry and Golden Corpus Harness](04b-provider-retry-and-golden-corpus-harness.md) - complete
   - Adds transient-failure resilience and regression checks around core extraction and retrieval behavior.
3. [04c - PDF and HTML Ingest Parsing](04c-pdf-and-html-ingest-parsing.md) - complete
   - Extends local ingest coverage to richer source formats while preserving inspectable failure modes.
4. [04d - Hook and Scheduler Templates](04d-hook-and-scheduler-templates.md) - next
   - Generates optional automation templates while keeping the default posture conservative and manual.
5. [04e - Optional Auto-Commit and Final Diagnostics](04e-optional-auto-commit-and-final-diagnostics.md) - pending
   - Adds explicit automation policy for commits and closes recovery gaps in user-facing diagnostics.

Each milestone should leave the CLI runnable and covered by focused tests. Later milestones may adjust earlier code, but they should preserve accepted Phase 1-3 behavior and the public interfaces already shipped in earlier Phase 4 milestones.

## Phase Deliverables

- Concurrent ingest lockfile with stale-lock handling and clear user-facing errors.
- Resume behavior for interrupted ingest operations.
- Retry/backoff for transient provider failures.
- Golden corpus harness for extraction and retrieval regression checks.
- PDF and HTML parsing without OCR or browser automation.
- Session-start hook templates and cron templates that are off by default.
- Optional auto-commit policy controlled by config and disabled by default.
- Polish for atomic writes, error reporting, and recovery diagnostics where needed by the above work.

## Final Acceptance Criteria

- Concurrent ingest attempts do not corrupt raw files, notes, review state, or indexes.
- Interrupted ingest can resume without duplicate notes, duplicate review items, or lost raw files.
- Transient provider failures retry with backoff and leave recoverable state on final failure.
- Golden corpus checks catch extraction or retrieval regressions.
- PDF and local HTML files can be ingested, with failures surfaced cleanly when parsing is impossible.
- Hook and cron templates are available but not automatically installed or enabled.
- Auto-commit remains off by default and only commits intended changes when explicitly configured.
- `kb doctor` reports recovery problems clearly.

## Phase Test Strategy

- Unit tests for lock and resume semantics, atomic write helpers, retry classification, parser output, template generation, and auto-commit policy decisions.
- Mock-provider tests for retries, permanent failures, resume after provider failure, and golden corpus extraction and retrieval.
- CLI smoke tests using temporary KB directories for interrupted ingest recovery, parser failures, template generation, and disabled-by-default automation.
- Manual acceptance with interrupted ingest, transient provider failures, sample PDF and HTML files, generated templates, and optional auto-commit in a temporary git-backed KB.

## Out of Scope

- Phase 5+ backlog: additional LLM providers, local provider support, embedding-based retrieval, MCP wrapper, TUI/web review interface, privacy/redaction tooling, and multi-machine conflict helpers.
- Image OCR for PDFs.
- Remote web fetching for HTML ingestion.
- Autonomous knowledge mutation without review.
