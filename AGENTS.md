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

## Milestone Status Maintenance

When a milestone is completed (for example `01a`), keep its plan file as the historical completion record and update status in place.

- Mark the milestone plan with explicit completion metadata (at minimum: `Status: Complete` and completion date).
- Add or maintain a short completion record listing implemented files and verification commands/results.
- Do not delete or repurpose completed milestone plan files.
- For subsequent work, treat completed milestones as locked historical context and build forward from the next milestone unless fixing a confirmed regression.
- If completion details change (for example, additional verification), append/update the completion record rather than removing prior context.
- Also update the parent phase plan milestone table/order (for example `01-agent-first-walking-skeleton.md`) in the same change so `Complete`/`Next`/`Pending` states stay synchronized.

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
