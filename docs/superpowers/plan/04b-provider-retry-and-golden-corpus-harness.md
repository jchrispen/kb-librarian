# Phase 04b - Provider Retry and Golden Corpus Harness

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Make provider-backed behavior more resilient and easier to regression-test. At the end of this milestone, transient provider failures retry with bounded backoff, permanent failures remain non-destructive, and a deterministic golden corpus harness catches extraction and retrieval regressions.

## Depends On

- Phase 04a must be complete.
- Reuse provider abstractions, mock-provider fixtures, retrieval commands, and resumed ingest state from earlier phases.

## Deliverables

- Configurable retry and backoff around provider calls.
- Retry and final-failure diagnostics in `.kb/errors.log`.
- Golden corpus fixtures under the repository test tree.
- Deterministic golden corpus test commands using the mock provider.
- Optional real-provider golden runs gated behind explicit opt-in.

## Implementation Tasks

1. Add retry and backoff for provider failures.
   - Wrap provider calls with configurable max attempts, base delay, max delay, and jitter.
   - Retry transient failures such as timeouts, rate limits, and 5xx responses.
   - Do not retry deterministic prompt or schema validation failures without changing input.
   - Record retry counts and final failures in `.kb/errors.log`.

2. Preserve recoverable ingest behavior on final failure.
   - Ensure provider failure during ingest leaves enough checkpoint state for `kb ingest --resume`.
   - Keep retry behavior from duplicating notes, review items, or raw-file movement.
   - Surface whether the failure was retried and why it finally stopped.

3. Add a golden corpus harness.
   - Create a small representative corpus under the test tree, not in the user's default KB.
   - Include markdown and txt inputs, expected note counts, expected note types, expected retrieval phrases, expected source IDs for sample queries, and expected citation presence.
   - Run with the mock provider for deterministic CI tests.

4. Add optional real-provider golden runs.
   - Allow a real-provider golden run only when credentials and an explicit opt-in flag are present.
   - Keep the default test suite offline and deterministic.
   - Separate expected behavior from performance-sensitive or non-deterministic observations.

5. Tie regression checks to the core product question.
   - Measure regressions around whether `kb context` helps an agent with fewer tokens.
   - Include `kb explore` breadth and citation presence where the corpus can check them deterministically.
   - Keep failures actionable rather than relying on vague quality impressions.

6. Add tests.
   - Unit-test retry classification, backoff parameter handling, final-failure persistence, and resume-after-provider-failure behavior.
   - Mock-provider-test the golden corpus harness for extraction and retrieval expectations.
   - Add any lightweight CLI entry or test runner smoke checks needed to execute the corpus locally.

## Public Interfaces

- Existing provider-backed commands, now with retry behavior
- Config additions for provider retry and backoff settings
- Test harness interfaces for golden corpus runs

## Data/State Changes

- Extends `.kb/errors.log` with retry counts and failure classifications.
- May extend `.kb/state.json` checkpoints with retry or provider-attempt metadata where useful for resume.
- Adds golden corpus fixtures and expectations under the repository test tree.

## Test Plan

- Unit tests for retry classification and failure persistence.
- Mock-provider tests for deterministic golden corpus expectations.
- Manual check: simulate a transient provider error, confirm retry or resume behavior, then run the corpus harness.

## Acceptance Criteria

- Transient provider failures retry with bounded backoff.
- Permanent failures remain non-destructive and resumable.
- Golden corpus checks catch extraction or retrieval regressions deterministically.
- Default regression testing remains offline unless the user explicitly opts into live-provider checks.

## Out of Scope

- PDF and HTML parser support.
- Hook and scheduler templates.
- Auto-commit policy.
