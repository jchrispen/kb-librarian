# Phase 02e - Preamble Install, Ingest Reports, and Session Proof

Status: Complete
Completed: 2026-05-06

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Finish the Phase 2 ergonomics loop. At the end of this milestone, the KB can install or refresh agent-facing preamble guidance, `kb ingest` reports are operationally clear, and the phase has an end-to-end proof that a normal agent session can retrieve, explore, cite, log use, and keep review short.

## Depends On

- Phase 02b, 02c, and 02d should be complete.
- Reuse `kb init`, ingest flow, retrieval commands, usage logging, and review state from earlier milestones.

## Deliverables

- `kb init --hooks` that creates or refreshes `PREAMBLE.md`.
- Revised `PREAMBLE.md` text aligned with spec section 8.2.
- Explicit CLI instructions for including the preamble in agent sessions.
- Improved `kb ingest` reports with note IDs, review item IDs, and outcome counts.
- Phase 2 manual acceptance record.

## Implementation Tasks

1. Install the revised agent preamble.
   - Generate `PREAMBLE.md` using the revised text from spec section 8.2.
   - Make `kb init --hooks` create or refresh the preamble.
   - Print explicit instructions for including it in agent sessions.
   - Do not write into external agent configuration files unless the implementation adds an explicit destination path that is intentionally supported.

2. Preserve safe init behavior.
   - Keep `kb init` idempotent.
   - Refresh only tool-managed preamble content when the user requests hooks installation.
   - Avoid overwriting unrelated user-authored files.

3. Improve ingest reports.
   - Print counts for processed files, created notes, source appends, merge proposals, disputes, classification items, skipped candidates, duplicates, unsupported files, and errors.
   - Include note IDs and review item IDs in the report.
   - Support `--json` for ingest reports if the CLI already has compatible JSON output infrastructure; otherwise implement it here as part of the milestone.
   - Keep `--quiet` quiet except for errors.

4. Tighten report consistency.
   - Make report fields stable across create/append/merge/dispute/classification outcomes.
   - Ensure durable review IDs appear anywhere review work is surfaced.
   - Keep human-readable and JSON output aligned.

5. Run the Phase 2 manual acceptance scenario.
   - Start with a Phase 1 or Phase 2 KB containing 20-50 notes and several pending review items.
   - Run `kb context` and `kb explore` for a realistic agent task.
   - Include the CLI-provided citation block in an agent response.
   - Run `kb log-use` for used notes.
   - Resolve at least three review items through `kb review explain` and `kb review accept/reject/defer`.
   - Confirm the review session stays under roughly 5 minutes.
   - Record the commands and observed results in the milestone completion notes or PR description.

6. Add tests.
   - Unit-test preamble rendering, hook-install idempotency, ingest report formatting, and JSON/human output parity.
   - CLI smoke-test `kb init --hooks` and improved `kb ingest` reports in temporary KB directories.

## Public Interfaces

- `kb init [--data-dir <path>] [--hooks]`
- `kb ingest [<file>] [--force] [--quiet] [--json]`

All earlier Phase 2 interfaces should continue to work.

## Data/State Changes

- Refreshes `PREAMBLE.md`.
- May update `.kb/config.yaml` only if hook-related configuration needs explicit persisted state.
- Improves ingest output and may write durable review IDs alongside normal ingest outcomes.
- Does not introduce new canonical note mutation paths beyond previously accepted workflows.

## Test Plan

- Unit tests for preamble and report rendering.
- CLI smoke tests for hook install and ingest reporting.
- Manual end-to-end acceptance covering retrieval, exploration, citation, usage logging, and review resolution.

## Acceptance Criteria

- `kb init --hooks` refreshes `PREAMBLE.md` and prints usable session guidance.
- `kb ingest` reports clearly state what happened and which note/review IDs need attention.
- Human-readable and JSON ingest outputs stay aligned.
- The phase-level manual scenario demonstrates a usable normal agent session with bounded review.

## Completion Record

Completed on 2026-05-06.

Implemented files:

- `src/kb_librarian/init.py`
- `src/kb_librarian/ingest.py`
- `src/kb_librarian/cli.py`
- `tests/test_init.py`
- `tests/test_ingest.py`
- `tests/test_cli.py`
- `README.md`
- `docs/commands.md`
- `docs/configuration.md`
- `docs/user-guide.md`

Verification:

- `python3 -m pytest tests/test_init.py tests/test_ingest.py tests/test_cli.py` - passed, 29 tests.
- `python3 -m pytest` - passed, 76 tests.
- Phase 2 acceptance proof in `/tmp/kb-02e-acceptance-juo5w2x2` - passed with 20 seed notes, `kb init --hooks`, JSON ingest reports for `classification-2026-05-06-001` and `duplicate-2026-05-06-001`, `kb context`, `kb explore`, citation block reuse, `kb log-use 2026-05-06-agent-context-retrieval-pattern-02`, and three explained review resolutions: accepted `classification-2026-05-06-001`, accepted `searchmiss-2026-05-06-001`, and rejected `duplicate-2026-05-06-001`. Review resolution elapsed time was 1.33 seconds; final `kb review` reported `Review items: 0`.

## Out of Scope

- Session-start automation beyond explicit preamble installation.
- Cron templates, auto-commit, retry/backoff, or other Phase 4 work.
- Compaction or Phase 3 hygiene queues.
