# Phase 06e - Auth Policy, Doctor, and Security Polish

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete

## Goal

Harden provider auth behavior after the new backends and vendor CLI delegation modes land by making precedence, diagnostics, masking, and user guidance consistent across all supported providers.

## Depends On

- Phase 06b complete.
- Phase 06c complete.
- Phase 06d complete.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4`
- Reasoning effort: `medium`
- Why: this is primarily a consistency and hardening milestone, but it still touches security-sensitive surfaces and final user-visible behavior.

## Deliverables

- Consistent credential precedence rules across providers.
- Secret masking for all new auth material in logs, diagnostics, subprocess command rendering, and rendered config/reporting surfaces.
- Improved `kb doctor` findings for auth state, backend compatibility, and remediation.
- Final documentation pass across README, configuration, commands, and troubleshooting.

## Implementation Tasks

1. Finalize credential precedence.
   - Define and document exactly how API-key, vendor CLI delegation, token environment variables, and local-backend auth choices interact.
   - Ensure no silent fallback hides broken auth state.

2. Harden secret handling.
   - Audit logs, error rendering, doctor output, subprocess argv/env reporting, and debug paths for token leakage.
   - Add targeted masking and regression tests.

3. Improve doctor coverage.
   - Ensure users can tell whether the problem is config, missing CLI binary, credentials, expired login, endpoint compatibility, backend reachability, or model availability.

4. Verify delegation safety.
   - Ensure Claude Code and Codex CLI delegation adapters use completion-only prompts, no model-initiated tools, bounded timeouts, and explicit working directories.
   - Ensure subprocess errors are normalized into provider errors without leaking sensitive env values or token paths.

5. Final docs pass.
   - Update setup examples, migration notes, and troubleshooting guidance for all supported providers and auth modes.

## Public Interfaces

- CLI command interfaces remain unchanged.
- User-visible diagnostics become more specific and more consistent.

## Data/State Changes

- No intended artifact-format changes beyond config validation and diagnostics.
- Error/reporting surfaces may include richer non-secret auth metadata.

## Test Plan

- Regression coverage for secret masking and auth precedence.
- Doctor tests for final auth/backend diagnostic matrix.
- CLI smoke tests covering representative successful and failing configurations.

## Acceptance Criteria

- Auth precedence is documented, deterministic, and covered by tests.
- New auth modes do not leak sensitive material through normal CLI workflows.
- Vendor CLI delegation cannot mutate the KB or workspace through model-initiated tools.
- Users have a single coherent troubleshooting path for local backends and cloud auth modes.

## Out of Scope

- New provider families beyond those added earlier in Phase 6.
- Non-auth-related product changes.

## Completion Record

Completed on 2026-05-09.

Implemented files:

- `src/kb_librarian/providers.py`
- `src/kb_librarian/doctor.py`
- `tests/test_providers.py`
- `tests/test_doctor.py`
- `README.md`
- `docs/configuration.md`
- `docs/troubleshooting.md`
- `docs/superpowers/plan/06-provider-backends-and-account-auth.md`
- `docs/superpowers/plan/06e-auth-policy-doctor-and-security-polish.md`

Verification:

- `python3 -m pytest --override-ini addopts='' tests/test_providers.py tests/test_doctor.py -q` - passed, 67 passed.
- `python3 -m pytest --override-ini addopts='' tests/test_config.py tests/test_cli.py tests/test_providers.py tests/test_doctor.py -q` - passed, 118 passed.
