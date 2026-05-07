# User Guide

## Overview

KB Librarian maintains a local knowledge base for coding and design agents.

The durable artifact is a directory of markdown notes with frontmatter. The `kb` CLI is the primary access path for creating notes, ingesting source material, rebuilding indexes, inspecting review work, and retrieving context.

## Before You Start

You need:

- Python 3.11+
- A writable KB data directory
- `ANTHROPIC_API_KEY` if you want to use the default provider-backed ingest and context flow

Install the package:

```bash
python3 -m pip install -e .
```

## Initialize a KB

Create a KB data directory:

```bash
kb init --data-dir /path/to/kb
```

Install or refresh the agent-facing preamble:

```bash
kb init --hooks --data-dir /path/to/kb
```

`--hooks` writes the tool-managed `PREAMBLE.md` and prints the path to include in agent session instructions. It does not edit external agent configuration files.

This creates:

- top-level KB files such as `INDEX.md` and `PREAMBLE.md`
- `topics/` for canonical notes
- `raw/` for ingest input
- `review/` for human review queues
- `.kb/` for config, generated state, and append-only logs

If you omit `--data-dir`, KB Librarian resolves the data directory in this order:

1. `--data-dir`
2. `KB_DATA_DIR`
3. `data_dir` stored in `<default-data-dir>/.kb/config.yaml`
4. `/mnt/c/workspace/source/internal/kb`

## Create Notes Directly

Use `kb add` when you already know the topic and note type.

Example:

```bash
cat <<'EOF2' | kb add --data-dir /path/to/kb --topic agent-systems --type technique
## Use when

You need a stable cross-agent interface.

## Core idea

Use a CLI as the access contract.
EOF2
```

Topics can be nested with slash delimiters, for example `agent-systems/retrieval`.

If you do not provide enough metadata for direct note creation, `kb add` queues the input under `raw/` instead.

## Ingest Source Material

Use `kb ingest` to process markdown or text files.

Ingest one file:

```bash
kb ingest notes/design-idea.md --data-dir /path/to/kb
```

Process pending files already placed in `raw/`:

```bash
kb ingest --data-dir /path/to/kb
```

Resume an interrupted ingest:

```bash
kb ingest --resume --data-dir /path/to/kb
```

Current shipped ingest support is:

- `.md`
- `.txt`

Ingest may:

- create notes
- append sources to existing notes
- queue classification review items
- queue merge proposals
- mark disputes
- record duplicate and unsupported-file review items
- archive processed raw files

The default report includes stable counts plus the note IDs and review item IDs that need attention. JSON output is available for automation:

```bash
kb ingest --json --data-dir /path/to/kb
```

Use `--quiet` when only errors should be printed.

Ingest holds `.kb/ingest.lock` while mutating KB state and records progress in `.kb/state.json`. If another ingest is active, the command refuses to process files. If a lock or checkpoint is stale after an interruption, prefer `--resume`; use `--force` only when you want to discard stale recovery state and start a new ingest.

## Rebuild Indexes

Run `kb reindex` after manual note edits or when you want to rebuild generated state.

```bash
kb reindex --data-dir /path/to/kb
```

This rebuilds generated artifacts such as:

- top-level and topic `INDEX.md` files
- paginated `INDEX-2.md`, `INDEX-3.md`, and later markdown index pages when a configured page size is exceeded
- `.kb/backlinks.json`
- `.kb/index-manifest.json`
- `.kb/stats.json`
- `.kb/fts.sqlite`

Pagination keeps large markdown indexes readable for humans. Retrieval commands continue to use note metadata and `.kb/fts.sqlite`, not the paginated markdown files.

To detect overlapping notes and queue compaction plus hygiene work:

```bash
kb reindex --scan-clusters --data-dir /path/to/kb
```

This scan respects `review.duplicate_cluster_threshold`, `review.compaction_cooldown_days`, `review.stale_after_days`, and `review.orphan_after_days`. It creates or refreshes review items only; it does not rewrite notes.

## Check KB Health

Run `kb doctor` when retrieval looks stale, after manual edits, after a sync conflict, or before trusting a large KB handoff:

```bash
kb doctor --data-dir /path/to/kb
```

Doctor prints grouped `ok`, `warn`, and `error` findings. Errors produce a nonzero exit code. Warnings usually point to rebuildable generated state or provider setup that only matters when running provider-backed commands.

For a fast offline confidence check of the local package:

```bash
kb doctor --self-test
```

The self-test uses a temporary KB and the deterministic mock provider. It does not mutate your configured KB.

## Search and Inspect Notes

Use `kb search` for precise lookup:

```bash
kb search "sqlite fts5" --data-dir /path/to/kb
```

Filter by topic or knowledge type:

```bash
kb search "retrieval" --topic agent-systems --type technique --data-dir /path/to/kb
```

Include a citation block when you plan to cite search results:

```bash
kb search "retrieval" --with-citations --data-dir /path/to/kb
```

Searches are logged to `.kb/usage.log` without note bodies. Empty or weak searches are logged to `.kb/search-misses.log`; repeated misses become review items under `review/search-misses.md`. If results appear but are not useful, record that signal explicitly:

```bash
kb search "retrieval" --report-miss --data-dir /path/to/kb
```

Inspect one note:

```bash
kb get 2026-05-04-agent-context-cli-contract --data-dir /path/to/kb
```

Get a summary instead of full markdown:

```bash
kb get 2026-05-04-agent-context-cli-contract --summary --data-dir /path/to/kb
```

## Inspect Topics

List topics and direct note counts:

```bash
kb topics --data-dir /path/to/kb
```

Render nested topics as a tree:

```bash
kb topics --tree --data-dir /path/to/kb
```

When review state exists, these views also include stale/orphan/review counts per topic.

Rename or promote topics when the move is mechanical:

```bash
kb topic rename old-topic new-topic --data-dir /path/to/kb
kb topic promote retrieval --under agent-systems --data-dir /path/to/kb
```

These commands move note files, update note frontmatter topics, and rebuild indexes. If the KB is inside git, they require a clean worktree unless you pass `--force`.

Use review-gated proposals when the move requires judgment:

```bash
kb topic split mixed-topic --into agent-systems/retrieval agent-systems/review --data-dir /path/to/kb
kb topic merge agent-systems/retrieval retrieval-patterns --as agent-systems/retrieval --data-dir /path/to/kb
```

Apply accepted split or merge proposals with `kb review accept <item-id>`.

## Retrieve Task Context

Use `kb context` before coding, design, debugging, or review work.

```bash
kb context "review this architecture" --mode architecture --data-dir /path/to/kb
```

Supported modes:

- `coding`
- `architecture`
- `debugging`
- `writing`
- `research`
- `review`

JSON output is available:

```bash
kb context "review this architecture" --mode architecture --json --data-dir /path/to/kb
```

Context output includes source notes and a `KB sources` citation block by default. JSON output includes structured `citations`.

Context retrievals are logged automatically. Use `--report-miss` when the returned context is not useful enough and should become KB improvement feedback.

## Explore Ideas

Use `kb explore` for ideation, alternatives, architecture options, tradeoffs, and adjacent concepts.

```bash
kb explore "ways to reduce token burn while preserving agent access" --data-dir /path/to/kb
```

`kb explore` uses broader recall than `kb context` and includes source notes plus a `KB sources` citation block by default. JSON output includes structured `citations`:

```bash
kb explore "agent context alternatives" --json --data-dir /path/to/kb
```

Exploration retrievals are logged automatically. Use `--report-miss` when exploration misses the useful adjacent concepts you expected.

## Track Usage and Misses

Record that an agent actually used or cited a note:

```bash
kb log-use 2026-05-04-agent-context-cli-contract --task "cited during architecture review" --data-dir /path/to/kb
```

Inspect lightweight usage summaries:

```bash
kb usage --data-dir /path/to/kb
kb usage --since 7d --data-dir /path/to/kb
kb usage --note 2026-05-04-agent-context-cli-contract --data-dir /path/to/kb
```

Usage summaries include retrieval counts, explicit note-use counts, top retrieved notes, logged note uses, and search-miss counts. The logs are append-only JSONL files and do not store note bodies:

- `.kb/usage.log`
- `.kb/search-misses.log`
- `.kb/stats.json`

Flag suspect notes when retrieved context is wrong, stale, or otherwise not useful:

```bash
kb flag-suspect 2026-05-04-agent-context-cli-contract "outdated guidance for current workflow" --data-dir /path/to/kb
```

This appends a structured suspect event and upserts either a `low_utility` item or a contradiction-oriented `dispute` item.

## Review Pending Work

Use `kb review` to inspect bounded review queues.

```bash
kb review --data-dir /path/to/kb
```

Review items are stored in `review/review-items.json` with stable IDs, status, payload, and history. The markdown files under `review/` are generated inspection surfaces:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/pending-compaction.md`
- `review/pending-topic.md`
- `review/disputes.md`
- `review/stale.md`
- `review/orphans.md`
- `review/search-misses.md`
- `review/low-utility.md`

`kb review` and `kb review list` read from `review/review-items.json`, hide resolved items from default output, hide deferred items until due, and bound the number of listed items using `review.max_review_items_per_run`. If a Phase 1 KB only has markdown queues, the first review run imports those entries into durable state and rerenders the queue files.

Repeated or explicitly reported search misses become `searchmiss` review items. Accepting a search-miss item records a resolution note; it does not create or rewrite canonical notes automatically.

Compaction cluster items can be turned into proposal drafts:

```bash
kb compact <topic-or-cluster> --data-dir /path/to/kb
```

The target can be a topic name, a compaction review item ID, or a `cluster-...` ID from `review/pending-compaction.md`. The command stores provider-drafted frontmatter, body markdown, source-note dispositions, and a diff summary as another pending compaction review item. It does not mutate canonical notes until you accept the resulting proposal:

```bash
kb review accept <compaction-item-id> --data-dir /path/to/kb
```

Accepted compaction creates a canonical note, preserves source-note provenance, supersedes or archives source notes according to the proposal, and rebuilds indexes. If the KB is inside git, acceptance requires a clean worktree unless you pass `--force`.

Explain one review item:

```bash
kb review explain <item-id> --data-dir /path/to/kb
```

Resolve a classification item by creating a note:

```bash
kb review accept <item-id> --topic agent-systems --type technique --data-dir /path/to/kb
```

Resolve a classification item by appending the source to an existing note:

```bash
kb review accept <item-id> --note-id 2026-05-04-existing-note --data-dir /path/to/kb
```

Resolve a merge item with explicit body-append approval:

```bash
kb review accept <item-id> --append-body --data-dir /path/to/kb
```

Reject or defer an item:

```bash
kb review reject <item-id> --data-dir /path/to/kb
kb review defer <item-id> --days 30 --data-dir /path/to/kb
```

For `searchmiss` items, provide a resolution note:

```bash
kb review accept <item-id> --resolution-note "Added retrieval phrase and topic seed note." --data-dir /path/to/kb
```

In the current shipped CLI, accept actions are supported for classification, merge, dispute, search-miss, compaction, and topic proposal items. Duplicate and unsupported-file items should be handled with `reject` or `defer`.

## Recommended Early Workflow

1. Run `kb init` once.
2. Add a few seed notes with `kb add`.
3. Put candidate source files in `raw/` or point `kb ingest` at them directly.
4. Run `kb reindex` when you make manual changes.
5. Use `kb search` for exact concepts.
6. Use `kb context` before architecture or coding work.
7. Use `kb explore` when you need alternatives or adjacent ideas.
8. Run `kb log-use` after citing a note in agent work.
9. Run `kb usage` periodically to inspect retrieval and note-use signals.
10. Run `kb reindex --scan-clusters` periodically when note overlap is likely.
11. Run `kb doctor` when generated state, review state, or provider setup may be stale.
12. Check `kb review` periodically for classification, merge, compaction, topic, dispute, search-miss, duplicate, and unsupported-file items.
