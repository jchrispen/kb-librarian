# Agent Instructions

## Project Context

KB Librarian is an agent-first local knowledge base CLI (entry point: `kb`).

- Canonical design spec: `docs/specs/kb-librarian.md` — source of truth for product behavior, architecture, data model, and CLI contracts.
- Transient plans/research: `docs/plans/` (personal-workflow convention).
- Build & test: `docs/dev/testing.md`.

Read the spec before implementing or extending. If a plan and the spec conflict, the spec wins — update the plan or ask before implementing.

Phases 1–6 are shipped. The spec's "Deferred backlog" (embedding-based retrieval, MCP wrapper, TUI/web review, multi-machine helpers, interactive cloud-call confirmation) is out of scope unless explicitly requested. Treat completed work as locked historical context (readable via git history); build forward unless fixing a confirmed regression.

## Workflow

This repo uses the `personal-workflow` skill for specs, plans, ADRs, and notes — durable artifacts in `docs/` (`specs/`, `adrs/`, `notes/`, `dev/`), transient plans in `docs/plans/`. Use the skill for creating plans, compacting shipped plans into ADRs/notes, and rebuilding indexes. General working conventions (commit cadence, diff scope, subagent and review strategy, docs cadence) come from the global harness and the skill — they are not restated here.

When milestone work changes user-visible behavior (CLI commands, flags, outputs, setup flow, review workflow), update user-facing docs (`README.md`, `docs/userguide.md`, the reference docs) in the same change.
