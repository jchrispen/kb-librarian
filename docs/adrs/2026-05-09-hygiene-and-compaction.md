---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Hygiene and Compaction: Always Review-Gated, Never Autonomous

## Context

Over time, the KB accumulates overlapping notes (covered by different sessions), stale notes (not retrieved in a long time), orphaned notes (no backlinks), and low-utility notes (flagged by agents). A mechanism was needed to detect and address these without risking autonomous knowledge mutation.

## Decision

All knowledge mutations (compaction application, topic reorganization) are review-gated. Cluster detection is deterministic — it uses title/phrase/tag/backlink overlap plus repeated `adds_nuance` verdicts from the review queue. Detection produces proposals with provenance (source note IDs, disposition plans, diff summaries); it never rewrites notes automatically. A cooldown config prevents queue flooding from repeated runs. `kb doctor` is the canonical health check surface: it is read-only, returns nonzero on errors, and covers broken artifacts, stale locks, interrupted state, and auth/backend problems.

## Alternatives considered

- **Autonomous compaction (no review gate)** — rejected: knowledge mutations without human review risk silent loss of nuance or context that the agent did not recognize as unique. This was explicitly out of scope for the entire roadmap.
- **Similarity threshold auto-merge** — rejected: semantic similarity does not imply safe mergability; two notes can be lexically similar while capturing different decisions.
- **Doctor as a mutation surface** — rejected: `kb doctor` is intentionally read-only. Mixing diagnostics and mutation makes it impossible to run safely in CI or monitoring contexts.

## Consequences

- Every destructive hygiene mutation requires an accepted review item and a clean-worktree check before proceeding.
- Compaction proposals must preserve enough provenance for the human to make the acceptance decision without re-reading the full cluster.
- Cooldown config must be tunable so high-ingest environments don't flood the queue.
- `kb doctor` exit code must be nonzero on any error finding, so it is usable as a CI health gate.
