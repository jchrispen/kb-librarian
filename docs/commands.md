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
kb search <query> [--data-dir <path>] [--topic <topic>] [--type <knowledge-type>] [--budget <tokens>] [--json]
```

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

Show bounded review counts and queue excerpts backed by durable review state.

```bash
kb review [list] [--data-dir <path>]
```

`kb review` and `kb review list` are equivalent. They read pending items from `review/review-items.json`, create that file by importing Phase 1 markdown queues when needed, and rerender markdown views under `review/`.

Rendered review views include:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/disputes.md`
- `review/search-misses.md`

Current shipped behavior is summary output only; accepting, rejecting, deferring, and explaining review items are still planned commands.

## `kb context`

Retrieve high-precision context for a task and return a compact cited response.

```bash
kb context <task> [--mode <mode>] [--budget <tokens>] [--json] [--data-dir <path>]
```

Supported modes:

- `coding`
- `architecture`
- `debugging`
- `writing`
- `research`
- `review`

## Current Command Surface

The current shipped CLI does not yet include these planned commands:

- `kb explore`
- `kb usage`
- `kb log-use`
- `kb doctor`
- actionable `kb review accept|reject|defer|explain`
- topic reorganization commands
