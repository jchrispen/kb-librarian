---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Robustness Patterns: Atomic Writes, PID Locks, and Checkpoint Resume

## Context

Ingest and compaction operations mutate KB state across multiple files and the SQLite index. Interrupted runs (power loss, kill signal, agent timeout) left the KB in partial states that were difficult to detect and recover from. Provider transient failures caused entire ingest runs to fail rather than retry.

## Decision

Three patterns apply across all mutation operations:

1. **Atomic writes everywhere.** All note, review, index, and state file writes use write-to-temp-then-rename (`atomic.py`). No partial write is ever visible to a concurrent reader.

2. **PID-stamped lockfile for ingest.** Ingest acquires `.kb/ingest.lock` (containing the PID) before mutating any state. `kb ingest --resume` reads the checkpoint at `.kb/state.json` and replays from the last completed file without duplicating notes, review items, or archived raw files. Stale lock auto-recovery without explicit `--force` or `--resume` is forbidden.

3. **Checkpoint-based resume.** `.kb/state.json` tracks per-file ingest progress. On resume, idempotency is enforced: all writes downstream of the checkpoint are content-addressed or guard-checked before writing. Failed SQLite index rebuilds preserve the previous good index.

Provider transient failures use configurable retry/backoff (`provider_retry.py`); permanent failures leave recoverable state and report via `kb doctor`.

## Alternatives considered

- **Stale lock auto-recovery** — rejected: auto-deleting a lock without knowing whether the previous process is truly dead risks concurrent mutation. Explicit `--force` or `--resume` is required.
- **Partial index replacement on failed rebuild** — rejected: a partial index produces subtly wrong search results that are harder to detect than a missing index. The previous good index is always preserved on rebuild failure.
- **Single-pass ingest (no checkpoint)** — rejected: long ingest runs over large document sets fail silently at the end; checkpoint-based resume makes interruption safe.

## Consequences

- Operation IDs must be attached to all ingest-related log entries so recovery guidance is actionable.
- `kb doctor` must surface stale locks and interrupted checkpoints as a Recovery subsystem finding with a specific remediation step.
- Resume idempotency requires all writes downstream of the checkpoint to be content-addressed or guard-checked before writing.
