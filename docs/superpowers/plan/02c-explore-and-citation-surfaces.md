# Phase 02c - Explore and Citation Surfaces

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Add a distinct ideation retrieval path and make retrieval output easier for agents to cite directly. At the end of this milestone, agents can call `kb explore` for broader associations and can request or receive structured citation blocks from retrieval commands.

## Depends On

- Phase 01e must be complete.
- Phase 02a and 02b may exist independently of this milestone, but `kb explore` itself should not depend on review action support.
- Reuse lexical retrieval, provider synthesis, note metadata, and source-note formatting from Phase 1.

## Deliverables

- `kb explore` with broader-recall retrieval and synthesis.
- Citation block generation for `kb context`, `kb explore`, and `kb search`.
- Default citations for `kb context` and `kb explore`.
- Structured citation fields in JSON output.

## Implementation Tasks

1. Implement `kb explore` command parsing.
   - Accept `kb explore "<problem>" [--budget <tokens>] [--json]`.
   - Use `retrieval.explore_budget_tokens` when `--budget` is omitted.
   - Validate budget and output mode consistently with the existing CLI.

2. Build broad-recall retrieval for exploration.
   - Expand beyond Phase 1 context ranking through tags, retrieval phrases, note types, related notes, and backlinks.
   - Prefer higher recall than `kb context` while still excluding archived, superseded, and clearly irrelevant notes.
   - Keep disputed or low-confidence notes eligible when relevant, but expose trust state clearly.

3. Implement exploration synthesis.
   - Use the provider synthesis path or a closely related provider operation if the codebase needs a separate prompt.
   - Ask for spec-aligned sections: directly relevant concepts, adjacent patterns, tensions/tradeoffs, possible analogies, anti-patterns to avoid, open questions, and source notes.
   - Keep `kb explore` behavior distinct from `kb context`; do not collapse it into a mode flag alias.

4. Add citation block generation.
   - Add `--with-citations` to `kb context`, `kb explore`, and `kb search`.
   - Include citation blocks by default for `kb context` and `kb explore`.
   - Keep `kb search` concise by default and include the citation block only when the flag is passed.
   - Each citation entry must include note ID, relative path, confidence, status, and title.

5. Extend JSON output.
   - Include citation entries as structured fields in JSON output for all three retrieval commands.
   - Keep any new fields additive and backward-compatible where possible.

6. Handle empty or weak exploration results.
   - Return a concise explanation when exploration does not surface useful material.
   - Avoid manufacturing broad synthesis unsupported by notes.

7. Add tests.
   - Unit-test broader retrieval selection, citation formatting, default-vs-flag citation behavior, and empty-result handling.
   - Mock-provider-test `kb explore` synthesis shape and source grounding.
   - CLI smoke-test `explore` and citation flags in temporary KB directories.

## Public Interfaces

- `kb explore "<problem>" [--budget <tokens>] [--json] [--with-citations]`
- `kb context "<task>" [--mode <mode>] [--budget <tokens>] [--json] [--with-citations]`
- `kb search "<query>" [--topic <topic>] [--type <knowledge-type>] [--budget <tokens>] [--json] [--with-citations]`

## Data/State Changes

- Reads notes and generated indexes.
- May update disposable retrieval stats only if that infrastructure already exists; otherwise defer persistent usage logging to Phase 02d.
- Does not modify canonical notes.

## Test Plan

- Unit tests for retrieval breadth, citation rendering, and JSON output shape.
- Mock-provider tests for exploratory synthesis.
- CLI smoke tests for `kb explore` and citation flags.

## Acceptance Criteria

- `kb explore` returns broader, useful adjacent concepts distinct from `kb context`.
- Retrieval commands can emit citation-ready source blocks with required metadata.
- JSON outputs include the same citation information as structured fields.
- Empty-result behavior remains clear and non-destructive.

## Out of Scope

- Usage logging and search-miss logging.
- Review action workflows.
- Preamble installation and ingest-report polish.
