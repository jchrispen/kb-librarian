# Phase 04c - PDF and HTML Ingest Parsing

Canonical spec: `docs/superpowers/specs/kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-07

## Goal

Expand ingest coverage to common local document formats without relying on OCR or remote browsing. At the end of this milestone, `kb ingest` can parse local PDF and HTML files, preserve useful source metadata, and fail safely when extraction is impossible.

## Depends On

- Phase 04a should be complete so parser failures integrate with lock, checkpoint, and resume behavior.
- Reuse ingest reporting, review state, and source-metadata handling from earlier phases.

## Deliverables

- PDF parsing for local `.pdf` files.
- HTML parsing for local `.html` and `.htm` files.
- Clean parser failure logging and review surfacing.
- Source metadata preservation for pages, titles, links, and local source hints when available.

## Implementation Tasks

1. Add PDF parsing.
   - Support `.pdf` files in `kb ingest`.
   - Extract text with a maintained local parser library.
   - Preserve page numbers in source metadata when available.
   - Do not implement image OCR in this phase.
   - If text extraction is empty or fails, leave the file in `raw/`, log the error, and surface a review item.

2. Add HTML parsing.
   - Support `.html` and `.htm` files in `kb ingest`.
   - Extract visible text, headings, title, links, and source URL metadata when present.
   - Ignore scripts, styles, navigation boilerplate, and hidden elements where the parser can identify them.
   - Preserve enough structure for extraction prompts to distinguish headings from body text.
   - Do not fetch remote pages; parse local files only.

3. Integrate new parsers with ingest safety.
   - Route parser failures through existing error logging, checkpoint state, and review or reporting surfaces.
   - Keep parser behavior idempotent so retries or resumes do not corrupt raw-file handling.
   - Ensure ingest reports describe PDF and HTML outcomes clearly.

4. Preserve source metadata for downstream trust.
   - Add parser-derived source fields only where they can be represented cleanly in existing note-source metadata.
   - Keep page numbers, HTML titles, and local source hints inspectable for later review.
   - Avoid inventing unsupported provenance fields without a clear schema slot.

5. Add tests.
   - Parser-test PDF and HTML samples with deterministic expected text snippets and source metadata.
   - CLI smoke-test PDF and HTML ingest in temporary KB directories.
   - Unit-test parser failure handling and empty-extraction behavior.

## Public Interfaces

- `kb ingest [<file>] [--force] [--quiet] [--json] [--resume]`

Supported ingest formats now include `.pdf`, `.html`, and `.htm` in addition to earlier formats.

## Data/State Changes

- May add parser-derived source metadata to note `sources`.
- Extends `.kb/errors.log` and review surfaces with PDF or HTML parser diagnostics.
- Leaves failed parser inputs in `raw/` for later handling.

## Test Plan

- Unit and parser-specific tests for deterministic text extraction behavior.
- CLI smoke tests using temporary KB directories and local sample files.
- Manual check: ingest one PDF and one HTML file, then inspect resulting notes or failure surfaces.

## Acceptance Criteria

- Local PDF and HTML files can be ingested without remote fetching or OCR.
- Parser failures are surfaced cleanly and do not corrupt ingest state.
- Useful source metadata is preserved when available.
- Resume and retry flows remain safe around parser-backed ingest.

## Out of Scope

- Image OCR for PDFs.
- Remote web fetching.
- Hook templates and auto-commit policy.

## Completion Record

Implemented files:

- `src/kb_librarian/parsers.py`
- `src/kb_librarian/ingest.py`
- `src/kb_librarian/review.py`
- `pyproject.toml`
- `tests/test_ingest.py`
- `README.md`
- `docs/command-reference.md`
- `docs/user-guide.md`
- `docs/superpowers/plan/04-robustness-and-polish.md`
- `docs/superpowers/plan/04c-pdf-and-html-ingest-parsing.md`

Verification:

- `python3 -m pytest -q tests/test_ingest.py tests/test_review.py tests/test_cli.py` - 46 passed, 2 skipped before local `pypdf` installation.
- `/tmp/kb-librarian-04c-venv/bin/python -m pytest -q tests/test_ingest.py tests/test_review.py tests/test_cli.py` - 48 passed with `pypdf` installed.
