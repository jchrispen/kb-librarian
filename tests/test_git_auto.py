from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kb_librarian.config import default_config
from kb_librarian.git_auto import build_commit_message, capture_git_snapshot, maybe_auto_commit


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_auto_commit_disabled_by_default(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")

    data_dir = tmp_path
    config = default_config(data_dir)
    before = capture_git_snapshot(data_dir)
    (data_dir / "note.md").write_text("note\n", encoding="utf-8")

    result = maybe_auto_commit(data_dir, config, operation="ingest", identifiers=["note"], before=before)

    assert result.attempted is False
    assert _run_git(tmp_path, "status", "--porcelain")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_auto_commit_refuses_preexisting_changes_by_default(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")

    data_dir = tmp_path
    (data_dir / "unrelated.md").write_text("unrelated\n", encoding="utf-8")
    before = capture_git_snapshot(data_dir)
    (data_dir / "operation.md").write_text("operation\n", encoding="utf-8")
    config = default_config(data_dir)
    config["git"]["auto_commit"] = True

    result = maybe_auto_commit(data_dir, config, operation="ingest", identifiers=["operation"], before=before)

    assert result.attempted is True
    assert result.committed is False
    assert "pre-existing changes" in result.message
    assert _run_git(tmp_path, "status", "--porcelain")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_auto_commit_commits_operation_paths(tmp_path):
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")

    data_dir = tmp_path
    config = default_config(data_dir)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(data_dir)
    (data_dir / "operation.md").write_text("operation\n", encoding="utf-8")

    result = maybe_auto_commit(data_dir, config, operation="ingest", identifiers=["2026-05-07-note"], before=before)

    assert result.committed is True
    assert result.commit_sha
    assert _run_git(tmp_path, "log", "-1", "--pretty=%s") == "kb: ingest 2026-05-07-note"
    assert _run_git(tmp_path, "status", "--porcelain") == ""


def test_build_commit_message_limits_identifier_list():
    message = build_commit_message(operation="review", identifiers=["a", "b", "c", "d"])

    assert message == "kb: review a, b, c, +1 more"


def test_auto_commit_respects_reindex_scope_policy(tmp_path):
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True

    result = maybe_auto_commit(tmp_path, config, operation="reindex", identifiers=["manual"])

    assert result.attempted is True
    assert result.committed is False
    assert "git.commit_reindexes is false" in result.message
