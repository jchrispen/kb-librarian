---
status: accepted
date: 2026-06-26
spec: ../specs/kb-librarian.md
supersedes: 2026-05-09-adopt-personal-workflow-convention
---

# Migrate to the Single-`docs/` Personal-Workflow Topology

## Context

[2026-05-09-adopt-personal-workflow-convention](2026-05-09-adopt-personal-workflow-convention.md) adopted an earlier version of the personal-workflow convention: durable artifacts in `docs/`, transient working state in a separate `.agents/workflow/` tree, and the skill installed per-project via a git submodule (`.agents/agent-skills`) with `.claude/skills/*` symlinks.

Two things changed:

- The Claude harness now loads the personal-workflow skill (and the rest) **globally** from `~/.claude`, making the per-project submodule and symlinks redundant.
- The personal-workflow convention itself moved to a **single `docs/` tree**: transient plans live in `docs/plans/`, developer internals in `docs/dev/`, with a single `docs/userguide.md`. There is no separate `.agents/workflow/` tree.

## Decision

Migrate the repo to the current single-`docs/` topology and remove the per-project skill-delivery rig.

- Remove the `.agents/agent-skills` submodule, `.gitmodules`, and the `.claude/skills/*` symlinks (skills load globally now).
- Move transient state from `.agents/workflow/plans|research/` to `docs/plans/`; delete `.agents/`.
- Add `docs/dev/` for developer internals (`testing.md`, `releasing.md`, `codex-kb-demo.md`).
- Rename `docs/user-guide.md` → `docs/userguide.md`; flatten `docs/notes/` to `<area>-<topic>.md`.
- Trim `AGENTS.md` to project-specific context plus pointers; general working conventions come from the global harness and the skill.

## Consequences

- All future plans go in `docs/plans/YYYY-MM-DD-<slug>/`.
- The convention is no longer vendored in this repo; it relies on the globally installed skill.
- User-facing reference docs (`command-reference.md`, `configuration.md`, `troubleshooting.md`) remain as separate files at `docs/` root, linked from `README.md` and `docs/userguide.md`.
- Compaction, indexes, and status mechanics follow the current `personal-workflow` skill references.
