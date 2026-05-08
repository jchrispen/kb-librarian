# `/kb`

Use for KB Librarian CLI usage, retrieval, and task-oriented KB workflow help.

Read first:

- `docs/command-reference.md`
- `docs/user-guide.md`
- `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`

Procedure:

1. Confirm whether the goal is retrieval usage, ingest/setup help, debugging a `kb` command, or validating expected CLI behavior.
2. Prefer the documented CLI contract in `docs/command-reference.md` and the canonical behavior in the design spec over guesswork.
3. For agent-task retrieval, prefer `kb context` for task help, `kb explore` for ideation, `kb search` for precise lookup, and `kb get` only after a note is clearly relevant.
4. When troubleshooting, inspect the smallest relevant docs, config, and command surfaces before changing code.
5. If the user asks for behavior changes, keep work scoped to the requested `kb` surface and update user-facing docs in the same change when behavior changes.

Report:

- Commands or docs consulted.
- Observed behavior or requested change.
- Any verification performed or follow-up gaps.
