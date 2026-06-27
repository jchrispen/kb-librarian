# CLI Delegation Pitfalls

**Spec:** ../../specs/kb-librarian.md
**Last updated:** 2026-05-09

## What

Auth via vendor CLI delegation (`claude -p`, `codex exec`) can fail silently in ways that look like provider errors but are actually auth state issues. Four distinct failure modes must be handled separately.

## Why it matters

Collapsing these into a generic provider error makes it impossible for the user to know what to fix. Each requires a different remediation step.

| Failure mode | Symptom | Remediation |
|---|---|---|
| Missing binary | `FileNotFoundError` or `which` returns nothing | Install the CLI |
| Missing login state | Subprocess exits nonzero with "not logged in" | `claude auth login` or `codex login` |
| Expired login | Subprocess exits nonzero with token/session error | Re-run the login command |
| Transport error | Subprocess exits nonzero with network/timeout message | Retry; check connectivity |

Additional pitfalls:
- `claude auth status` may not exist on all Claude Code versions; fall back to a guarded live probe only on explicit opt-in, never automatically.
- Codex device-auth (`codex login --device-auth`) is only available when the OpenAI account has device-code auth enabled — this cannot be probed without a live network call.
- Subprocess argv and env must both be audited for token values — they can appear in either location and require targeted masking.
- Silent fallback from expired vendor CLI login to API key is forbidden unless the user explicitly configures a fallback chain.

## See also

- [../../adrs/2026-05-09-account-auth-delegation.md](../../adrs/2026-05-09-account-auth-delegation.md)
- [../../adrs/2026-05-09-provider-backend-seams.md](../../adrs/2026-05-09-provider-backend-seams.md)
