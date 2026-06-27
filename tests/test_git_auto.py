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


from unittest.mock import patch, MagicMock
import subprocess
from kb_librarian.git_auto import (
    AutoCommitResult, GitSnapshot, _log_auto_commit_event,
    _operation_paths, _parse_porcelain_status, _preexisting_unrelated_paths,
    _relative_prefix, _relative_to_root, _run_git, build_commit_message,
    capture_git_snapshot, git_worktree_root,
)


# has_changes property

def test_git_snapshot_has_changes_true():
    snap = GitSnapshot(root=None, entries={"file.md": object()})
    assert snap.has_changes is True


def test_git_snapshot_has_changes_false():
    snap = GitSnapshot(root=None, entries={})
    assert snap.has_changes is False


# capture_git_snapshot when root is None

def test_capture_git_snapshot_no_git_repo(tmp_path):
    # tmp_path is not inside a git repo → root is None
    with patch("kb_librarian.git_auto.git_worktree_root", return_value=None):
        snap = capture_git_snapshot(tmp_path)
    assert snap.root is None
    assert snap.entries == {}


# capture_git_snapshot when git status fails

def test_capture_git_snapshot_status_failure(tmp_path):
    fake_root = tmp_path
    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stderr = "fatal: not a repo"
    fake_result.stdout = ""
    with patch("kb_librarian.git_auto.git_worktree_root", return_value=fake_root):
        with patch("kb_librarian.git_auto._run_git", return_value=fake_result):
            snap = capture_git_snapshot(tmp_path)
    assert snap.error == "fatal: not a repo"
    assert snap.entries == {}


# maybe_auto_commit: before_snapshot.root is None (lines 88-94)

def test_maybe_auto_commit_no_git_repo(tmp_path):
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    no_repo_snap = GitSnapshot(root=None, entries={})
    result = maybe_auto_commit(tmp_path, config, operation="ingest", before=no_repo_snap)
    assert result.attempted is True
    assert result.committed is False
    assert "not inside a git worktree" in result.message


# maybe_auto_commit: before_snapshot.error is not None (lines 96-102)

def test_maybe_auto_commit_before_snapshot_error(tmp_path):
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    error_snap = GitSnapshot(root=tmp_path, entries={}, error="git exploded")
    result = maybe_auto_commit(tmp_path, config, operation="ingest", before=error_snap)
    assert result.attempted is True
    assert result.committed is False
    assert "git status failed before operation" in result.message


# maybe_auto_commit: after snapshot root is None (lines 124-130)

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_maybe_auto_commit_after_snapshot_root_becomes_none(tmp_path):
    _run_git_direct(tmp_path, "init")
    _run_git_direct(tmp_path, "config", "user.email", "t@t.com")
    _run_git_direct(tmp_path, "config", "user.name", "Test")
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(tmp_path)
    # After snapshot: root returns None to simulate worktree gone
    none_snap = GitSnapshot(root=None, entries={})
    with patch("kb_librarian.git_auto.capture_git_snapshot", return_value=none_snap):
        result = maybe_auto_commit(tmp_path, config, operation="ingest", before=before)
    assert "no longer inside a git worktree" in result.message


# maybe_auto_commit: after snapshot error (lines 132-138)

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_maybe_auto_commit_after_snapshot_error(tmp_path):
    _run_git_direct(tmp_path, "init")
    _run_git_direct(tmp_path, "config", "user.email", "t@t.com")
    _run_git_direct(tmp_path, "config", "user.name", "Test")
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(tmp_path)
    err_snap = GitSnapshot(root=tmp_path, entries={}, error="disk read error")
    with patch("kb_librarian.git_auto.capture_git_snapshot", return_value=err_snap):
        result = maybe_auto_commit(tmp_path, config, operation="ingest", before=before)
    assert "git status failed after operation" in result.message


# maybe_auto_commit: no operation paths (lines 142-148)

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_maybe_auto_commit_no_new_changes(tmp_path):
    _run_git_direct(tmp_path, "init")
    _run_git_direct(tmp_path, "config", "user.email", "t@t.com")
    _run_git_direct(tmp_path, "config", "user.name", "Test")
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(tmp_path)
    # No files written after snapshot → no new operation paths
    result = maybe_auto_commit(tmp_path, config, operation="ingest", before=before)
    assert result.committed is False
    assert "no new operation changes" in result.message


# maybe_auto_commit: git add fails (lines 167-175)

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_maybe_auto_commit_add_fails(tmp_path):
    _run_git_direct(tmp_path, "init")
    _run_git_direct(tmp_path, "config", "user.email", "t@t.com")
    _run_git_direct(tmp_path, "config", "user.name", "Test")
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(tmp_path)
    (tmp_path / "note.md").write_text("content\n")
    fail_result = MagicMock()
    fail_result.returncode = 1
    fail_result.stderr = "git add failed: permission denied"
    fail_result.stdout = ""
    success = MagicMock()
    success.returncode = 0
    success.stdout = ""
    success.stderr = ""

    def _fake_run(root, *args):
        if args[0] == "status":
            return _run_git(root, *args)
        elif args[0] == "add":
            return fail_result
        return success

    with patch("kb_librarian.git_auto._run_git", side_effect=_fake_run):
        result = maybe_auto_commit(tmp_path, config, operation="ingest", before=before)
    assert result.committed is False
    assert "Auto-commit failed" in result.message


# maybe_auto_commit: git commit fails (lines 179-187)

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not available")
def test_maybe_auto_commit_commit_fails(tmp_path):
    _run_git_direct(tmp_path, "init")
    _run_git_direct(tmp_path, "config", "user.email", "t@t.com")
    _run_git_direct(tmp_path, "config", "user.name", "Test")
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = True
    before = capture_git_snapshot(tmp_path)
    (tmp_path / "note.md").write_text("content\n")
    fail_commit = MagicMock()
    fail_commit.returncode = 1
    fail_commit.stderr = "nothing to commit"
    fail_commit.stdout = ""
    success = MagicMock()
    success.returncode = 0
    success.stdout = ""
    success.stderr = ""

    def _fake_run(root, *args):
        if args[0] == "status":
            return _run_git(root, *args)
        elif args[0] == "add":
            return success
        elif args[0] == "commit":
            return fail_commit
        return success

    with patch("kb_librarian.git_auto._run_git", side_effect=_fake_run):
        result = maybe_auto_commit(tmp_path, config, operation="ingest", before=before)
    assert result.committed is False
    assert "Auto-commit failed" in result.message


# build_commit_message: no identifiers (line 209)

def test_build_commit_message_no_identifiers():
    msg = build_commit_message(operation="compact")
    assert msg == "kb: compact"


# git_worktree_root: git not found (FileNotFoundError, lines 220-221)

def test_git_worktree_root_git_not_found(tmp_path):
    with patch("kb_librarian.git_auto.subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = git_worktree_root(tmp_path)
    assert result is None


# _operation_paths: after.root is None (line 236)

def test_operation_paths_root_none(tmp_path):
    after = GitSnapshot(root=None, entries={"file.md": object()})
    before = GitSnapshot(root=None, entries={})
    result = _operation_paths(tmp_path, after=after, before=before, allow_unrelated=False)
    assert result == []


# _operation_paths: path doesn't start with data_prefix (line 242)

def test_operation_paths_filters_outside_data_prefix(tmp_path):
    root = tmp_path
    data_dir = tmp_path / "kb"
    data_dir.mkdir()
    after = GitSnapshot(root=root, entries={"outside/file.md": object(), "kb/note.md": object()})
    before = GitSnapshot(root=root, entries={})
    result = _operation_paths(data_dir, after=after, before=before, allow_unrelated=False)
    assert "kb/note.md" in result
    assert "outside/file.md" not in result


# _operation_paths: allow_unrelated skips before paths (line 244)

def test_operation_paths_allow_unrelated_skips_before_paths(tmp_path):
    root = tmp_path
    after = GitSnapshot(root=root, entries={"note.md": object(), "pre.md": object()})
    before = GitSnapshot(root=root, entries={"pre.md": object()})
    result = _operation_paths(tmp_path, after=after, before=before, allow_unrelated=True)
    assert "note.md" in result
    assert "pre.md" not in result


# _preexisting_unrelated_paths: root is None (line 258)

def test_preexisting_unrelated_paths_root_none(tmp_path):
    before = GitSnapshot(root=None, entries={"a.md": object(), "b.md": object()})
    result = _preexisting_unrelated_paths(before=before, related_paths=[], related_dirs=[])
    assert sorted(result) == ["a.md", "b.md"]


# _relative_prefix: ValueError (lines 273-274)

def test_relative_prefix_outside_root(tmp_path):
    # data_dir is not under root → ValueError → returns ""
    root = tmp_path / "root"
    root.mkdir()
    data_dir = tmp_path / "other"
    data_dir.mkdir()
    result = _relative_prefix(data_dir, root=root)
    assert result == ""


# _relative_to_root: ValueError (lines 287-288)

def test_relative_to_root_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "outside" / "file.md"
    result = _relative_to_root(path, root=root)
    assert result is None


# _parse_porcelain_status: empty line (line 295)

def test_parse_porcelain_status_skips_empty_lines():
    result = _parse_porcelain_status("\n\n M file.md\n")
    assert "file.md" in result


# _parse_porcelain_status: rename with " -> " (lines 299-303)

def test_parse_porcelain_status_handles_rename():
    result = _parse_porcelain_status("R  old.md -> new.md\n")
    assert "old.md" in result
    assert "new.md" in result


# _parse_porcelain_status: empty path_text (304->293)

def test_parse_porcelain_status_empty_path_text():
    # Line with only 2 chars (status only) → path_text = "" → not added
    result = _parse_porcelain_status("M \n")
    assert result == {}


# _run_git: FileNotFoundError (lines 317-318)

def test_run_git_git_not_found(tmp_path):
    with patch("kb_librarian.git_auto.subprocess.run", side_effect=FileNotFoundError("no git")):
        result = _run_git(tmp_path, "status")
    assert result.returncode == 127


def _run_git_direct(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()
