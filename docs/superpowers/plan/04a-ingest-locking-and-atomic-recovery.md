# Phase 04a - Ingest Locking and Atomic Recovery

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make ingest safe under interruption and concurrent use. At the end of this milestone, `kb ingest` acquires a durable lock, records resumable checkpoint state, writes critical artifacts atomically, and can resume the last interrupted ingest without duplicating work.

## Depends On

- Phases 01-03 must be complete.
- Reuse ingest state, review item creation, note-writing helpers, and `kb doctor` surfaces from earlier phases.

## Deliverables

- `.kb/ingest.lock` with stale-lock detection.
- Checkpointed ingest state in `.kb/state.json`.
- `kb ingest --resume`.
- Atomic writes for notes, review state, generated indexes, and critical state files.
- Recovery diagnostics in `.kb/errors.log` and user-facing CLI output.

## Implementation Tasks

1. Add an ingest lockfile.
   - Acquire `.kb/ingest.lock` before `kb ingest` mutates raw files, notes, review queues, or generated indexes.
   - Store PID, hostname, command, started timestamp, data directory, and current raw file in the lock.
   - If another active lock exists, exit with a clear message and do not process anything.
   - If the lock is stale, report it and allow recovery only through an explicit `--force` or resume flow.
   - Always release the lock on normal completion.

2. Add ingest checkpoint and resume state.
   - Record operation checkpoints in `.kb/state.json`.
   - Track current raw file, hash, parse status, extracted candidate IDs, integration decisions, files written, archive target, and last completed stage.
   - Make writes idempotent so retrying or resuming does not duplicate notes, sources, review items, or archived raw files.
   - Add `kb ingest --resume` to continue the last interrupted ingest.
   - Add clear diagnostics when resume is impossible and manual cleanup is required.

3. Make ingest and index writes atomic.
   - Write note files, review state, generated indexes, and state files through temporary files followed by atomic rename.
   - Ensure partial FTS or index generation cannot replace a good existing index.
   - Preserve previous generated indexes if a rebuild fails.
   - Log failures to `.kb/errors.log` with operation, file, stage, and exception summary.

4. Tighten recovery diagnostics.
   - Add consistent operation IDs to ingest-related logs and messages.
   - Make error output tell the user whether to rerun, resume, run doctor, or inspect a review item.
   - Ensure `kb doctor` can later surface interrupted operations and stale locks clearly.

5. Add tests.
   - Unit-test lock acquisition and release, stale-lock detection, checkpoint serialization, idempotent resume, atomic write helpers, and failure preservation behavior.
   - CLI smoke-test interrupted ingest recovery and concurrent-ingest refusal using temporary KB directories.

## Public Interfaces

- `kb ingest [<file>] [--force] [--quiet] [--json] [--resume]`
- Existing `kb doctor [--self-test]`, now expected to report lock and interrupted-state issues once integrated

## Data/State Changes

- Adds `.kb/ingest.lock`.
- Extends `.kb/state.json` with resumable ingest checkpoints.
- Extends `.kb/errors.log` with lock, stage, and recovery diagnostics.
- Uses atomic write paths for notes, review state, and generated indexes.

## Test Plan

- Unit tests for lock and checkpoint behavior.
- CLI smoke tests for lock contention, interruption, and resume.
- Manual check: interrupt ingest mid-run, then resume and confirm no duplicate notes or review items are created.

## Acceptance Criteria

- Concurrent ingest attempts do not corrupt state.
- Interrupted ingest can resume without duplicating notes, review items, or raw-file archiving.
- Partial failures do not replace known-good generated indexes.
- User-facing recovery guidance is explicit and actionable.

## Out of Scope

- Provider retry and backoff.
- PDF and HTML parsing.
- Hook templates and auto-commit policy.
