# Troubleshooting

## `kb ingest` or `kb context` fails with an API key error

The default config uses Anthropic provider routes.

Set the configured environment variable before running provider-backed commands:

```bash
export ANTHROPIC_API_KEY=your_key_here
```

If you want offline development behavior, reconfigure the provider manually to use the codebase's `mock` provider.

## `kb search` or `kb context` returns nothing useful

Check these in order:

1. Confirm the KB has notes under `topics/`
2. Run `kb reindex --data-dir <path>`
3. Try `kb search` with a more exact phrase
4. Inspect one expected note with `kb get <id>`
5. Check whether the note is archived or disputed

## `kb add` did not create a note

If `--topic` and `--type` were missing, `kb add` queues the input under `raw/` instead of creating a canonical note immediately.

## `kb ingest` processed nothing

Possible causes:

- there were no eligible files in `raw/`
- the file extension was unsupported
- the file could not be decoded as UTF-8 text
- provider-backed extraction failed

Check:

- `raw/`
- `review/`
- `.kb/errors.log`

## Review items are piling up

In the current shipped CLI, `kb review` is summary-only. You can inspect these files directly:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/disputes.md`

Actionable review commands are planned, but not currently shipped.

## I edited note files manually and results look stale

Run:

```bash
kb reindex --data-dir <path>
```

That rebuilds generated indexes and metadata from the canonical note files.

## I am not sure which data directory the CLI is using

The resolution order is:

1. `--data-dir`
2. `KB_DATA_DIR`
3. configured `data_dir`
4. `/mnt/c/workspace/source/internal/kb`

Use `--data-dir` explicitly if you want to remove ambiguity.
