# Phase 04d - Hook and Scheduler Templates

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete (2026-05-07)

## Goal

Provide optional automation templates without changing the default manual posture. At the end of this milestone, the KB can generate session-start and scheduled-run templates under tool-managed directories and print explicit installation guidance without modifying external tools automatically.

## Depends On

- Phases 01-04c should be complete enough that generated templates can point at stable commands.
- Reuse `kb init --hooks`, preamble or hook-generation behavior, and `kb doctor` guidance from earlier phases.

## Deliverables

- Optional hook templates under a documented `.kb/` subdirectory.
- Session-start template for bounded health or retrieval checks.
- Scheduler templates for periodic ingest, reindex, and doctor runs.
- Explicit installation instructions without automatic registration.

## Implementation Tasks

1. Add session-start hook templates.
   - Generate optional templates under the KB data directory, for example `.kb/hooks/`.
   - Include a session-start template that runs a bounded health or retrieval check suitable for agent startup.
   - Keep hooks disabled by default through `hooks.session_start_ingest: false`.
   - `kb init --hooks` may create templates and print installation instructions, but must not silently install into external tools.

2. Add scheduler templates.
   - Generate optional cron or system-scheduler templates under `.kb/hooks/` or another documented generated-template directory.
   - Include examples for periodic `kb ingest`, `kb reindex --scan-clusters`, and `kb doctor`.
   - Templates must use explicit data-directory arguments and conservative logging.
   - Do not register scheduled jobs automatically.

3. Preserve conservative defaults.
   - Keep all automation templates opt-in and non-executing until the user installs them.
   - Avoid enabling session-start ingest or scheduler-driven mutation by default.
   - Make generated instructions explicit about review, logging, and operational caution.

4. Tighten template rendering and diagnostics.
   - Ensure template paths, command examples, and comments are deterministic.
   - Surface any template-generation failures through normal error reporting.
   - Keep generated output compatible with the documented supported environments, or clearly label it as example-only.

5. Add tests.
   - Unit-test template rendering, idempotent regeneration, and config-controlled defaults.
   - CLI smoke-test `kb init --hooks` and template generation in temporary KB directories.

## Public Interfaces

- `kb init [--data-dir <path>] [--hooks]`
- Config additions for hook or template generation behavior as needed

## Data/State Changes

- Adds optional generated hook and scheduler templates under a documented `.kb/` subdirectory.
- May update `.kb/config.yaml` only if explicit persisted hook-template settings are needed.
- Does not install anything into external tools automatically.

## Test Plan

- Unit tests for deterministic template generation.
- CLI smoke tests for `kb init --hooks` behavior in temporary KB directories.
- Manual check: generate templates and confirm the printed instructions are explicit and non-destructive.

## Acceptance Criteria

- Hook and scheduler templates are available on request.
- Template generation is idempotent and conservative.
- No external tool configuration is modified automatically.
- Generated guidance makes the disabled-by-default posture clear.

## Out of Scope

- Auto-registration of hooks or scheduled jobs.
- Auto-commit policy.
- Parser and retry logic beyond any commands referenced by templates.

## Completion Record (2026-05-07)

- Implemented files:
  - `src/kb_librarian/init.py`
  - `src/kb_librarian/cli.py`
  - `tests/test_init.py`
  - `tests/test_cli.py`
  - `docs/command-reference.md`
  - `docs/user-guide.md`
  - `docs/configuration.md`
- Verification:
  - `pytest -q tests/test_init.py tests/test_cli.py`
  - Result: `30 passed`
