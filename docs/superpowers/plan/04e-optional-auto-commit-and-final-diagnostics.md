# Phase 04e - Optional Auto-Commit and Final Diagnostics

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-07

## Goal

Add explicit automation around git commits without weakening the default trust posture, and finish the remaining diagnostic polish across recovery flows. At the end of this milestone, users can opt into carefully scoped auto-commit behavior, and the system reports recovery, dependency, and automation issues clearly.

## Depends On

- Phases 04a through 04d should be complete.
- Reuse clean-worktree checks, mutation flows, hook generation, `kb doctor`, and recovery diagnostics from earlier phases.

## Deliverables

- Optional auto-commit policy controlled by config and off by default.
- Clear commit-message conventions for successful automated operations.
- Expanded `kb doctor` diagnostics for stale locks, interrupted operations, parser dependencies, and auto-commit configuration issues.
- Final consistency pass on operation IDs and recovery messaging.

## Implementation Tasks

1. Add optional auto-commit policy.
   - Keep `git.auto_commit: false` as the default.
   - When enabled, commit note or review changes after successful ingest, accepted review mutation, compaction, or topic reorganization.
   - Do not commit generated index-only changes unless the config explicitly opts in.
   - Refuse auto-commit when the worktree has unrelated changes unless the config explicitly allows it.
   - Use clear commit messages that identify operation type and affected note or review IDs.

2. Keep auto-commit safety conservative.
   - Never auto-commit failed or partially completed operations.
   - Keep review-gated mutations review-gated; auto-commit may only run after success, not replace acceptance.
   - Make it explicit when auto-commit was skipped due to policy or worktree state.

3. Expand diagnostics and doctor coverage.
   - Ensure `kb doctor` reports stale locks, interrupted operations, failed parser dependencies, and auto-commit configuration issues.
   - Add or preserve consistent operation IDs across ingest, review mutations, compaction, and topic reorganization logs.
   - Keep error output explicit about whether the next action is rerun, resume, doctor, or manual inspection.

4. Tighten configuration and reporting.
   - Add any needed config seam for commit selection, unrelated-change policy, and generated-artifact commit behavior.
   - Keep the defaults local-first and low-surprise.
   - Ensure logs and CLI output tell the user when an auto-commit happened and what it included.

5. Add tests.
   - Unit-test auto-commit policy decisions, commit-message selection, unrelated-change refusal, and doctor reporting for automation issues.
   - CLI smoke-test auto-commit in a temporary git-backed KB and confirm disabled-by-default behavior when the feature is not enabled.

## Public Interfaces

- Existing successful mutation commands, now optionally followed by auto-commit when configured
- Config additions for `git.auto_commit` and related policy controls
- `kb doctor [--self-test]`, now with expanded recovery and automation diagnostics

## Data/State Changes

- May create git commits only when auto-commit is explicitly enabled.
- Extends `.kb/errors.log` and diagnostic surfaces with automation-related reporting.
- May extend config handling for commit policy details.

## Test Plan

- Unit tests for commit-policy logic and diagnostic output.
- CLI smoke tests in temporary git-backed KB directories.
- Manual check: enable auto-commit in a temporary KB, run a successful operation, and confirm only intended changes are committed.

## Acceptance Criteria

- Auto-commit remains off by default and only commits intended changes when explicitly configured.
- Worktree-safety rules prevent noisy or unsafe automatic commits.
- `kb doctor` reports recovery and automation issues clearly.
- Operation and recovery messages are consistent across robustness flows.

## Completion Record

Implemented files:

- `src/kb_librarian/git_auto.py`
- `src/kb_librarian/cli.py`
- `src/kb_librarian/config.py`
- `src/kb_librarian/doctor.py`
- `tests/test_git_auto.py`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_doctor.py`
- `README.md`
- `docs/command-reference.md`
- `docs/configuration.md`
- `docs/troubleshooting.md`
- `docs/user-guide.md`

Behavior shipped:

- Added guarded `git.auto_commit` support with scoped policy keys for ingests, reviews, reindexes, topic reorganizations, and unrelated-change handling.
- Wired successful ingest/add, review accept, compaction proposal, reindex, and topic mutation/proposal commands to optional post-operation commits.
- Added conservative worktree checks, operation-oriented commit messages, CLI/JSON auto-commit reporting, and `.kb/errors.log` automation diagnostics.
- Expanded `kb doctor` with parser dependency and auto-commit configuration findings.
- Documented auto-commit configuration, command behavior, doctor coverage, and troubleshooting.

Verification:

- `pytest -q tests/test_git_auto.py tests/test_config.py tests/test_doctor.py tests/test_cli.py::test_kb_ingest_mock_provider_smoke tests/test_cli.py::test_kb_ingest_auto_commit_smoke` - passed, 19 passed.
- `pytest -q` - passed, 143 passed, 3 skipped.

Implementation commit:

- `4272a8f Implement optional KB auto-commit policy`

## Out of Scope

- New Phase 5 provider or retrieval features.
- Autonomous mutation without review.
- Automatic installation of external automation tools.
