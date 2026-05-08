# `/milestone-ship`

Use for completing one implementation milestone end to end.

Read first:

- The requested milestone plan under `docs/superpowers/plan/`
- `docs/superpowers/specs/2026-05-04-kb-librarian-agent-first-design.md`
- `AGENTS.md`

Procedure:

1. Read only the active milestone plan and the spec sections needed for that milestone.
2. Confirm milestone boundaries, public interfaces, acceptance criteria, and out-of-scope items.
3. Implement the milestone with minimal code changes that preserve prior milestone behavior unless the spec requires otherwise.
4. Update user-facing docs in the same change when user-visible behavior changes.
5. Run the relevant automated tests and any milestone-specific verification commands.
6. Update the milestone plan in place with completion status, changed files, and verification results.
7. Update the parent phase plan so milestone state stays synchronized.
8. Commit only when the user asks for a commit.

Report:

- Files changed.
- Verification performed.
- Any deferred follow-up that remains out of scope.
