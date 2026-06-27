# Self-contained build/test via a local .venv/.
# Transient build/test products land under build/; release artifacts under dist/.
# Skip writing bytecode so the source tree stays clean (redirecting it is costly
# on Windows/drvfs mounts: it mirrors the whole venv and is slow to delete).
export PYTHONDONTWRITEBYTECODE = 1

VENV := .venv
PY := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help all venv clean clean-venv clean-all build test

help: ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) | \
		sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

all: clean build test ## Clean, build, then test

# Create the venv and install the package with test + build deps.
# The stamp file tracks pyproject.toml so deps reinstall when it changes.
$(VENV)/.stamp: pyproject.toml
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[test]" build
	touch $@

venv: $(VENV)/.stamp ## Create .venv/ and install deps

# Remove build/test artifacts; keeps the venv (slow to rebuild).
clean: ## Remove build/test artifacts (keeps .venv/)
	rm -rf build dist src/*.egg-info
	find . -type d -name __pycache__ -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} +

clean-venv: ## Remove only .venv/ (force a fresh dep install)
	rm -rf $(VENV)

clean-all: clean clean-venv ## Remove artifacts and .venv/

build: clean venv ## Build wheel + sdist into dist/
	$(PY) -m build

test: venv ## Run the test suite
	$(PY) -m pytest --color=yes
