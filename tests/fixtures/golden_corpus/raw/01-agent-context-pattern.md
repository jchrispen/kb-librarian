# Agent Context Retrieval Pattern

Use this pattern when coding agents need compact context before they act.

Prefer task-shaped retrieval over loading entire note histories.
Ground synthesis in cited notes and keep context packets focused.

When the task changes, regenerate a fresh context packet instead of reusing stale broad summaries.
This keeps agent responses tied to the active objective and reduces token spend on irrelevant history.
