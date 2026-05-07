"""Shared helpers for review-gated KB mutations."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from kb_librarian.errors import KBLibrarianError
from kb_librarian.notes import Note


def require_clean_worktree(data_dir: Path, *, force: bool, operation: str) -> None:
    """Require a clean git worktree when data_dir is inside one."""

    if force:
        return

    root = _git_worktree_root(data_dir)
    if root is None:
        return

    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        text=True,
        capture_output=True,
        check=False,
    )
    if status.returncode != 0:
        raise KBLibrarianError(
            f"{operation} requires a clean git worktree, but git status could not be read."
        )
    if status.stdout.strip():
        raise KBLibrarianError(
            f"{operation} requires a clean git worktree. Commit or stash changes, or rerun with --force."
        )


def atomic_write_note(path: Path, note: Note) -> None:
    """Write a note through a same-directory temp file and atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = note.to_markdown()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _git_worktree_root(data_dir: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(data_dir), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None
