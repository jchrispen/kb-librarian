# Command Reference

## `kb init`

Initialize a local KB data directory without overwriting existing content.

```bash
kb init [--data-dir <path>] [--hooks]
```

## `kb add`

Create a note directly when `--topic` and `--type` are present. Otherwise queue raw input.

```bash
kb add [--data-dir <path>] [--topic <topic>] [--type <knowledge-type>] [--from-file <path>]
```

Notes:

- Reads stdin when `--from-file` is omitted.
- Valid knowledge types include `fact`, `technique`, `heuristic`, `pattern`, `anti-pattern`, `decision`, and `open-question`.

## `kb reindex`

Rebuild markdown and lexical indexes from note files.

```bash
kb reindex [--data-dir <path>]
```

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

## `kb ingest`

Ingest one markdown or text file, or process pending files in `raw/`.

```bash
kb ingest [<file>] [--data-dir <path>] [--force] [--quiet]
```

Current shipped supported input formats:

- `.md`
- `.txt`

## `kb review`

Inspect and resolve review items backed by durable review state.

```bash
kb review [list] [--data-dir <path>]
kb review explain <item-id> [--data-dir <path>]
kb review accept <item-id> [--topic <topic> --type <knowledge-type> | --note-id <id>] [--append-body] [--resolution-note <text>] [--data-dir <path>]
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
- Duplicate and unsupported-file queue items should be handled with `reject` or `defer`.

Rendered review views include:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/disputes.md`
- `review/search-misses.md`

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

Context and exploration retrievals are logged to `.kb/usage.log` without note bodies. Empty retrievals are logged as search misses, and `--report-miss` records an explicit poor-result signal.

## `kb log-use`

Record that an agent actually used or cited a note.

```bash
kb log-use <id> [--task <task>] [--data-dir <path>]
```

The note ID is validated before the append-only event is written to `.kb/usage.log`.

## `kb usage`

Summarize retrieval and explicit note-use signals.

```bash
kb usage [--since <duration>] [--note <id>] [--data-dir <path>]
```

`--since` accepts durations such as `30m`, `24h`, `7d`, `2w`, or an ISO date. `--note` filters the summary to one validated note ID.

## Current Command Surface

The current shipped CLI does not yet include these planned commands:

- `kb doctor`
- topic reorganization commands
