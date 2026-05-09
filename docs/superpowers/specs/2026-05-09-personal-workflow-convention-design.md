# Personal Workflow Convention — Design Spec

**Status:** Designed; not yet adopted in this repo.
**Audience:** Jason (sole user); applied to any repo where AI-assisted work happens.
**Delivery:** A single skill in `.agents/agent-skills/skills/personal-workflow/`.

## Purpose

A consistent, lightweight convention for capturing AI-assisted software work across repos. Splits durable knowledge from transient working state, keeps individual files small and focused, and compacts shipped work into reusable decisions and notes rather than letting plans accumulate as historical clutter.

## Scope

- File and folder layout for specs, ADRs, notes, plans, and research in any repo.
- Lifecycle rules: when artifacts are created, mutated, frozen, or deleted.
- Status conventions and where they live.
- Packaging the convention as an agent-skills skill so any repo can adopt it via the existing submodule install flow.

## Out of scope

- Migration of existing `docs/superpowers/` content in kb-librarian. Migration is a follow-up task once this convention is approved.
- Team conventions; this is a personal convention.
- Slash commands or product-level CLI changes.

## Terminology note

"Compaction" in this spec refers to the workflow operation of distilling a shipped plan into durable artifacts. It is unrelated to kb-librarian's product-level note-compaction feature, which has the same name but a different meaning.

---

## §1 — Folder layout and naming

### Durable artifacts (`docs/`)

```text
docs/
├── specs/
│   ├── _index.md                    # list of all specs in this repo
│   ├── <project>.md                 # thin top-level spec (always exists)
│   └── <project>/                   # sub-specs folder, created when system grows
│       ├── _index.md                # lists children of this sub-spec set
│       └── <subsystem>.md           # one focused sub-spec
├── adrs/
│   ├── _index.md                    # chronological list with one-line summaries
│   └── YYYY-MM-DD-<slug>.md         # immutable; one decision per file
└── notes/
    ├── _index.md
    └── <area>/<topic>.md            # mutable; nested when topics cluster, flat otherwise
```

### Transient artifacts (`.agents/workflow/`)

```text
.agents/workflow/
├── plans/
│   └── YYYY-MM-DD-<slug>/
│       ├── index.md                 # goal, scope, out-of-scope, completion criteria
│       ├── tasks.md                 # required: ordered checklist
│       └── notes.md                 # optional: in-flight working notes
└── research/
    └── YYYY-MM-DD-<slug>/
        ├── index.md                 # the question and current thinking
        └── <topic>.md               # one file per investigation thread
```

### Naming rules

- **Slugs** are kebab-case, descriptive, ≤ 6 words: `kb-librarian-spec-rename`, `provider-fallback-policy`.
- **Dates** are ISO `YYYY-MM-DD`, set when the artifact is first created. Never edited later, even when work spans days.
- **ADRs** are dated, not numbered. Chronological order is what you actually read by, and dates avoid renumbering races and gaps.
- **`_index.md`** is the index-file convention. The underscore prefix sorts above siblings in most listings.
- **Sub-spec folders match the parent spec's filename stem.** `specs/foo.md` ↔ `specs/foo/`.

### Cross-references

- A plan's `index.md` lists any related research folder paths.
- A research folder's `index.md` lists the plan that triggered it (when triggered by one).
- ADRs and notes link back to the spec they apply to using a relative path.

---

## §2 — Lifecycle and compaction

### Creation triggers

| Artifact | Created when |
|---|---|
| Top-level spec (`docs/specs/<project>.md`) | Project starts; always exists |
| Sub-spec (`docs/specs/<project>/<subsystem>.md`) | A subsystem grows non-trivial enough to warrant its own focused spec |
| ADR | A decision is made that future-you would want to audit (chose X over Y, accepted a tradeoff) |
| Note | A reusable lesson, gotcha, or heuristic emerges that isn't a one-shot decision |
| Plan | Any unit of work expected to take more than one substantive turn — small/medium/large all get a folder |
| Research | Open-ended exploration that informs a plan but isn't itself the plan |

### Mutability

- **Specs** — mutable. Update in place when the system changes. Specs describe current truth, not history.
- **ADRs** — immutable once written. To reverse, write a new ADR that supersedes it (link both ways).
- **Notes** — mutable. Edit, refine, merge over time.
- **Plans / research** — mutable while active, deleted when the work ships or is abandoned.

### Plan completion = compaction

1. Verify the work shipped: tests pass, commits landed, behavior verified.
2. Walk the plan's `index.md`, `tasks.md`, optional `notes.md`, and any linked research folders.
3. **Extract:**
   - Each meaningful decision → new ADR in `docs/adrs/`.
   - Each reusable lesson, gotcha, or heuristic → new or updated note in `docs/notes/`.
   - Any spec changes (new behavior, changed contracts) → edits to the relevant spec.
4. Update affected `_index.md` files (specs, adrs, notes).
5. Commit the extracted artifacts as one commit.
6. **Delete** the plan folder and any linked research folders as a separate commit, so the extraction is reviewable independently of the deletion.

### Abandoned plans

If a plan is abandoned (not shipping), still run a stripped-down compaction: extract any decisions about *why we abandoned it* as ADRs, then delete. No silent cleanup.

### `tasks.md` format

- Ordered checklist using `- [ ]` / `- [x]` markers.
- One line per task; link to the relevant file/line as work progresses.
- Mirrors in-session `TaskCreate` state when work spans sessions.

---

## §3 — Status

Status lives where it's meaningful, nowhere else.

| Artifact | Status mechanism | Why |
|---|---|---|
| **Plan** `index.md` | One-line marker at top: `Status: active \| blocked \| shipping` | At-a-glance state when listing the plans folder |
| **Plan** `tasks.md` | The `- [ ]` / `- [x]` checklist itself | Granular progress with no separate state to drift |
| **ADR** | Frontmatter `status: accepted \| superseded-by-<filename-stem> \| deprecated` (e.g. `superseded-by-2026-05-09-provider-fallback-policy`) | Decisions are immutable but their standing changes |
| **Research** `index.md` | One-line marker: `Status: open \| concluded` | Mirrors plan conventions |
| **Spec** | None | Specs describe current truth. If wrong, edit it. No "draft" state — that's what plans are for. |
| **Note** | None | Notes are mutable topical content; the content itself is the state. |

### Aggregation

- `_index.md` files reflect per-artifact status by pulling the one-line marker or frontmatter from siblings. Re-rendered when status changes.
- A plan that ships is *compacted, then deleted*. "Shipped" never appears in the plans folder; the absence of the plan is the signal.
- Superseded ADRs stay in place (immutability). Their `_index.md` entry shows the supersession arrow: `2026-04-01-old-decision → superseded by 2026-05-09-new-decision`.

### Out-of-band

In-session progress (mid-task, mid-conversation) lives in `TaskCreate`, not on disk. Files only reflect status that should survive the session.

---

## §4 — Skill packaging

The convention is delivered as one process skill in the existing `.agents/agent-skills/` submodule, installed into consumer repos by the existing `scripts/install.sh`.

### Skill identity

```yaml
---
name: personal-workflow
description: Use when starting/finishing AI-assisted work in a repo — creating a spec, plan, research thread, ADR, or note; compacting a shipped plan; or bootstrapping the convention into a new repo.
---
```

Path: `.agents/agent-skills/skills/personal-workflow/SKILL.md` (kept under 100 lines per the agent-skills authoring rules).

### SKILL.md shape

A thin router. The body contains:

1. The folder topology in 5–10 lines (the §1 trees, condensed).
2. An intent-to-reference router table:

| Intent | Reference |
|---|---|
| Bootstrap a new (or existing) repo | `references/bootstrap.md` |
| Start or update a spec / sub-spec | `references/artifacts.md` |
| Start a plan, research, ADR, or note | `references/artifacts.md` |
| Update plan status, supersede an ADR | `references/status.md` |
| Compact a shipped or abandoned plan | `references/compact-plan.md` |
| Regenerate `_index.md` files | `references/indexes.md` |

### Reference files (loaded on demand)

- `references/bootstrap.md` — creates `docs/{specs,adrs,notes}/` and `.agents/workflow/{plans,research}/`, seeds `_index.md` files, creates `docs/specs/<project>.md` stub. Idempotent.
- `references/artifacts.md` — naming rules, templates, and frontmatter formats for all five artifact types in one place.
- `references/status.md` — the §3 status table plus mechanics for marking, superseding, and aggregating.
- `references/compact-plan.md` — the step-by-step process from §2; explicit about ADR vs note vs spec-edit routing and the two-commit pattern.
- `references/indexes.md` — `_index.md` format and rebuild instructions.

### Authoring rules honored

- **Trigger-based description.** Leads with "Use when …" and enumerates concrete triggers.
- **Progressive disclosure.** SKILL.md is short; detail in `references/`.
- **Tool-name portability.** Body uses verbs ("read the file", "create the folder") rather than Claude-specific tool names; where a Claude tool is unavoidable, a one-line note maps the equivalent for Codex / OpenCode.
- **One skill, one job.** All triggers cohere around the same workflow lifecycle.

### Skill index entry

A one-line entry added to `.agents/agent-skills/AGENTS.md`:

> `personal-workflow` — Use when starting/finishing AI-assisted work, creating spec/plan/ADR/note/research artifacts, or bootstrapping the convention into a repo.

### Consumer-repo touchpoints

No additional changes to consumer-repo `AGENTS.md` or `CLAUDE.md` beyond what the existing `scripts/install.sh` performs.

---

## Appendix — Open questions for follow-up

These are not blockers for this design but should be tracked:

1. **Migration plan for kb-librarian.** Existing `docs/superpowers/{specs,plan,plans}/` content needs an explicit migration: existing top-level spec → `docs/specs/kb-librarian.md`; phased plan files → either compacted into ADRs or deleted; dated side-plans → already complete, candidates for compaction.
2. **Index regeneration cadence.** Whether `_index.md` files are rebuilt manually, by the skill on each operation, or by a future hook.
3. **Cross-repo ADR/note discoverability.** Whether ADRs ever need to be findable outside the originating repo (probably no; revisit if it becomes painful).
