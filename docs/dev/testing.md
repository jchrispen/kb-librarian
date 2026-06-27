# Testing

## Install

`make` is self-contained: `make test` and `make build` create a local `.venv/` (keyed on `pyproject.toml`) and install the package with test + build deps on first run. To work in the venv directly, install it yourself:

```bash
python3 -m pip install -e ".[test]"
```

The CLI entry point is `kb` (`kb_librarian.cli:main`). Requires Python >= 3.11.

## Make targets

Run `make` (or `make help`) to list targets; `help` is the default goal, so a bare `make` prints usage rather than building.

| Target | Action |
|---|---|
| `make help` | List available targets |
| `make test` | Run the test suite (creates/uses `.venv/`) |
| `make build` | Clean, then build wheel + sdist into `dist/` |
| `make clean` | Remove `build/`, `dist/`, `*.egg-info`, stray `__pycache__` (keeps `.venv/`) |
| `make clean-venv` | Remove only `.venv/` (force a fresh dependency install) |
| `make clean-all` | `clean` plus `clean-venv` |
| `make all` | `clean` → `build` → `test` |

## Run tests

```bash
pytest          # or: make test
```

`pytest` is configured in `pyproject.toml` with `pythonpath=["src"]` and `testpaths=["tests"]`, and every run includes a terminal code-coverage summary for `kb_librarian` (`--cov`, branch coverage on). `make test` keeps pytest color enabled for green pass markers and red failures; the test harness writes progress percentages in plain text so later passing files do not look red after the first failure. Transient products (pytest cache, coverage data) are written under `build/`, and bytecode writes are disabled to keep the source tree clean; `make clean` removes `build/`, `dist/`, `*.egg-info`, and any stray `__pycache__`.

Run the deterministic corpus regression checks alone:

```bash
pytest -q tests/test_golden_corpus.py
```
