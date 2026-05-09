# Phase 05e - Privacy and Redaction Controls

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete

Completed: 2026-05-07

## Goal

Add first-pass privacy controls for provider-backed operations: validate privacy config, block cloud provider attempts when policy disallows them, redact configured sensitive patterns from provider-bound payloads, and make privacy posture visible in diagnostics.

## Depends On

- Phase 05c complete.
- Existing provider policy execution path.

## Deliverables

- Privacy config validation for cloud allowance, blocked topics, redaction regexes, and future confirmation seam.
- Runtime cloud-provider gating for provider-backed calls.
- Provider-bound payload redaction for ingest, context/explore synthesis, and compaction proposal drafting.
- Doctor diagnostics for cloud routes that are blocked by privacy policy.
- User-facing docs and focused tests.

## Implementation Tasks

1. Validate privacy config.
   - Ensure boolean cloud-policy keys are booleans.
   - Ensure blocked topics and redaction patterns are lists of strings.
   - Reject invalid redaction regexes.

2. Enforce provider privacy.
   - Treat Anthropic and Codex as cloud providers.
   - Block cloud provider attempts when `privacy.cloud_llm_allowed` is false.
   - Block cloud provider attempts for configured `privacy.blocked_topics` when the operation has note-topic context.

3. Redact provider payloads.
   - Apply `privacy.redact_patterns` to provider-bound text before LLM calls.
   - Do not mutate canonical notes, raw files, review state, or indexes as a side effect of redaction.

4. Add diagnostics and docs.
   - Warn in `kb doctor` when cloud routes are configured but cloud LLMs are disabled.
   - Document scope and troubleshooting behavior.

## Public Interfaces

- Config key `privacy.require_confirmation_for_cloud_llm` is added as a validated seam but no interactive confirmation flow is implemented.
- Existing commands are unchanged; behavior is controlled by config.

## Data/State Changes

- No canonical note or review schema changes.
- Redaction applies only to provider-bound payloads.

## Test Plan

- Unit tests for privacy validation and redaction.
- Provider-policy tests for cloud blocking before provider construction.
- Ingest integration test proving provider text is redacted without mutating archived raw content.
- Doctor test for cloud-route privacy warning.

## Acceptance Criteria

- Invalid privacy config fails validation with clear errors.
- Cloud provider calls are blocked when config disallows them.
- Configured redaction patterns are applied before provider calls.
- Existing workflows remain stable with default privacy config.

## Out of Scope

- Interactive confirmation prompts before cloud LLM calls.
- Local-only note metadata and routing.
- Privacy review workflows.
- Full secret scanning or policy inference.

## Completion Record

Completed on 2026-05-07.

Implemented files:

- `src/kb_librarian/privacy.py`
- `src/kb_librarian/config.py`
- `src/kb_librarian/providers.py`
- `src/kb_librarian/ingest.py`
- `src/kb_librarian/context.py`
- `src/kb_librarian/compaction.py`
- `src/kb_librarian/doctor.py`
- `tests/test_privacy.py`
- `tests/test_config.py`
- `tests/test_providers.py`
- `tests/test_ingest.py`
- `tests/test_doctor.py`
- `README.md`
- `docs/configuration.md`
- `docs/command-reference.md`
- `docs/user-guide.md`
- `docs/troubleshooting.md`
- `docs/superpowers/plan/05-provider-expansion-and-local-first.md`
- `docs/superpowers/plan/05e-privacy-and-redaction-controls.md`

Verification:

- `pytest -q tests/test_config.py tests/test_privacy.py tests/test_providers.py tests/test_ingest.py tests/test_context.py tests/test_compaction.py tests/test_doctor.py` - passed, 94 passed, 2 skipped.
- `pytest -q` - passed, 190 passed, 3 skipped.
