# kb-librarian Personal Workflow Migration — Design Spec

**Status:** Approved; implementation pending.
**Date:** 2026-05-09
**Audience:** Jason (sole user).

## Purpose

Migrate kb-librarian's existing `docs/superpowers/` workflow artifacts to the personal-workflow convention (`docs/specs/`, `docs/adrs/`, `docs/notes/`, `.agents/workflow/`). All ~29 shipped plan files are compacted thematically into ADRs and notes, then deleted. The existing spec files are replaced by a fresh top-level spec written from current code.

## Scope

- Bootstrap the personal-workflow folder structure in kb-librarian.
- Thematic compaction of all shipped plan files into 6–8 ADRs and 2 notes.
- Fresh `docs/specs/kb-librarian.md` written from current codebase (not migrated verbatim).
- Deletion of `docs/superpowers/` tree after extraction.
- Update all `_index.md` files.

## Out of scope

- Changes to product docs (`docs/user-guide.md`, `docs/configuration.md`, etc.).
- Changes to source code or tests.
- Migration of `.agents/agent-skills/` submodule (already correct).

## Target folder structure

```
docs/
├── specs/
│   ├── _index.md
│   └── kb-librarian.md          ← fresh write from current code
├── adrs/
│   ├── _index.md
│   └── 2026-05-09-*.md          ← 6–8 thematic ADRs
└── notes/
    ├── _index.md
    ├── providers/
    │   └── cli-delegation-pitfalls.md
    └── ingest/
        └── pdf-html-parsing.md

.agents/workflow/
├── plans/                       ← empty after migration
└── research/                    ← empty after migration
```

`docs/superpowers/` is deleted entirely after extraction.

## ADR themes

| File stem | Decisions captured |
|---|---|
| `agent-first-architecture` | KB as in-process substrate; SQLite storage; no separate server |
| `provider-backend-seams` | Provider/credential-source abstraction; fallback policy design |
| `account-auth-delegation` | Anthropic CLI delegation; Codex CLI delegation; auth policy |
| `retrieval-and-search` | FTS-first; embedding seams deferred; context/explore/search surface |
| `hygiene-and-compaction` | Compaction-detection flow; doctor diagnostics; review-gated mutations |
| `robustness-patterns` | Ingest locking; atomic recovery; provider retry |
| `adopt-personal-workflow-convention` | Decision to adopt this convention in this repo |

The existing `docs/superpowers/specs/2026-05-09-personal-workflow-convention-design.md` is folded into the `adopt-personal-workflow-convention` ADR.

## Notes

- `providers/cli-delegation-pitfalls.md` — auth refresh edge cases from Phase 06 work.
- `ingest/pdf-html-parsing.md` — parsing quirks encountered in Phase 04c.

## Commit strategy

Four commits in order:

1. **Bootstrap** — create new folder structure, seed `_index.md` files.
2. **Extract** — write fresh spec, all ADRs, all notes, update indexes.
3. **Delete old plans** — `rm -rf docs/superpowers/plan/ docs/superpowers/plans/`
4. **Delete old specs** — `rm -rf docs/superpowers/specs/ docs/superpowers/`

Commits 3 and 4 are separate so the extraction is reviewable independently of the cleanup.

## Approach

Thematic extraction: read all plan files together, group decisions by domain, write ADRs per domain. This produces fewer, higher-signal records than one-per-plan compaction.

## Source files consumed

**Plans (all shipped):**
- `docs/superpowers/plan/01-*.md` through `docs/superpowers/plan/06e-*.md` (~25 files)
- `docs/superpowers/plans/2026-05-07-packaging-and-distribution.md`
- `docs/superpowers/plans/2026-05-07-repo-command-specs.md`
- `docs/superpowers/plans/2026-05-07-test-coverage-tracking.md`
- `docs/superpowers/plans/2026-05-09-implement-personal-workflow-skill.md`

**Specs (replaced or folded):**
- `docs/superpowers/specs/kb-librarian-agent-first-design.md` — source of truth for fresh spec write
- `docs/superpowers/specs/2026-05-09-personal-workflow-convention-design.md` → folded into ADR
- `docs/superpowers/specs/archived/2026-05-03-kb-librarian-design.md` → deleted (superseded)
