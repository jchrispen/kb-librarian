# Release Guide

## Goal

Build distributable artifacts so users can download a file, install it with `pip`, and run `kb` without an editable source checkout.

## Build Local Artifacts

Create a build virtual environment and install the build frontend:

```bash
python3 -m venv .venv-build
. .venv-build/bin/activate
python3 -m pip install build
```

Build a source distribution and wheel:

```bash
python3 -m build      # or: make build (runs make clean first)
```

This writes release artifacts to `dist/`. Transient build/test products go under `build/` instead; `make clean` removes both.

## Test A Built Wheel

Install the wheel into a clean virtual environment:

```bash
python3 -m venv .venv-release-test
. .venv-release-test/bin/activate
python3 -m pip install dist/kb_librarian-0.1.0-py3-none-any.whl
kb --help
```

## Ship Downloadable Artifacts

The repository workflow at `.github/workflows/release-package.yml` builds `dist/*.whl` and `dist/*.tar.gz` for:

- manual `workflow_dispatch` runs
- pushed tags matching `v*`

Tagged runs also publish those files to the GitHub release for the tag, giving users a direct download target for `pip install /path/to/file.whl`.

## Optional Next Step: PyPI

If you want `python3 -m pip install kb-librarian` without downloading a file first, add a trusted-publisher PyPI release step after package ownership and credentials are set up.
