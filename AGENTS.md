# Agent Instructions

## Project Context

This repo contains planning and design material for KB Librarian.

- Canonical design spec: `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`
- Implementation plans: `docs/superpowers/plan/`

When implementing or extending KB Librarian, read the canonical design spec for product behavior, architecture, data model, and CLI contracts. Use the implementation plan files for sequencing and task boundaries. If a plan and the spec appear to conflict, treat the spec as the source of truth and update the plan or ask for clarification before implementing.

The Phase 5+ items in the spec are deferred backlog. Do not create or implement Phase 5 work unless explicitly requested.

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
