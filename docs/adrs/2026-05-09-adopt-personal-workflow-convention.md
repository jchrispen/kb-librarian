---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Adopt Personal Workflow Convention for AI-Assisted Work

## Context

kb-librarian development accumulated ad-hoc plan files in `docs/superpowers/plan/` (25 phased files) and `docs/superpowers/plans/` (4 dated files), plus design specs in `docs/superpowers/specs/`. These lived alongside each other with no clear lifecycle rules: plans were never compacted, old specs were never superseded, and there was no distinction between durable decisions and transient working state.

## Decision

Adopt the personal-workflow convention (`docs/specs/`, `docs/adrs/`, `docs/notes/`, `.agents/workflow/`) in this repo. Durable artifacts (specs, decisions, lessons) live in `docs/`; transient working state (active plans, research) lives in `.agents/workflow/`. Shipped plans are compacted into ADRs and notes, then deleted. The convention is packaged as an agent-skills skill (`personal-workflow`) and installed via the existing submodule.

Design spec: `docs/superpowers/specs/2026-05-09-personal-workflow-convention-design.md` (superseded by this migration; the spec file is deleted as part of the migration).

## Alternatives considered

- **Keep the `docs/superpowers/` layout** — rejected: no lifecycle rules means plans accumulate indefinitely; no distinction between durable and transient means agents load irrelevant historical plans into context.
- **Flat docs structure (no subfolders)** — rejected: specs, ADRs, and notes have different mutability and lifecycle rules; mixing them in one folder makes status and lifecycle unclear.
- **External wiki or Notion** — rejected: out of scope for a local-first personal tool; markdown in the repo is grep-able and agent-readable without additional tooling.

## Consequences

- All future plans go in `.agents/workflow/plans/YYYY-MM-DD-<slug>/` (folder per plan).
- Shipped plans are compacted; abandoned plans get an ADR explaining why they were abandoned.
- `docs/superpowers/` is deleted after this migration; do not recreate it.
- The `_index.md` files in `docs/specs/`, `docs/adrs/`, and `docs/notes/` are rebuilt whenever artifacts change.
