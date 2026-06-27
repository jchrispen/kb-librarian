# Agent Instructions

## Project Context

This repo contains planning and design material for KB Librarian.

- Canonical design spec: `docs/specs/kb-librarian.md`
- Implementation plans: `.agents/workflow/plans/`

When implementing or extending KB Librarian, read the canonical design spec for product behavior, architecture, data model, and CLI contracts. Use the implementation plan files for sequencing and task boundaries. If a plan and the spec appear to conflict, treat the spec as the source of truth and update the plan or ask for clarification before implementing.

Phases 1–6 are shipped. The spec's "Deferred backlog" (currently: embedding-based retrieval, MCP wrapper, TUI/web review, multi-machine helpers, interactive cloud-call confirmation) is not in scope unless explicitly requested.

## Milestone Implementation

When implementing a milestone, assign the whole runnable increment to a coding-focused model with enough reasoning for cross-file integration, CLI behavior, and tests. Use smaller models only for isolated helper changes or narrow test additions.

Keep implementation scoped to the active milestone. Preserve earlier milestone interfaces and data layouts unless the canonical spec requires a change. Run the relevant automated tests before handing off, and note any tests that could not be run.

## Documentation and Commit Cadence

When milestone work changes user-visible behavior (CLI commands, flags, outputs, setup flow, or review workflow), update user-facing documentation in the same milestone rather than deferring documentation to later.

Commit implementation work in regular, meaningful increments during the milestone (not only one large end-state commit), and make a final milestone commit after verification and plan-status updates are complete.

## Milestone Status Maintenance

When a milestone is completed, compact the plan using the `personal-workflow` skill's compact-plan flow: extract decisions and notable outcomes into `docs/adrs/` or `docs/notes/`, then delete the plan folder. The durable record lives in those extracted artifacts, not in the plan itself.

- Before compacting, ensure the extracted ADR or note captures: what was built, key decisions made, and any interfaces or data layouts that subsequent milestones depend on.
- For subsequent work, treat completed milestones as locked historical context (readable via git history) and build forward from the next milestone unless fixing a confirmed regression.
- Also update the parent phase plan milestone table (for example `01-agent-first-walking-skeleton.md`) in the same change so `Complete`/`Next`/`Pending` states stay synchronized.

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
