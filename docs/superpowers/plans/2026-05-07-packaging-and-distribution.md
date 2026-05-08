Status: Complete
Date: 2026-05-07

# Packaging And Distribution

## Goal

Make KB Librarian's existing Python package shape easier to consume as a normal CLI: users should be able to install with `pip`, install from a downloaded wheel, and run `kb` without relying on an editable source checkout.

## Scope

- Update end-user docs to recommend standard `pip install .` for local source installs.
- Document wheel-based installation for users downloading release artifacts.
- Add a small release/build guide for maintainers.
- Add repository automation that builds wheel and sdist artifacts for tagged releases and manual dispatch.

## Out Of Scope

- PyPI publication credentials and ownership setup.
- Changes to runtime CLI behavior.

## Completion Record

- Implemented in `README.md`, `docs/user-guide.md`, `docs/codex-kb-demo.md`, `docs/releasing.md`, `.github/workflows/release-package.yml`, and `.gitignore`.
- Verification: `python3 -m venv /tmp/opencode/kb-build-venv && /tmp/opencode/kb-build-venv/bin/python -m pip install build && /tmp/opencode/kb-build-venv/bin/python -m build && python3 -m venv /tmp/opencode/kb-release-venv && /tmp/opencode/kb-release-venv/bin/python -m pip install dist/kb_librarian-0.1.0-py3-none-any.whl && /tmp/opencode/kb-release-venv/bin/kb --help` -> build succeeded and the wheel-installed CLI printed command help.
