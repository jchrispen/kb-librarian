Status: Complete
Date: 2026-05-07

# Test Coverage Tracking

## Goal

Add repository-local code coverage measurement so standard `pytest` runs report how much of `kb_librarian` is exercised.

## Scope

- Add `pytest-cov` to test dependencies.
- Configure pytest to emit terminal coverage by default.
- Configure coverage to measure `kb_librarian` with branch coverage.
- Include subprocess-executed CLI smoke tests in coverage reporting.
- Update user-facing docs with the new behavior.

## Completion Record

- Implemented in `pyproject.toml`, `README.md`, `docs/commands.md`, and `docs/user-guide.md`.
- Verification: `./.venv/bin/python -m pytest -q` -> `195 passed, 1 skipped`, total coverage `79%` with subprocess-driven CLI tests included.
