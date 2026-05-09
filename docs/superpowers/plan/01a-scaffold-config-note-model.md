# Phase 01a - Scaffold, Config, and Note Model

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete  
Completed: 2026-05-05  
Archive note: This milestone plan is retained as the completed implementation record. Do not delete it; future work should treat it as historical context and build from Phase 01b unless a regression in this milestone is found.

## Completion Record

Implemented in:

- `pyproject.toml`
- `src/kb_librarian/`
- `tests/`

Verified with:

- `python3 -m pytest` -> 18 passed
- Editable install smoke check for `kb --help`
- Manual `kb init --data-dir <tmp>` idempotency check

## Goal

Create a bootable Python CLI and local KB file model. At the end of this milestone, an agent can install the package, run `kb init`, and validate markdown/frontmatter notes even though ingest, retrieval, and provider-backed behavior are not implemented yet.

## Depends On

- No earlier implementation milestone.
- Use the canonical spec for directory layout, config fields, note schema, note types, and body templates.

## Deliverables

- Python package scaffold with `kb` console-script entry point.
- CLI parser with stable help text and placeholder subcommands for Phase 1 commands.
- Config resolution and default data directory support.
- Idempotent `kb init`.
- Markdown/frontmatter note model with validation, deterministic ID generation, and note body templates.

## Implementation Tasks

1. Scaffold the package.
   - Add `pyproject.toml` with package metadata, runtime dependencies, test dependencies, and a `kb` console script.
   - Use `src/kb_librarian/` with modules for CLI, config, notes, filesystem paths, and errors.
   - Make `kb --help` work from an editable install.

2. Define the CLI shell.
   - Add subcommands for `init`, `add`, `ingest`, `reindex`, `search`, `context`, `get`, and `review`.
   - Implement only `init` in this milestone.
   - Placeholder commands must return a clear "not implemented in this milestone" error and nonzero exit code.
   - All commands must support `--help`.

3. Implement config resolution.
   - Resolve `data_dir` in this order: explicit `--data-dir`, `KB_DATA_DIR`, configured value from `~/.kb/config.yaml`, then `~/.kb/.library`.
   - Load and write YAML config.
   - Include phase-1 config sections from the spec: `providers`, `operations`, `retrieval`, `ingest`, `indexes`, `review`, `git`, `privacy`, and `hooks`.
   - Validate required config keys and produce actionable errors for malformed config.

4. Implement `kb init`.
   - Create `INDEX.md`, `PREAMBLE.md`, `topics/`, `raw/`, `raw/processed/`, `review/`, and `.kb/`.
   - Create review queue files needed by Phase 1: `pending-classification.md`, `pending-merge.md`, and `disputes.md`.
   - Create `.kb/config.yaml`, `.kb/ingested.json`, `.kb/errors.log`, `.kb/backlinks.json`, `.kb/stats.json`, `.kb/state.json`, and `.kb/index-manifest.json` with safe initial contents.
   - Keep `kb init` idempotent and never overwrite existing notes, review content, or user-edited config unless an explicit future flag is added.

5. Implement the note model.
   - Parse markdown files with YAML frontmatter and markdown body.
   - Write markdown with stable frontmatter ordering.
   - Validate required fields: `id`, `title`, `summary`, `topic`, `created`, `updated`, `knowledge_type`, `status`, `confidence`, `retrieval_phrases`, and `tags`.
   - Enforce enum values from the spec for `knowledge_type`, `status`, `confidence`, and `staleness_risk` when present.
   - Preserve optional fields such as `basis`, `sources`, `agent_use`, `applies_when`, `does_not_apply_when`, `failure_modes`, `reviewed_by_user`, and `disputes`.

6. Add deterministic note ID and template helpers.
   - Generate note IDs as `<yyyy-mm-dd>-<slug>`.
   - Add a deterministic numeric suffix on collision.
   - Provide body templates for `fact`, `technique`, `heuristic`, `pattern`, `anti-pattern`, `decision`, and `open-question`.
   - Keep templates aligned with spec section 3.4.

7. Add tests.
   - Unit-test config resolution order, default config rendering, idempotent init behavior, note parsing, note writing, schema validation, enum validation, ID generation, and body templates.
   - Add a CLI smoke test for `kb --help` and `kb init --data-dir <temp-dir>`.

## Public Interfaces

- `kb --help`
- `kb init [--data-dir <path>] [--hooks]`
- Placeholder help for `kb add`, `kb ingest`, `kb reindex`, `kb search`, `kb context`, `kb get`, and `kb review`.

## Data/State Changes

- Creates the KB data directory layout.
- Writes `.kb/config.yaml` and initial `.kb/` state files.
- Writes empty or starter `INDEX.md`, `PREAMBLE.md`, and Phase 1 review queue files.
- Does not create canonical note files yet except through test fixtures.

## Test Plan

- Unit tests for deterministic config, path, note, and template logic.
- CLI smoke tests using temporary data directories only.
- Manual check: run `kb init --data-dir <tmp>` twice and confirm the second run does not overwrite existing files.

## Acceptance Criteria

- Package installs in editable mode and exposes `kb`.
- `kb --help` and `kb init --help` work.
- `kb init` creates the expected data repo structure and initial state files.
- Config resolution follows the required precedence.
- Note parsing/writing and validation work against representative frontmatter/body examples.
- Note ID generation is deterministic and collision-safe.

## Out of Scope

- Direct note creation through `kb add`.
- Generated indexes, FTS, search, and note retrieval.
- Provider calls, ingest, extraction, classification, integration, and review behavior.
- `kb context`.
