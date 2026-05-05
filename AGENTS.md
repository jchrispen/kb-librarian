# Agent Instructions

## Project Context

This repo contains planning and design material for KB Librarian.

- Canonical design spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`
- Implementation plans: `docs/superpowers/plan/`

When implementing or extending KB Librarian, read the canonical design spec for product behavior, architecture, data model, and CLI contracts. Use the implementation plan files for sequencing and task boundaries. If a plan and the spec appear to conflict, treat the spec as the source of truth and update the plan or ask for clarification before implementing.

The Phase 5+ items in the spec are deferred backlog. Do not create or implement Phase 5 work unless explicitly requested.

## Milestone Implementation

When implementing a milestone, assign the whole runnable increment to a coding-focused model with enough reasoning for cross-file integration, CLI behavior, and tests. Use smaller models only for isolated helper changes or narrow test additions.

Keep implementation scoped to the active milestone. Preserve earlier milestone interfaces and data layouts unless the canonical spec requires a change. Run the relevant automated tests before handing off, and note any tests that could not be run.

## Implementation Planning

When creating implementation plans, optimize for future coding agents with limited context.

Use a hierarchy:

- One concise parent plan per phase.
- Several milestone plans per phase.

Each milestone must be:

- Independently executable.
- Small enough for one agent session.
- Ordered by dependency.
- Runnable and testable at the end.
- Explicit about public interfaces, data/state changes, tests, acceptance criteria, and out-of-scope items.

Avoid large omnibus task lists. Split work by runnable increments, not by abstract subsystem. Do not create implementation code unless explicitly requested.
