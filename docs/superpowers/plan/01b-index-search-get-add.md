# Phase 01b - Index, Search, Get, and Add

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Complete  
Completed: 2026-05-05  
Archive note: This milestone plan is retained as the completed implementation record. Do not delete it; future work should treat it as historical context and build from Phase 01c unless a regression in this milestone is found.

## Completion Record

Implemented in:

- `src/kb_librarian/cli.py`
- `src/kb_librarian/errors.py`
- `src/kb_librarian/storage.py`
- `src/kb_librarian/indexing.py`
- `src/kb_librarian/search_index.py`
- `tests/test_cli.py`
- `tests/test_storage.py`
- `tests/test_indexing.py`

Verified with:

- `python3 -m pytest` -> 27 passed

## Goal

Make manually created KB notes retrievable. At the end of this milestone, an agent can add seed notes, rebuild deterministic local indexes, search those notes, and inspect a selected note by ID.

## Depends On

- Phase 01a must be complete.
- Reuse the Phase 01a config, note model, body templates, and `kb init` layout.

## Deliverables

- `kb add` for direct note creation and raw queue fallback.
- Deterministic generated indexes.
- SQLite FTS5 or equivalent local lexical index.
- `kb reindex`.
- `kb search`.
- `kb get`.

## Implementation Tasks

1. Implement note storage paths.
   - Store canonical notes under `topics/<topic>/<id>.md`.
   - Create topic directories and `scope.txt` when needed.
   - Normalize topic names safely for paths while preserving the note `topic` value.
   - Detect duplicate note IDs across all topics before writing.

2. Implement direct `kb add`.
   - With `--topic` and `--type`, create a single note from `--from-file` or stdin.
   - Use the note schema, ID generator, and body templates from Phase 01a.
   - Populate required frontmatter with safe defaults: `status: active`, `confidence: medium`, current `created`/`updated`, empty `retrieval_phrases` when none are supplied, and tags derived only when deterministic.
   - Reject unsupported `knowledge_type` values before writing files.
   - Run validation before writing and reindex after successful note creation.

3. Implement raw queue fallback for `kb add`.
   - If the user does not provide enough metadata for direct note creation, copy the input into `raw/`.
   - Print the queued raw path.
   - Do not call a provider or attempt extraction in this milestone.

4. Implement generated markdown indexes.
   - Rebuild top-level `INDEX.md` from topics.
   - Rebuild `topics/<topic>/INDEX.md` from notes in that topic.
   - Include note ID, title, summary, knowledge type, confidence, status, and updated date.
   - Keep output deterministic when note files are unchanged.

5. Implement backlinks and manifest generation.
   - Generate `.kb/backlinks.json` from explicit note ID references in note bodies and frontmatter fields where applicable.
   - Generate `.kb/index-manifest.json` with build time, note count, topic count, and indexed files.
   - Treat generated artifacts as disposable.

6. Implement local lexical indexing.
   - Use SQLite FTS5 unless unavailable in the target Python runtime; otherwise use a documented local equivalent.
   - Index fields from spec section 5.4: ID, title, summary, topic, knowledge type, tags, retrieval phrases, agent use, applicability fields, body, status, confidence, and updated date.
   - Store the index at `.kb/fts.sqlite`.
   - Rebuild the index from markdown notes, not from prior index state.

7. Implement `kb reindex`.
   - Rebuild markdown indexes, backlinks, manifest, and lexical index.
   - Support deterministic repeated runs.
   - Print changed/generated artifact paths unless a future quiet flag is added.
   - Return nonzero if any note fails validation.

8. Implement `kb search`.
   - Search titles, summaries, tags, retrieval phrases, and bodies through the lexical index.
   - Apply ranking weights from config: title, summary, retrieval phrase, tag, and body.
   - Support `--topic`, `--type`, `--budget`, and `--json`.
   - Return summaries and source paths by default, not full note bodies.
   - Include status and confidence in every result.

9. Implement `kb get`.
   - `kb get <id>` prints the full note.
   - `kb get <id> --summary` prints title, summary, type, status, confidence, topic, path, and retrieval phrases.
   - Return a clear error when the ID is missing or ambiguous.

10. Add tests.
    - Unit-test note path resolution, direct add defaults, raw queue fallback, index rendering, FTS document construction, ranking weights, and ID lookup.
    - CLI smoke-test `add`, `reindex`, `search`, and `get` in temporary KB directories.

## Public Interfaces

- `kb add [--topic <topic>] [--type <knowledge-type>] [--from-file <path>]`
- `kb reindex`
- `kb search "<query>" [--topic <topic>] [--type <knowledge-type>] [--budget <tokens>] [--json]`
- `kb get <id> [--summary]`

## Data/State Changes

- Writes canonical notes under `topics/<topic>/<id>.md`.
- May copy incomplete add input into `raw/`.
- Generates `INDEX.md`, `topics/<topic>/INDEX.md`, `.kb/backlinks.json`, `.kb/fts.sqlite`, and `.kb/index-manifest.json`.
- Updates no existing note bodies.

## Test Plan

- Unit tests for deterministic indexing and retrieval behavior.
- CLI smoke tests using temporary KB directories seeded by `kb add`.
- Manual check: add several notes, run `kb search`, inspect one with `kb get`, rerun `kb reindex`, and confirm results stay stable.

## Acceptance Criteria

- `kb add` can create valid seed notes from file input or stdin.
- Incomplete add input is queued under `raw/` without extraction.
- `kb reindex` rebuilds all generated indexes from notes.
- `kb search` returns relevant cited summaries with topic/type filters.
- `kb get` can inspect full and summary note output.
- Repeated reindexing is deterministic when notes have not changed.

## Out of Scope

- Provider abstraction and LLM-backed extraction.
- `kb ingest`.
- Integration verdicts, merge/dispute queues, and `kb review`.
- `kb context` synthesis.
