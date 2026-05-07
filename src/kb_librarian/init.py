"""Data directory initialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kb_librarian.config import default_config, read_config_file, validate_config, write_config_file
from kb_librarian.paths import (
    DIRECTORIES,
    LOG_FILES,
    REVIEW_QUEUE_FILES,
    REVIEW_STATE_FILE,
    ROOT_FILES,
    STATE_FILES,
    config_path,
)
from kb_librarian.review import ensure_review_state

LEGACY_PREAMBLE_CONTENT = (
    "# KB Preamble\n\n"
    "Agents should use the `kb` CLI for task-shaped context instead of reading the full KB.\n"
)


ROOT_FILE_CONTENT = {
    "INDEX.md": "# KB Index\n\nThis index is managed by KB Librarian.\n",
}

REVIEW_FILE_CONTENT = {
    "review/pending-classification.md": "# Pending Classification\n\n",
    "review/pending-merge.md": "# Pending Merge\n\n",
    "review/pending-compaction.md": "# Pending Compaction\n\n",
    "review/pending-topic.md": "# Pending Topic Reorganization\n\n",
    "review/disputes.md": "# Disputes\n\n",
    "review/stale.md": "# Stale Notes\n\n",
    "review/orphans.md": "# Orphan Notes\n\n",
    "review/search-misses.md": "# Search Misses\n\n",
    "review/low-utility.md": "# Low Utility\n\n",
}


def initialize_data_dir(data_dir: str | Path, *, hooks: bool = False) -> list[Path]:
    """Create the Phase 1 KB directory layout without overwriting user files."""

    root = Path(data_dir).expanduser()
    created: list[Path] = []
    root.mkdir(parents=True, exist_ok=True)

    for directory in DIRECTORIES:
        path = root / directory
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
        else:
            path.mkdir(parents=True, exist_ok=True)

    index_path = root / "INDEX.md"
    if _write_text_if_missing(index_path, ROOT_FILE_CONTENT["INDEX.md"]):
        created.append(index_path)

    preamble_path = root / "PREAMBLE.md"
    preamble_content = render_preamble(root)
    if hooks:
        if _install_managed_preamble(preamble_path, preamble_content):
            created.append(preamble_path)
    elif _write_text_if_missing(preamble_path, preamble_content):
        created.append(preamble_path)

    for filename in REVIEW_QUEUE_FILES:
        path = root / filename
        if _write_text_if_missing(path, REVIEW_FILE_CONTENT[filename]):
            created.append(path)

    review_state = root / REVIEW_STATE_FILE
    if not review_state.exists():
        ensure_review_state(root, render=False)
        created.append(review_state)

    config_file = config_path(root)
    if not config_file.exists():
        write_config_file(config_file, default_config(root, hooks=hooks))
        created.append(config_file)
    else:
        validate_config(read_config_file(config_file))

    for filename, payload in STATE_FILES.items():
        path = root / filename
        if _write_json_if_missing(path, payload):
            created.append(path)

    for filename in LOG_FILES:
        path = root / filename
        if _write_text_if_missing(path, ""):
            created.append(path)

    return created


def render_preamble(data_dir: str | Path) -> str:
    """Render the agent-facing preamble for a KB data directory."""

    return (
        "# Knowledge Base\n\n"
        f"This system has a curated agent context knowledge base at `{Path(data_dir).expanduser()}`.\n\n"
        "The knowledge base is published as markdown files, but agents should normally retrieve through the `kb` CLI rather than browsing files directly.\n\n"
        "## Primary commands\n\n"
        "- `kb context \"<task>\" --mode <mode> --budget <tokens>` - use before coding, architecture, debugging, review, or writing tasks.\n"
        "- `kb explore \"<problem>\" --budget <tokens>` - use for ideation, alternatives, tradeoffs, and novel concept discovery.\n"
        "- `kb search \"<query>\"` - use for precise lookup of known concepts.\n"
        "- `kb get <id>` - use only when a specific note is clearly relevant.\n\n"
        "## When to consult\n\n"
        "Consult the KB when the task may benefit from stored techniques, heuristics, design judgments, coding patterns, anti-patterns, prior decisions, or niche concepts.\n\n"
        "## Retrieval discipline\n\n"
        "1. Prefer `kb context` for task help.\n"
        "2. Prefer `kb explore` for broad ideation.\n"
        "3. Prefer `kb search` for precise lookup.\n"
        "4. Do not load multiple notes just in case.\n"
        "5. Respect the user's context budget.\n\n"
        "## Trust signals\n\n"
        "Each note may include:\n\n"
        "- `knowledge_type`\n"
        "- `confidence`\n"
        "- `status`\n"
        "- `basis`\n"
        "- `applies_when`\n"
        "- `does_not_apply_when`\n"
        "- `failure_modes`\n"
        "- `updated`\n\n"
        "If a note is disputed, stale, low-confidence, or outside its applicability bounds, say so.\n\n"
        "## Citation requirement\n\n"
        "When using KB material in a response, include the CLI-provided citation block or end with:\n\n"
        "---\n"
        "**KB sources:** [<note-id>](<path>) (confidence: <level>)\n\n"
        "After using a note, call `kb log-use <id>` when practical.\n\n"
        "## Correction behavior\n\n"
        "If the user corrects a KB-derived claim or says a retrieved note was not useful, run:\n\n"
        "`kb flag-suspect <id> \"<reason>\"`\n"
    )


def render_preamble_guidance(data_dir: str | Path) -> str:
    """Return CLI guidance for including the installed preamble in agent sessions."""

    preamble_path = Path(data_dir).expanduser() / "PREAMBLE.md"
    return (
        f"Agent preamble installed at {preamble_path}\n"
        "Include that file in agent session instructions, or paste its contents into the session preamble.\n"
        f"Recommended first check: kb context \"<task>\" --data-dir {Path(data_dir).expanduser()}\n"
    )


def _write_text_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _install_managed_preamble(path: Path, content: str) -> bool:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True

    current = path.read_text(encoding="utf-8")
    if current == content:
        return False
    if _is_managed_preamble(current):
        path.write_text(content, encoding="utf-8")
        return True
    return False


def _is_managed_preamble(content: str) -> bool:
    if content == LEGACY_PREAMBLE_CONTENT:
        return True
    if not content.startswith("# Knowledge Base\n\nThis system has a curated agent context knowledge base at `"):
        return False
    expected_tail = render_preamble("<data-dir>").split("`<data-dir>`", maxsplit=1)[1]
    return content.endswith(expected_tail)


def _write_json_if_missing(path: Path, payload: Any) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True
