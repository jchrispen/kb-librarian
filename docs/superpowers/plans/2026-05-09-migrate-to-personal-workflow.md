# kb-librarian Personal Workflow Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all `docs/superpowers/` workflow artifacts to the personal-workflow convention, compacting shipped plans into thematic ADRs and notes, writing a fresh top-level spec, and deleting the old tree.

**Architecture:** Thematic extraction — read all shipped plan files, write 7 ADRs grouped by decision domain plus 2 notes, bootstrap the new folder structure, then delete the old `docs/superpowers/` tree in two commits (extraction separate from cleanup).

**Tech Stack:** Markdown only. No code changes. Working directory: `/mnt/c/workspace/source/internal/kb-librarian/`.

**Spec:** `docs/superpowers/specs/2026-05-09-kb-librarian-personal-workflow-migration-design.md`

---

## File structure

Files created:

| Path | Responsibility |
|---|---|
| `docs/specs/_index.md` | Specs index |
| `docs/specs/kb-librarian.md` | Fresh top-level spec (written from current code) |
| `docs/adrs/_index.md` | ADRs index (newest first) |
| `docs/adrs/2026-05-09-agent-first-architecture.md` | Architecture decisions |
| `docs/adrs/2026-05-09-provider-backend-seams.md` | Provider abstraction decisions |
| `docs/adrs/2026-05-09-account-auth-delegation.md` | Auth delegation decisions |
| `docs/adrs/2026-05-09-retrieval-and-search.md` | Retrieval surface decisions |
| `docs/adrs/2026-05-09-hygiene-and-compaction.md` | Hygiene/compaction decisions |
| `docs/adrs/2026-05-09-robustness-patterns.md` | Robustness/recovery decisions |
| `docs/adrs/2026-05-09-adopt-personal-workflow-convention.md` | Decision to adopt this convention |
| `docs/notes/_index.md` | Notes index |
| `docs/notes/providers/cli-delegation-pitfalls.md` | Auth refresh edge cases |
| `docs/notes/ingest/pdf-html-parsing.md` | Parser quirks and workarounds |
| `.agents/workflow/plans/.gitkeep` | Keeps directory tracked |
| `.agents/workflow/research/.gitkeep` | Keeps directory tracked |

Files deleted (in Tasks 8–9):
- `docs/superpowers/plan/` (entire tree, ~25 files)
- `docs/superpowers/plans/` (entire tree, 4 files + this plan)
- `docs/superpowers/specs/` (entire tree, 3 files)
- `docs/superpowers/` (directory itself)

---

## Task 1: Bootstrap new folder structure

**Files:**
- Create: `docs/specs/`, `docs/adrs/`, `docs/notes/`, `docs/notes/providers/`, `docs/notes/ingest/`
- Create: `.agents/workflow/plans/`, `.agents/workflow/research/`

- [ ] **Step 1: Create all directories**

```bash
mkdir -p docs/specs docs/adrs docs/notes/providers docs/notes/ingest
mkdir -p .agents/workflow/plans .agents/workflow/research
```

- [ ] **Step 2: Seed `docs/specs/_index.md`**

Create `docs/specs/_index.md`:

```markdown
# Specs Index

_Last updated: 2026-05-09_

<!-- One bullet per artifact. Re-rendered when artifacts change. See .claude/skills/personal-workflow/references/indexes.md. -->
```

- [ ] **Step 3: Seed `docs/adrs/_index.md`**

Create `docs/adrs/_index.md`:

```markdown
# ADRs Index

_Last updated: 2026-05-09_

<!-- One bullet per artifact, newest first. Re-rendered when artifacts change. -->
```

- [ ] **Step 4: Seed `docs/notes/_index.md`**

Create `docs/notes/_index.md`:

```markdown
# Notes Index

_Last updated: 2026-05-09_

<!-- One bullet per note. Re-rendered when artifacts change. -->
```

- [ ] **Step 5: Add .gitkeep files so empty workflow dirs are tracked**

```bash
touch .agents/workflow/plans/.gitkeep .agents/workflow/research/.gitkeep
```

- [ ] **Step 6: Verify layout**

```bash
find docs/specs docs/adrs docs/notes .agents/workflow -type f | sort
```

Expected output:
```
.agents/workflow/plans/.gitkeep
.agents/workflow/research/.gitkeep
docs/adrs/_index.md
docs/notes/_index.md
docs/specs/_index.md
```

- [ ] **Step 7: Commit**

```bash
git add docs/specs/ docs/adrs/ docs/notes/ .agents/workflow/
git commit -m "Bootstrap personal-workflow folder structure"
```

---

## Task 2: Write fresh `docs/specs/kb-librarian.md`

**Files:**
- Create: `docs/specs/kb-librarian.md`

This spec is written from the current codebase — not migrated from the old design doc.

- [ ] **Step 1: Create `docs/specs/kb-librarian.md` with this content**

```markdown
# kb-librarian

**Status:** Active development. Core workflow, ergonomics, hygiene, robustness, and provider expansion shipped. Embedding retrieval and remaining provider backends deferred.

## Purpose

A local-first knowledge base CLI for coding agents. Stores durable knowledge as markdown files in a separate data directory (`~/.kb/.library` by default), builds disposable local indexes for retrieval, and gives agents a stable command-line interface for finding task-shaped context.

## Scope

- `kb init` — initialize a KB at the configured data directory
- `kb add` — add a note directly
- `kb ingest` — ingest markdown, text, PDF, or local HTML files into candidate notes
- `kb reindex` — rebuild the FTS5 search index
- `kb compact` — compact overlapping note clusters (review-gated)
- `kb search` — lexical full-text search
- `kb get` — fetch a note by ID
- `kb topics` / `kb topic` — topic tree and per-topic note listing
- `kb review` — manage the review queue (hygiene signals, compaction proposals)
- `kb context` — high-precision task-shaped context retrieval
- `kb explore` — broad-recall ideation retrieval
- `kb log-use` / `kb flag-suspect` / `kb usage` — usage tracking and miss feedback
- `kb doctor` — read-only health diagnostics

## Out of scope

- Embedding/semantic retrieval (seams exist; no production backend shipped)
- Remote web fetching for HTML ingest
- MCP server wrapper (explicitly deferred)
- Server or daemon mode
- Multi-user or team conventions

## Architecture

Local-first Python CLI. No server, no daemon, no network calls in the critical path.

**Storage:** Notes are markdown files with YAML frontmatter at `~/.kb/.library/notes/`. A SQLite FTS5 database at `~/.kb/.library/index.db` provides lexical search. All writes use atomic temp-file-then-rename via `atomic.py`.

**Provider abstraction:** Providers are abstracted behind a credential-source/backend seam (`provider_seams.py`). The credential source (API key, vendor CLI delegation, env token) is decoupled from the transport (direct HTTP, Ollama, vLLM, LM Studio, Claude Code CLI, Codex CLI). Fallback chains are explicit and bounded.

**Review gating:** All destructive mutations (compaction application, topic reorganization) require an accepted review item and a clean worktree check before proceeding.

## Components

| Module | Responsibility |
|---|---|
| `cli.py` | CLI entry point and command routing |
| `notes.py` | Note model (Note dataclass, YAML frontmatter, deterministic ID) |
| `storage.py` | File I/O, KB publish/read, INDEX.md and PREAMBLE.md generation |
| `paths.py` | KB path resolution (`KB_DIR`, per-command overrides) |
| `atomic.py` | Atomic file write (write to temp, rename) |
| `config.py` | Config model, per-operation overrides, default-provider policy |
| `search_index.py` | SQLite FTS5 index build and query |
| `retrieval.py` | Search, context, and explore surface logic |
| `ingest.py` | Ingest pipeline orchestration |
| `parsers.py` | Format-specific parsing (markdown, text, PDF, local HTML) |
| `ingest_recovery.py` | Checkpoint/resume, PID-stamped lock management |
| `compaction.py` | Cluster detection, proposal drafting, application |
| `hygiene.py` | Orphan/stale/low-utility note detection |
| `mutations.py` | Note mutations (merge, supersede) |
| `topic_mutations.py` | Topic reorganization |
| `review.py` | Review queue management |
| `providers.py` | Provider registry |
| `provider_seams.py` | Credential-source/backend seam definitions |
| `provider_retry.py` | Retry/backoff logic for transient provider failures |
| `privacy.py` | Secret scrubbing and redaction |
| `context.py` | Context surface formatting and citation blocks |
| `usage.py` | Usage logging and miss-feedback tracking |
| `git_auto.py` | Optional auto-commit after mutations |
| `doctor.py` | Health check subsystems (artifacts, index, locks, auth, backends) |
| `errors.py` | Shared error types |
| `init.py` | `kb init` command |

## Open questions

- Embedding retrieval: which backend first? When does the seam graduate to production?
- Index regeneration cadence: hook-driven, on-demand, or scheduled?
- Cross-KB discovery: is a meta-index ever needed across multiple KB directories?
```

- [ ] **Step 2: Verify file exists and is non-empty**

```bash
wc -l docs/specs/kb-librarian.md
```

Expected: 80+ lines.

---

## Task 3: Write `docs/adrs/2026-05-09-agent-first-architecture.md`

**Files:**
- Create: `docs/adrs/2026-05-09-agent-first-architecture.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Agent-First Architecture: In-Process CLI with Local SQLite Storage

## Context

kb-librarian needed a retrieval substrate that coding agents could call from a shell subprocess without standing up a server, managing sockets, or handling network partitions. The target environment is a developer workstation or CI container where the agent already has filesystem access.

## Decision

The system is an in-process Python CLI (`kb`) that operates entirely on a local filesystem KB at `~/.kb/.library` (configurable via `KB_DIR`). The core note model is markdown-with-YAML-frontmatter, with deterministic content-addressed IDs. Lexical indexing uses SQLite FTS5. The KB is treated as a published file artifact, not a database — notes are readable directly without the CLI if needed.

## Alternatives considered

- **Embedded HTTP server / daemon** — rejected: adds process lifecycle management, port conflicts, and auth concerns that are out of scope for a personal CLI tool.
- **Pure SQLite storage (no markdown files)** — rejected: agents lose the ability to read notes directly as files; markdown-first keeps the KB human- and agent-readable without tooling.
- **Embeddings-first retrieval** — deferred, not rejected: embedding retrieval seams are scaffolded (config, candidate/ranker stubs, doctor visibility) but no production embedding backend was shipped. FTS5 handles the primary retrieval use case with zero latency.
- **MCP wrapper** — explicitly deferred to a future phase.

## Consequences

- Every command must be stateless enough to run from a shell subprocess without shared memory.
- The file artifact must be self-describing (frontmatter, INDEX.md, PREAMBLE.md) so agents can read it directly if needed without the CLI.
- Retrieval is always local and latency-free for lexical queries.
- The candidate/ranker seam must be preserved without regression as embedding retrieval is added later.
```

- [ ] **Step 2: Verify frontmatter is valid**

```bash
head -6 docs/adrs/2026-05-09-agent-first-architecture.md
```

Expected: starts with `---`, contains `status: accepted`, `date: 2026-05-09`, `spec:` line.

---

## Task 4: Write `docs/adrs/2026-05-09-provider-backend-seams.md`

**Files:**
- Create: `docs/adrs/2026-05-09-provider-backend-seams.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Provider Backend Seams: Explicit Bounded Fallback Chains

## Context

kb-librarian needs to call LLM providers for note generation, ingest enrichment, and compaction. The set of viable providers (direct API, local Ollama/vLLM/LM Studio, vendor CLI delegation) was growing, and each has different credential mechanisms. Without an explicit seam, adding a new backend risked breaking existing routes or introducing implicit silent switching.

## Decision

Providers are abstracted behind two orthogonal seams: the credential source (API key, vendor CLI delegation, environment token) and the transport backend (direct HTTP, Ollama, vLLM, LM Studio, Claude Code CLI subprocess, Codex CLI subprocess). These compose independently. Per-operation config overrides compose with a global default-provider policy. Fallback chains are explicit and bounded — every fallback step is named in config, cycles are rejected by validation at load time, and silent implicit switching is forbidden.

## Alternatives considered

- **Dynamic runtime provider scoring** — rejected: non-deterministic; makes debugging impossible when a request goes to an unexpected provider.
- **Cost-based automatic optimization** — rejected: requires live cost information and introduces surprise billing; out of scope for a personal tool.
- **Hidden implicit fallback** — rejected: when a provider fails, the user must know which provider was tried and why it failed; silent fallback makes this impossible.
- **Single-provider hardcoding** — rejected: the personal tool needs to work across different machine setups (API key available vs. only CLI auth vs. local model).

## Consequences

- Provider selection must be deterministic and explainable from config alone.
- Error output must include the attempted provider sequence and the stop reason.
- Existing API-key routes cannot regress when new backend or auth modes are added.
- `kb doctor` must validate provider config (no cycles, all named backends resolvable) at startup.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-provider-backend-seams.md
```

---

## Task 5: Write `docs/adrs/2026-05-09-account-auth-delegation.md`

**Files:**
- Create: `docs/adrs/2026-05-09-account-auth-delegation.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Account Auth Delegation: Subprocess Delegation, No Credential Parsing

## Context

Users with Anthropic or OpenAI subscriptions can access model capacity without managing API keys if the vendor's CLI is installed and logged in. kb-librarian needed a way to use this capacity without storing, parsing, or transmitting vendor credentials itself.

## Decision

For Anthropic: delegate via `claude -p` (Claude Code CLI subprocess), authenticated through `claude auth login` OAuth or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. For Codex/OpenAI: delegate via `codex exec`, authenticated through `codex login` browser OAuth or `codex login --device-auth` device-code flow. In both cases, kb-librarian never parses credential-cache files, OS keyrings, or browser cookies. Delegation always runs completion-only (no model-initiated tool calls), with a bounded timeout and an explicit working directory. API-key auth remains the default when vendor CLIs are not configured.

## Alternatives considered

- **Parse `~/.claude/.credentials.json` or `~/.codex/auth.json` directly** — rejected: these are internal cache files with no stability contract; format changes would silently break auth.
- **Embedded OAuth web server** — rejected: adds port management and browser redirect handling that is out of scope for a CLI tool.
- **Browser session scraping** — rejected: fragile, security-sensitive, no stable interface.
- **Direct Messages API calls using subscription OAuth tokens** — rejected: Anthropic has not published a stable third-party contract for using account OAuth tokens against the Messages API.

## Consequences

- Auth can fail at the subprocess level for reasons not visible to kb-librarian (expired login, wrong CLI version, missing binary).
- `kb doctor` must distinguish at least four auth failure modes as separate findings: missing binary, missing login state, expired login state, and transport errors. These require different remediation steps and must not be collapsed into a generic error.
- Subprocess argv and env must be audited separately from log output — token values can appear in either and require targeted masking by `privacy.py`.
- Silent fallback from expired vendor CLI login to API key is forbidden unless explicitly configured by the user.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-account-auth-delegation.md
```

---

## Task 6: Write `docs/adrs/2026-05-09-retrieval-and-search.md`

**Files:**
- Create: `docs/adrs/2026-05-09-retrieval-and-search.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Retrieval and Search: FTS-First, Two Surfaces, Embedding Seams Deferred

## Context

Agents need to retrieve notes from the KB in two modes: task-shaped context (high precision, relevant to a specific coding task) and ideation/exploration (broad recall, discovering what exists). A third path — embedding/semantic retrieval — was considered but the infrastructure cost and latency implications were uncertain.

## Decision

Phase 1 ships SQLite FTS5 lexical search only. Two retrieval surfaces exist: `kb context` (high-precision, task-shaped, returns citation blocks) and `kb explore` (broad-recall, ideation, returns broader note summaries). Embedding retrieval seams were scaffolded in Phase 5d — config validation, candidate/ranker seam stubs, and doctor visibility — but no production embedding backend was shipped. Search-miss logging turns repeated poor retrieval into reviewable queue items rather than silent failures.

## Alternatives considered

- **Embeddings-first retrieval** — deferred: embedding retrieval has higher latency, requires a model call or local embedding server, and the FTS5 path handles the primary use case with zero latency. The seam exists; embedding can be added later without redesigning the surface.
- **Single unified search surface** — rejected: task-shaped context and broad exploration have different precision/recall trade-offs; a single surface would require callers to tune parameters that should be surface-specific defaults.
- **Remote web fetching for HTML ingest** — rejected: out of scope; local-only HTML ingest covers the primary use case (saved pages, local docs).

## Consequences

- The candidate/ranker seam must be preserved without regression when embedding retrieval is added.
- The FTS5 index must survive atomic rebuild failures — a failed rebuild preserves the previous good index rather than leaving the DB in a partial state.
- Search-miss logging must capture enough context (query, result count, session) for the review queue to surface actionable patterns.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-retrieval-and-search.md
```

---

## Task 7: Write `docs/adrs/2026-05-09-hygiene-and-compaction.md`

**Files:**
- Create: `docs/adrs/2026-05-09-hygiene-and-compaction.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Hygiene and Compaction: Always Review-Gated, Never Autonomous

## Context

Over time, the KB accumulates overlapping notes (covered by different sessions), stale notes (not retrieved in a long time), orphaned notes (no backlinks), and low-utility notes (flagged by agents). A mechanism was needed to detect and address these without risking autonomous knowledge mutation.

## Decision

All knowledge mutations (compaction application, topic reorganization) are review-gated. Cluster detection is deterministic — it uses title/phrase/tag/backlink overlap plus repeated `adds_nuance` verdicts from the review queue. Detection produces proposals with provenance (source note IDs, disposition plans, diff summaries); it never rewrites notes automatically. A cooldown config prevents queue flooding from repeated runs. `kb doctor` is the canonical health check surface: it is read-only, returns nonzero on errors, and covers broken artifacts, stale locks, interrupted state, and auth/backend problems.

## Alternatives considered

- **Autonomous compaction (no review gate)** — rejected: knowledge mutations without human review risk silent loss of nuance or context that the agent did not recognize as unique. This was explicitly out of scope for the entire roadmap.
- **Similarity threshold auto-merge** — rejected: semantic similarity does not imply safe mergability; two notes can be lexically similar while capturing different decisions.
- **Doctor as a mutation surface** — rejected: `kb doctor` is intentionally read-only. Mixing diagnostics and mutation makes it impossible to run safely in CI or monitoring contexts.

## Consequences

- Every destructive hygiene mutation requires an accepted review item and a clean-worktree check before proceeding.
- Compaction proposals must preserve enough provenance for the human to make the acceptance decision without re-reading the full cluster.
- Cooldown config must be tunable so high-ingest environments don't flood the queue.
- `kb doctor` exit code must be nonzero on any error finding, so it is usable as a CI health gate.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-hygiene-and-compaction.md
```

---

## Task 8: Write `docs/adrs/2026-05-09-robustness-patterns.md`

**Files:**
- Create: `docs/adrs/2026-05-09-robustness-patterns.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Robustness Patterns: Atomic Writes, PID Locks, and Checkpoint Resume

## Context

Ingest and compaction operations mutate KB state across multiple files and the SQLite index. Interrupted runs (power loss, kill signal, agent timeout) left the KB in partial states that were difficult to detect and recover from. Provider transient failures caused entire ingest runs to fail rather than retry.

## Decision

Three patterns apply across all mutation operations:

1. **Atomic writes everywhere.** All note, review, index, and state file writes use write-to-temp-then-rename (`atomic.py`). No partial write is ever visible to a concurrent reader.

2. **PID-stamped lockfile for ingest.** Ingest acquires `.kb/ingest.lock` (containing the PID) before mutating any state. `kb ingest --resume` reads the checkpoint at `.kb/state.json` and replays from the last completed file without duplicating notes, review items, or archived raw files. Stale lock auto-recovery without explicit `--force` or `--resume` is forbidden.

3. **Checkpoint-based resume.** `.kb/state.json` tracks per-file ingest progress. On resume, idempotency is enforced: all writes downstream of the checkpoint are content-addressed or guard-checked before writing. Failed SQLite index rebuilds preserve the previous good index.

Provider transient failures use configurable retry/backoff (`provider_retry.py`); permanent failures leave recoverable state and report via `kb doctor`.

## Alternatives considered

- **Stale lock auto-recovery** — rejected: auto-deleting a lock without knowing whether the previous process is truly dead risks concurrent mutation. Explicit `--force` or `--resume` is required.
- **Partial index replacement on failed rebuild** — rejected: a partial index produces subtly wrong search results that are harder to detect than a missing index. The previous good index is always preserved on rebuild failure.
- **Single-pass ingest (no checkpoint)** — rejected: long ingest runs over large document sets fail silently at the end; checkpoint-based resume makes interruption safe.

## Consequences

- Operation IDs must be attached to all ingest-related log entries so recovery guidance is actionable.
- `kb doctor` must surface stale locks and interrupted checkpoints as a Recovery subsystem finding with a specific remediation step.
- Resume idempotency requires all writes downstream of the checkpoint to be content-addressed or guard-checked before writing.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-robustness-patterns.md
```

---

## Task 9: Write `docs/adrs/2026-05-09-adopt-personal-workflow-convention.md`

**Files:**
- Create: `docs/adrs/2026-05-09-adopt-personal-workflow-convention.md`

- [ ] **Step 1: Create the ADR**

```markdown
---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Adopt Personal Workflow Convention for AI-Assisted Work

## Context

kb-librarian development accumulated ad-hoc plan files in `docs/superpowers/plan/` (25 phased files) and `docs/superpowers/plans/` (4 dated files), plus design specs in `docs/superpowers/specs/`. These lived alongside each other with no clear lifecycle rules: plans were never compacted, old specs were never superseded, and there was no distinction between durable decisions and transient working state.

## Decision

Adopt the personal-workflow convention (`docs/specs/`, `docs/adrs/`, `docs/notes/`, `.agents/workflow/`) in this repo. Durable artifacts (specs, decisions, lessons) live in `docs/`; transient working state (active plans, research) lives in `.agents/workflow/`. Shipped plans are compacted into ADRs and notes, then deleted. The convention is packaged as an agent-skills skill (`personal-workflow`) and installed via the existing submodule.

Design spec: `docs/superpowers/specs/2026-05-09-personal-workflow-convention-design.md` (superseded by this migration; the spec file is deleted as part of the migration).

## Alternatives considered

- **Keep the `docs/superpowers/` layout** — rejected: no lifecycle rules means plans accumulate indefinitely; no distinction between durable and transient means agents load irrelevant historical plans into context.
- **Flat docs structure (no subfolders)** — rejected: specs, ADRs, and notes have different mutability and lifecycle rules; mixing them in one folder makes status and lifecycle unclear.
- **External wiki or Notion** — rejected: out of scope for a local-first personal tool; markdown in the repo is grep-able and agent-readable without additional tooling.

## Consequences

- All future plans go in `.agents/workflow/plans/YYYY-MM-DD-<slug>/` (folder per plan).
- Shipped plans are compacted; abandoned plans get an ADR explaining why they were abandoned.
- `docs/superpowers/` is deleted after this migration; do not recreate it.
- The `_index.md` files in `docs/specs/`, `docs/adrs/`, and `docs/notes/` are rebuilt whenever artifacts change.
```

- [ ] **Step 2: Verify frontmatter**

```bash
head -6 docs/adrs/2026-05-09-adopt-personal-workflow-convention.md
```

---

## Task 10: Write notes

**Files:**
- Create: `docs/notes/providers/cli-delegation-pitfalls.md`
- Create: `docs/notes/ingest/pdf-html-parsing.md`

- [ ] **Step 1: Create `docs/notes/providers/cli-delegation-pitfalls.md`**

```markdown
# CLI Delegation Pitfalls

**Spec:** ../../specs/kb-librarian.md
**Last updated:** 2026-05-09

## What

Auth via vendor CLI delegation (`claude -p`, `codex exec`) can fail silently in ways that look like provider errors but are actually auth state issues. Four distinct failure modes must be handled separately.

## Why it matters

Collapsing these into a generic provider error makes it impossible for the user to know what to fix. Each requires a different remediation step.

| Failure mode | Symptom | Remediation |
|---|---|---|
| Missing binary | `FileNotFoundError` or `which` returns nothing | Install the CLI |
| Missing login state | Subprocess exits nonzero with "not logged in" | `claude auth login` or `codex login` |
| Expired login | Subprocess exits nonzero with token/session error | Re-run the login command |
| Transport error | Subprocess exits nonzero with network/timeout message | Retry; check connectivity |

Additional pitfalls:
- `claude auth status` may not exist on all Claude Code versions; fall back to a guarded live probe only on explicit opt-in, never automatically.
- Codex device-auth (`codex login --device-auth`) is only available when the OpenAI account has device-code auth enabled — this cannot be probed without a live network call.
- Subprocess argv and env must both be audited for token values — they can appear in either location and require targeted masking.
- Silent fallback from expired vendor CLI login to API key is forbidden unless the user explicitly configures a fallback chain.

## See also

- [../../adrs/2026-05-09-account-auth-delegation.md](../../adrs/2026-05-09-account-auth-delegation.md)
- [../../adrs/2026-05-09-provider-backend-seams.md](../../adrs/2026-05-09-provider-backend-seams.md)
```

- [ ] **Step 2: Create `docs/notes/ingest/pdf-html-parsing.md`**

```markdown
# PDF and HTML Ingest Parsing Quirks

**Spec:** ../../specs/kb-librarian.md
**Last updated:** 2026-05-09

## What

PDF and local HTML ingest have specific scope limitations and failure modes that differ from markdown/text ingest.

## Why it matters

Parser failures that are silently dropped create gaps in the KB that are invisible until a user notices missing knowledge. The failure must be surfaced as a reviewable item.

### PDF

- **Local only.** No OCR, no image extraction. PDFs with image-only content (scanned documents) will produce empty or near-empty notes.
- **Parser failures** must surface as reviewable items in the review queue, not as silent drops. The failing file path and failure stage must be included.
- **Error log.** Failures that block ingest should write to `.kb/errors.log` with stage information so `kb doctor` can surface them.

### HTML

- **Local only.** Remote URLs are explicitly out of scope for Phase 4c. Only file-path arguments are accepted; URLs are rejected with a clear error.
- **No JavaScript rendering.** HTML is parsed statically; dynamic content loaded by JS is not captured.
- **Parser failures** follow the same pattern as PDF: reviewable item, not silent drop.

## See also

- [../../adrs/2026-05-09-robustness-patterns.md](../../adrs/2026-05-09-robustness-patterns.md)
```

- [ ] **Step 3: Verify both notes exist**

```bash
ls docs/notes/providers/ docs/notes/ingest/
```

Expected: `cli-delegation-pitfalls.md` and `pdf-html-parsing.md`.

---

## Task 11: Update `_index.md` files and commit extraction

**Files:**
- Modify: `docs/specs/_index.md`
- Modify: `docs/adrs/_index.md`
- Modify: `docs/notes/_index.md`

- [ ] **Step 1: Update `docs/specs/_index.md`**

Replace the content with:

```markdown
# Specs Index

_Last updated: 2026-05-09_

- [kb-librarian](kb-librarian.md) — local-first knowledge base CLI for coding agents
```

- [ ] **Step 2: Update `docs/adrs/_index.md`**

Replace the content with:

```markdown
# ADRs Index

_Last updated: 2026-05-09_

- [2026-05-09-adopt-personal-workflow-convention](2026-05-09-adopt-personal-workflow-convention.md) — adopt personal-workflow convention for workflow artifacts _(accepted)_
- [2026-05-09-robustness-patterns](2026-05-09-robustness-patterns.md) — atomic writes, PID locks, checkpoint resume _(accepted)_
- [2026-05-09-hygiene-and-compaction](2026-05-09-hygiene-and-compaction.md) — always review-gated, never autonomous _(accepted)_
- [2026-05-09-retrieval-and-search](2026-05-09-retrieval-and-search.md) — FTS-first, two surfaces, embedding seams deferred _(accepted)_
- [2026-05-09-account-auth-delegation](2026-05-09-account-auth-delegation.md) — subprocess delegation, no credential parsing _(accepted)_
- [2026-05-09-provider-backend-seams](2026-05-09-provider-backend-seams.md) — explicit bounded fallback chains _(accepted)_
- [2026-05-09-agent-first-architecture](2026-05-09-agent-first-architecture.md) — in-process CLI with local SQLite storage _(accepted)_
```

- [ ] **Step 3: Update `docs/notes/_index.md`**

Replace the content with:

```markdown
# Notes Index

_Last updated: 2026-05-09_

## Providers

- [providers/cli-delegation-pitfalls.md](providers/cli-delegation-pitfalls.md) — auth refresh edge cases for vendor CLI delegation

## Ingest

- [ingest/pdf-html-parsing.md](ingest/pdf-html-parsing.md) — PDF and HTML parser scope limits and failure modes
```

- [ ] **Step 4: Verify all index files**

```bash
cat docs/specs/_index.md && echo "---" && cat docs/adrs/_index.md && echo "---" && cat docs/notes/_index.md
```

- [ ] **Step 5: Commit all extracted artifacts**

```bash
git add docs/specs/ docs/adrs/ docs/notes/
git commit -m "Extract decisions and notes from shipped kb-librarian plans"
```

---

## Task 12: Delete old plan files

**Files:**
- Delete: `docs/superpowers/plan/` (entire tree)
- Delete: `docs/superpowers/plans/` (entire tree)

- [ ] **Step 1: Delete the old plan directories**

```bash
rm -rf docs/superpowers/plan docs/superpowers/plans
```

- [ ] **Step 2: Verify deletion**

```bash
ls docs/superpowers/
```

Expected: only `specs/` remains (and possibly this plan file itself if run before commit — that is expected).

- [ ] **Step 3: Commit the deletion**

```bash
git add -A docs/superpowers/plan docs/superpowers/plans
git commit -m "Delete shipped plan files after compaction"
```

---

## Task 13: Delete old spec files and clean up

**Files:**
- Delete: `docs/superpowers/specs/` (entire tree)
- Delete: `docs/superpowers/` (directory, now empty)

- [ ] **Step 1: Delete remaining superpowers content**

```bash
rm -rf docs/superpowers
```

- [ ] **Step 2: Verify `docs/superpowers/` is gone**

```bash
ls docs/
```

Expected: `adrs/  configuration.md  notes/  specs/  (other product docs)` — no `superpowers/`.

- [ ] **Step 3: Commit the cleanup**

```bash
git add -A docs/superpowers
git commit -m "Delete old superpowers specs tree after migration"
```

---

## Task 14: Final verification

- [ ] **Step 1: Verify full new structure**

```bash
find docs/specs docs/adrs docs/notes .agents/workflow -type f | sort
```

Expected (13 files total):
```
.agents/workflow/plans/.gitkeep
.agents/workflow/research/.gitkeep
docs/adrs/2026-05-09-account-auth-delegation.md
docs/adrs/2026-05-09-agent-first-architecture.md
docs/adrs/2026-05-09-adopt-personal-workflow-convention.md
docs/adrs/2026-05-09-hygiene-and-compaction.md
docs/adrs/2026-05-09-provider-backend-seams.md
docs/adrs/2026-05-09-retrieval-and-search.md
docs/adrs/2026-05-09-robustness-patterns.md
docs/notes/_index.md
docs/notes/ingest/pdf-html-parsing.md
docs/notes/providers/cli-delegation-pitfalls.md
docs/specs/_index.md
docs/specs/kb-librarian.md
docs/adrs/_index.md
```

- [ ] **Step 2: Verify `docs/superpowers/` is gone**

```bash
ls docs/superpowers 2>&1
```

Expected: `ls: cannot access 'docs/superpowers': No such file or directory`

- [ ] **Step 3: Verify all ADR frontmatter is valid**

```bash
grep -l "status:" docs/adrs/*.md | wc -l
```

Expected: `7` (all 7 ADRs have status frontmatter).

- [ ] **Step 4: Verify git log shows 4 commits for this work**

```bash
git log --oneline -6
```

Expected last 4 commits (newest first):
1. `Delete old superpowers specs tree after migration`
2. `Delete shipped plan files after compaction`
3. `Extract decisions and notes from shipped kb-librarian plans`
4. `Bootstrap personal-workflow folder structure`

---

## Self-review

**Spec coverage:**
- Bootstrap new folder structure → Task 1 ✓
- Fresh `docs/specs/kb-librarian.md` from current code → Task 2 ✓
- 7 thematic ADRs → Tasks 3–9 ✓
- 2 notes → Task 10 ✓
- Update `_index.md` files → Task 11 ✓
- 4-commit strategy (bootstrap, extract, delete plans, delete specs) → Tasks 1, 11, 12, 13 ✓
- Final verification → Task 14 ✓

**Placeholder scan:** No TBD/TODO. All ADR content is concrete. All commands have expected outputs.

**Type consistency:** Frontmatter field names (`status`, `date`, `spec`) are consistent across all 7 ADRs. Index bullet format is consistent across all 3 `_index.md` files. File stem naming (`2026-05-09-<slug>`) is consistent.

**Spec gaps:** The design spec notes the `adopt-personal-workflow-convention` ADR should fold in the existing convention design spec — handled in Task 9 (the ADR references the source spec, and the source spec is deleted in Task 13). ✓
