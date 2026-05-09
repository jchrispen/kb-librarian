# Configuration Reference

## Config File Location

KB Librarian stores its config at:

```text
~/.kb/config.yaml
```

The default config is created by `kb init` and points at the default library in `~/.kb/.library`.
When you pass `--data-dir` or set `KB_DATA_DIR`, that explicit library uses its own config at `<data-dir>/.kb/config.yaml`.

## Data Directory Resolution

The CLI resolves the KB data directory in this order:

1. `--data-dir`
2. `KB_DATA_DIR`
3. configured `data_dir` from `~/.kb/config.yaml`
4. `~/.kb/.library`

## Default Config Shape

The generated config includes these sections:

- `data_dir`
- `providers`
- `operations`
- `retrieval`
- `ingest`
- `indexes`
- `review`
- `git`
- `privacy`
- `hooks`

## Example

```yaml
data_dir: ~/.kb/.library

providers:
  anthropic:
    backend: direct_http
    credential_source: api_key_env
    api_key_env: ANTHROPIC_API_KEY
  codex:
    backend: direct_http
    credential_source: api_key_env
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    timeout_seconds: 120
  local:
    backend: ollama
    base_url: http://127.0.0.1:11434
    timeout_seconds: 120
  retry:
    max_attempts: 3
    base_delay_seconds: 0.25
    max_delay_seconds: 2.0
    jitter_seconds: 0.1
  policy:
    default_provider: anthropic
    fallback: {}

operations:
  extract:    { provider: anthropic, model: claude-sonnet-4-6 }
  compact:    { provider: anthropic, model: claude-sonnet-4-6 }
  classify:   { provider: anthropic, model: claude-haiku-4-5 }
  integrate:  { provider: anthropic, model: claude-haiku-4-5 }
  synthesize: { provider: anthropic, model: claude-haiku-4-5 }

retrieval:
  default_budget_tokens: 1500
  context_budget_tokens: 1800
  explore_budget_tokens: 3000
  index_token_cap: 5000
  lexical_index: true
  embeddings: false
  embedding_index_path: .kb/embeddings.sqlite
  embedding_provider: null
  embedding_model: null
  embedding_dimensions: null
  title_weight: 5
  summary_weight: 4
  retrieval_phrase_weight: 4
  tag_weight: 3
  body_weight: 1

ingest:
  max_notes_per_doc: 7
  prefer_skip_over_low_value_note: true
  low_confidence_goes_to_review: true

indexes:
  topic_sort: alphabetical
  top_level_min_notes: 1
  topic_page_size: 50
  top_level_page_size: 100

review:
  stale_after_days: 180
  orphan_after_days: 30
  duplicate_cluster_threshold: 4
  compaction_cooldown_days: 30
  max_review_items_per_run: 10

git:
  auto_commit: false
  commit_ingests: true
  commit_reviews: true
  commit_reindexes: false
  commit_topic_reorganizations: true
  allow_unrelated_changes: false
  require_clean_worktree_for_rewrites: true

privacy:
  cloud_llm_allowed: true
  blocked_topics: []
  redact_patterns: []
  require_confirmation_for_cloud_llm: false

hooks:
  session_start_ingest: false
```

## Provider Configuration

The generated config routes operations through Anthropic by default. The default cloud seam is `backend: direct_http` with `credential_source: api_key_env`, and the environment variable named by `providers.anthropic.api_key_env` must be set for those operations to work.

Example:

```bash
export ANTHROPIC_API_KEY=your_key_here
```

If the key is missing, provider-backed commands fail with an explicit error.

Anthropic routes also support opt-in Claude Code CLI delegation when you want subscription-backed access instead of `ANTHROPIC_API_KEY`:

```yaml
providers:
  anthropic:
    backend: vendor_cli
    credential_source: vendor_cli
    cli_command: claude
    timeout_seconds: 120
```

Before using that route, sign in locally:

```bash
claude auth login
claude auth status --json
```

For scriptable environments, you can delegate through a Claude Code OAuth token instead:

```yaml
providers:
  anthropic:
    backend: vendor_cli
    credential_source: token_env
    token_env: CLAUDE_CODE_OAUTH_TOKEN
    cli_command: claude
    timeout_seconds: 120
```

Generate the token with `claude setup-token`, export `CLAUDE_CODE_OAUTH_TOKEN`, and run `kb doctor` to verify the active backend and auth-source findings. KB Librarian does not parse Claude Code credential files or keychains directly.

The generated config also includes an opt-in Codex-compatible provider section for cloud-backed routes through an OpenAI Responses API compatible endpoint:

```yaml
providers:
  codex:
    backend: direct_http
    credential_source: api_key_env
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    timeout_seconds: 120
```

To route provider-backed operations to Codex, set operation routes to `provider: codex` and choose the model IDs you want to use:

```yaml
operations:
  extract:    { provider: codex, model: gpt-5.1-codex }
  compact:    { provider: codex, model: gpt-5.1-codex }
  classify:   { provider: codex, model: gpt-5.1-codex }
  integrate:  { provider: codex, model: gpt-5.1-codex }
  synthesize: { provider: codex, model: gpt-5.1-codex }
```

Set the configured API key environment variable before running Codex-routed commands:

```bash
export OPENAI_API_KEY=your_key_here
```

`providers.codex.base_url` may point at another Responses API compatible endpoint. `kb doctor` reports the active backend and credential source, and warns when Codex routes are configured but the credential environment variable is unset.

If you prefer ChatGPT account auth instead of `OPENAI_API_KEY`, switch Codex to vendor CLI delegation:

```yaml
providers:
  codex:
    backend: vendor_cli
    credential_source: vendor_cli
    cli_command: codex
    base_url: https://api.openai.com/v1
    timeout_seconds: 120
```

Before using that route, sign in locally:

```bash
codex login
codex login status
```

For headless environments, use the supported device flow instead:

```bash
codex login --device-auth
codex login status
```

Codex vendor CLI delegation always uses Codex's built-in ChatGPT/OpenAI account path. Keep `providers.codex.base_url` at `https://api.openai.com/v1` for `backend: vendor_cli`; alternate Responses API compatible endpoints remain available only through `backend: direct_http` with API-key auth. KB Librarian does not parse Codex credential files or forward ChatGPT account auth to arbitrary OpenAI-compatible endpoints.

The generated config also includes an opt-in local provider section. Ollama remains the default:

```yaml
providers:
  local:
    backend: ollama
    base_url: http://127.0.0.1:11434
    timeout_seconds: 120
```

Local provider routes are selected only by editing operation routes. For example:

```yaml
operations:
  extract:    { provider: local, model: llama3.2 }
  compact:    { provider: local, model: llama3.2 }
  classify:   { provider: local, model: llama3.2 }
  integrate:  { provider: local, model: llama3.2 }
  synthesize: { provider: local, model: llama3.2 }
```

To switch to `vllm`, set the backend explicitly and point `base_url` at the running server root or `/v1` base:

```yaml
providers:
  local:
    backend: vllm
    base_url: http://127.0.0.1:8000
    timeout_seconds: 120
```

To switch to `lm_studio`, set the backend and point `base_url` at the LM Studio local server:

```yaml
providers:
  local:
    backend: lm_studio
    base_url: http://127.0.0.1:1234
    timeout_seconds: 120
```

When all provider-backed operation routes use `provider: local`, cloud credentials are not required. Start the selected backend and make the configured model available before running provider-backed commands. For Ollama, pull the model first:

```bash
ollama pull llama3.2
```

`kb doctor` checks local-provider reachability and reports missing routed models when local routes are configured. For Ollama it suggests `ollama pull`; for vLLM it expects the routed model to be served by the running instance; for LM Studio it expects the routed model to be loaded in the local server.

Cloud providers also accept explicit seam fields:

- `backend`: `direct_http`, or `vendor_cli` for Anthropic and Codex
- `credential_source`: `api_key_env`, `vendor_cli`, Anthropic `token_env`, or future `command`
- `cli_command`: optional executable name or path for vendor-CLI delegation; defaults to `claude` for Anthropic and `codex` for Codex
- `timeout_seconds`: optional provider timeout override for Anthropic and Codex routes

Existing API-key configs remain valid when these fields are absent. Anthropic and Codex `vendor_cli` delegation are implemented in this release. Direct-http `credential_source: command` and unsupported auth/endpoint combinations still fail explicitly instead of silently falling back to API-key behavior.

`providers.retry` controls bounded retry/backoff behavior for provider-backed operations:

- `max_attempts`: maximum total attempts per provider call (including the first attempt)
- `base_delay_seconds`: initial backoff delay before retry 2
- `max_delay_seconds`: upper bound for exponential backoff delay
- `jitter_seconds`: random jitter added to each retry delay

Transient failures (for example timeouts, rate limits, and provider 5xx responses) are retried. Non-transient schema/validation failures stop without retry.

`providers.policy` controls deterministic provider selection and explicit fallback:

- `default_provider`: provider used when an operation omits `provider`
- `fallback`: per-operation list of fallback providers to try after the selected provider exhausts retry attempts for a transient failure

Fallback entries can be provider names, which reuse the operation model, or mappings with an explicit model:

```yaml
providers:
  policy:
    default_provider: anthropic
    fallback:
      synthesize:
        - provider: local
          model: llama3.2
      extract:
        - provider: codex
          model: gpt-5.1-codex
```

Fallback never runs for configuration errors, missing credentials, provider response schema errors, or other non-transient failures. Provider attempts are bounded by the configured fallback list and each attempted provider uses `providers.retry` independently. `kb doctor` validates fallback references and checks configured fallback providers such as Codex credentials or local backend reachability.

## Privacy Configuration

`privacy.cloud_llm_allowed` controls whether cloud providers (`anthropic` and `codex`) may be attempted by provider-backed commands. When it is `false`, cloud provider attempts fail before provider construction; route operations to `provider: local` for fully local provider-backed flows.

`privacy.blocked_topics` blocks cloud provider attempts when an operation has note-topic context matching a listed topic. This applies to context/explore synthesis and compaction proposal drafting. Ingest has no reliable final topic before provider classification, so use local routes when ingesting sensitive raw material.

`privacy.redact_patterns` is a list of Python regular expressions applied to provider-bound text and structured payload strings before LLM calls. Redaction does not mutate raw files, canonical notes, review state, or generated indexes.

`privacy.require_confirmation_for_cloud_llm` is validated as a future seam. The current CLI does not implement interactive confirmation prompts.

## Git Automation

Auto-commit is off by default:

```yaml
git:
  auto_commit: false
```

When `git.auto_commit` is set to `true`, successful note/review operations may create commits after the command completes. The scoped policy keys are:

- `commit_ingests`: commit successful `kb ingest` and `kb add` note/raw-input changes.
- `commit_reviews`: commit `kb review accept` changes and `kb compact` proposals.
- `commit_topic_reorganizations`: commit `kb topic` rename/promote/split/merge changes.
- `commit_reindexes`: commit index-only `kb reindex` changes. This stays `false` by default to avoid noisy generated-artifact commits.
- `allow_unrelated_changes`: permit auto-commit when the git worktree already had unrelated changes. This stays `false` by default.

If auto-commit is enabled but skipped, the CLI prints the reason and appends an automation diagnostic to `.kb/errors.log`.

## Offline Mock Provider

The codebase supports a deterministic `mock` provider for tests and offline smoke checks, but `kb init` does not configure it by default.

That means changing to the mock provider is a manual config edit for developers, not normal end-user setup.

## Retrieval Settings

Useful retrieval settings:

- `context_budget_tokens`: default token budget for `kb context`
- `explore_budget_tokens`: default token budget for `kb explore`
- `lexical_index`: must remain `true`; lexical retrieval is the active retrieval source
- `embeddings`: future seam only; default `false` and not supported as an active retrieval source yet
- `embedding_index_path`: future generated embedding index path, relative to the data directory
- `embedding_provider`, `embedding_model`, `embedding_dimensions`: validated future embedding settings
- `title_weight`: title match weight in lexical ranking
- `summary_weight`: summary match weight
- `retrieval_phrase_weight`: retrieval phrase match weight
- `tag_weight`: tag match weight
- `body_weight`: body text match weight

`kb doctor` reports the embedding seam status. If `retrieval.embeddings` is set to `true`, doctor warns that embedding retrieval is deferred and lexical retrieval remains active.

Retrieval commands also write append-only usage signals to `.kb/usage.log`. Empty, weak, or explicitly reported poor searches write `.kb/search-misses.log`; suspect-note feedback from `kb flag-suspect` is also appended to `.kb/usage.log`. Aggregate counters in `.kb/stats.json` are rebuildable from those logs.

## Index Settings

Useful index settings:

- `topic_sort`: ordering policy for generated topic listings
- `top_level_min_notes`: configured threshold reserved for top-level index summaries
- `topic_page_size`: note rows per topic index page before `INDEX-2.md`, `INDEX-3.md`, and later pages are generated
- `top_level_page_size`: topic rows per top-level index page before root `INDEX-2.md`, `INDEX-3.md`, and later pages are generated

Paginated markdown indexes are human inspection surfaces. `kb search`, `kb context`, and `kb explore` use the local lexical index and note metadata, not paginated markdown files.

## Ingest Settings

Useful ingest settings:

- `max_notes_per_doc`: maximum candidate notes extracted from one document
- `prefer_skip_over_low_value_note`: prefer no note over weak notes
- `low_confidence_goes_to_review`: route ambiguous classification to review

## Review Settings

Useful review settings:

- `stale_after_days`: baseline age window before stale-note flags are considered
- `orphan_after_days`: age window used when checking isolated notes with no links or recent usage
- `duplicate_cluster_threshold`: minimum overlapping notes required before `kb reindex --scan-clusters` queues a compaction cluster
- `compaction_cooldown_days`: days before a rejected compaction cluster can be reopened by another scan
- `max_review_items_per_run`: maximum items shown by default in `kb review`

## Hooks Flag

For newly created KBs, `kb init --hooks` sets:

```yaml
hooks:
  session_start_ingest: true
```

It also creates or refreshes the tool-managed `PREAMBLE.md`, generates optional templates under `.kb/hooks/`, and prints instructions for manual installation. It does not install external hooks, edit external agent configuration files, or enable automation.
