# KB Librarian

KB Librarian is a local-first knowledge base CLI for coding agents.

It stores durable knowledge as markdown files in a separate data directory, builds disposable local indexes for retrieval, and gives agents a stable command-line interface for finding task-shaped context.

## Status

KB Librarian ships a full agent workflow: KB setup, ingest, lexical retrieval, hygiene and compaction, hierarchical topics, a concept graph, and multi-provider routing (Anthropic by default, plus opt-in Codex and local backends). See [Core Commands](#core-commands) for the command surface.

Embedding/semantic retrieval, an MCP server wrapper, and the remaining provider backends are deferred. The canonical "Out of scope" and "Open questions" lists live in the [design spec](docs/specs/kb-librarian.md).

## What It Does

- Publishes the KB as markdown files plus frontmatter in a local data directory
- Builds local lexical indexes for note retrieval
- Ingests markdown, text, PDF, and local HTML files into candidate notes
- Preserves review-gated behavior for risky integrations
- Returns compact, cited context for coding and design tasks
- Detects overlapping note clusters and drafts reviewable compaction proposals without rewriting notes
- Applies accepted compaction proposals with source-note supersession and index regeneration
- Surfaces stale, orphan, and low-utility notes with explainable review evidence
- Builds a concept graph from note references and backlinks, with optional interactive HTML visualization
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

Create and activate a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
```

Install from a local checkout:

```bash
python3 -m pip install .
```

Install from a downloaded release wheel:

```bash
python3 -m pip install kb_librarian-0.1.0-py3-none-any.whl
```

Editable install remains available for development:

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
- `kb graph`: build the concept graph from note references and backlinks; `--json` for machine output, `--html PATH` to write an interactive Cytoscape visualizer
- `kb review`: inspect and resolve bounded review queues backed by `review/review-items.json`
- `kb context`: retrieve compact cited context for a task
- `kb explore`: retrieve broader adjacent ideas and alternatives
- `kb log-use`: record that an agent used or cited a note
- `kb flag-suspect`: record correction/suspect feedback and upsert hygiene review items
- `kb usage`: summarize retrieval, note-use, and search-miss signals

## Provider Notes

Provider-backed operations (`kb ingest`, `kb context`, `kb explore`, `kb compact`) route through Anthropic by default and need either `ANTHROPIC_API_KEY` or a vendor-CLI configuration. The default seam is `backend: direct_http` with `credential_source: api_key_env`, so existing API-key configs work unchanged.

Beyond the default, the generated config exposes opt-in routes for:

- **Codex** (`providers.codex`) — OpenAI Responses-compatible cloud routes via `OPENAI_API_KEY` or `codex` vendor-CLI delegation.
- **Local** (`providers.local`) — `ollama` (default), `vllm`, and `lm_studio` over their OpenAI-compatible local APIs.
- **Vendor-CLI delegation** — Anthropic via `claude`, Codex via `codex`, instead of API keys.
- **Retry, policy, and fallback** (`providers.retry`, `providers.policy`) — bounded backoff plus explicit default-provider selection and per-operation fallback chains.
- **Privacy** (`privacy`) — block cloud calls globally or per topic, and redact provider-bound payload text without mutating stored notes.

Configured auth is authoritative: the selected backend/credential source decides which credentials are used, ambient credentials for other modes are ignored, and missing auth fails explicitly rather than silently falling back. `kb doctor` reports the active seam and flags missing CLI installs, login state, or env setup. A deterministic `mock` provider exists for offline development and tests but is not written by `kb init`.

See the [Configuration Reference](docs/configuration.md) for the full provider/auth/privacy config shape.

## Documentation

The repo follows the personal-workflow docs convention. Durable artifacts live under `docs/`; transient plans live in `docs/plans/`.

User-facing:

- [User Guide](docs/userguide.md)
- [Configuration Reference](docs/configuration.md)
- [Command Reference](docs/command-reference.md)
- [Troubleshooting](docs/troubleshooting.md)

Developer-facing (`docs/dev/`):

- [Testing](docs/dev/testing.md) — install, run tests, coverage
- [Release Guide](docs/dev/releasing.md)
- [Codex KB Demo Guide](docs/dev/codex-kb-demo.md)

Design & decisions:

- [Canonical Design Spec](docs/specs/kb-librarian.md)
- [Architecture Decisions](docs/adrs/_index.md) · [Notes](docs/notes/_index.md)

Optional live-provider smoke run (explicit opt-in):

```bash
KB_GOLDEN_LIVE=1 ANTHROPIC_API_KEY=... pytest -q tests/test_golden_corpus.py::test_golden_corpus_live_provider_opt_in_smoke
```
