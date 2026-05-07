# KB Librarian

KB Librarian is a local-first knowledge base CLI for coding agents.

It stores durable knowledge as markdown files in a separate data directory, builds disposable local indexes for retrieval, and gives agents a stable command-line interface for finding task-shaped context.

## Status

This repository currently ships the Phase 1 core workflow, Phase 2 daily-use ergonomics, Phase 3 hygiene/topic surfaces, Phase 4 robustness/polish features, the Phase 5a local-provider core, Phase 5b Codex provider support, Phase 5c provider policy/fallback support, and Phase 5e privacy/redaction controls:

- `kb init`
- `kb add`
- `kb ingest`
- `kb reindex`
- `kb compact`
- `kb search`
- `kb get`
- `kb topics`
- `kb topic`
- `kb review`
- `kb context`
- `kb explore`
- `kb log-use`
- `kb flag-suspect`
- `kb usage`
- `kb doctor`

Remaining Phase 5+ provider and retrieval extensions remain deferred backlog in the design and implementation plans.

## What It Does

- Publishes the KB as markdown files plus frontmatter in a local data directory
- Builds local lexical indexes for note retrieval
- Ingests markdown, text, PDF, and local HTML files into candidate notes
- Preserves review-gated behavior for risky integrations
- Returns compact, cited context for coding and design tasks
- Detects overlapping note clusters and drafts reviewable compaction proposals without rewriting notes
- Applies accepted compaction proposals with source-note supersession and index regeneration
- Surfaces stale, orphan, and low-utility notes with explainable review evidence
- Reports KB health with read-only doctor diagnostics and an offline self-test
- Supports slash-delimited hierarchical topics with nested topic indexes and `kb topics --tree`
- Supports clean-worktree-protected topic rename/promote operations and review-gated topic split/merge proposals
- Routes provider-backed operations through Anthropic by default, an opt-in Codex-compatible cloud provider, or an opt-in local Ollama provider
- Supports explicit provider policy with deterministic default-provider selection and bounded per-operation fallback
- Supports first-pass privacy controls for blocking cloud providers and redacting provider-bound payload text
- Retries transient provider failures with bounded backoff and logs retry/final-stop diagnostics
- Includes deterministic golden corpus regression tests plus opt-in live-provider smoke coverage
- Generates optional hook/scheduler templates without installing them automatically
- Supports guarded opt-in git auto-commit for successful KB mutations

## Install

Requirements:

- Python 3.11+
- Optional: `ANTHROPIC_API_KEY` for the default provider-backed ingest and context flow
- Optional: `OPENAI_API_KEY` for Codex-routed provider-backed operations

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

By default, KB Librarian stores its library at `~/.kb/.library` with config at `~/.kb/config.yaml`.
Pass `--data-dir` or set `KB_DATA_DIR` to use a different library.

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
- `review/pending-compaction.md`: generated view for compaction clusters and proposals
- `review/pending-topic.md`: generated view for topic split and merge proposals
- `review/parser-failures.md`: generated view for supported files that could not be parsed
- `review/disputes.md`: generated view for contradictions
- `review/stale.md`: generated view for stale-note reverification work
- `review/orphans.md`: generated view for isolated note cleanup
- `review/search-misses.md`: generated view for repeated or explicitly reported search misses
- `review/low-utility.md`: generated view for low-utility and suspect signals
- `PREAMBLE.md`: agent-facing retrieval guidance installed by `kb init --hooks`

## Core Commands

- `kb init`: create the KB directory structure and default config
- `kb add`: create a note directly, or queue raw input when metadata is incomplete
- `kb ingest`: process markdown, text, PDF, or local HTML files into notes or review items, with lock/resume recovery
- `kb reindex`: rebuild markdown indexes, backlinks, manifest, stats, lexical index, and optionally scan compaction clusters
- `kb doctor`: inspect config, notes, review state, generated indexes, retrieval state, ingest recovery, errors, and provider routes
- `kb compact`: draft a review-gated canonical note proposal for a topic or cluster
- `kb search`: search notes by title, summary, tags, retrieval phrases, and body text
- `kb get`: inspect one note by ID
- `kb topics`: list topics and render nested hierarchy with `--tree`
- `kb topic`: rename/promote topics directly and create review-gated split/merge proposals
- `kb review`: inspect and resolve bounded review queues backed by `review/review-items.json`
- `kb context`: retrieve compact cited context for a task
- `kb explore`: retrieve broader adjacent ideas and alternatives
- `kb log-use`: record that an agent used or cited a note
- `kb flag-suspect`: record correction/suspect feedback and upsert hygiene review items
- `kb usage`: summarize retrieval, note-use, and search-miss signals

## Provider Notes

The default operation routes use Anthropic. That means `kb ingest`, `kb context`, `kb explore`, and `kb compact` need `ANTHROPIC_API_KEY` unless you reconfigure providers in `.kb/config.yaml`.

The generated config includes an opt-in `providers.codex` section for OpenAI Responses API compatible cloud routes. To use it, set operation routes to `provider: codex`, choose Codex model IDs, set `OPENAI_API_KEY` or the configured credential environment variable, and run `kb doctor` to verify the route configuration.

The generated config also includes an opt-in `providers.local` section for Ollama. To run provider-backed operations locally, set the operation routes to `provider: local`, choose an installed Ollama model, start Ollama, and run `kb doctor` to verify reachability.

Provider retries are configurable under `providers.retry` (`max_attempts`, `base_delay_seconds`, `max_delay_seconds`, `jitter_seconds`) and apply to provider-backed operations. Explicit provider policy is configured under `providers.policy`: `default_provider` supplies a provider when an operation omits one, and `fallback` lists bounded per-operation fallback routes that run only after transient failures exhaust retries.

Privacy controls are configured under `privacy`. Set `cloud_llm_allowed: false` to block Anthropic/Codex provider attempts, use `blocked_topics` to block cloud calls for selected note topics when topic context is available, and use `redact_patterns` to redact provider-bound payload text without mutating stored KB artifacts.

For offline development or tests, the codebase also supports a deterministic `mock` provider, but it is not the default config written by `kb init`.

## Documentation

- [User Guide](docs/user-guide.md)
- [Codex KB Demo Guide](docs/codex-kb-demo.md)
- [Configuration Reference](docs/configuration.md)
- [Command Reference](docs/commands.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Canonical Design Spec](docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md)

## Development Notes

The root README is the end-user entry point. The files under `docs/superpowers/` are design and planning artifacts for implementation work.

Run deterministic corpus regression checks:

```bash
pytest -q tests/test_golden_corpus.py
```

Optional live-provider smoke run (explicit opt-in):

```bash
KB_GOLDEN_LIVE=1 ANTHROPIC_API_KEY=... pytest -q tests/test_golden_corpus.py::test_golden_corpus_live_provider_opt_in_smoke
```
