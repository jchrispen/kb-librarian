# Agent Instructions

## Project Context

KB Librarian is an agent-first local knowledge base CLI (entry point: `kb`).

- Canonical design spec: `docs/specs/kb-librarian.md` — source of truth for product behavior, architecture, data model, and CLI contracts. Read it before implementing or extending; if a plan conflicts with the spec, the spec wins.
- Transient plans/research: `docs/plans/`.
- Build & test: `docs/dev/testing.md`.

Phases 1–6 are shipped. The spec's "Deferred backlog" (embedding-based retrieval, MCP wrapper, TUI/web review, multi-machine helpers, interactive cloud-call confirmation) is out of scope unless explicitly requested.
