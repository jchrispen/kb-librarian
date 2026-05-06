# Phase 01e - Context and End-to-End Proof

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Status: Complete
Completed: 2026-05-05
Archive note: This milestone plan is retained as the completed implementation record. Do not delete it; future work should treat it as historical context and build from Phase 02 unless a regression in this milestone is found.

## Completion Record

Implemented in:

- `src/kb_librarian/cli.py`
- `src/kb_librarian/context.py`
- `src/kb_librarian/providers.py`
- `tests/test_cli.py`
- `tests/test_context.py`
- `tests/test_providers.py`

Verified with:

- `pytest -q` -> 49 passed
- Manual acceptance:
  - Seeded 24 notes in `/tmp/kb-librarian-01e-manual`, then ran:
    - `PYTHONPATH=src python3 -m kb_librarian.cli reindex --data-dir /tmp/kb-librarian-01e-manual`
    - `PYTHONPATH=src python3 -m kb_librarian.cli context "review this architecture" --mode architecture --budget 1800 --data-dir /tmp/kb-librarian-01e-manual`
  - Observed result: compact markdown context with the required sections and cited source note IDs/relative paths, confidence, and status without requiring direct KB browsing.

## Goal

Finish the Phase 1 proof: an agent can call `kb context` with a task and receive compact, useful, cited context from the local KB without reading the full artifact.

## Depends On

- Phases 01a, 01b, 01c, and 01d must be complete.
- Reuse lexical retrieval, note metadata, provider protocol, mock provider, and integration-safe source data.

## Deliverables

- `kb context`.
- Context modes from the spec.
- High-precision candidate retrieval for task-shaped context.
- Provider-backed context synthesis.
- Basic source citation output.
- End-to-end acceptance scenario with 20-50 real notes.

## Implementation Tasks

1. Implement context command parsing.
   - Accept `kb context "<task>" [--mode <mode>] [--budget <tokens>] [--json]`.
   - Support modes from the spec: `coding`, `architecture`, `debugging`, `writing`, `research`, and `review`.
   - Use `retrieval.context_budget_tokens` from config when `--budget` is omitted.
   - Validate mode and budget before retrieval.

2. Build high-precision candidate retrieval.
   - Query the lexical index with task text and mode-aware weighting.
   - Rank using exact ID/title match, title match, summary match, retrieval phrase match, tag/topic match, note type fit, body match, usage history if already available, recency/staleness, status, and confidence.
   - Exclude archived notes by default.
   - Keep disputed, stale, or low-confidence notes eligible only when clearly relevant and mark their trust state in output.
   - Cap selected note text to fit within the requested budget.

3. Add mode-aware note type preferences.
   - `coding`: prefer techniques, patterns, anti-patterns, and heuristics.
   - `architecture`: prefer decisions, patterns, heuristics, and anti-patterns.
   - `debugging`: prefer techniques, anti-patterns, facts, and failure modes.
   - `writing`: prefer decisions, heuristics, patterns, and stored framing guidance.
   - `research`: prefer facts with stronger source emphasis and staleness visibility.
   - `review`: prefer anti-patterns, heuristics, failure modes, and decisions.

4. Implement context synthesis.
   - Use the provider protocol's context synthesis method.
   - Send selected note summaries, relevant body excerpts, trust metadata, and source paths.
   - Ask for compact markdown with spec sections: directly relevant techniques, applicable heuristics, warnings/failure modes, suggested agent behavior, and source notes.
   - Require all synthesized claims to be grounded in selected source notes.
   - Use the mock provider for deterministic tests.

5. Add basic source citations.
   - Include source note IDs and relative paths in every non-JSON response.
   - Include confidence and status for each source note.
   - JSON output must include selected notes and citation metadata as structured fields.
   - Do not implement Phase 2's enhanced citation block machinery here.

6. Handle no-result and low-result cases.
   - Return a concise message when no useful notes are found.
   - Suggest `kb search` for precise lookup when appropriate.
   - Do not log search misses in Phase 1; that belongs to Phase 2.

7. Add end-to-end tests and fixtures.
   - Unit-test candidate selection, mode weighting, budget trimming, source citation formatting, and no-result behavior.
   - Mock-provider-test context synthesis.
   - CLI smoke-test `kb context` against a temporary KB seeded with representative notes.

8. Run the manual Phase 1 acceptance scenario.
   - Add or ingest 20-50 real notes.
   - Run `kb reindex`.
   - Run `kb context "review this architecture" --mode architecture --budget 1800`.
   - Confirm output is compact, cited, and useful without loading the full KB.
   - Record the command and observed result in the milestone completion notes or PR description.

## Public Interfaces

- `kb context "<task>" [--mode <mode>] [--budget <tokens>] [--json]`

Existing Phase 1 interfaces should continue to work: `kb init`, `kb add`, `kb ingest`, `kb reindex`, `kb search`, `kb get`, and `kb review`.

## Data/State Changes

- Reads notes and generated indexes.
- Does not write canonical notes.
- May update disposable retrieval stats only if the earlier implementation already introduced `.kb/stats.json` counters; otherwise leave usage logging to Phase 2.

## Test Plan

- Unit tests for high-precision retrieval, mode-aware ranking, budget handling, citation formatting, and empty-result behavior.
- Mock-provider tests for synthesis output shape and source grounding.
- CLI smoke tests using temporary KB directories.
- Manual acceptance with 20-50 real notes and a realistic agent task.

## Acceptance Criteria

- `kb context` supports all Phase 1 modes and budget handling.
- Context retrieval favors precision and does not require reading full topic indexes or note directories.
- Output is compact, useful, and cited with note IDs and paths.
- JSON output includes selected note and citation metadata.
- No-result behavior is clear and non-destructive.
- The 20-50 note manual scenario demonstrates useful task context for an agent.

## Out of Scope

- `kb explore`.
- Usage logging, `kb log-use`, and search-miss logging.
- Enhanced citation block generation from Phase 2.
- Embeddings and semantic retrieval.
- Any Phase 3 or Phase 4 hygiene/robustness work.
