Status: Complete
Date: 2026-05-07

# Repo Command Specs

## Goal

Add lightweight repo-native command specs for recurring workflows without expanding `AGENTS.md` into a second design spec.

## Scope

- Keep `AGENTS.md` focused on dispatch rules and precedence.
- Add concise command specs under `docs/commands/` for `/release`, `/milestone-ship`, and `/coverage`.
- Prefer file references over duplicated guidance so agents can load only the command they need.

## Out Of Scope

- Host-level slash-command registration.
- Changes to product runtime behavior.

## Completion Record

- Implemented in `AGENTS.md`, `docs/commands/release/SKILL.md`, `docs/commands/milestone-ship/SKILL.md`, `docs/commands/coverage/SKILL.md`, `docs/command-reference.md`, `README.md`, and linked historical plan references.
- Verification: confirmed `AGENTS.md` routes repo-local slash-style requests to `docs/commands/`, confirmed the new command spec files exist, and confirmed no remaining references point at `docs/commands.md`.
