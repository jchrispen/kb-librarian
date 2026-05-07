"""Optional git auto-commit helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class GitStatusEntry:
    status: str
    path: str


@dataclass(frozen=True)
class GitSnapshot:
    root: Path | None
    entries: dict[str, GitStatusEntry]
    error: str | None = None

    @property
    def has_changes(self) -> bool:
        return bool(self.entries)


@dataclass(frozen=True)
class AutoCommitResult:
    attempted: bool
    committed: bool
    message: str
    commit_sha: str | None = None
    paths: tuple[str, ...] = ()


POLICY_KEYS = {
    "ingest": "commit_ingests",
    "review": "commit_reviews",
    "compact": "commit_reviews",
    "topic": "commit_topic_reorganizations",
    "reindex": "commit_reindexes",
}


def capture_git_snapshot(data_dir: Path) -> GitSnapshot:
    """Capture current git status for the worktree containing data_dir."""

    root = git_worktree_root(data_dir)
    if root is None:
        return GitSnapshot(root=None, entries={})
    status = _run_git(root, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).strip() or "unknown git status failure"
        return GitSnapshot(root=root, entries={}, error=detail)
    return GitSnapshot(root=root, entries=_parse_porcelain_status(status.stdout))


def maybe_auto_commit(
    data_dir: Path,
    config: Mapping[str, Any],
    *,
    operation: str,
    identifiers: Sequence[str] = (),
    before: GitSnapshot | None = None,
    related_before_paths: Sequence[Path] = (),
    related_before_dirs: Sequence[Path] = (),
) -> AutoCommitResult:
    """Commit operation changes when configured, returning a user-facing result."""

    git_config = config.get("git", {})
    if not isinstance(git_config, Mapping) or not bool(git_config.get("auto_commit", False)):
        return AutoCommitResult(attempted=False, committed=False, message="Auto-commit disabled.")

    policy_key = POLICY_KEYS.get(operation, f"commit_{operation}s")
    if not bool(git_config.get(policy_key, True)):
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit skipped: git.{policy_key} is false.",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    before_snapshot = before or capture_git_snapshot(data_dir)
    if before_snapshot.root is None:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message="Auto-commit skipped: data directory is not inside a git worktree.",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result
    if before_snapshot.error is not None:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit skipped: git status failed before operation: {before_snapshot.error}",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    allow_unrelated = bool(git_config.get("allow_unrelated_changes", False))
    unrelated_before = _preexisting_unrelated_paths(
        before=before_snapshot,
        related_paths=related_before_paths,
        related_dirs=related_before_dirs,
    )
    if unrelated_before and not allow_unrelated:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=(
                "Auto-commit skipped: worktree had pre-existing changes. "
                "Commit or stash them, or set git.allow_unrelated_changes: true."
            ),
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    after = capture_git_snapshot(data_dir)
    if after.root is None:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message="Auto-commit skipped: data directory is no longer inside a git worktree.",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result
    if after.error is not None:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit skipped: git status failed after operation: {after.error}",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    operation_paths = _operation_paths(data_dir, after=after, before=before_snapshot, allow_unrelated=allow_unrelated)
    if not operation_paths:
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message="Auto-commit skipped: no new operation changes were found.",
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    commit_message = build_commit_message(operation=operation, identifiers=identifiers)
    _log_auto_commit_event(
        data_dir,
        operation=operation,
        result=AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit committing: {commit_message}",
            paths=tuple(operation_paths),
        ),
    )
    log_path = _relative_log_path(data_dir, root=after.root)
    if log_path and log_path not in operation_paths:
        operation_paths.append(log_path)

    add = _run_git(after.root, "add", "--", *operation_paths)
    if add.returncode != 0:
        detail = (add.stderr or add.stdout).strip() or "git add failed"
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit failed: {detail}",
            paths=tuple(operation_paths),
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    commit = _run_git(after.root, "commit", "-m", commit_message)
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout).strip() or "git commit failed"
        result = AutoCommitResult(
            attempted=True,
            committed=False,
            message=f"Auto-commit failed: {detail}",
            paths=tuple(operation_paths),
        )
        _log_auto_commit_event(data_dir, operation=operation, result=result)
        return result

    rev = _run_git(after.root, "rev-parse", "--short", "HEAD")
    sha = rev.stdout.strip() if rev.returncode == 0 else None
    result = AutoCommitResult(
        attempted=True,
        committed=True,
        message=f"Auto-committed {len(operation_paths)} path(s): {commit_message}",
        commit_sha=sha,
        paths=tuple(operation_paths),
    )
    return result


def build_commit_message(*, operation: str, identifiers: Sequence[str] = ()) -> str:
    clean_ids = [str(item).strip() for item in identifiers if str(item).strip()]
    if clean_ids:
        if len(clean_ids) <= 3:
            suffix = ", ".join(clean_ids)
        else:
            suffix = f"{', '.join(clean_ids[:3])}, +{len(clean_ids) - 3} more"
        return f"kb: {operation} {suffix}"
    return f"kb: {operation}"


def git_worktree_root(data_dir: Path) -> Path | None:
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


def _operation_paths(
    data_dir: Path,
    *,
    after: GitSnapshot,
    before: GitSnapshot,
    allow_unrelated: bool,
) -> list[str]:
    if after.root is None:
        return []
    data_prefix = _relative_prefix(data_dir, root=after.root)
    paths: list[str] = []
    before_paths = set(before.entries)
    for path in sorted(after.entries):
        if data_prefix and not (path == data_prefix or path.startswith(f"{data_prefix}/")):
            continue
        if allow_unrelated and path in before_paths:
            continue
        paths.append(path)
    return paths


def _preexisting_unrelated_paths(
    *,
    before: GitSnapshot,
    related_paths: Sequence[Path],
    related_dirs: Sequence[Path],
) -> list[str]:
    if not before.entries:
        return []
    if before.root is None:
        return sorted(before.entries)
    exact = {_relative_to_root(path, root=before.root) for path in related_paths}
    prefixes = {_relative_to_root(path, root=before.root) for path in related_dirs}
    exact.discard(None)
    prefixes.discard(None)
    return sorted(
        path
        for path in before.entries
        if path not in exact and not any(path.startswith(f"{prefix}/") for prefix in prefixes if prefix)
    )


def _relative_prefix(data_dir: Path, *, root: Path) -> str:
    try:
        relative = data_dir.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    value = relative.as_posix()
    return "" if value == "." else value


def _relative_log_path(data_dir: Path, *, root: Path) -> str | None:
    path = data_dir / ".kb" / "errors.log"
    return _relative_to_root(path, root=root)


def _relative_to_root(path: Path, *, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _parse_porcelain_status(output: str) -> dict[str, GitStatusEntry]:
    entries: dict[str, GitStatusEntry] = {}
    for line in output.splitlines():
        if not line:
            continue
        status = line[:2]
        path_text = line[3:] if len(line) > 3 else ""
        if " -> " in path_text:
            old_path, new_path = path_text.split(" -> ", maxsplit=1)
            for path in (old_path, new_path):
                if path:
                    entries[path] = GitStatusEntry(status=status, path=path)
            continue
        if path_text:
            entries[path_text] = GitStatusEntry(status=status, path=path_text)
    return entries


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args=["git", *args], returncode=127, stdout="", stderr=str(exc))


def _log_auto_commit_event(data_dir: Path, *, operation: str, result: AutoCommitResult) -> None:
    path = data_dir / ".kb" / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    outcome = "committed" if result.committed else "skipped"
    if result.message.startswith("Auto-commit committing:"):
        outcome = "committing"
    paths = ",".join(result.paths)
    sha = result.commit_sha or ""
    message = result.message.replace("\n", " ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{stamp} stage=auto-commit operation={operation} outcome={outcome} "
            f"commit={sha} paths={paths!r} message={message!r}\n"
        )
