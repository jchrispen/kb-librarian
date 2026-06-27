# Agent Instructions

## Project Context

KB Librarian is an agent-first local knowledge base CLI (entry point: `kb`).

- Canonical design spec: `docs/specs/kb-librarian.md` — source of truth for product behavior, architecture, data model, and CLI contracts. Read it before implementing or extending; if a plan conflicts with the spec, the spec wins.
- Transient plans/research: `docs/plans/`.
- Build & test: `docs/dev/testing.md`.

The core agent workflow is shipped (see README "Core Commands"). Deferred work lives in the spec's **Out of scope** and **Open questions** sections (embedding/semantic retrieval, MCP wrapper, remaining provider backends) — out of scope unless explicitly requested. Active transient work is tracked in `docs/plans/`.
