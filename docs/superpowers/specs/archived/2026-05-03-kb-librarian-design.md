# KB Librarian — Design Spec

**Date:** 2026-05-03
**Status:** Approved design, ready for implementation planning
**Tool repo:** `/mnt/c/workspace/source/internal/kb-librarian/`
**Default data repo:** `/mnt/c/workspace/source/internal/kb/`

## 1. Overview

A personal/team knowledge base built as plain markdown files in git, curated by an LLM "librarian" that ingests raw inputs, atomizes them into linked concept-notes, and maintains the graph over time. Designed to be readable and trustworthy by humans, retrievable and citable by AI agents, and portable across agent tools (Claude Code, Codex, Aider, future tools).

### 1.1 Goals

1. **Single source of truth in plain markdown + git** — readable by any tool, version-controlled by default.
2. **Cross-agent usability** — works identically from Claude, Codex, Aider, and future tools.
3. **Token-efficient retrieval** — agents consult the KB via thin auto-loaded indexes and on-demand reads, not by loading the whole graph.
4. **Anti-black-hole mechanics** — explicit curation surfaces (review queue, staleness flags, search-miss logging, compaction) so the KB stays trusted and findable, not a write-only graveyard.
5. **Visible AI use** — when an agent uses a KB note in a response, the user sees it cited.
6. **Graceful evolution** — start flat, support hierarchical topic reorganization without painful migrations.

### 1.2 Non-goals

- Real-time multi-user collaboration (single-user; teams sync via git like any repo).
- Web/mobile UI (CLI only; mobile capture via syncthing/dropbox).
- Format conversion or export tooling (Obsidian, Logseq, etc., already work on plain markdown + frontmatter).
- Encryption at rest (filesystem-level only).
- OCR / image extraction from PDFs (image-only PDFs surface in review with manual-paste prompt).

### 1.3 Design principles

- **The store is files.** Not a database, not an index — markdown files in git. Any tool that reads files works.
- **CLI is the contract.** Cross-tool portability comes from a `kb` CLI. Per-tool integrations are thin sugar (a Claude skill, a Codex config line) over the same CLI.
- **Trust the store yourself.** The librarian never silently rewrites a note's body. Source additions are automatic; body changes go through review.
- **Lazy retrieval.** Agents auto-load only a thin top-level index; everything else is on-demand.
- **Visible curation.** All maintenance touches surface through one command: `kb review`.

## 2. Architecture

### 2.1 Directory layout (KB data)

```
kb/                                          # data repo (git)
├── INDEX.md                                 # auto-loaded thin index
├── PREAMBLE.md                              # agent behavioral preamble
├── topics/
│   └── <topic>/                             # one dir per topic
│       ├── INDEX.md                         # topic index (lazy-loaded)
│       ├── scope.txt                        # one-paragraph "what this is for"
│       └── <id>.md                          # individual notes
├── raw/                                     # ingest queue
│   ├── <pending files>
│   └── processed/YYYY-MM/                   # archived after ingest
│       └── duplicates/                      # exact-duplicate ingests
├── review/                                  # human-touch surfaces
│   ├── pending-classification.md
│   ├── pending-compaction.md
│   ├── disputes.md
│   ├── duplicates.md
│   ├── stale.md
│   ├── orphans.md
│   ├── search-misses.md
│   ├── merge-proposals.md
│   └── rejected/                            # archived rejected proposals
└── .kb/                                     # tool-managed state
    ├── config.yaml
    ├── ingested.json                        # hash -> ingest record
    ├── backlinks.json                       # cached link graph
    ├── stats.json                           # per-note hit counts
    ├── usage.log                            # all CLI invocations
    ├── errors.log
    ├── search-misses.log
    └── state.json                           # in-flight operation tracking
```

### 2.2 Components

1. **`kb` CLI** — pip-installable Python package; subcommands cover the full lifecycle (`init`, `add`, `ingest`, `search`, `get`, `topics`, `review`, `compact`, `topic promote/split/merge/rename`, `usage`, `log-use`, `flag-suspect`, `reindex`, `doctor`). Talks to LLM via a provider abstraction.
2. **Provider abstraction** — `LLMProvider` protocol with adapter per backend. Anthropic implemented first; OpenAI / Ollama / others added later without librarian changes.
3. **Librarian engine** — internal module orchestrating extract (raw → atomic notes) and compact (cluster → canonical) operations. Uses Sonnet 4.6 for atomization and compaction; Haiku 4.5 for cheap classification. All model choices configurable per-operation.
4. **Index builder** — deterministic regeneration of `INDEX.md`, per-topic `INDEX.md`, and `backlinks.json` from frontmatter. Indexes are never the source of truth; they rebuild from notes.
5. **Review queue** — markdown files in `review/` listing items needing human attention. Surface aggregated by `kb review`.
6. **`/kb-flag` Claude skill** — sugar wrapper that drafts a takeaway from recent conversation, opens `$EDITOR`, pipes to `kb add`. Codex/Aider use `kb add` directly.

### 2.3 Data flow

- **Drop file:** `raw/foo.md` → `kb ingest` → librarian (parse, classify, atomize, integrate) → notes in `topics/<topic>/` → indexes regenerated → raw moves to `raw/processed/`.
- **Flag conversation:** `/kb-flag` drafts → `$EDITOR` → `kb add` → `raw/` → next ingest cycle.
- **Agent retrieval:** auto-loaded `INDEX.md` → agent picks topic → reads topic INDEX → reads specific notes via Read tool, or `kb search` for cross-topic queries.
- **Compaction:** threshold-detected cluster → librarian proposes diff → user accepts/rejects → notes rewritten, links updated → git commit.
- **Hierarchy reorganization:** `kb topic promote/split/merge/rename` → atomic file moves + frontmatter rewrite + index regeneration + single git commit.

## 3. Note schema and data model

### 3.1 Frontmatter

```yaml
---
id: 2026-05-03-vitamin-d-uvb-requirement      # date prefix + slug
title: Vitamin D synthesis requires UVB exposure
topic: nutrition                              # slash-path; e.g., world-religions/christianity
created: 2026-05-03
updated: 2026-05-03
sources:
  - type: article
    ref: raw/processed/2026-05/2026-05-03-paper.md
  - type: conversation
    ref: kb-flag-2026-05-03-1432
confidence: high                              # high | medium | low
status: active                                # active | disputed | superseded
disputes: []                                  # list of note IDs in conflict (when disputed)
tags: [vitamin-d, sunlight, uvb]
---
```

### 3.2 Body conventions

- **Declarative concept-statement titles** ("Vitamin D synthesis requires UVB exposure"), not noun phrases ("Vitamin D"). The title IS the summary; INDEX entries reuse it verbatim.
- **One concept per note** (medium granularity per Question 3 / Option B). 200–1000 words, self-contained, with `[[wikilinks]]` to related concepts.
- **Standard structure:** brief summary paragraph; key claims as bullets; supporting detail; explicit links section optional.

### 3.3 Links

- `[[wiki-style]]` resolved by **filename match** (Obsidian-compatible). This means files can move during topic reorganization without breaking links.
- `kb` maintains `.kb/backlinks.json` as a cached link graph; regenerated on every ingest, compaction, and `kb reindex`.

### 3.4 Confidence model

- Three tiers: `high` / `medium` / `low`. LLM self-assesses based on source type + claim strength. User can edit. Default fallback: `medium`.
- Visible to agents — they should weight `low` claims with less authority and may flag them when responding.

### 3.5 Conflict (dispute) handling

- When integration finds a contradicting claim: both notes get `status: disputed` and a `disputes:` entry pointing at each other. Surfaces in `review/disputes.md`.
- User resolves by editing one or running compaction on the cluster.

### 3.6 Topic structure

- **Slash-path topics:** `topic: world-religions/christianity` mirrors filesystem `topics/world-religions/christianity/`.
- **Notes can live at any level** of the tree (parent-level notes hold cross-cutting content).
- **Recommended max depth: 3 levels.** Warn at 4+.
- **Tags are cross-cutting** — for concerns that don't fit a single topic-path. Topic = home; tags = cross-cuts.

## 4. CLI

### 4.1 Command surface

```
kb init [--data-dir <path>] [--hooks]    Initialize a KB; optionally inject preamble into agent configs
kb add [--topic <t>] [--from-file <f>]   Add raw input from stdin / $EDITOR / file
kb ingest [<file>] [--force] [--quiet]   Process raw/ (or one file)
kb search "<query>" [--topic <t>] [--budget <n>] [--json]
kb get <id> [--summary]
kb topics [--tree]
kb review                                Show all queues
kb compact <topic-or-cluster>            Run compaction with diff review
kb topic promote <topic...> --under <parent>
kb topic split <topic> --into <new...>
kb topic merge <a> <b> --as <name>
kb topic rename <old> <new>
kb usage [--since <duration>] [--note <id>]
kb log-use <id>                          Soft logging from agents
kb flag-suspect <id> "<reason>"          Agent-side trust feedback
kb reindex                               Rebuild INDEX/backlinks from frontmatter
kb doctor [--self-test]                  Self-diagnostic
```

### 4.2 Configuration (`.kb/config.yaml`)

```yaml
data_dir: /mnt/c/workspace/source/internal/kb
providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
operations:
  extract:    { provider: anthropic, model: claude-sonnet-4-6 }
  compact:    { provider: anthropic, model: claude-sonnet-4-6 }
  classify:   { provider: anthropic, model: claude-haiku-4-5 }
  integrate:  { provider: anthropic, model: claude-haiku-4-5 }
search:
  default_budget_tokens: 1500
  index_token_cap: 5000          # paginate topic INDEX above this
indexes:
  topic_sort: alphabetical        # alphabetical | recency | hits
  top_level_min_notes: 1          # hide topics below this in top-level INDEX
review:
  stale_after_days: 180
  orphan_after_days: 30
  duplicate_cluster_threshold: 4   # auto-flag for compaction
  compaction_cooldown_days: 30
hooks:
  session_start_ingest: false
```

### 4.3 Provider abstraction

```python
class LLMProvider(Protocol):
    def complete(
        self,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int,
        response_format: Literal["text", "json"] = "text",
    ) -> str: ...
```

The librarian module calls only this interface. Adding a new provider = one adapter file + one config entry. Anthropic is built first (Phase 1); OpenAI, Ollama, others are explicitly Phase 5+.

### 4.4 Packaging

- `pyproject.toml` with `[project.scripts] kb = "kb.cli:main"`.
- Distributable via `pip install kb-librarian` (PyPI eventually) or `pip install -e .` for local dev.
- Dependencies (initial): `anthropic`, `pyyaml`, `click` or `typer`, `python-frontmatter`, `pypdf`, `readability-lxml`.
- Tool repo separate from KB data repo: tool is reinstalled without affecting your knowledge.

## 5. Ingest pipeline

### 5.1 Top-level flow

```
For each file in raw/:
  1. Dedup check     — SHA-256; consult .kb/ingested.json
  2. Parse           — md/pdf/html/txt → text
  3. Determine topic — subdir → frontmatter → classifier → review queue
  4. Atomize         — Sonnet call: doc → list of candidate concept-notes
  5. Integrate       — for each candidate: similarity check → create / merge proposal / dispute / source-append
  6. Update indexes  — top-level INDEX, topic INDEX, backlinks.json
  7. Archive raw     — move to raw/processed/YYYY-MM/
```

### 5.2 Dedup behavior

- **Hash present, status=success:** duplicate. File moves to `raw/processed/YYYY-MM/duplicates/`, entry written to `review/duplicates.md`. Surfaces in `kb review`. Does **not** silently re-process.
- **Hash present, status=error:** retried; cleared on success.
- **Hash absent:** processed normally.
- **Same filename, different content:** treated as new (different hash); archive auto-suffixes (`paper.pdf` → `paper-2.pdf`); new hash recorded in `.kb/ingested.json`.
- **Updated version of previously-ingested file** (filename match, hash differs): processed as new + entry in `review/duplicates.md` flagging "you re-ingested an updated version; existing notes may need revision."
- **`kb ingest <file> --force`** clears the hash entry and re-runs.
- **Near-duplicates** (same content in different formats, e.g., PDF + HTML) caught at integration step, not file level — matching atomic claims hit the `identical` verdict and append sources.

### 5.3 Parsing

- `.md`, `.txt` — direct text
- `.pdf` — `pypdf` for text-only PDFs; image-only PDFs queued in review with manual-paste prompt
- `.html` — `readability-lxml` strips chrome
- `.json` — extractor per known schema (chat exports etc.)
- Unsupported types — leave in `raw/`, log to `.kb/errors.log`, surface in `kb review`

### 5.4 Topic determination

In order:
1. File at `raw/<topic>/...` → use that topic path
2. File frontmatter has `topic:` → use it
3. Classifier call (Haiku) — given existing topic tree + scope blurbs, returns most-specific fitting topic + confidence
   - High confidence → use it
   - Low confidence or "create new" → write to `review/pending-classification.md`, skip extraction this run; user confirms in review and file re-queues

The classifier prompt includes the **full topic tree** with scope blurbs, so it can pick a leaf, a parent, or "no fit." It prefers existing topics over creating new ones.

### 5.5 Atomization

Single structured call to the configured `extract` model (Sonnet 4.6 default). Prompt includes:

- Topic and scope blurb
- Existing topic INDEX (titles only) for similarity priming
- Document text (chunked by section/heading if >50K tokens; cross-chunk dedup pass after)

Output (JSON): list of `{title, body, suggested_links, claims, confidence}`. Titles must be declarative concept-statements; bodies 200–1000 words.

### 5.6 Integration — the trust-preserving core

For each candidate note from atomization:

```
a. Cheap candidate filter: title-token overlap + tag match against topic INDEX → top 5 existing notes
b. Verdict call (Haiku): "Is candidate <X> identical | adds_nuance | contradicts | unrelated to existing note <Y>?"
c. Action by verdict:
   - identical    → AUTOMATIC: append new source to existing note's `sources:` field. No body change.
   - adds_nuance  → PROPOSAL: write to review/merge-proposals.md with diff. User accepts or rejects. Never silent body edits.
   - contradicts  → AUTOMATIC: mark BOTH notes status: disputed, link via `disputes:` field, append to review/disputes.md.
   - unrelated    → AUTOMATIC: create new note with full frontmatter.
```

**Key invariant:** the librarian never silently edits an existing note's body. Sources additions are safe (provenance grows). Body edits go through review. This preserves "trust the store yourself."

### 5.7 Index regeneration

Deterministic rebuild from frontmatter — never hand-maintained. Runs after every ingest, compact, and reorganization, or on demand via `kb reindex`. Generates:

- `kb/INDEX.md` — top-level: topics + descendant counts + last-update + scope blurb
- `kb/topics/<topic>/INDEX.md` — local sub-tree + declarative-title note list
- `.kb/backlinks.json` — `[[wikilink]]` → list of referencing notes

Topic INDEXes paginate when over `index_token_cap` (default 5K tokens) — split by date range.

### 5.8 Archival

- Successful ingest: file moves to `raw/processed/YYYY-MM/<filename>`. Hash recorded in `.kb/ingested.json`.
- Filename collision in `processed/`: auto-suffix.
- Errored ingest: file stays in `raw/`, error logged to `.kb/errors.log`. Retried on next run.

### 5.9 Cost rough-out

Per typical 10-page raw doc:
- Classify (Haiku): ~$0.001
- Atomize (Sonnet): ~$0.15
- Integration LLM checks: ~$0.02
- **Total: ~$0.17 per doc, ~$2/week at 10 docs/week.**

## 6. Hierarchical topics

### 6.1 Principles

- **Start flat, evolve via tooling.** Hierarchy is supported from day one but not required. Most topics stay 1–2 levels.
- **Filesystem mirrors hierarchy.** No metadata-only hierarchy.
- **Reorganization must be cheap** — supported by atomic CLI commands that move files, rewrite frontmatter, regenerate indexes, and commit in one shot.
- **Wikilinks survive reorganization** because they resolve by filename, not path.

### 6.2 Layout example

```
topics/world-religions/
├── INDEX.md
├── scope.txt
├── comparison-of-creation-myths.md      ← cross-cutting parent-level note
├── christianity/
│   ├── INDEX.md
│   ├── scope.txt
│   └── 2026-05-03-trinity-doctrine.md
├── islam/
│   └── ...
└── buddhism/
    └── ...
```

### 6.3 Frontmatter and search

- `topic: world-religions/christianity` — full slash-path. Self-describing.
- `kb search --topic world-religions` searches the parent and all descendants.
- Top-level INDEX caps display depth at 1 (just topic-level + roll-up counts) for terseness; sub-topics visible when you Read the topic INDEX.

### 6.4 Reorganization commands

- **`kb topic promote <topic...> --under <parent>`** — moves topic dirs into a new parent, updates `topic:` fields, regenerates indexes. Single git commit. Stubs `scope.txt` for the new parent.
- **`kb topic split <topic> --into <new...>`** — LLM-assisted: classifies each note into a new sub-topic, surfaces assignments in `review/topic-split-proposal.md` for approval, then moves files. Notes that don't fit any sub-topic stay at the parent level.
- **`kb topic merge <a> <b> --as <name>`** — combines over-similar topics (rare).
- **`kb topic rename <old> <new>`** — trivial dir move + frontmatter update + reindex.

### 6.5 Auto-classify with hierarchy

The classifier prompt includes the full topic tree with scope blurbs. It picks the **most specific** existing topic that fits — leaf if specific, parent if broad. Returns "create new" (going to review) only when nothing fits.

### 6.6 Limits

- Recommended max depth: 3 levels.
- Warn at 4+ — typically a sign of over-categorization.
- Hard limit: none. The librarian's prompt nudges toward flatter structures.
- **No multiple parents.** A note has one `topic:` (its home). Cross-cutting goes through `tags:`.

## 7. Retrieval

### 7.1 Token-efficient layered loading

| File | Loaded when | Typical size |
|---|---|---|
| `kb/INDEX.md` | Auto-loaded every session via agent's preamble | <1 KB |
| `kb/topics/<topic>/INDEX.md` | On-demand Read when agent picks topic | 2–5 KB at 50 notes |
| Individual note | On-demand Read | 300–1500 tokens |
| `backlinks.json` | Never loaded — only queried via CLI | n/a |

Total auto-load cost: ~500–1000 tokens at 30 topics, **flat** as the KB grows. Only topic count affects always-in-context size.

### 7.2 INDEX format examples

Top-level (terse):
```markdown
# Knowledge Base
- [nutrition](topics/nutrition/) — diet, supplements, sleep+health (42 notes, updated 2026-05-01)
- [ml-research](topics/ml-research/) — papers, training, inference (87 notes, updated 2026-05-03)
- [world-religions](topics/world-religions/) — comparative religion (95 notes across 3 sub-topics, updated 2026-05-03)
```

Topic INDEX:
```markdown
# Nutrition
> Diet, supplements, sleep–health interactions.
> Out of scope: clinical medical advice, individual case studies.

- [vitamin-d-uvb](2026-05-03-vitamin-d-uvb.md) — UVB exposure required for cutaneous synthesis
- [magnesium-cofactors](2026-04-12-magnesium-absorption.md) — vitamin D and B6 are absorption cofactors
```

### 7.3 `kb search` returns summaries by default

```
$ kb search "vitamin d UVB"
1. vitamin-d-uvb (nutrition, conf=high) — UVB exposure required for synthesis
2. sunlight-windows (nutrition, conf=medium) — Glass blocks UVB; sunlight through windows insufficient
3. seasonal-vitamin-d (nutrition, conf=high) — Northern latitudes can't synthesize Oct-Mar
```

Agent reads `kb get <id>` only when the body is needed. `--budget <n>` caps result-set size in tokens; default 1500.

Ranking signals: frontmatter topic/tag match, title match, body match, recency tiebreaker. Returns IDs + summaries + scores, never full bodies (use `--full` to override).

### 7.4 Agent integration

Each agent tool's auto-load file references `kb/PREAMBLE.md`:

| Tool | Auto-load file |
|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (global) or per-project |
| Codex CLI | `AGENTS.md` |
| Aider | `CONVENTIONS.md` (referenced via `.aider.conf.yml`) |
| Cursor | `.cursorrules` |
| Future | One config entry away |

`kb init` writes the preamble and offers to inject the include line into each detected agent's auto-load file. User confirms per-agent.

### 7.5 PREAMBLE.md (verbatim, lives in `kb/`)

```markdown
# Knowledge Base
This system has a curated knowledge base at `<data-dir>`.
The top-level index is at `kb/INDEX.md` — read it now.

## When to consult
- Before answering questions in any listed topic, check that topic.
- After a response that taught you something the KB lacks, run `/kb-flag` (Claude) or `kb add` (other tools).

## How to retrieve
1. Skim top-level INDEX.md — pick relevant topic.
2. Read at most ONE topic INDEX.md.
3. Use `kb search "..."` for cross-topic queries — don't read multiple topic indexes.
4. Read individual notes only when the INDEX line suggests direct relevance.
5. Never load multiple notes "just in case."

## Trust signals on each note
- `confidence: high|medium|low`
- `status: active|disputed|superseded`
- `updated:` date
If you use a note and the user corrects it, run `kb flag-suspect <id> "<reason>"`.

## Citation requirement
When you use a KB note in a response, end the response with a citation block:

---
**KB sources:** [<note-id>](<path>) (confidence: <level>) · [<note-id>](<path>) (confidence: <level>)

After using a note, also call `kb log-use <id>` to record the use (soft logging — best-effort).
```

### 7.6 In-response citation

Committed format: **end-of-response citation block** (Question 7 layer-2 / Option A).

```
...your answer text...

---
**KB sources:** [vitamin-d-uvb-requirement](kb/topics/nutrition/2026-05-03-vitamin-d-uvb.md) (confidence: high) · [seasonal-vitamin-d](kb/topics/nutrition/2026-04-12-seasonal-vitamin-d.md) (confidence: high)
```

### 7.7 Soft usage logging

- Agent calls `kb log-use <id>` after using a note. Best-effort; not enforced.
- All `kb` CLI invocations append to `.kb/usage.log` regardless.
- Hit counts feed `.kb/stats.json`, used for staleness flags and compaction prioritization.
- **Hard tracking** (PostToolUse hook on Read scanning for `kb/topics/` paths) documented as opt-in.

## 8. Compaction

### 8.1 Trigger

Threshold-based, with mandatory review gate (Question 8 / Option C).

**Detection mechanism.** Compaction candidates are detected by a periodic scan that runs as a post-pass after `kb ingest` (and on demand via `kb reindex --scan-clusters`):

1. For each topic (leaf-level), compute pairwise similarity across all notes using the cheap candidate filter (title-token overlap + tag overlap).
2. For pairs scoring above a similarity floor, run a Haiku verdict call: `identical | adds_nuance | contradicts | unrelated`.
3. Group `identical` and `adds_nuance` pairs into clusters via union-find.
4. When a cluster size reaches `duplicate_cluster_threshold` (default 4), write the cluster to `review/pending-compaction.md` with note IDs, similarity scores, and a one-line summary of overlap.
5. To keep the scan cheap, the cluster scan runs only on topics that received new notes since the last scan (tracked in `.kb/state.json`). A full rescan is available via `kb reindex --scan-clusters --all`.

**Other compaction triggers** (additive):
- A topic accumulates >N unresolved `adds_nuance` proposals in `review/merge-proposals.md` — the related notes get bundled as a single compaction candidate.
- User invokes `kb compact <topic>` directly without a flagged cluster — librarian generates a proposal across the whole topic.

Flagged clusters appear in `review/pending-compaction.md`. User runs `kb compact <cluster-id>` (or `kb compact <topic>`) to enter the proposal flow.

### 8.2 Proposal flow

1. Librarian (Sonnet) reads the cluster, drafts a canonical note.
2. CLI shows a diff: which notes will be deleted, which will be created, which links rewrite.
3. User accepts or rejects.
4. **Accept:** notes rewritten, deletions performed (destructive — git is the version history; no archive folder), backlinks rewritten, indexes regenerated, single git commit.
5. **Reject:** proposal moves to `review/rejected/<date>-<id>.md`. Same cluster won't re-flag for `compaction_cooldown_days` (default 30).

### 8.3 Subtree-aware compaction

`kb compact world-religions` operates on the whole subtree; `kb compact world-religions/christianity` operates on a single leaf. Threshold flags fire per leaf-topic by default.

## 9. Anti-black-hole mechanisms

The token-efficient index solves retrieval cost; this section solves findability and trust over time.

### 9.1 Failure modes addressed

| Failure mode | Mitigation |
|---|---|
| Agent doesn't consult the KB | PREAMBLE explicitly instructs consultation per topic; topics-of-interest visible in auto-load |
| Notes outdated/contradicted | `updated:` date in INDEX entries; `status: disputed` visible to agents; stale flagging (>180d in active topics → review); source provenance enables verification |
| Same concept in many places | Threshold-triggered compaction with review-gated diff |
| Information not findable | Search-miss logging (`kb search` → `.kb/search-misses.log` → `review/search-misses.md` as "consider adding a note about: X") |
| Orphaned notes | No-link-in/out after 30d → `review/orphans.md` |
| Trust collapse from wrong content | Confidence tiers visible to agents; `kb flag-suspect` for in-session correction; visible audit trail in git |
| Can't tell what's in the KB | Declarative titles; per-topic scope blurbs; `kb topics --tree` |

### 9.2 The `kb review` curation hub

Single command surfaces all human-touch surfaces:

```
$ kb review
Pending auto-classifications: 3
  raw/processed/2026-05-02-paper.md → suggested topic: ml-research (confirm? [y/n])
Compaction candidates: 1 cluster
  nutrition/ — 4 near-duplicate notes about vitamin D
Stale notes (>180d, active topic): 2
Orphan notes (no links, >30d): 1
Disputed: 0
Duplicate ingest attempts: 2
  raw/2026-05-03-paper.pdf — already ingested 2026-04-12 (created 3 notes)
Frequent search misses: 2
  "kubernetes ingress" (5 misses, no notes)
```

Designed to be a ~5-minute weekly ritual. Sustainability is the point — perfect curation that you abandon is worse than imperfect curation you keep doing.

### 9.3 Telemetry

- `.kb/stats.json` — hit count per note, last-hit time
- `.kb/usage.log` — full CLI invocation history
- `.kb/search-misses.log` — queries with zero or low-relevance results

All stats stay in `.kb/`. Nothing leaves the machine except LLM API calls.

## 10. Conversation flagging — `/kb-flag`

### 10.1 Claude skill flow

1. User: `/kb-flag` (optionally `/kb-flag <topic-hint>`).
2. Skill reads the last N conversation turns.
3. Drafts a markdown takeaway with frontmatter (topic filled if hinted, blank otherwise).
4. Opens `$EDITOR` with the draft.
5. User edits + saves (or quits empty to abort).
6. Skill pipes saved content to `kb add --topic <hint>`.
7. `kb add` writes to `raw/<topic>/` (or `raw/`) and exits.
8. Next `kb ingest` (manual, hooked, or cron) processes it normally.

### 10.2 Cross-tool equivalent

Codex / Aider / others use `kb add` directly. Reads stdin or pops `$EDITOR` if neither stdin nor `--from-file` provided. Same destination, same downstream pipeline.

## 11. Edge cases

| Case | Behavior |
|---|---|
| API key missing/invalid | `kb ingest` fails fast with clear message; raw files untouched |
| API rate-limit / 5xx | Exponential backoff (3 retries); persistent failure → file stays in `raw/`, error logged, surfaces in review |
| Mid-pipeline crash | Each step idempotent; `.kb/state.json` tracks in-flight; `kb ingest --resume` is the default |
| User manually edits a note | Safe — librarian never auto-edits bodies, only appends sources |
| Concurrent ingest | Lockfile at `.kb/ingest.lock`; second instance refuses |
| INDEX corruption / hand-edit | `kb reindex` regenerates from frontmatter; INDEX is never source of truth |
| Topic INDEX exceeds token cap | Auto-paginates by date range |
| Git conflicts (multi-machine sync) | Sorted-key frontmatter minimizes diff churn; `kb reindex` after pull |
| Compaction proposal rejected | Original notes untouched; cooldown prevents re-flag |
| `/kb-flag` invoked with no recent conversation | Skill warns and offers manual entry mode |
| Skill drafts but user quits editor empty | `kb add` aborts gracefully |
| Image-only PDF | Queued in review with manual-paste prompt |
| Broken `[[wikilink]]` (target deleted/renamed) | `kb doctor` flags; `kb reindex` reports |

## 12. Testing strategy

### 12.1 Layered approach

LLM-driven systems can't be unit-tested in the usual sense. Strategy:

1. **Deterministic logic — full unit-test coverage:** parsers, frontmatter parse/write round-trip, hash dedup, topic resolution chain, INDEX regeneration (golden output), backlink graph, CLI argument parsing, lock/state handling.
2. **Provider abstraction — mocked LLM tests:** librarian module tested against mock provider returning scripted responses. Verifies atomization → integration decision flow, create/merge/dispute branching per verdict, error-path handling, idempotency on re-run.
3. **Golden corpus — real LLM calls, on demand:** ~10 representative raw documents with expected outputs (note count, topics, links). `kb test --corpus tests/golden/`. Few cents per run, not in default CI. Catches prompt regressions when prompts change.
4. **`kb doctor` self-diagnostic:** user-facing. Checks config validity, API reachability, frontmatter integrity, INDEX/frontmatter consistency, backlinks consistency, orphans, stats readability, disk/permissions.
5. **Manual smoke test before release:** end-to-end `kb init` → drop PDF → ingest → verify → `/kb-flag` from session → `kb review` → `kb compact` → `kb search`.

## 13. Build phases

### Phase 1 — Walking skeleton

The smallest thing that proves the loop works.

1. Python package scaffold (`pyproject.toml`, `kb` entry point).
2. Provider abstraction + Anthropic adapter only.
3. Frontmatter parsing/writing + note schema.
4. `kb init` — directory structure, config, preamble.
5. `kb add` — stdin / `$EDITOR` → `raw/`.
6. `kb ingest` — atomization + integration (create / identical / contradicts / adds_nuance) + topic determination chain (subdir → frontmatter → classifier → review queue).
7. `kb reindex` — INDEX.md regeneration from frontmatter.
8. `kb search` — basic title/body/tag matching with budget flag.
9. `kb get` — fetch a note.
10. `kb topics` — list.

Definition of done: drop a markdown file → ingest → notes appear → search and retrieve them.

### Phase 2 — Daily-use ergonomics

1. `kb review` + the queues it surfaces (classification, duplicates, disputes, errors).
2. `/kb-flag` Claude skill (drafts → `$EDITOR` → `kb add`).
3. PDF + HTML parsers.
4. Hash-based dedup with `raw/processed/duplicates/` archival.
5. `kb usage` + soft `kb log-use` + `.kb/usage.log`.
6. Citation block enforcement in PREAMBLE.

Definition of done: this is what you live with daily.

### Phase 3 — Long-term hygiene

1. Compaction (threshold detection + LLM-proposed diff + accept/reject).
2. Stale-note flagging.
3. Orphan flagging.
4. Search-miss logging.
5. `kb doctor` self-diagnostic.
6. INDEX pagination.
7. **Hierarchical topics + reorganization commands** (`kb topic promote/split/merge/rename`).

### Phase 4 — Robustness and polish

1. Concurrent-ingest lockfile + `--resume`.
2. Golden corpus test harness (`kb test`).
3. Better backoff/retry on API failures.
4. SessionStart hook + cron job templates (opt-in via `kb init --hooks`).
5. End-to-end fixtures (`kb doctor --self-test`).

### Phase 5+ — Deferred

- Additional LLM providers (OpenAI, Ollama).
- MCP server wrapper (only if structured tool calls beat shelling out).
- Embedding-based semantic similarity for the integration step.
- Web UI / TUI for `kb review`.
- Multi-machine sync conflict tooling.
- Auto-summarized topic scope blurbs.

## 14. Out of scope (explicit)

- Real-time collaboration / multi-user edits.
- Versioned semantic history (git is the version history).
- Format conversion to other note tools (Obsidian/Logseq already work on plain markdown).
- Mobile capture (drop files via syncthing/dropbox; CLI runs on host).
- Image/diagram extraction from PDFs.
- Encryption at rest.
- Telemetry to external services.

## 15. Success criteria

You'll know this design is working when:

1. You stop thinking about "where do my notes go" — the system has obvious places for everything.
2. Agents start citing KB notes in answers without reminders.
3. `kb review` takes <5 minutes a week and the queues stay short.
4. You can switch from Claude to Codex (or back) without losing access to the KB.
5. After 6 months, the KB hasn't become a black hole — search returns useful results, compactions have happened, stale notes are flagged.

## Appendix A — Open questions resolved during brainstorm

| # | Question | Decision |
|---|---|---|
| 1 | Primary input source | Mix: drop into `raw/` + skill-flagged conversations |
| 2 | Topic separation | Hybrid — root + topic-scoped subdirs with cross-links |
| 3 | Atomicity | One concept per note (medium granularity) |
| 4 | Retrieval mechanism | `kb` CLI as universal contract; thin auto-loaded INDEX; optional Claude skill |
| 5 | Topic assignment for `raw/` | Subdir convention + auto-classify fallback |
| 6 | Librarian engine location | Standalone CLI calls LLM API directly |
| 7 | Conversation flagging UX | Agent drafts, user confirms in `$EDITOR` |
| 8 | Compaction trigger | Threshold-triggered with mandatory review gate; destructive (git is history) |
| 9 | Schema basics | Frontmatter as specified, `[[wikilinks]]`, `raw/processed/` archival, 3-tier confidence, dispute via status + link |
| 10 | Token efficiency | Hierarchical lazy index loading, summary-by-default search, declarative titles, budget flags, agent discipline in PREAMBLE |
| 11 | Anti-black-hole | Active retrieval prompting + staleness flags + source provenance + compaction + search-miss logging + `kb review` ritual |
| 12 | Visible AI use | End-of-response citation block + soft `kb log-use` + visible Bash/Read tool calls |
| 13 | Hierarchical topics | Slash-path topics; filesystem mirrors hierarchy; reorganization via `kb topic promote/split/merge/rename`; recommended max depth 3 |
| 14 | Pluggable LLM provider | `LLMProvider` protocol; Anthropic first; OpenAI / Ollama deferred |
| 15 | Distribution | pip-installable Python package; tool repo separate from KB data repo |
