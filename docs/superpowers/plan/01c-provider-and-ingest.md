# Phase 01c - Provider and Ingest

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-05
Archive note: This milestone plan is retained as the completed implementation record. Do not delete it; future work should treat it as historical context and build from Phase 01d unless a regression in this milestone is found.

## Completion Record

Implemented in:

- `src/kb_librarian/cli.py`
- `src/kb_librarian/config.py`
- `src/kb_librarian/errors.py`
- `src/kb_librarian/providers.py`
- `src/kb_librarian/ingest.py`
- `tests/test_cli.py`
- `tests/test_ingest.py`
- `tests/test_providers.py`

Verified with:

- `python3 -m pytest` -> 38 passed

## Goal

Add provider-backed markdown/txt ingest. At the end of this milestone, raw files can be parsed, deduplicated, classified, extracted into candidate notes, written as new notes when no integration risk is detected, archived, and tested without network access through a mock provider.

## Depends On

- Phase 01a and Phase 01b must be complete.
- Reuse note validation, topic storage, `kb reindex`, and lexical lookup from earlier milestones.

## Deliverables

- `LLMProvider` protocol.
- Anthropic adapter.
- Deterministic mock provider.
- Markdown/txt parser for ingest inputs.
- Hash-based raw file deduplication and archive behavior.
- Extraction and classification flow.
- `kb ingest` for files and pending raw queue items.

## Implementation Tasks

1. Define the provider abstraction.
   - Add an `LLMProvider` protocol with methods for candidate extraction, classification, integration verdicts, and context synthesis.
   - Implement the extraction and classification methods in this milestone.
   - Keep integration verdict and context synthesis protocol methods present but unused until later milestones.

2. Implement provider configuration.
   - Read provider operation mapping from `.kb/config.yaml`.
   - Route `extract` and `classify` to configured provider/model values.
   - Read Anthropic credentials from the configured `api_key_env`.
   - Produce clear errors when credentials are missing for a real provider call.

3. Implement the Anthropic adapter.
   - Send extraction and classification prompts using structured JSON response expectations.
   - Validate provider JSON before using it.
   - Reject malformed responses with clear errors and `.kb/errors.log` entries.
   - Keep retries/backoff out of scope until Phase 4.

4. Implement the mock provider.
   - Provide deterministic extraction and classification responses for tests.
   - Make it selectable through config or test harness injection.
   - Ensure CLI smoke tests can run without network access.

5. Implement markdown/txt parsing.
   - Support `.md` and `.txt`.
   - Preserve enough heading and paragraph structure for extraction.
   - Reject unsupported file extensions by leaving the file in `raw/`, appending `.kb/errors.log`, and surfacing the issue through the basic ingest report.

6. Implement raw file selection and deduplication.
   - `kb ingest <file>` processes one explicit file.
   - `kb ingest` processes pending `.md` and `.txt` files in `raw/`.
   - Track file hashes and ingest status in `.kb/ingested.json`.
   - Same hash plus previous success moves the file to `raw/processed/YYYY-MM/duplicates/` and reports it as a duplicate.
   - Same hash plus previous error may retry.
   - Same filename with a different hash processes as new and records a possible updated version warning.

7. Implement extraction.
   - Ask the provider for at most `ingest.max_notes_per_doc` candidate notes per input.
   - Candidate JSON must include title, summary, likely `knowledge_type`, body, retrieval phrases, tags, confidence, utility score, and optional applicability/failure-mode fields.
   - Prefer `no durable notes worth creating` over low-value notes.
   - Reject or skip candidates that fail schema validation, recording an error or classification review entry.

8. Implement classification.
   - Determine topic and likely note type when not already supplied by extraction.
   - Low-confidence or ambiguous topic/type candidates must go to `review/pending-classification.md`.
   - High-confidence candidates with no likely existing matches can be written as new notes.
   - Use existing lexical lookup only as a conservative risk check; full integration verdict handling belongs to Phase 01d.

9. Write created notes and archive inputs.
   - Create notes under `topics/<topic>/<id>.md` with source metadata pointing to the ingested raw file.
   - Run validation before writing.
   - Run `kb reindex` after successful note creation.
   - Archive successfully processed raw files under `raw/processed/YYYY-MM/`.

10. Add ingest reports.
    - Print counts for processed files, created notes, classification items, skipped candidates, duplicates, unsupported files, and errors.
    - Include note IDs and relevant review file paths.
    - Keep `--quiet` quiet except for errors.

11. Add tests.
    - Unit-test parser behavior, hash tracking, duplicate handling, archive paths, provider JSON validation, candidate validation, and classification routing.
    - Mock-provider-test extraction and classification without network.
    - CLI smoke-test `kb ingest` with explicit files and pending `raw/` files in temporary KB directories.

## Public Interfaces

- `kb ingest [<file>] [--force] [--quiet]`
- Provider selection through `.kb/config.yaml`.
- Existing `kb reindex`, called after successful note creation.

## Data/State Changes

- Writes `.kb/ingested.json` and `.kb/errors.log`.
- Writes new canonical notes for high-confidence, low-risk candidates.
- Writes ambiguous candidates to `review/pending-classification.md`.
- Archives processed raw files under `raw/processed/YYYY-MM/`.
- Moves duplicate raw files under `raw/processed/YYYY-MM/duplicates/`.
- Regenerates indexes after successful ingest.

## Test Plan

- Unit tests for deterministic ingest parsing, dedup, provider response validation, candidate validation, and raw archive behavior.
- Mock-provider tests for extraction and classification.
- CLI smoke tests using temporary KB directories and no network access.
- Manual check: place `.md` and `.txt` files in `raw/`, run `kb ingest`, confirm notes or classification entries are created, and confirm processed files are archived.

## Acceptance Criteria

- `kb ingest` processes explicit markdown/txt files and pending raw queue files.
- Duplicate and unsupported files are handled without corrupting raw state.
- Mock-provider ingest creates valid notes or classification review entries.
- Anthropic adapter is wired through config and validates structured JSON.
- Created notes include retrieval phrases, type, topic, confidence, source metadata, and valid frontmatter.
- Ingest reports clearly state what happened.

## Out of Scope

- Full integration verdict handling for identical/adds-nuance/contradicts.
- Basic `kb review` output beyond writing pending classification entries.
- `kb context`.
- Provider retry/backoff, lockfiles, resume, PDF, and HTML parsing.
