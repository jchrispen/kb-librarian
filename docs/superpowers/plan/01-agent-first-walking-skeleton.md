# Phase 01 - Agent-First Walking Skeleton

Canonical spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

## Goal

Build the smallest useful KB Librarian that proves the agent-first thesis: an agent can ask for task-shaped context through `kb context`, receive useful cited material from a local markdown knowledge artifact, and avoid loading the full KB.

Phase 1 starts from an empty tool repo and should not depend on later-phase ergonomics, compaction, PDF/HTML parsing, embeddings, hooks, cron jobs, or automation.

## Milestone Order

1. [01a - Scaffold, Config, and Note Model](01a-scaffold-config-note-model.md)
   - Bootstraps the Python package, `kb` entry point, config loading, `kb init`, and markdown/frontmatter note model.
2. [01b - Index, Search, Get, and Add](01b-index-search-get-add.md)
   - Adds direct note creation, deterministic generated indexes, local lexical search, and note inspection.
3. [01c - Provider and Ingest](01c-provider-and-ingest.md)
   - Adds the provider abstraction, Anthropic adapter, mock provider, markdown/txt ingest, extraction, classification, dedup, and raw archiving.
4. [01d - Integration and Basic Review](01d-integration-review.md)
   - Adds candidate matching, integration verdict handling, source appends, merge/dispute review queues, and basic review output.
5. [01e - Context and End-to-End Proof](01e-context-e2e.md)
   - Adds `kb context`, context modes, synthesis, source citations, and the 20-50 note acceptance scenario.

Each milestone should leave the CLI runnable and covered by focused tests. Later milestones may adjust earlier code, but they should preserve the public behavior accepted by earlier milestones.

## Phase Deliverables

- Python package scaffold with a console-script entry point named `kb`.
- Config loading with default data directory support for `/mnt/c/workspace/source/internal/kb`.
- Local KB data directory initialization matching the spec's published-artifact layout.
- Markdown note model with YAML frontmatter parsing, validation, deterministic ID generation, and note body templates.
- Disposable local lexical index using SQLite FTS5 or an equivalent local index.
- Provider abstraction with an Anthropic adapter and deterministic mock provider.
- Markdown/txt ingest with note type extraction, retrieval phrase extraction, source tracking, and raw file deduplication.
- Basic integration flow with verdicts `identical`, `adds_nuance`, `contradicts`, and `unrelated` treated as create-new-note.
- CLI commands: `kb init`, `kb add`, `kb ingest`, `kb reindex`, `kb search`, `kb context`, `kb get`, and basic `kb review`.

## Final Acceptance Criteria

- A fresh checkout can install the package and run `kb --help`.
- `kb init` creates an idempotent local KB data directory with config and expected folders.
- Markdown/frontmatter notes validate and round-trip without losing required fields.
- `kb add` can create seed notes and `kb ingest` can process markdown/txt raw files.
- Integration handles identical, adds-nuance, contradiction, and unrelated outcomes without silent body rewrites.
- `kb reindex` produces a usable local lexical index.
- `kb search`, `kb context`, and `kb get` return cited note references.
- With 20-50 real notes, `kb context` retrieves useful task context for an agent without requiring the agent to read the full KB.

## Phase Test Strategy

- Unit tests for deterministic logic: config, frontmatter parsing/writing, schema validation, ID generation, markdown round-trip, index generation, ranking, and review queue rendering.
- Mock-provider tests for extraction, classification, integration verdicts, contradiction handling, and context synthesis.
- CLI smoke tests using temporary KB directories for every Phase 1 command.
- Manual acceptance with 20-50 real notes and a realistic `kb context` task.

## Out of Scope

- Stable review item IDs and actionable review accept/reject/defer commands.
- `kb explore`, usage logging, search-miss logging, and citation block generation beyond basic source notes.
- Compaction, stale-note flagging, orphan flagging, low-utility queues, `kb doctor`, INDEX pagination, and topic reorganization.
- Concurrent ingest locking, resume, retry/backoff, PDF/HTML parsing, hooks, cron templates, and auto-commit.
- Additional providers, local providers, embeddings, MCP wrapper, TUI/web review, privacy/redaction tooling, and multi-machine conflict helpers.
