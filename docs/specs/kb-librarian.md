# kb-librarian

**Status:** Active development. Core workflow, ergonomics, hygiene, robustness, and provider expansion shipped. Embedding retrieval and remaining provider backends deferred.

## Purpose

A local-first knowledge base CLI for coding agents. Stores durable knowledge as markdown files in a separate data directory (`~/.kb/.library` by default), builds disposable local indexes for retrieval, and gives agents a stable command-line interface for finding task-shaped context.

## Scope

- `kb init` — initialize a KB at the configured data directory
- `kb add` — add a note directly
- `kb ingest` — ingest markdown, text, PDF, or local HTML files into candidate notes
- `kb reindex` — rebuild the FTS5 search index
- `kb compact` — compact overlapping note clusters (review-gated)
- `kb search` — lexical full-text search
- `kb get` — fetch a note by ID
- `kb topics` / `kb topic` — topic tree and per-topic note listing
- `kb graph` — concept graph of note references (`disputes`, `[[id]]` wikilinks) and backlinks
- `kb review` — manage the review queue (hygiene signals, compaction proposals)
- `kb context` — high-precision task-shaped context retrieval
- `kb explore` — broad-recall ideation retrieval
- `kb log-use` / `kb flag-suspect` / `kb usage` — usage tracking and miss feedback
- `kb doctor` — read-only health diagnostics

## Out of scope

- Embedding/semantic retrieval (seams exist; no production backend shipped)
- Remote web fetching for HTML ingest
- MCP server wrapper (explicitly deferred)
- Server or daemon mode
- Multi-user or team conventions

## Architecture

Local-first Python CLI. No server, no daemon, no network calls in the critical path.

**Storage:** Notes are markdown files with YAML frontmatter at `~/.kb/.library/notes/`. A SQLite FTS5 database at `~/.kb/.library/index.db` provides lexical search. All writes use atomic temp-file-then-rename via `atomic.py`.

**Provider abstraction:** Providers are abstracted behind a credential-source/backend seam (`provider_seams.py`). The credential source (API key, vendor CLI delegation, env token) is decoupled from the transport (direct HTTP, Ollama, vLLM, LM Studio, Claude Code CLI, Codex CLI). Fallback chains are explicit and bounded.

**Review gating:** All destructive mutations (compaction application, topic reorganization) require an accepted review item and a clean worktree check before proceeding.

## Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI entry point and command routing |
| `notes.py` | Note model (Note dataclass, YAML frontmatter, deterministic ID) |
| `storage.py` | File I/O, KB publish/read, INDEX.md and PREAMBLE.md generation |
| `paths.py` | KB path resolution (`KB_DIR`, per-command overrides) |
| `atomic.py` | Atomic file write (write to temp, rename) |
| `config.py` | Config model, per-operation overrides, default-provider policy |
| `search_index.py` | SQLite FTS5 index build and query |
| `retrieval.py` | Search, context, and explore surface logic |
| `graph.py` | Concept graph derivation (reference edges, backlinks) |
| `ingest.py` | Ingest pipeline orchestration |
| `parsers.py` | Format-specific parsing (markdown, text, PDF, local HTML) |
| `ingest_recovery.py` | Checkpoint/resume, PID-stamped lock management |
| `compaction.py` | Cluster detection, proposal drafting, application |
| `hygiene.py` | Orphan/stale/low-utility note detection |
| `mutations.py` | Note mutations (merge, supersede) |
| `topic_mutations.py` | Topic reorganization |
| `review.py` | Review queue management |
| `providers.py` | Provider registry |
| `provider_seams.py` | Credential-source/backend seam definitions |
| `provider_retry.py` | Retry/backoff logic for transient provider failures |
| `privacy.py` | Secret scrubbing and redaction |
| `context.py` | Context surface formatting and citation blocks |
| `usage.py` | Usage logging and miss-feedback tracking |
| `git_auto.py` | Optional auto-commit after mutations |
| `doctor.py` | Health check subsystems (artifacts, index, locks, auth, backends) |
| `errors.py` | Shared error types |
| `init.py` | `kb init` command |

## Open questions

- Embedding retrieval: which backend first? When does the seam graduate to production?
- Index regeneration cadence: hook-driven, on-demand, or scheduled?
- Cross-KB discovery: is a meta-index ever needed across multiple KB directories?
