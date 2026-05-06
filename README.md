# KB Librarian

KB Librarian is a local-first knowledge base CLI for coding agents.

It stores durable knowledge as markdown files in a separate data directory, builds disposable local indexes for retrieval, and gives agents a stable command-line interface for finding task-shaped context.

## Status

This repository currently ships the Phase 1 core workflow plus Phase 2 daily-use ergonomics:

- `kb init`
- `kb add`
- `kb ingest`
- `kb reindex`
- `kb search`
- `kb get`
- `kb review`
- `kb context`
- `kb explore`
- `kb log-use`
- `kb usage`

Planned later-phase features such as compaction, doctor diagnostics, and automation templates are documented in the design and implementation plans, but they are not part of the shipped CLI yet.

## What It Does

- Publishes the KB as markdown files plus frontmatter in a local data directory
- Builds local lexical indexes for note retrieval
- Ingests markdown and text files into candidate notes
- Preserves review-gated behavior for risky integrations
- Returns compact, cited context for coding and design tasks

## Install

Requirements:

- Python 3.11+
- Optional: `ANTHROPIC_API_KEY` for the default provider-backed ingest and context flow

Install in editable mode:

```bash
python3 -m pip install -e .
```

Verify the CLI:

```bash
kb --help
```

## Quick Start

1. Initialize a KB data directory.

```bash
kb init --data-dir /path/to/kb
```

To install or refresh agent session guidance:

```bash
kb init --hooks --data-dir /path/to/kb
```

2. Create a note directly.

```bash
printf '## Core idea\n\nUse a CLI as the stable agent contract.\n' | \
  kb add --data-dir /path/to/kb --topic agent-systems --type technique
```

3. Rebuild indexes.

```bash
kb reindex --data-dir /path/to/kb
```

4. Search for a known concept.

```bash
kb search "stable agent contract" --data-dir /path/to/kb
```

5. Get task-shaped context.

```bash
kb context "review this architecture" --mode architecture --data-dir /path/to/kb
```

## Data Directory Layout

By default, KB Librarian uses `/mnt/c/workspace/source/internal/kb` unless you pass `--data-dir` or set `KB_DATA_DIR`.

A KB data directory contains:

- `INDEX.md`
- `PREAMBLE.md`
- `topics/`
- `raw/`
- `review/`
- `.kb/`

The markdown notes are the durable knowledge artifact. Review items are durable state in `review/review-items.json`; the markdown queue files in `review/` are generated inspection surfaces. Generated indexes and local state under `.kb/` can be rebuilt.

Important review files:

- `review/review-items.json`: source of truth for review item IDs, status, payload, and history
- `review/pending-classification.md`: generated view for classification review
- `review/pending-merge.md`: generated view for merge proposals
- `review/disputes.md`: generated view for contradictions
- `review/search-misses.md`: generated view for repeated or explicitly reported search misses
- `PREAMBLE.md`: agent-facing retrieval guidance installed by `kb init --hooks`

## Core Commands

- `kb init`: create the KB directory structure and default config
- `kb add`: create a note directly, or queue raw input when metadata is incomplete
- `kb ingest`: process markdown or text files into notes or review items
- `kb reindex`: rebuild markdown indexes, backlinks, manifest, stats, and lexical index
- `kb search`: search notes by title, summary, tags, retrieval phrases, and body text
- `kb get`: inspect one note by ID
- `kb review`: inspect and resolve bounded review queues backed by `review/review-items.json`
- `kb context`: retrieve compact cited context for a task
- `kb explore`: retrieve broader adjacent ideas and alternatives
- `kb log-use`: record that an agent used or cited a note
- `kb usage`: summarize retrieval, note-use, and search-miss signals

## Provider Notes

The default config uses the Anthropic provider routes. That means `kb ingest` and `kb context` need `ANTHROPIC_API_KEY` unless you reconfigure the provider in `.kb/config.yaml`.

For offline development or tests, the codebase also supports a deterministic `mock` provider, but it is not the default config written by `kb init`.

## Documentation

- [User Guide](docs/user-guide.md)
- [Configuration Reference](docs/configuration.md)
- [Command Reference](docs/commands.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Canonical Design Spec](docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md)

## Development Notes

The root README is the end-user entry point. The files under `docs/superpowers/` are design and planning artifacts for implementation work.
