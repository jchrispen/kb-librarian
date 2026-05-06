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

This creates:

- top-level KB files such as `INDEX.md` and `PREAMBLE.md`
- `topics/` for canonical notes
- `raw/` for ingest input
- `review/` for human review queues
- `.kb/` for config and generated state

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

## Rebuild Indexes

Run `kb reindex` after manual note edits or when you want to rebuild generated state.

```bash
kb reindex --data-dir /path/to/kb
```

This rebuilds generated artifacts such as:

- top-level and topic `INDEX.md` files
- `.kb/backlinks.json`
- `.kb/index-manifest.json`
- `.kb/fts.sqlite`

## Search and Inspect Notes

Use `kb search` for precise lookup:

```bash
kb search "sqlite fts5" --data-dir /path/to/kb
```

Filter by topic or knowledge type:

```bash
kb search "retrieval" --topic agent-systems --type technique --data-dir /path/to/kb
```

Inspect one note:

```bash
kb get 2026-05-04-agent-context-cli-contract --data-dir /path/to/kb
```

Get a summary instead of full markdown:

```bash
kb get 2026-05-04-agent-context-cli-contract --summary --data-dir /path/to/kb
```

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

## Review Pending Work

Use `kb review` to inspect bounded review queues.

```bash
kb review --data-dir /path/to/kb
```

Review items are stored in `review/review-items.json` with stable IDs, status, payload, and history. The markdown files under `review/` are generated inspection surfaces:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/disputes.md`
- `review/search-misses.md`

`kb review` and `kb review list` read from `review/review-items.json`, hide resolved items from default output, and bound the number of listed items using `review.max_review_items_per_run`. If a Phase 1 KB only has markdown queues, the first review run imports those entries into durable state and rerenders the queue files.

Current shipped review behavior is still read-only summary output. Actionable review commands are planned but not part of the current CLI.

## Recommended Early Workflow

1. Run `kb init` once.
2. Add a few seed notes with `kb add`.
3. Put candidate source files in `raw/` or point `kb ingest` at them directly.
4. Run `kb reindex` when you make manual changes.
5. Use `kb search` for exact concepts.
6. Use `kb context` before architecture or coding work.
7. Check `kb review` periodically for classification, merge, dispute, duplicate, and unsupported-file items.
