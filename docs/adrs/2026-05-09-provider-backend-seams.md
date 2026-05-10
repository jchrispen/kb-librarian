---
status: accepted
date: 2026-05-09
spec: ../specs/kb-librarian.md
---

# Provider Backend Seams: Explicit Bounded Fallback Chains

## Context

kb-librarian needs to call LLM providers for note generation, ingest enrichment, and compaction. The set of viable providers (direct API, local Ollama/vLLM/LM Studio, vendor CLI delegation) was growing, and each has different credential mechanisms. Without an explicit seam, adding a new backend risked breaking existing routes or introducing implicit silent switching.

## Decision

Providers are abstracted behind two orthogonal seams: the credential source (API key, vendor CLI delegation, environment token) and the transport backend (direct HTTP, Ollama, vLLM, LM Studio, Claude Code CLI subprocess, Codex CLI subprocess). These compose independently. Per-operation config overrides compose with a global default-provider policy. Fallback chains are explicit and bounded — every fallback step is named in config, cycles are rejected by validation at load time, and silent implicit switching is forbidden.

## Alternatives considered

- **Dynamic runtime provider scoring** — rejected: non-deterministic; makes debugging impossible when a request goes to an unexpected provider.
- **Cost-based automatic optimization** — rejected: requires live cost information and introduces surprise billing; out of scope for a personal tool.
- **Hidden implicit fallback** — rejected: when a provider fails, the user must know which provider was tried and why it failed; silent fallback makes this impossible.
- **Single-provider hardcoding** — rejected: the personal tool needs to work across different machine setups (API key available vs. only CLI auth vs. local model).

## Consequences

- Provider selection must be deterministic and explainable from config alone.
- Error output must include the attempted provider sequence and the stop reason.
- Existing API-key routes cannot regress when new backend or auth modes are added.
- `kb doctor` must validate provider config (no cycles, all named backends resolvable) at startup.
