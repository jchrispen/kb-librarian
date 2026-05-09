# Phase 06b - Additional Local Provider Backends

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete

## Goal

Expand local-first execution beyond Ollama by adding first-class support for additional local backends, prioritizing vLLM and LM Studio.

## Depends On

- Phase 06a complete.
- Existing local-provider behavior from Phase 05a.

## Model and Effort Recommendation

- Recommended model: `gpt-5.4` or stronger
- Reasoning effort: `high`
- Why: this milestone requires careful adapter factoring across different local server contracts while preserving the current structured provider protocol and diagnostics.

## Deliverables

- Local backend validation that supports more than `ollama`.
- First-class support for `vllm`.
- First-class support for `lm_studio`.
- Reusable local transport seam for OpenAI-compatible local servers where appropriate.
- Backend-specific doctor checks and troubleshooting docs.

## Implementation Tasks

1. Generalize local backend selection.
   - Extend `providers.local.backend` validation beyond `ollama`.
   - Keep the config conservative and explicit; no auto-detection by default.

2. Add vLLM support.
   - Implement the minimal adapter needed for KB Librarian's structured extraction, classification, integration, and synthesis flows.
   - Prefer compatibility with documented vLLM server APIs rather than project-specific hacks.

3. Add LM Studio support.
   - Implement a first-class adapter or supported compatibility mode with explicit docs.
   - Handle common local-server errors clearly, especially model-not-loaded and incompatible endpoint shape failures.

4. Preserve Ollama behavior.
   - Keep current Ollama routes and diagnostics intact.
   - Avoid forcing config migrations for existing local users.

5. Add diagnostics and tests.
   - Extend `kb doctor` to check backend reachability and routed model presence for each supported local backend.
   - Add route and failure coverage for all supported local backends.

## Public Interfaces

- `providers.local.backend` supports at least `ollama`, `vllm`, and `lm_studio` after this milestone.
- Existing CLI command interfaces remain unchanged.

## Data/State Changes

- Config schema expands with backend-specific local settings where necessary.
- Diagnostics gain backend-specific findings and remediation hints.

## Test Plan

- `tests/test_config.py` for backend-specific config validation.
- `tests/test_providers.py` for local adapter behavior and error mapping.
- CLI smoke tests that route operations through each supported local backend using fixtures or stub servers.
- `tests/test_doctor.py` for local reachability and model diagnostics.

## Acceptance Criteria

- Users can route provider-backed operations to Ollama, vLLM, or LM Studio through documented config changes.
- Existing Ollama users see no breaking behavior.
- Backend-specific failures are visible and actionable in CLI and doctor output.

## Out of Scope

- Browser-managed auth for local servers.
- Automatic selection among multiple running local backends.

## Completion Record

Completed on 2026-05-08.

Implemented files:

- `src/kb_librarian/provider_seams.py`
- `src/kb_librarian/providers.py`
- `src/kb_librarian/doctor.py`
- `tests/test_config.py`
- `tests/test_providers.py`
- `tests/test_doctor.py`
- `README.md`
- `docs/configuration.md`
- `docs/troubleshooting.md`
- `docs/superpowers/plan/06-provider-backends-and-account-auth.md`
- `docs/superpowers/plan/06b-additional-local-provider-backends.md`

Verification:

- `python3 -m pytest --override-ini addopts='' tests/test_config.py tests/test_providers.py tests/test_doctor.py -q` - passed, 68 passed.
