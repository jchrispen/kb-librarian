---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Retrieval and Search: FTS-First, Two Surfaces, Embedding Seams Deferred

## Context

Agents need to retrieve notes from the KB in two modes: task-shaped context (high precision, relevant to a specific coding task) and ideation/exploration (broad recall, discovering what exists). A third path — embedding/semantic retrieval — was considered but the infrastructure cost and latency implications were uncertain.

## Decision

Phase 1 ships SQLite FTS5 lexical search only. Two retrieval surfaces exist: `kb context` (high-precision, task-shaped, returns citation blocks) and `kb explore` (broad-recall, ideation, returns broader note summaries). Embedding retrieval seams were scaffolded in Phase 5d — config validation, candidate/ranker seam stubs, and doctor visibility — but no production embedding backend was shipped. Search-miss logging turns repeated poor retrieval into reviewable queue items rather than silent failures.

## Alternatives considered

- **Embeddings-first retrieval** — deferred: embedding retrieval has higher latency, requires a model call or local embedding server, and the FTS5 path handles the primary use case with zero latency. The seam exists; embedding can be added later without redesigning the surface.
- **Single unified search surface** — rejected: task-shaped context and broad exploration have different precision/recall trade-offs; a single surface would require callers to tune parameters that should be surface-specific defaults.
- **Remote web fetching for HTML ingest** — rejected: out of scope; local-only HTML ingest covers the primary use case (saved pages, local docs).

## Consequences

- The candidate/ranker seam must be preserved without regression when embedding retrieval is added.
- The FTS5 index must survive atomic rebuild failures — a failed rebuild preserves the previous good index rather than leaving the DB in a partial state.
- Search-miss logging must capture enough context (query, result count, session) for the review queue to surface actionable patterns.
