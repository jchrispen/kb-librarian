---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Agent-First Architecture: In-Process CLI with Local SQLite Storage

## Context

kb-librarian needed a retrieval substrate that coding agents could call from a shell subprocess without standing up a server, managing sockets, or handling network partitions. The target environment is a developer workstation or CI container where the agent already has filesystem access.

## Decision

The system is an in-process Python CLI (`kb`) that operates entirely on a local filesystem KB at `~/.kb/.library` (configurable via `KB_DIR`). The core note model is markdown-with-YAML-frontmatter, with deterministic content-addressed IDs. Lexical indexing uses SQLite FTS5. The KB is treated as a published file artifact, not a database — notes are readable directly without the CLI if needed.

## Alternatives considered

- **Embedded HTTP server / daemon** — rejected: adds process lifecycle management, port conflicts, and auth concerns that are out of scope for a personal CLI tool.
- **Pure SQLite storage (no markdown files)** — rejected: agents lose the ability to read notes directly as files; markdown-first keeps the KB human- and agent-readable without tooling.
- **Embeddings-first retrieval** — deferred, not rejected: embedding retrieval seams are scaffolded (config, candidate/ranker stubs, doctor visibility) but no production embedding backend was shipped. FTS5 handles the primary retrieval use case with zero latency.
- **MCP wrapper** — explicitly deferred to a future phase.

## Consequences

- Every command must be stateless enough to run from a shell subprocess without shared memory.
- The file artifact must be self-describing (frontmatter, INDEX.md, PREAMBLE.md) so agents can read it directly if needed without the CLI.
- Retrieval is always local and latency-free for lexical queries.
- The candidate/ranker seam must be preserved without regression as embedding retrieval is added later.
