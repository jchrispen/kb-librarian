"""Ingest lockfile and checkpoint state helpers."""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.atomic import atomic_write_json
from kb_librarian.errors import IngestError

STATE_VERSION = 1
RUNNING_INGEST_STATUSES = {"running", "error", "interrupted"}


def new_operation_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"ingest-{stamp}-{uuid.uuid4().hex[:8]}"


def state_path(data_dir: Path) -> Path:
    return data_dir / ".kb" / "state.json"


def lock_path(data_dir: Path) -> Path:
    return data_dir / ".kb" / "ingest.lock"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_state(data_dir: Path) -> dict[str, Any]:
    path = state_path(data_dir)
    if not path.exists():
        return {"version": STATE_VERSION}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"Malformed ingest state at {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise IngestError(f"Ingest state at {path} must be an object.")
    loaded.setdefault("version", STATE_VERSION)
    return loaded


def write_state(data_dir: Path, state: Mapping[str, Any]) -> None:
    payload = dict(state)
    payload.setdefault("version", STATE_VERSION)
    atomic_write_json(state_path(data_dir), payload)


def current_ingest_checkpoint(data_dir: Path) -> dict[str, Any] | None:
    state = load_state(data_dir)
    ingest = state.get("ingest")
    if not isinstance(ingest, dict):
        return None
    return ingest


def resumable_ingest_checkpoint(data_dir: Path) -> dict[str, Any] | None:
    ingest = current_ingest_checkpoint(data_dir)
    if ingest is None:
        return None
    if str(ingest.get("status")) in RUNNING_INGEST_STATUSES:
        return ingest
    return None


def lock_status(data_dir: Path) -> tuple[str, dict[str, Any] | None]:
    path = lock_path(data_dir)
    if not path.exists():
        return "missing", None
    payload = _read_json_file(path)
    if payload is None:
        return "stale", None
    return ("active" if _lock_is_active(payload) else "stale"), payload


@dataclass
class IngestLock:
    data_dir: Path
    operation_id: str

    @classmethod
    def acquire(
        cls,
        data_dir: Path,
        *,
        operation_id: str,
        command: str,
        force: bool,
        resume: bool,
    ) -> "IngestLock":
        path = lock_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "operation_id": operation_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "command": command,
            "started": now_iso(),
            "data_dir": data_dir.as_posix(),
            "current_raw_file": None,
        }

        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                existing = _read_json_file(path)
                if existing is not None and _lock_is_active(existing):
                    op = str(existing.get("operation_id", "unknown"))
                    pid = str(existing.get("pid", "unknown"))
                    current = existing.get("current_raw_file") or "(none yet)"
                    raise IngestError(
                        "Another ingest is already running "
                        f"(operation_id={op}, pid={pid}, current_raw_file={current}). "
                        "Wait for it to finish or run `kb doctor` if it appears stuck."
                    )
                if not (force or resume):
                    op = "unknown" if existing is None else str(existing.get("operation_id", "unknown"))
                    raise IngestError(
                        f"Stale ingest lock found at {path} (operation_id={op}). "
                        "Run `kb ingest --resume` to recover the interrupted operation, "
                        "or `kb ingest --force` to discard the stale lock and start over."
                    )
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue

            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            return cls(data_dir=data_dir, operation_id=operation_id)

    def update_current_file(self, path: Path | None) -> None:
        payload = _read_json_file(lock_path(self.data_dir)) or {}
        if payload.get("operation_id") != self.operation_id:
            return
        payload["current_raw_file"] = path.as_posix() if path is not None else None
        payload["updated"] = now_iso()
        atomic_write_json(lock_path(self.data_dir), payload)

    def release(self) -> None:
        path = lock_path(self.data_dir)
        payload = _read_json_file(path)
        if payload is not None and payload.get("operation_id") != self.operation_id:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class IngestCheckpoint:
    def __init__(self, data_dir: Path, *, operation_id: str) -> None:
        self.data_dir = data_dir
        self.operation_id = operation_id

    def start_operation(self, files: Iterable[Path], *, resumed: bool = False) -> None:
        self._replace(
            {
                "operation_id": self.operation_id,
                "status": "running",
                "resumed": resumed,
                "data_dir": self.data_dir.as_posix(),
                "pending_files": [path.as_posix() for path in files],
                "current_raw_file": None,
                "current_hash": None,
                "parse_status": None,
                "candidate_ids": [],
                "integration_decisions": [],
                "files_written": [],
                "archive_target": None,
                "last_completed_stage": "started",
                "started_at": now_iso(),
                "updated_at": now_iso(),
            }
        )

    def start_file(self, path: Path) -> None:
        ingest = self._current()
        ingest.update(
            {
                "status": "running",
                "current_raw_file": path.as_posix(),
                "current_hash": None,
                "parse_status": None,
                "candidate_ids": [],
                "integration_decisions": [],
                "files_written": [],
                "archive_target": None,
                "last_completed_stage": "selected",
                "updated_at": now_iso(),
            }
        )
        self._save(ingest)

    def update(self, stage: str, **fields: Any) -> None:
        ingest = self._current()
        ingest.update(fields)
        ingest["last_completed_stage"] = stage
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def append_decision(self, decision: Mapping[str, Any]) -> None:
        ingest = self._current()
        decisions = ingest.get("integration_decisions")
        if not isinstance(decisions, list):
            decisions = []
        decisions.append(dict(decision))
        ingest["integration_decisions"] = decisions
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def record_written_file(self, path: Path) -> None:
        ingest = self._current()
        written = ingest.get("files_written")
        if not isinstance(written, list):
            written = []
        rendered = path.as_posix()
        if rendered not in written:
            written.append(rendered)
        ingest["files_written"] = written
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def complete_file(self, *, archive_target: Path | None) -> None:
        ingest = self._current()
        ingest["archive_target"] = archive_target.as_posix() if archive_target is not None else None
        ingest["last_completed_stage"] = "archived" if archive_target is not None else "completed-file"
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def finish(self, *, errors: int) -> None:
        ingest = self._current()
        ingest["status"] = "error" if errors else "complete"
        ingest["last_completed_stage"] = "error" if errors else "complete"
        ingest["finished_at"] = now_iso()
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def fail(self, *, stage: str, error: str) -> None:
        ingest = self._current()
        ingest["status"] = "error"
        ingest["last_completed_stage"] = stage
        ingest["error"] = error
        ingest["updated_at"] = now_iso()
        self._save(ingest)

    def _replace(self, ingest: dict[str, Any]) -> None:
        state = load_state(self.data_dir)
        state["ingest"] = ingest
        write_state(self.data_dir, state)

    def _current(self) -> dict[str, Any]:
        state = load_state(self.data_dir)
        ingest = state.get("ingest")
        if not isinstance(ingest, dict) or ingest.get("operation_id") != self.operation_id:
            raise IngestError(
                f"Ingest checkpoint for operation {self.operation_id} is missing. "
                "Run `kb doctor` and inspect `.kb/state.json` before retrying."
            )
        return dict(ingest)

    def _save(self, ingest: dict[str, Any]) -> None:
        state = load_state(self.data_dir)
        state["ingest"] = ingest
        write_state(self.data_dir, state)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _lock_is_active(payload: Mapping[str, Any]) -> bool:
    host = str(payload.get("hostname", ""))
    if host and host != socket.gethostname():
        return True
    try:
        pid = int(payload.get("pid", 0))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
