# `/coverage`

Use for test coverage inspection or coverage-focused improvement work.

Read first:

- `pyproject.toml`
- `docs/superpowers/plans/2026-05-07-test-coverage-tracking.md`

Procedure:

1. Confirm whether the goal is measurement, diagnosis, or raising coverage.
2. Inspect current pytest and coverage configuration before changing code or tests.
3. Run the smallest relevant pytest command that produces useful coverage output.
4. If asked to improve coverage, target meaningful gaps in current behavior rather than writing superficial tests.
5. Prefer focused tests near the changed behavior over broad incidental churn.
6. Summarize the resulting coverage signal, missing areas, and confidence limits.

Report:

- Commands run.
- Coverage results observed.
- Highest-value uncovered areas or tests added.
