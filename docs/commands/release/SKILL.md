# `/release`

Use for packaging and release-prep work.

Read first:

- `docs/releasing.md`
- `pyproject.toml`

Procedure:

1. Confirm the requested release scope: docs-only, local build verification, git tag/release prep, or publication.
2. Inspect package metadata, release docs, and current git state.
3. Build artifacts with `python -m build` in an isolated virtual environment.
4. Verify the built wheel in a clean virtual environment with `kb --help`.
5. If requested, prepare release notes from the relevant commits and changed docs.
6. Do not create tags, GitHub releases, or publish to package indexes unless explicitly requested.

Report:

- Built artifact names.
- Verification commands and results.
- Any blockers for shipping.
