# KB Librarian — Agent-First Design Spec

**Status:** Phases 1–6 implemented; this spec describes the shipped system.  
**Tool repo:** `/mnt/c/workspace/source/internal/kb-librarian/`  
**Default data repo:** `~/.kb/.library`, next to the default config at `~/.kb/config.yaml`  
**Design thesis:** Agent-accessible knowledge artifact, published as files, inspectable by humans when needed.

---

## 1. Overview

KB Librarian is a local-first agent context system. It maintains a durable knowledge artifact that improves over time and is optimized for AI agents to retrieve, reuse, cite, and act on knowledge during coding, architecture, design, and ideation work.

The canonical published artifact is a set of markdown files in git. These files are inspectable by humans and portable across tools. However, direct human browsing is not the primary product. The primary product is useful, task-shaped context returned to coding and reasoning agents through a stable `kb` CLI.

### 1.1 Product thesis

> Build an agent context substrate: a durable knowledge artifact published as files, optimized for agent retrieval, task assistance, and concept reuse. Human readability matters insofar as it supports inspection, correction, and trust.

This is not a personal wiki. It is an agent-accessible knowledge artifact.

### 1.2 Goals

1. **Agent accessibility is the requirement** — agents must be able to retrieve useful context cheaply, reliably, and without loading the full knowledge base.
2. **Files are the published artifact** — markdown + frontmatter in git remains the durable, inspectable, portable representation.
3. **CLI is the access contract** — `kb` is the stable interface across Claude, Codex, Aider, Cursor, and future tools.
4. **Task-shaped context** — agents should ask for context for a task, not browse a wiki manually.
5. **Multiple retrieval modes** — precise lookup, compact task context, and broad ideation are distinct operations.
6. **Judgment-heavy knowledge support** — the KB must preserve not only facts, but techniques, heuristics, design judgments, anti-patterns, decisions, and open questions.
7. **Knowledge improves through use** — search misses, agent usage, corrections, review queues, and compaction make the artifact better over time.
8. **Trust-preserving curation** — the librarian never silently rewrites existing note bodies; substantive changes are review-gated.
9. **Low review burden** — maintenance should fit into 5 minutes, 2–3 times per week.
10. **Cross-agent portability** — every agent should get equivalent value through the same CLI and file artifact.

### 1.3 Non-goals

- Personal-wiki browsing as the primary UX.
- Real-time multi-user collaboration.
- Web/mobile UI.
- Complex team permissions or authorship workflow.
- Format conversion/export tooling.
- Encryption-at-rest features in the MVP.
- Image/OCR extraction from PDFs.
- Fully autonomous knowledge mutation without review.

### 1.4 Design principles

- **Files are the durable published artifact.** Markdown files are canonical, inspectable, portable, and versioned.
- **Indexes are disposable retrieval accelerators.** Search indexes, embeddings, backlinks, usage stats, and caches can be regenerated from the file artifact.
- **The CLI is the agent access layer.** Agents should prefer `kb context`, `kb explore`, and `kb search` over direct filesystem browsing.
- **Agents ask for task-shaped context.** The KB returns compact context relevant to a current task, not an arbitrary pile of notes.
- **Knowledge has types.** Facts, techniques, heuristics, decisions, patterns, anti-patterns, and open questions require different schemas and trust models.
- **Trust is maintained through inspectability and review.** The librarian may create notes, append sources, and flag conflicts automatically; it does not silently rewrite existing note bodies.
- **Capture less, preserve more value.** The ingest pipeline should prefer skipping weak material over creating low-value notes.
- **Human accessibility is a support function.** Human readability exists to enable inspection, correction, and audit, not to optimize for wiki browsing.

---

## 2. Architecture

### 2.1 System shape

KB Librarian has three layers:

1. **Published artifact** — markdown notes, indexes, and review queues in git.
2. **Retrieval layer** — disposable local indexes generated from the artifact: lexical index, backlink graph, usage stats, and optionally embeddings later.
3. **Agent interface** — the `kb` CLI, especially `kb context`, `kb explore`, `kb search`, and `kb get`.

Agents should usually interact with layer 3. Humans inspect and correct layer 1. Layer 2 accelerates retrieval but is not the source of truth.

### 2.2 Directory layout

The default library is stored at `~/.kb/.library`, while the default config lives one level up at `~/.kb/config.yaml`.
Explicit libraries selected with `--data-dir` keep their config under the library's own `.kb/` directory.

```text
kb/                                          # data repo (git)
├── INDEX.md                                 # thin published top-level index
├── PREAMBLE.md                              # agent behavioral preamble
├── topics/
│   └── <topic>/
│       ├── INDEX.md                         # topic index, generated
│       ├── scope.txt                        # what this topic is for / not for
│       └── <id>.md                          # context notes
├── raw/                                     # ingest queue
│   ├── <pending files>
│   └── processed/YYYY-MM/
│       └── duplicates/
├── review/                                  # human-touch surfaces
│   ├── pending-classification.md
│   ├── pending-merge.md
│   ├── pending-compaction.md
│   ├── disputes.md
│   ├── stale.md
│   ├── orphans.md
│   ├── search-misses.md
│   ├── low-utility.md
│   ├── rejected/
│   └── review-items.json                    # stable IDs and machine-readable review state
└── .kb/                                     # tool-managed disposable state
    ├── config.yaml
    ├── ingested.json
    ├── backlinks.json
    ├── stats.json
    ├── usage.log
    ├── errors.log
    ├── search-misses.log
    ├── state.json
    ├── fts.sqlite                           # local lexical index, generated
    └── index-manifest.json                  # generated index metadata
```

### 2.3 Components

1. **`kb` CLI** — pip-installable Python package and universal agent contract.
2. **Provider abstraction** — `LLMProvider` protocol with pluggable backends and credential sources. Implemented adapters: Anthropic (direct HTTP and Claude Code CLI delegation), Codex (OpenAI-compatible direct HTTP and Codex CLI delegation), local backends (Ollama, vLLM, LM Studio), and a deterministic mock for tests. Per-operation routing with bounded fallback chains and shared retry/backoff policy. See §13.
3. **Librarian engine** — orchestrates parsing, classification, extraction, integration, review proposals, and compaction.
4. **Retrieval engine** — powers `search`, `context`, and `explore` using generated indexes and note metadata. The candidate-source and ranker interfaces include an embedding seam reserved for a later phase; lexical retrieval is the default and only enabled implementation.
5. **Index builder** — regenerates `INDEX.md`, paginated topic indexes, backlinks, and local lexical index from notes.
6. **Review queue** — bounded, actionable curation workflow with stable review item IDs and a JSON source-of-truth backing the rendered markdown queues.
7. **Privacy layer** — provider-payload redaction and cloud-call gating applied before LLM calls. Notes on disk are never mutated by privacy controls. See §14.
8. **Diagnostics** — `kb doctor` runs layout, schema, index, review-state, provider, auth, and recovery checks; offline `--self-test` exercises ingest → reindex → search through the mock provider.
9. **Agent integrations** — thin wrappers or preamble entries that tell each tool how to call `kb`.

### 2.4 Primary data flow

```text
Raw input
  → kb ingest
  → parse/classify
  → extract candidate context notes
  → integrate with existing notes
  → update markdown artifact
  → rebuild disposable indexes
  → expose via kb context / search / explore
```

### 2.5 Retrieval data flow

```text
Agent task
  → kb context / kb explore / kb search
  → lexical + metadata retrieval
  → optional LLM rerank/synthesis
  → compact task-shaped context
  → citation block / source IDs
```

---

## 3. Knowledge model

### 3.1 Note types

Notes are not all wiki articles. Each note has a `knowledge_type`.

```yaml
knowledge_type: fact | technique | heuristic | pattern | anti-pattern | decision | open-question
```

#### Type definitions

| Type | Purpose | Example |
|---|---|---|
| `fact` | Externally verifiable claim | SQLite supports FTS5. |
| `technique` | Reusable method an agent can apply | Use AST-aware codemods for mechanical refactors. |
| `heuristic` | Judgment or rule of thumb | Prefer CLI contracts for cross-agent tools. |
| `pattern` | Reusable design structure | Files as published artifact, CLI as access layer. |
| `anti-pattern` | Known failure mode | Do not rely on agent memory as source of truth. |
| `decision` | Chosen direction with rationale | KB Librarian is single-user first. |
| `open-question` | Unresolved issue worth revisiting | Should embeddings move earlier than Phase 5? |

### 3.2 Frontmatter schema

```yaml
---
id: 2026-05-04-agent-context-cli-contract
title: Use a CLI as the stable contract between agents and knowledge tools
summary: A stable CLI lets multiple agents access the same knowledge artifact without tool-specific memory systems.
topic: agent-systems
created: 2026-05-04
updated: 2026-05-04
knowledge_type: technique
status: active                         # active | disputed | superseded | needs-review | archived
confidence: high                       # high | medium | low
basis:
  - design judgment
  - repeated agent-tooling failure mode
sources:
  - type: conversation
    ref: kb-flag-2026-05-04-2312
retrieval_phrases:
  - agent-accessible knowledge
  - cross-agent memory
  - portable context substrate
  - reduce agent tool lock-in
agent_use:
  - architecture review
  - coding agent setup
  - knowledge system design
applies_when:
  - building local-first tools
  - supporting multiple agent CLIs
  - avoiding vendor-specific memory systems
does_not_apply_when:
  - building real-time collaborative SaaS
  - needing centralized permissions
failure_modes:
  - CLI output becomes too verbose
  - agents bypass the CLI and scan files manually
staleness_risk: low                    # low | medium | high
reviewed_by_user: true
disputes: []
tags: [cli, retrieval, agent-context, portability]
---
```

### 3.3 Required fields

Minimum viable fields:

```yaml
id:
title:
summary:
topic:
created:
updated:
knowledge_type:
status:
confidence:
retrieval_phrases:
tags:
```

### 3.4 Recommended body structure

Body structure depends on note type, but agent utility should dominate.

#### Technique / pattern / anti-pattern

```markdown
## Use when

## Core idea

## Agent instruction

## Procedure / application

## Failure modes

## Related
```

#### Heuristic / judgment

```markdown
## Judgment

## Basis

## Applies when

## Does not apply when

## Failure modes

## Related
```

#### Fact

```markdown
## Claim

## Evidence

## Caveats

## Staleness risk

## Related
```

#### Decision

```markdown
## Decision

## Rationale

## Alternatives considered

## Consequences

## Revisit if
```

#### Open question

```markdown
## Question

## Why it matters

## Current thinking

## Possible answers

## What would resolve it
```

### 3.5 Retrieval phrases

`retrieval_phrases` are first-class metadata. They teach the KB alternate ways an agent might search for the note.

Example:

```yaml
retrieval_phrases:
  - reduce token burn
  - lower context usage
  - agent context budget
  - niche coding techniques
  - task-shaped context
  - avoid loading whole KB
```

These phrases improve recall without requiring embeddings in the MVP.

### 3.6 Agent instructions

High-value notes should include an `Agent instruction` body section or `agent_instruction` field.

Example:

```markdown
## Agent instruction

When this note is relevant, prefer calling `kb context` for compact task context instead of reading multiple topic indexes. Use `kb explore` only when the user asks for alternatives, architecture, ideation, or novel approaches.
```

Agent instructions turn notes into behavior-shaping context modules rather than passive prose.

### 3.7 Beliefs, judgments, and facts

The KB supports both externally verifiable facts and personal/design judgments.

- Facts require source quality, evidence, and staleness tracking.
- Judgments require basis, applicability boundaries, and failure modes.

For this system, judgment-heavy knowledge is expected to be especially valuable because the KB is intended to help agents apply niche techniques, design preferences, and accumulated reasoning during coding and ideation.

---

## 4. CLI

### 4.1 Command surface

```text
kb init [--data-dir <path>] [--hooks]
kb add [--topic <t>] [--type <type>] [--from-file <f>]
kb ingest [<file>] [--force] [--quiet]
kb context "<task>" [--mode <mode>] [--budget <tokens>] [--json]
kb explore "<problem>" [--budget <tokens>] [--json]
kb search "<query>" [--topic <t>] [--type <type>] [--budget <tokens>] [--json]
kb get <id> [--summary]
kb topics [--tree]
kb review [list|accept|reject|defer|explain]
kb compact <topic-or-cluster>
kb topic promote <topic...> --under <parent>
kb topic split <topic> --into <new...>
kb topic merge <a> <b> --as <name>
kb topic rename <old> <new>
kb usage [--since <duration>] [--note <id>]
kb log-use <id> [--task "<task>"]
kb flag-suspect <id> "<reason>"
kb reindex [--scan-clusters] [--all]
kb doctor [--self-test]
```

### 4.2 Command roles

| Command | Role | Retrieval posture |
|---|---|---|
| `kb context` | Return compact task-shaped context | high precision |
| `kb explore` | Return broader associations and ideas | higher recall |
| `kb search` | Find known notes | precise lookup |
| `kb get` | Inspect one note | full detail |
| `kb review` | Improve artifact | human curation |

### 4.3 `kb context`

Primary command for coding and design agents.

```bash
kb context "I am designing a CLI knowledge system for coding agents" --mode architecture --budget 1800
```

Output shape:

```markdown
# KB Context

## Directly relevant techniques
...

## Applicable heuristics
...

## Warnings / failure modes
...

## Suggested agent behavior
...

## Source notes
- [note-id](path) — confidence: high

---
**KB sources:** ...
```

`kb context` should be optimized for:

- compactness
- direct usefulness
- high precision
- minimal irrelevant material
- citation-ready output

### 4.4 Context modes

```text
coding        # implementation help, niche techniques, code review, refactors
architecture  # system design, tradeoffs, structural choices
debugging     # diagnosis strategies, known failure modes
writing       # drafting using stored preferences and concepts
research      # factual synthesis with stronger source emphasis
review        # critique and design review
```

Mode affects ranking and output synthesis.

### 4.5 `kb explore`

Used for ideation and novel concept discovery.

```bash
kb explore "ways to reduce token burn while preserving agent access to niche techniques" --budget 3000
```

Output shape:

```markdown
# Exploration

## Directly relevant concepts
...

## Adjacent patterns
...

## Tensions / tradeoffs
...

## Possible analogies
...

## Anti-patterns to avoid
...

## Open questions
...

## Source notes
...
```

`kb explore` should tolerate broader results than `kb context`. Its purpose is not only to find known answers, but to surface useful adjacent concepts.

### 4.6 `kb search`

Precise lookup over note titles, summaries, tags, retrieval phrases, and body text.

```bash
kb search "sqlite fts5 local index" --type technique --budget 1200
```

Returns summaries by default, not full bodies.

### 4.7 Configuration

The default config produced by `kb init` is:

```yaml
data_dir: ~/.kb/.library

providers:
  anthropic:
    backend: direct_http
    credential_source: api_key_env
    api_key_env: ANTHROPIC_API_KEY
  codex:
    backend: direct_http
    credential_source: api_key_env
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    timeout_seconds: 120
  local:
    backend: ollama          # ollama | vllm | lm_studio
    base_url: http://127.0.0.1:11434
    timeout_seconds: 120
  retry:
    max_attempts: 3
    base_delay_seconds: 0.25
    max_delay_seconds: 2.0
    jitter_seconds: 0.1
  policy:
    default_provider: anthropic
    fallback: {}             # per-operation fallback chains, see §13.3

operations:
  extract:    { provider: anthropic, model: claude-sonnet-4-6 }
  compact:    { provider: anthropic, model: claude-sonnet-4-6 }
  classify:   { provider: anthropic, model: claude-haiku-4-5 }
  integrate:  { provider: anthropic, model: claude-haiku-4-5 }
  synthesize: { provider: anthropic, model: claude-haiku-4-5 }

retrieval:
  default_budget_tokens: 1500
  context_budget_tokens: 1800
  explore_budget_tokens: 3000
  index_token_cap: 5000
  lexical_index: true
  embeddings: false                    # reserved seam; lexical is the only enabled source
  embedding_index_path: .kb/embeddings.sqlite
  embedding_provider: null
  embedding_model: null
  embedding_dimensions: null
  title_weight: 5
  summary_weight: 4
  retrieval_phrase_weight: 4
  tag_weight: 3
  body_weight: 1

ingest:
  max_notes_per_doc: 7
  prefer_skip_over_low_value_note: true
  low_confidence_goes_to_review: true

indexes:
  topic_sort: alphabetical
  top_level_min_notes: 1
  topic_page_size: 50
  top_level_page_size: 100

review:
  stale_after_days: 180
  orphan_after_days: 30
  duplicate_cluster_threshold: 4
  compaction_cooldown_days: 30
  max_review_items_per_run: 10

git:
  auto_commit: false
  commit_ingests: true
  commit_reviews: true
  commit_reindexes: false
  commit_topic_reorganizations: true
  allow_unrelated_changes: false
  require_clean_worktree_for_rewrites: true

privacy:
  cloud_llm_allowed: true
  blocked_topics: []
  redact_patterns: []
  require_confirmation_for_cloud_llm: false

hooks:
  session_start_ingest: false
```

Provider sections accept additional keys beyond the defaults shown above to switch backends or credential sources — for example `backend: vendor_cli` with `credential_source: vendor_cli` to delegate to the Claude Code or Codex CLI, or `backend: vllm` / `backend: lm_studio` for alternative local servers. See §13 for the full provider/auth model.

---

## 5. Retrieval design

### 5.1 Retrieval goals

Retrieval must support three different jobs:

1. **Precise lookup** — find a known note or concept.
2. **Task context** — provide compact, relevant context for an agent’s current work.
3. **Ideation/exploration** — surface adjacent concepts, patterns, anti-patterns, and open questions.

These should not share one generic search mode.

### 5.2 Precision and recall

- **Precision**: returned items are actually relevant.
- **Recall**: the system finds most relevant items that exist in the KB.

`kb context` should favor precision.  
`kb explore` should favor broader recall.  
`kb search` should favor exactness and inspectability.

### 5.3 Retrieval signals

Initial retrieval should rank by:

1. exact ID/title match
2. title match
3. summary match
4. retrieval phrase match
5. tag/topic match
6. note type fit
7. body match
8. usage history
9. recency/staleness
10. status/confidence

### 5.4 Local lexical index

Move local lexical search into the MVP. Use a generated SQLite FTS5 index or equivalent.

Index fields:

```text
id
title
summary
topic
knowledge_type
tags
retrieval_phrases
agent_use
applies_when
does_not_apply_when
body
status
confidence
updated
```

The index is disposable. `kb reindex` rebuilds it from markdown.

### 5.5 Optional embeddings

Embeddings are deferred, but the design should allow them as a generated index later.

Embeddings are useful for:

- cross-topic associations
- semantic paraphrases
- broad ideation
- duplicate detection

They should not replace the markdown artifact.

### 5.6 Citation-ready output

All agent-facing retrieval commands should be able to return source IDs and paths.

Example:

```bash
kb context "task" --with-citations
```

Citation formatting should be produced by the CLI rather than relying entirely on the agent.

---

## 6. Ingest pipeline

### 6.1 Top-level flow

```text
For each file in raw/:
  1. Dedup check
  2. Parse
  3. Determine topic and likely note types
  4. Extract candidate context notes
  5. Score candidate utility
  6. Integrate with existing notes
  7. Write notes / review proposals
  8. Rebuild indexes
  9. Archive raw input
```

### 6.2 Extraction posture

The librarian should extract fewer, stronger notes.

Default behavior:

- create at most 7 notes per normal document
- prefer reusable techniques, heuristics, decisions, patterns, and anti-patterns
- skip obvious, transient, or low-value claims
- send low-confidence or ambiguous candidates to review
- allow `no durable notes worth creating` as a valid result

### 6.3 Candidate note output

Extractor returns structured JSON:

```json
{
  "candidates": [
    {
      "title": "Use task-shaped context instead of broad wiki retrieval",
      "summary": "Agents get better value from compact context generated for the current task than from manually browsing a knowledge base.",
      "knowledge_type": "heuristic",
      "body": "...",
      "retrieval_phrases": ["task-shaped context", "agent context", "reduce token burn"],
      "agent_use": ["architecture review", "coding agent setup"],
      "applies_when": ["agent needs compact prior knowledge"],
      "does_not_apply_when": ["human is casually browsing notes"],
      "failure_modes": ["context command becomes too broad"],
      "claims": ["..."],
      "confidence": "high",
      "utility_score": "high"
    }
  ]
}
```

### 6.4 Integration

For each candidate:

```text
a. Retrieve likely matches across topic, parent topic, tags, retrieval phrases, and global title/body index.
b. Ask verdict model: identical | adds_nuance | contradicts | unrelated.
c. Act by verdict:
   - identical    → append source automatically
   - adds_nuance  → review-gated merge proposal
   - contradicts  → mark disputed and queue review
   - unrelated    → create new note
```

### 6.5 Cross-topic checks

Integration must not only compare within the assigned topic. Contradictions and duplicates can cross topic boundaries.

Candidate matching should include:

1. assigned topic
2. parent topic
3. tags across all topics
4. retrieval phrases across all topics
5. global title/body index

### 6.6 Dedup behavior

Hash-based raw file dedup remains as originally designed:

- same hash + success → move to duplicates and surface in review
- same hash + error → retry
- same filename + different hash → process as new and flag possible updated version
- near-duplicates → handled during integration, not file-level dedup

### 6.7 Parsing

MVP:

- `.md`
- `.txt`

Later:

- `.pdf`
- `.html`
- known `.json` export schemas

Unsupported files remain in `raw/`, log to `.kb/errors.log`, and surface in `kb review`.

---

## 7. Review workflow

### 7.1 Review principle

Review must be bounded, actionable, and useful. The user is willing to do roughly 5 minutes, 2–3 times per week, not 30-minute curation sessions.

### 7.2 Stable review item IDs

Every review item gets a stable ID stored in `review/review-items.json` and rendered into markdown queue files.

Example:

```markdown
## item: merge-2026-05-04-001

Candidate note appears to add nuance to existing note `2026-05-04-agent-context-cli-contract`.

Suggested action: accept merge proposal.
```

### 7.3 Review commands

```bash
kb review list
kb review explain <item-id>
kb review accept <item-id>
kb review reject <item-id>
kb review defer <item-id> --days 30
```

### 7.4 Review queues

| Queue | Purpose |
|---|---|
| `pending-classification.md` | Topic/type uncertainty |
| `pending-merge.md` | Adds-nuance proposals |
| `pending-compaction.md` | Duplicate clusters |
| `disputes.md` | Contradictions |
| `stale.md` | Notes needing reverification |
| `orphans.md` | Unlinked notes |
| `search-misses.md` | Queries with poor results |
| `low-utility.md` | Notes that agents retrieve but do not use well |

### 7.5 Review output

```text
$ kb review
Review items: 7

High priority:
  dispute-2026-05-04-001 — conflicting heuristics about CLI vs MCP access layer
  merge-2026-05-04-002 — duplicate task-context notes

Medium priority:
  classification-2026-05-04-003 — unknown topic for raw/codex-refactor-note.md
  searchmiss-2026-05-04-004 — "reduce token burn" missed useful notes 4 times

Suggested time: choose 3 items now.
```

The CLI should encourage bounded review rather than exhaustive cleanup.

---

## 8. Agent integration

### 8.1 Agent behavior

Agents should primarily retrieve through the CLI.

Recommended order:

1. Use `kb context` before coding, design, debugging, or review tasks.
2. Use `kb explore` when the user asks for ideas, alternatives, architecture, or novel approaches.
3. Use `kb search` when looking for a specific known concept.
4. Use `kb get` only after a note is clearly relevant.
5. Do not scan multiple topic indexes or notes “just in case.”
6. Cite KB notes when used.
7. Log use with `kb log-use` when possible.

### 8.2 Revised PREAMBLE.md

```markdown
# Knowledge Base

This system has a curated agent context knowledge base at `<data-dir>`.

The knowledge base is published as markdown files, but agents should normally retrieve through the `kb` CLI rather than browsing files directly.

## Primary commands

- `kb context "<task>" --mode <mode> --budget <tokens>` — use before coding, architecture, debugging, review, or writing tasks.
- `kb explore "<problem>" --budget <tokens>` — use for ideation, alternatives, tradeoffs, and novel concept discovery.
- `kb search "<query>"` — use for precise lookup of known concepts.
- `kb get <id>` — use only when a specific note is clearly relevant.

## When to consult

Consult the KB when the task may benefit from stored techniques, heuristics, design judgments, coding patterns, anti-patterns, prior decisions, or niche concepts.

## Retrieval discipline

1. Prefer `kb context` for task help.
2. Prefer `kb explore` for broad ideation.
3. Prefer `kb search` for precise lookup.
4. Do not load multiple notes just in case.
5. Respect the user's context budget.

## Trust signals

Each note may include:

- `knowledge_type`
- `confidence`
- `status`
- `basis`
- `applies_when`
- `does_not_apply_when`
- `failure_modes`
- `updated`

If a note is disputed, stale, low-confidence, or outside its applicability bounds, say so.

## Citation requirement

When using KB material in a response, include the CLI-provided citation block or end with:

---
**KB sources:** [<note-id>](<path>) (confidence: <level>)

After using a note, call `kb log-use <id>` when practical.

## Correction behavior

If the user corrects a KB-derived claim or says a retrieved note was not useful, run:

`kb flag-suspect <id> "<reason>"`
```

### 8.3 Agent-facing output style

CLI output should be concise and structured. Avoid long prose unless explicitly requested.

Good output sections:

- Directly relevant techniques
- Applicable heuristics
- Warnings / failure modes
- Suggested next action
- Source notes

---

## 9. Indexes and generated artifacts

### 9.1 Generated indexes

Generated artifacts:

- `INDEX.md`
- `topics/<topic>/INDEX.md`
- `.kb/backlinks.json`
- `.kb/fts.sqlite`
- `.kb/stats.json`
- `.kb/index-manifest.json`

Only markdown notes and review decisions are canonical. Generated indexes are disposable.

### 9.2 Top-level INDEX.md

Top-level index remains thin. It is useful for human inspection and emergency agent fallback, but is not the preferred retrieval mechanism.

Example:

```markdown
# Knowledge Base

This is an agent context artifact. Prefer `kb context`, `kb explore`, or `kb search` for retrieval.

- [agent-systems](topics/agent-systems/) — agent tooling, context design, retrieval patterns (42 notes)
- [coding-techniques](topics/coding-techniques/) — refactors, debugging, testing, implementation strategies (67 notes)
- [architecture](topics/architecture/) — design heuristics, tradeoffs, system patterns (38 notes)
```

### 9.3 Topic INDEX.md

Topic indexes summarize notes and are useful for inspection, not primary retrieval.

Include:

- note ID
- title
- summary
- knowledge type
- confidence
- updated date

---

## 10. Compaction and improvement

### 10.1 Compaction purpose

Compaction is not just cleanup. It improves agent utility by turning fragmented notes into clearer reusable context modules.

### 10.2 Compaction triggers

Trigger candidates when:

- duplicate cluster size reaches threshold
- many `adds_nuance` proposals accumulate
- search frequently returns multiple overlapping notes
- agent context output repeatedly includes redundant notes
- user invokes `kb compact <topic-or-cluster>`

### 10.3 Compaction flow

```text
1. Librarian reads cluster.
2. Drafts canonical note.
3. Shows diff and source notes.
4. User accepts/rejects/defer.
5. Accepted compaction rewrites notes, updates links, regenerates indexes.
6. Git records history.
```

### 10.4 Supersession before deletion

For heavily used notes, prefer marking old notes as superseded before deletion.

```yaml
status: superseded
superseded_by: 2026-05-04-task-shaped-agent-context
```

Immediate deletion is allowed for low-use duplicate notes, but supersession improves transition safety.

### 10.5 Utility-based improvement

The system should track not only whether notes exist, but whether agents use them successfully.

Signals:

- note retrieved by `kb context`
- note cited by agent
- note flagged suspect
- search miss led to note creation
- note repeatedly retrieved but not used
- note caused user correction

This supports a `low-utility` review queue.

---

## 11. Trust and provenance

### 11.1 Trust model

Trust is not only factual accuracy. For this KB, trust means:

- the note says what kind of knowledge it is
- its basis is visible
- applicability boundaries are explicit
- failure modes are explicit
- source/provenance is inspectable
- disputed or stale notes are visible
- agents cite what they used

### 11.2 Confidence

`confidence` remains useful but should not be treated as enough by itself.

Better trust comes from:

```yaml
knowledge_type: heuristic
confidence: medium
basis:
  - personal experience
  - repeated design failure mode
reviewed_by_user: true
applies_when:
  - single-user local-first tools
does_not_apply_when:
  - multi-user enterprise systems
```

### 11.3 Facts versus judgments

For facts:

- emphasize evidence, sources, source quality, and staleness

For judgments:

- emphasize basis, applicability, non-applicability, and failure modes

### 11.4 Disputes

Contradictions should mark both notes as disputed and appear in `review/disputes.md`.

Disputes are useful. They preserve uncertainty rather than forcing premature synthesis.

---

## 12. Git behavior

### 12.1 Default posture

MVP default:

```yaml
git:
  auto_commit: false
  require_clean_worktree_for_rewrites: true
```

The user can inspect changes before committing.

### 12.2 Rewrite operations

Require a clean worktree for:

- compaction
- topic reorganization
- body merge acceptance
- bulk status changes

Unless `--force` is passed.

### 12.3 Optional auto-commit later

Later phases may support:

```yaml
git:
  auto_commit: true
  commit_ingests: true
  commit_reviews: true
  commit_reindexes: false
```

Generated indexes should generally not create noisy commits unless desired.

---

## 13. Provider backends and account auth

The provider abstraction is split into three orthogonal seams so a single config can describe a wide range of deployments without changing call sites.

### 13.1 Backend

The transport that actually performs an inference call. Supported values:

- `direct_http` — first-party HTTP API (Anthropic, OpenAI-compatible Codex)
- `vendor_cli` — delegate to a locally installed vendor CLI (`claude -p`, `codex exec`)
- `ollama` — local Ollama server
- `vllm` — local vLLM server (OpenAI-compatible transport reuse)
- `lm_studio` — local LM Studio server (OpenAI-compatible transport reuse)
- `mock` — deterministic in-process provider for tests

### 13.2 Credential source

How the runtime obtains an API key, bearer token, or session for the chosen backend:

- `api_key_env` — read an API key from a named environment variable (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
- `token_env` — read a vendor session token from a named environment variable (e.g. `CLAUDE_CODE_OAUTH_TOKEN`)
- `vendor_cli` — delegate authentication entirely to a logged-in vendor CLI (`claude auth login`, `codex login`)
- `command` — run a configured command and use its stdout as the credential

Backends and credential sources combine to describe specific deployments. Examples:

- Anthropic API key: `backend: direct_http`, `credential_source: api_key_env`, `api_key_env: ANTHROPIC_API_KEY`
- Claude subscription via CLI: `backend: vendor_cli`, `credential_source: vendor_cli`, optionally `cli_command: claude`
- Codex with bearer-token env var: `backend: direct_http`, `credential_source: token_env`, `token_env: OPENAI_API_KEY`
- Local Ollama: `backend: ollama`, `base_url: http://127.0.0.1:11434` (no credential source)
- Local vLLM/LM Studio: `backend: vllm` or `backend: lm_studio` with appropriate `base_url`

### 13.3 Per-operation routing and fallback

Each LLM operation (`extract`, `compact`, `classify`, `integrate`, `synthesize`) is independently routed to a provider and model in `operations.*`. Bounded fallback chains can be declared under `providers.policy.fallback`:

```yaml
providers:
  policy:
    default_provider: anthropic
    fallback:
      synthesize: [anthropic, codex, local]
```

Provider selection is deterministic and explainable. The doctor reports the resolved provider for each operation and any invalid policy graphs. Fallback attempts are bounded and logged so failures show the full attempted sequence.

### 13.4 Retry policy

Transient provider errors (timeouts, rate limits, 5xx) are retried with exponential backoff under `providers.retry`. Permanent errors (auth failures, 4xx other than 429, malformed responses) are non-retriable. Retry attempts are written to `.kb/errors.log` with operation IDs so a failure trace can be reconstructed after the fact.

### 13.5 Vendor-CLI delegation safety

Account-login backends shell out to the vendor CLI in completion-only mode (no tool use, bounded timeouts). The KB never grants the vendor CLI write access to the artifact. Detection of missing or expired logins surfaces in `kb doctor` with explicit remediation steps.

### 13.6 Secret scrubbing

Anything that could carry a credential — log entries, doctor output, error messages, and rendered subprocess invocations — is run through a redaction filter that masks API keys, bearer tokens, and OAuth artifacts. Secrets must not appear in `.kb/errors.log`, `kb doctor` output, or commit messages.

---

## 14. Privacy and redaction

Privacy controls are shipped and validated. Notes on disk are never mutated by privacy controls — redaction applies only to the payloads sent to providers.

### 14.1 Config

```yaml
privacy:
  cloud_llm_allowed: true
  blocked_topics: []
  redact_patterns: []
  require_confirmation_for_cloud_llm: false
```

### 14.2 Behavior

- **`cloud_llm_allowed`** — when `false`, attempts to call any cloud provider raise an error and surface in `kb doctor`. Local backends remain available.
- **`blocked_topics`** — list of topics that may not be sent to cloud providers. A provider call carrying notes from a blocked topic is rejected before the request leaves the machine.
- **`redact_patterns`** — list of regex patterns. Matches are replaced with `[REDACTED]` in provider payloads (recursively across structured fields). Patterns are not applied to notes or indexes on disk.
- **`require_confirmation_for_cloud_llm`** — config seam validated by `kb doctor`. The current implementation does not provide an interactive prompt; a future iteration may wire this through.

### 14.3 Future controls

Potential later features (not yet implemented):

- show LLM payload before sending (interactive confirmation flow)
- mark individual notes as local-only and exclude them from cloud calls
- per-operation privacy overrides

---

## 15. Testing strategy

### 15.1 Deterministic tests

Full unit coverage for:

- frontmatter parsing/writing
- schema validation
- markdown round-trip
- note ID generation
- topic resolution
- FTS index generation
- search ranking weights
- index regeneration
- backlink graph
- review item state
- CLI argument parsing
- lock/state handling

### 15.2 Mock-provider tests

Scripted LLM responses for:

- extraction
- note type assignment
- integration verdicts
- contradiction handling
- merge proposal creation
- context synthesis
- explore synthesis

### 15.3 Golden corpus

A small real corpus should validate:

- extracted note count
- note utility
- retrieval phrases
- `kb context` quality
- `kb explore` breadth
- citation output

The core test question:

> Does `kb context` help an agent do better work with fewer tokens?

### 15.4 Manual smoke test

```text
kb init
kb add a design note
kb ingest
kb context "review this architecture"
kb explore "novel ways to reduce token usage"
kb search "CLI contract"
kb get <note>
kb review
```

---

## 16. Build phases

Phases 1–6 are implemented. The historical breakdown is preserved here so plan files and changelogs remain interpretable.

### Phase 1 — Agent-first walking skeleton (shipped)

Goal: prove that an agent can retrieve useful task context.

1. Python package scaffold.
2. Provider abstraction + Anthropic adapter.
3. Markdown/frontmatter note schema.
4. `kb init`.
5. `kb add`.
6. `kb ingest` for markdown/txt.
7. Note type extraction.
8. Retrieval phrase extraction.
9. Basic integration: create / identical / adds_nuance / contradicts.
10. SQLite FTS or equivalent local lexical index.
11. `kb search`.
12. `kb context`.
13. `kb get`.
14. Basic `kb review` for classification/merge/dispute items.

Definition of done:

> Add 20–50 real notes, call `kb context` from an agent, and observe better coding/design help with lower token usage.

### Phase 2 — Daily-use ergonomics (shipped)

1. Better `kb review` commands with stable IDs.
2. `kb explore`.
3. Usage logging and `kb log-use`.
4. Search-miss logging.
5. Agent preamble installation.
6. Citation-block generation.
7. Better ingest reports.

Definition of done:

> The KB becomes useful in normal agent sessions and review stays under 5 minutes.

### Phase 3 — Long-term hygiene (shipped)

1. Compaction.
2. Stale-note flagging.
3. Orphan flagging.
4. Low-utility queue.
5. `kb doctor`.
6. INDEX pagination.
7. Hierarchical topics and reorganization commands.

### Phase 4 — Robustness and polish (shipped)

1. Concurrent-ingest lockfile and resume.
2. Retry/backoff.
3. Golden corpus harness.
4. PDF/HTML parsing.
5. Session-start hooks and cron templates.
6. Optional auto-commit policy.

### Phase 5 — Provider expansion and local-first (shipped)

1. Local Ollama provider.
2. Codex (OpenAI-compatible) provider.
3. Per-operation provider policy and bounded fallback chains.
4. Embedding retrieval seam (interfaces only; lexical remains the only enabled source).
5. Privacy and redaction controls (see §14).

### Phase 6 — Provider backends and account auth (shipped)

1. Provider backend and credential-source seams (see §13).
2. Additional local backends: vLLM and LM Studio.
3. Anthropic account-login auth via Claude Code CLI.
4. Codex account-login auth via Codex CLI.
5. Auth policy precedence, doctor coverage for auth state, and secret scrubbing across logs and diagnostics.

### Deferred backlog

Not implemented and not currently scheduled:

- Embedding-based retrieval (the seam exists; ranking and index build do not).
- MCP server wrapper, if it proves clearly better than shelling out.
- TUI/web review interface.
- Multi-machine conflict helpers.
- Interactive cloud-call confirmation prompt (the config seam exists).

---

## 17. Success criteria

The design is working when:

1. Agents get useful context through `kb context` without loading the whole KB.
2. Coding and architecture help improves because agents can reuse stored techniques and heuristics.
3. `kb explore` surfaces useful adjacent concepts during ideation.
4. The user trusts the artifact because note basis, applicability, and failure modes are visible.
5. Review stays bounded to roughly 5 minutes, 2–3 times per week.
6. The KB improves through search misses, corrections, usage, and compaction.
7. Markdown files remain portable and inspectable.
8. Switching agents does not reduce access to the KB.

---

## Appendix A — Key changes from the earlier design

These were the framing changes in the 2026-05-04 revision that established the agent-first direction:

1. Reframed the system from personal/team markdown KB to agent context substrate.
2. Changed the core principle from “the store is files” to “files are the durable published artifact.”
3. Made `kb context` a Phase 1 command and primary agent interface.
4. Added `kb explore` as a distinct ideation mode.
5. Kept `kb search` as precise lookup rather than overloading it for all retrieval.
6. Added `knowledge_type` to distinguish facts, techniques, heuristics, decisions, patterns, anti-patterns, and open questions.
7. Added `retrieval_phrases` as first-class metadata to improve agent recall.
8. Added `agent_use`, `applies_when`, `does_not_apply_when`, and `failure_modes` for judgment-heavy knowledge.
9. Moved local lexical indexing earlier.
10. Added stable review item IDs and action-oriented review commands.
11. Added utility-based improvement signals, including low-utility review.
12. Added privacy/provider seams without making them MVP scope.
13. Reduced early emphasis on hierarchy, PDF parsing, and compaction.
14. Changed the MVP success test to whether agents do better work with fewer tokens.

## Appendix B — Updates after Phase 4

The original spec scoped Phases 1–4 in detail and listed Phase 5+ as a deferred backlog. Subsequent implementation pulled most of that backlog into shipped work, and this spec has been updated in place to describe the resulting system. Notable changes from the original 2026-05-04 revision:

1. Local provider support (Ollama) and a Codex (OpenAI-compatible) provider became Phase 5.
2. Privacy and redaction controls became Phase 5e and are now described in §14 as shipped behavior, not future work.
3. A Phase 6 was added for provider backend and credential-source seams, additional local backends (vLLM, LM Studio), and account-login auth via the Claude Code and Codex CLIs. §13 documents the resulting model.
4. `§4.7` was expanded to reflect the real default config produced by `kb init`, including `providers.{anthropic,codex,local,retry,policy}`, paginated indexes, and the embedding seam keys.
5. Embedding-based retrieval remains deferred — only the seam interfaces exist; lexical retrieval is still the only enabled candidate source.
6. MCP server wrapper, TUI/web review interface, multi-machine helpers, and the interactive cloud-call confirmation flow remain deferred.
