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

ROOT_FILE_CONTENT = {
    "INDEX.md": "# KB Index\n\nThis index is managed by KB Librarian.\n",
    "PREAMBLE.md": (
        "# KB Preamble\n\n"
        "Agents should use the `kb` CLI for task-shaped context instead of reading the full KB.\n"
    ),
}

REVIEW_FILE_CONTENT = {
    "review/pending-classification.md": "# Pending Classification\n\n",
    "review/pending-merge.md": "# Pending Merge\n\n",
    "review/disputes.md": "# Disputes\n\n",
    "review/search-misses.md": "# Search Misses\n\n",
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

    for filename in ROOT_FILES:
        path = root / filename
        if _write_text_if_missing(path, ROOT_FILE_CONTENT[filename]):
            created.append(path)

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


def _write_text_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _write_json_if_missing(path: Path, payload: Any) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True
