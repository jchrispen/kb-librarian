# Testing

## Install

Install the package with test extras into a virtualenv:

```bash
python3 -m pip install -e ".[test]"
```

The CLI entry point is `kb` (`kb_librarian.cli:main`). Requires Python >= 3.11.

## Run tests

```bash
pytest
```

`pytest` is configured in `pyproject.toml` with `pythonpath=["src"]` and `testpaths=["tests"]`, and every run includes a terminal code-coverage summary for `kb_librarian` (`--cov`, branch coverage on).

Run the deterministic corpus regression checks alone:

```bash
pytest -q tests/test_golden_corpus.py
```
