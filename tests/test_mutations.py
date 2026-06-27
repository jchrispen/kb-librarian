from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kb_librarian.errors import KBLibrarianError
from kb_librarian.mutations import _git_worktree_root, atomic_write_note, require_clean_worktree
from kb_librarian.notes import Note


def _simple_note() -> Note:
    return Note(
        frontmatter={
            "id": "2026-01-01-test",
            "title": "Test",
            "summary": "Summary",
            "topic": "test",
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": [],
            "tags": [],
        },
        body="Body content.\n",
    )


def test_git_worktree_root_returns_none_when_git_not_found(tmp_path):
    # FileNotFoundError (git not in PATH) must be caught and return None.
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = _git_worktree_root(tmp_path)
    assert result is None


def test_require_clean_worktree_raises_when_git_status_fails(tmp_path):
    # Simulate: git is found and returns a repo root, but git status fails.
    def fake_run(args, **kwargs):
        if "rev-parse" in args:
            result = MagicMock()
            result.returncode = 0
            result.stdout = str(tmp_path) + "\n"
            return result
        if "status" in args:
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(KBLibrarianError, match="git status could not be read"):
            require_clean_worktree(tmp_path, force=False, operation="test-op")


def test_atomic_write_note_cleans_up_temp_file_on_replace_failure(tmp_path):
    # When os.replace fails mid-write, the finally block must unlink the temp file.
    note_path = tmp_path / "test-note.md"
    note = _simple_note()

    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            atomic_write_note(note_path, note)

    # No leftover .tmp files.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
