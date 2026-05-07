# Command Reference

## `kb init`

Initialize a local KB data directory without overwriting existing content.

```bash
kb init [--data-dir <path>] [--hooks]
```

`--hooks` creates or refreshes the tool-managed `PREAMBLE.md` and prints instructions for including that file in agent session instructions. It does not edit external agent configuration files.

## `kb add`

Create a note directly when `--topic` and `--type` are present. Otherwise queue raw input.

```bash
kb add [--data-dir <path>] [--topic <topic>] [--type <knowledge-type>] [--from-file <path>]
```

Notes:

- Reads stdin when `--from-file` is omitted.
- Valid knowledge types include `fact`, `technique`, `heuristic`, `pattern`, `anti-pattern`, `decision`, and `open-question`.

## `kb reindex`

Rebuild markdown and lexical indexes from note files. Use `--scan-clusters` to also detect overlapping note clusters and refresh stale/orphan/low-utility hygiene queues.

```bash
kb reindex [--data-dir <path>] [--scan-clusters]
```

Large top-level and topic indexes are paginated deterministically according to `indexes.top_level_page_size` and `indexes.topic_page_size`. Pagination affects only the published markdown inspection surfaces; retrieval commands use generated local indexes and note metadata.

## `kb doctor`

Run read-only diagnostics for the KB artifact and local generated state.

```bash
kb doctor [--data-dir <path>] [--self-test]
```

`kb doctor` groups findings by subsystem and prints `ok`, `warn`, or `error` severities. It returns nonzero when any `error` finding is present.

Checks include required layout, config validity, note schema validity, duplicate IDs, broken note ID references, review state readability, ingest lock/checkpoint recovery state, markdown index freshness, backlinks, manifest freshness, lexical index freshness, raw ingest errors, and provider route presence.

`kb doctor --self-test` creates a temporary KB, configures the deterministic mock provider, ingests one tiny fixture, rebuilds indexes, searches it, and runs doctor against the fixture. It is offline and does not mutate your configured KB.

## `kb search`

Search note titles, summaries, tags, retrieval phrases, and bodies.

```bash
kb search <query> [--data-dir <path>] [--topic <topic>] [--type <knowledge-type>] [--budget <tokens>] [--json] [--with-citations] [--report-miss]
```

Use `--with-citations` to include a citation block in human-readable output. JSON output includes a `citation` object on each result.

Searches are logged to `.kb/usage.log` without note bodies. Empty or weak searches are logged to `.kb/search-misses.log`; repeated misses become `searchmiss` review items. Use `--report-miss` when results were present but not useful.

## `kb explore`

Retrieve broader associations, adjacent patterns, tradeoffs, analogies, anti-patterns, and open questions for ideation.

```bash
kb explore <problem> [--budget <tokens>] [--json] [--with-citations] [--report-miss] [--data-dir <path>]
```

`kb explore` uses `retrieval.explore_budget_tokens` when `--budget` is omitted. Human-readable and JSON output include citation metadata by default.

## `kb get`

Inspect one note by ID.

```bash
kb get <id> [--data-dir <path>] [--summary]
```

## `kb topics`

List indexed topics and note counts.

```bash
kb topics [--tree] [--data-dir <path>]
```

Use `--tree` to render nested slash-delimited topics as a hierarchy. When `review/review-items.json` is available, `kb topics` and `kb topics --tree` include stale/orphan/review counts alongside note counts.

## `kb ingest`

Ingest one markdown, text, PDF, or local HTML file, or process pending files in `raw/`.

```bash
kb ingest [<file>] [--data-dir <path>] [--force] [--resume] [--quiet] [--json]
```

Current shipped supported input formats:

- `.md`
- `.txt`
- `.pdf`
- `.html`
- `.htm`

PDF parsing extracts text with `pypdf` and does not perform OCR. Local HTML parsing extracts visible text, headings, title, links, and source URL hints where present; it never fetches remote pages.

Human-readable and JSON reports include outcome counts, created/appended note IDs, review item IDs for queued work, parser failures, archived raw paths, warnings, and errors. `--quiet` suppresses the success report and only prints errors.

`kb ingest` writes `.kb/ingest.lock` while it is mutating raw files, notes, review state, or indexes. A second ingest exits without processing. If an earlier ingest was interrupted, run `kb ingest --resume`; use `--force` only when you intend to discard a stale lock/checkpoint and start over.

Provider-backed ingest phases use configurable retry/backoff (`providers.retry`) for transient failures and log retry/final-stop diagnostics to `.kb/errors.log`.

## `kb review`

Inspect and resolve review items backed by durable review state.

```bash
kb review [list] [--data-dir <path>]
kb review explain <item-id> [--data-dir <path>]
kb review accept <item-id> [--topic <topic> --type <knowledge-type> | --note-id <id>] [--append-body] [--resolution-note <text>] [--force] [--data-dir <path>]
kb review reject <item-id> [--data-dir <path>]
kb review defer <item-id> --days <n> [--data-dir <path>]
```

Notes:

- `kb review` and `kb review list` are equivalent. They read pending items from `review/review-items.json`, create that file by importing Phase 1 markdown queues when needed, and rerender markdown views under `review/`.
- Deferred items are hidden from default `review/list` output until due.
- `kb review explain` prints queue, status, priority, payload, target-note paths, and history for one item.
- `kb review accept` supports:
  - classification: create a note (`--topic` + `--type`) or append source to an existing note (`--note-id`)
  - merge: append candidate body to target note(s) only with explicit `--append-body`
  - dispute: acknowledge dispute on target note(s)
  - searchmiss: record a resolution note (`--resolution-note`)
  - compaction: apply a pending compaction proposal, create the canonical note, supersede/archive source notes according to the proposal, and reindex
  - topic: apply a pending topic split or merge proposal and reindex
- `kb review accept --force` bypasses the clean-git-worktree guard for compaction and topic mutations.
- Duplicate, parser-failure, and unsupported-file queue items should be handled with `reject` or `defer`.

Rendered review views include:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/pending-compaction.md`
- `review/pending-topic.md`
- `review/parser-failures.md`
- `review/disputes.md`
- `review/search-misses.md`
- `review/stale.md`
- `review/orphans.md`
- `review/low-utility.md`

## `kb compact`

Draft a canonical compaction proposal for a topic, compaction review item ID, or cluster ID.

```bash
kb compact <topic-or-cluster> [--json] [--data-dir <path>]
```

`kb compact` asks the configured `operations.compact` provider to draft frontmatter, body markdown, source-note dispositions, and a diff summary. The draft is stored as a pending `compaction` review item in `review/review-items.json` and rendered in `review/pending-compaction.md`; source notes are not rewritten.

Accepted compaction proposals are applied with `kb review accept <item-id>`. Acceptance requires a clean git worktree when the KB is inside git unless `--force` is supplied.

## `kb topic`

Reorganize topics directly, or create review-gated topic split and merge proposals.

```bash
kb topic rename <old> <new> [--force] [--data-dir <path>]
kb topic promote <topic...> --under <parent> [--force] [--data-dir <path>]
kb topic split <topic> --into <new...> [--data-dir <path>]
kb topic merge <a> <b> --as <name> [--data-dir <path>]
```

`rename` and `promote` move note files, update note frontmatter topics, clean generated topic artifacts, and rebuild indexes. They require a clean git worktree when the KB is inside git unless `--force` is supplied.

`split` and `merge` create pending `topic` review items rendered in `review/pending-topic.md`. Apply them with `kb review accept <item-id>`.

## `kb context`

Retrieve high-precision context for a task and return a compact cited response.

```bash
kb context <task> [--mode <mode>] [--budget <tokens>] [--json] [--with-citations] [--report-miss] [--data-dir <path>]
```

Supported modes:

- `coding`
- `architecture`
- `debugging`
- `writing`
- `research`
- `review`

Human-readable and JSON output include citation metadata by default.

`kb context` uses configurable provider retry/backoff for synthesis failures. Retry attempts and final-stop reasons are appended to `.kb/errors.log`.

Context and exploration retrievals are logged to `.kb/usage.log` without note bodies. Empty retrievals are logged as search misses, and `--report-miss` records an explicit poor-result signal.

## `kb log-use`

Record that an agent actually used or cited a note.

```bash
kb log-use <id> [--task <task>] [--data-dir <path>]
```

The note ID is validated before the append-only event is written to `.kb/usage.log`.

## `kb flag-suspect`

Record that a note appears incorrect, stale, or otherwise not useful.

```bash
kb flag-suspect <id> "<reason>" [--data-dir <path>]
```

`kb flag-suspect` appends a structured `suspect-flag` event to `.kb/usage.log`, then upserts either:

- a `low_utility` review item in `review/low-utility.md`, or
- a `dispute` review item in `review/disputes.md` when the reason looks contradiction-oriented.

## `kb usage`

Summarize retrieval and explicit note-use signals.

```bash
kb usage [--since <duration>] [--note <id>] [--data-dir <path>]
```

`--since` accepts durations such as `30m`, `24h`, `7d`, `2w`, or an ISO date. `--note` filters the summary to one validated note ID.

## Current Command Surface

The current shipped CLI includes the full Phase 1-3 command surface plus Phase 4a-4c robustness behavior on existing commands (ingest lock/resume, provider retry/backoff, golden corpus checks, and PDF/HTML ingest). Later Phase 4 additions (hook templates and auto-commit policy) remain planned work.

## Golden Corpus Regression Harness

Deterministic golden corpus checks are shipped as tests using the mock provider:

```bash
pytest -q tests/test_golden_corpus.py
```

Optional live-provider smoke coverage is gated behind explicit opt-in:

```bash
KB_GOLDEN_LIVE=1 ANTHROPIC_API_KEY=... pytest -q tests/test_golden_corpus.py::test_golden_corpus_live_provider_opt_in_smoke
```
