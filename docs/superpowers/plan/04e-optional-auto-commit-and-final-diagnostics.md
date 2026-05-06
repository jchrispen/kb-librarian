# Phase 04e - Optional Auto-Commit and Final Diagnostics

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

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

## Out of Scope

- New Phase 5 provider or retrieval features.
- Autonomous mutation without review.
- Automatic installation of external automation tools.
