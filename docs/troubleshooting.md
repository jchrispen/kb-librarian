# Troubleshooting

## `kb ingest` or `kb context` fails with an API key error

The default config uses Anthropic provider routes.

Set the configured environment variable before running provider-backed commands:

```bash
export ANTHROPIC_API_KEY=your_key_here
```

If you want local LLM behavior without cloud credentials, route provider-backed operations to the configured local provider:

```yaml
operations:
  extract:    { provider: local, model: llama3.2 }
  compact:    { provider: local, model: llama3.2 }
  classify:   { provider: local, model: llama3.2 }
  integrate:  { provider: local, model: llama3.2 }
  synthesize: { provider: local, model: llama3.2 }
```

Then start the selected backend and make the routed model available. For Ollama:

```bash
ollama pull llama3.2
```

For deterministic offline development or tests, reconfigure the provider manually to use the codebase's `mock` provider.

## Provider fallback does not run

Fallback is explicit and conservative. It runs only when `providers.policy.fallback.<operation>` is configured and the selected provider exhausts retries for a transient failure such as a timeout, rate limit, transport error, or provider 5xx response.

Fallback intentionally does not run for missing API keys, invalid provider names, malformed provider responses, validation errors, or other non-transient failures. Inspect `.kb/errors.log` for `provider-fallback` entries and run `kb doctor --data-dir <path>` to verify primary and fallback route configuration.

## Cloud provider blocked by privacy policy

When `privacy.cloud_llm_allowed` is `false`, Anthropic and Codex provider attempts fail before provider construction. Route provider-backed operations to `provider: local`, or set `privacy.cloud_llm_allowed: true` when cloud calls are acceptable.

For sensitive topics, `privacy.blocked_topics` also blocks cloud calls for operations with note-topic context, such as `kb context`, `kb explore`, and `kb compact`. Ingest does not know final note topics before provider classification, so use local routes when ingesting sensitive raw files.

Use `privacy.redact_patterns` for known sensitive string patterns. Redaction affects provider-bound payloads only; it does not edit raw files, notes, review items, or indexes.

## Codex provider routes fail

Codex-compatible routes use `providers.codex.api_key_env` and `providers.codex.base_url`.

Check these in order:

1. Confirm the configured environment variable is set, usually `OPENAI_API_KEY`
2. Confirm every Codex-routed operation uses an available model ID
3. Run `kb doctor --data-dir <path>` and inspect the Providers section
4. Increase `providers.codex.timeout_seconds` if requests time out
5. Check `providers.codex.base_url` if you use a non-default Responses API compatible endpoint
6. Switch operation routes to Anthropic or local if Codex access is unavailable

## Local provider routes fail

The local provider supports `ollama`, `vllm`, and `lm_studio` through `providers.local.backend` and `providers.local.base_url`.

Check these in order:

1. Confirm the selected backend is running at the configured base URL
2. For Ollama, run `ollama pull <model>` for every routed model
3. For vLLM, confirm the running server is serving the routed model and exposes an OpenAI-compatible `/v1` API
4. For LM Studio, confirm the local server is enabled and the routed model is loaded
5. Run `kb doctor --data-dir <path>` and inspect the Providers section
6. Increase `providers.local.timeout_seconds` if the model is slow to respond
7. Switch operation routes back to Anthropic if local generation is unavailable

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

## `kb ingest` reports an active or stale lock

`kb ingest` creates `.kb/ingest.lock` while it mutates KB files. If another ingest is genuinely running, wait for it to finish. If the prior process was interrupted, run:

```bash
kb ingest --resume --data-dir <path>
```

Use `kb doctor --data-dir <path>` to inspect the lock and `.kb/state.json` checkpoint. Use `kb ingest --force` only when you intend to discard stale recovery state and start over.

## Auto-commit did not create a commit

Auto-commit is disabled unless `git.auto_commit: true` is set in `.kb/config.yaml`. The operation-specific scope must also be enabled, such as `git.commit_ingests` for ingest or `git.commit_reviews` for review acceptance and compaction proposals.

If auto-commit is enabled but skipped, the CLI prints the reason. Common causes:

- the KB is not inside a git worktree
- the worktree had pre-existing unrelated changes
- the operation produced no new changes
- `git.commit_reindexes` is false for an index-only reindex

Run `kb doctor --data-dir <path>` to check automation configuration, parser dependencies, and recovery state.

## Review items are piling up

Use `kb review` to see bounded pending counts and stable item IDs:

```bash
kb review --data-dir <path>
```

Then resolve items directly:

```bash
kb review explain <item-id> --data-dir <path>
kb review accept <item-id> ... --data-dir <path>
kb review reject <item-id> --data-dir <path>
kb review defer <item-id> --days 30 --data-dir <path>
```

Useful queue-specific reminders:

- classification accepts need either `--topic` + `--type`, or `--note-id`
- merge accepts require `--append-body`
- search-miss accepts require `--resolution-note`
- duplicate and unsupported-file items should be rejected or deferred

The durable review source of truth is:

- `review/review-items.json`

You can inspect these generated markdown views directly:

- `review/pending-classification.md`
- `review/pending-merge.md`
- `review/disputes.md`
- `review/search-misses.md`

If `review/review-items.json` is missing in an older KB, `kb review` imports existing Phase 1 queue entries and rerenders the markdown views.

## I edited note files manually and results look stale

Run:

```bash
kb reindex --data-dir <path>
```

That rebuilds generated indexes and metadata from the canonical note files.

To inspect stale or broken generated state without mutating the KB, run:

```bash
kb doctor --data-dir <path>
```

Doctor returns nonzero for errors such as invalid notes, duplicate note IDs, broken note references, unreadable review state, or stale lexical index contents.

## I am not sure which data directory the CLI is using

The resolution order is:

1. `--data-dir`
2. `KB_DATA_DIR`
3. configured `data_dir` from `~/.kb/config.yaml`
4. `~/.kb/.library`

Use `--data-dir` explicitly if you want to remove ambiguity.
