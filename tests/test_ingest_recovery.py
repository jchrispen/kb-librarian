from __future__ import annotations

import json
import hashlib
import socket
import sqlite3
from pathlib import Path

import pytest

from kb_librarian import atomic
from kb_librarian.config import default_config, write_config_file
from kb_librarian.errors import IngestError
from kb_librarian.ingest import ingest
from kb_librarian.ingest_recovery import IngestCheckpoint, IngestLock, load_state, lock_status, write_state
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.search_index import build_lexical_index, query_candidates
from kb_librarian.storage import canonical_note_path, ensure_topic_layout


def _mock_config(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "mock-extract"}
    config["operations"]["classify"] = {"provider": "mock", "model": "mock-classify"}
    config["operations"]["integrate"] = {"provider": "mock", "model": "mock-integrate"}
    write_config_file(data_dir / ".kb" / "config.yaml", config)
    return config


def _seed_note_with_source(data_dir: Path, *, note_id: str, title: str, source_hash: str) -> Path:
    ensure_topic_layout(data_dir, "agent-systems")
    note = Note(
        frontmatter={
            "id": note_id,
            "title": title,
            "summary": "Recovered note summary.",
            "topic": "agent-systems",
            "created": "2026-05-07",
            "updated": "2026-05-07",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "sources": [{"type": "ingest", "ref": "raw/recover.md", "hash": source_hash}],
            "retrieval_phrases": ["cli context retrieval"],
            "tags": ["agent-systems"],
        },
        body="Recovered body.\n",
    )
    path = canonical_note_path(data_dir, "agent-systems", note_id)
    write_note(path, note)
    return path


def test_ingest_lock_acquire_refuses_active_and_releases(tmp_path):
    initialize_data_dir(tmp_path)
    lock = IngestLock.acquire(
        tmp_path,
        operation_id="ingest-test-1",
        command="kb ingest",
        force=False,
        resume=False,
    )

    with pytest.raises(IngestError, match="Another ingest is already running"):
        IngestLock.acquire(
            tmp_path,
            operation_id="ingest-test-2",
            command="kb ingest",
            force=False,
            resume=False,
        )

    status, payload = lock_status(tmp_path)
    assert status == "active"
    assert payload is not None
    assert payload["operation_id"] == "ingest-test-1"

    lock.release()
    assert lock_status(tmp_path) == ("missing", None)


def test_stale_ingest_lock_requires_force_or_resume(tmp_path):
    initialize_data_dir(tmp_path)
    lock_file = tmp_path / ".kb" / "ingest.lock"
    lock_file.write_text(
        json.dumps(
            {
                "operation_id": "old-op",
                "pid": 999_999_999,
                "hostname": socket.gethostname(),
                "command": "kb ingest",
                "started": "2026-05-07T00:00:00",
                "data_dir": tmp_path.as_posix(),
                "current_raw_file": "raw/item.md",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status, payload = lock_status(tmp_path)
    assert status == "stale"
    assert payload is not None
    assert payload["operation_id"] == "old-op"

    with pytest.raises(IngestError, match="Stale ingest lock"):
        IngestLock.acquire(
            tmp_path,
            operation_id="new-op",
            command="kb ingest",
            force=False,
            resume=False,
        )

    lock = IngestLock.acquire(
        tmp_path,
        operation_id="new-op",
        command="kb ingest --force",
        force=True,
        resume=False,
    )
    assert lock_status(tmp_path)[0] == "active"
    lock.release()


def test_ingest_checkpoint_serializes_stages(tmp_path):
    initialize_data_dir(tmp_path)
    source = tmp_path / "raw" / "item.md"
    source.write_text("# Item\n\nBody for checkpoint.\n", encoding="utf-8")

    checkpoint = IngestCheckpoint(tmp_path, operation_id="ingest-checkpoint-test")
    checkpoint.start_operation([source])
    checkpoint.start_file(source)
    checkpoint.update("parsed", current_hash="abc", parse_status="success")
    checkpoint.append_decision({"candidate_id": "cand", "verdict": "unrelated", "target_note_ids": []})
    checkpoint.record_written_file(tmp_path / "topics" / "agent-systems" / "note.md")
    checkpoint.complete_file(archive_target=tmp_path / "raw" / "processed" / "2026-05" / "item.md")

    state = load_state(tmp_path)
    ingest_state = state["ingest"]
    assert ingest_state["operation_id"] == "ingest-checkpoint-test"
    assert ingest_state["current_hash"] == "abc"
    assert ingest_state["last_completed_stage"] == "archived"
    assert ingest_state["integration_decisions"][0]["verdict"] == "unrelated"
    assert ingest_state["files_written"] == [(tmp_path / "topics" / "agent-systems" / "note.md").as_posix()]


def test_resume_does_not_duplicate_note_created_before_interruption(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    source = tmp_path / "raw" / "recover.md"
    source.write_text(
        "# CLI context retrieval\n\nPrefer task-shaped context for coding agents instead of broad browsing.\n",
        encoding="utf-8",
    )
    digest = _sha256(source)
    _seed_note_with_source(
        tmp_path,
        note_id="2026-05-07-cli-context-retrieval",
        title="CLI context retrieval",
        source_hash=digest,
    )
    write_state(
        tmp_path,
        {
            "version": 1,
            "ingest": {
                "operation_id": "ingest-resume-test",
                "status": "error",
                "data_dir": tmp_path.as_posix(),
                "pending_files": [source.as_posix()],
                "current_raw_file": source.as_posix(),
                "current_hash": digest,
                "parse_status": "success",
                "candidate_ids": [],
                "integration_decisions": [],
                "files_written": [],
                "archive_target": None,
                "last_completed_stage": "indexed",
                "started_at": "2026-05-07T00:00:00",
                "updated_at": "2026-05-07T00:00:00",
            },
        },
    )

    report = ingest(tmp_path, config=config, resume=True)

    assert report.processed_files == 1
    assert report.created_notes == []
    assert not source.exists()
    assert len(list((tmp_path / "topics").rglob("2026-05-07-cli-context-retrieval*.md"))) == 1
    entries = json.loads((tmp_path / ".kb" / "ingested.json").read_text(encoding="utf-8"))
    assert entries[-1]["status"] == "success"
    assert load_state(tmp_path)["ingest"]["status"] == "complete"


def test_atomic_write_text_preserves_existing_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "artifact.txt"
    path.write_text("old\n", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic.atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob(".artifact.txt.*.tmp"))


def test_atomic_write_bytes_handles_unlink_failure_during_cleanup(tmp_path, monkeypatch):
    # When os.replace raises AND the temp cleanup also raises FileNotFoundError,
    # the exception must not propagate (the original error is re-raised instead).
    path = tmp_path / "target.txt"
    original_unlink = Path.unlink

    def fail_replace(src, dst):
        raise OSError("replace failed")

    def fail_unlink(self, missing_ok=False):
        raise FileNotFoundError("already gone")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match="replace failed"):
        atomic.atomic_write_bytes(path, b"data")


def test_fsync_dir_handles_os_open_failure(tmp_path, monkeypatch):
    import os as _os
    from kb_librarian.atomic import _fsync_dir

    def fail_open(path, flags):
        raise OSError("cannot open dir")

    monkeypatch.setattr(_os, "open", fail_open)
    # Should return silently without raising.
    _fsync_dir(tmp_path)


def test_failed_lexical_index_rebuild_preserves_existing_index(tmp_path):
    index_path = tmp_path / "fts.sqlite"
    build_lexical_index(
        index_path,
        [
            {
                "id": "note-1",
                "path": "topics/a/note-1.md",
                "title": "Stable search document",
                "summary": "A stable summary.",
                "topic": "a",
                "knowledge_type": "technique",
                "tags": "stable",
                "retrieval_phrases": "stable search",
                "agent_use": "",
                "applies_when": "",
                "does_not_apply_when": "",
                "body": "Body text.",
                "status": "active",
                "confidence": "high",
                "updated": "2026-05-07",
            }
        ],
    )

    with pytest.raises((KeyError, sqlite3.Error)):
        build_lexical_index(index_path, [{"id": "broken"}])

    results = query_candidates(index_path, query="stable search")
    assert [item["id"] for item in results] == ["note-1"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Additional coverage tests for ingest_recovery.py

import os
import socket
from unittest.mock import patch

from kb_librarian.ingest_recovery import (
    IngestCheckpoint, IngestLock, _lock_is_active, _read_json_file,
    current_ingest_checkpoint, load_state, lock_status, resumable_ingest_checkpoint,
)
from kb_librarian.init import initialize_data_dir


# load_state: state file absent → returns default (line 41)

def test_load_state_returns_default_when_file_missing(tmp_path):
    result = load_state(tmp_path)
    assert result == {"version": 1}


# load_state: malformed JSON (lines 44-45)

def test_load_state_raises_on_bad_json(tmp_path):
    sp = tmp_path / ".kb" / "state.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(IngestError, match="Malformed ingest state"):
        load_state(tmp_path)


# load_state: valid JSON but not a dict (line 47)

def test_load_state_raises_when_not_dict(tmp_path):
    sp = tmp_path / ".kb" / "state.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(IngestError, match="must be an object"):
        load_state(tmp_path)


# lock_status: lock file exists but is corrupt JSON → "stale", None (line 81)

def test_lock_status_corrupt_lock_file_is_stale(tmp_path):
    initialize_data_dir(tmp_path)
    lp = tmp_path / ".kb" / "ingest.lock"
    lp.write_text("not valid json", encoding="utf-8")
    status, payload = lock_status(tmp_path)
    assert status == "stale"
    assert payload is None


# IngestLock.acquire: stale lock unlink raises FileNotFoundError mid-loop (lines 135-136)

def test_ingest_lock_acquire_handles_vanishing_stale_lock(tmp_path):
    initialize_data_dir(tmp_path)
    lp = tmp_path / ".kb" / "ingest.lock"
    stale_payload = json.dumps({
        "operation_id": "old-op", "pid": 999_999_999,
        "hostname": socket.gethostname(), "command": "kb ingest",
        "started": "2026-01-01T00:00:00", "data_dir": tmp_path.as_posix(),
        "current_raw_file": None,
    })

    original_open = os.open
    original_unlink = Path.unlink
    call_count = {"n": 0}
    unlink_count = {"n": 0}

    def fake_open(path, flags, mode=0o644):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: write the stale file then raise FileExistsError
            import pathlib
            pathlib.Path(path).write_text(stale_payload, encoding="utf-8")
            raise FileExistsError("exists")
        return original_open(path, flags, mode)

    def fake_unlink(self, missing_ok=False):
        unlink_count["n"] += 1
        if unlink_count["n"] == 1:
            original_unlink(self, missing_ok=missing_ok)
            raise FileNotFoundError("gone")
        return original_unlink(self, missing_ok=missing_ok)

    with patch("kb_librarian.ingest_recovery.os.open", side_effect=fake_open):
        with patch("kb_librarian.ingest_recovery.Path.unlink", autospec=True, side_effect=fake_unlink):
            # FileNotFoundError from unlink should be swallowed; then the loop retries
            # and succeeds because fake_open now returns real fd on second call
            lock = IngestLock.acquire(
                tmp_path, operation_id="new-op", command="kb ingest",
                force=True, resume=False,
            )
    lock.release()


# IngestLock.update_current_file: operation_id mismatch → early return (line 149)

def test_update_current_file_ignores_mismatched_operation_id(tmp_path):
    initialize_data_dir(tmp_path)
    lock = IngestLock.acquire(
        tmp_path, operation_id="op-a", command="kb ingest",
        force=False, resume=False,
    )
    # Create a second lock object with a different operation_id
    other_lock = IngestLock(data_dir=tmp_path, operation_id="op-b")
    # Should return early without modifying anything
    other_lock.update_current_file(tmp_path / "raw" / "file.md")
    payload = _read_json_file(tmp_path / ".kb" / "ingest.lock")
    assert payload is not None
    assert payload.get("current_raw_file") is None  # unchanged
    lock.release()


# IngestLock.release: operation_id mismatch → early return (line 158)

def test_release_ignores_other_operation_lock(tmp_path):
    initialize_data_dir(tmp_path)
    lock = IngestLock.acquire(
        tmp_path, operation_id="op-a", command="kb ingest",
        force=False, resume=False,
    )
    # A different lock object must not release op-a's lock
    other = IngestLock(data_dir=tmp_path, operation_id="op-b")
    other.release()  # should return early
    status, _ = lock_status(tmp_path)
    assert status == "active"
    lock.release()


# IngestLock.release: lock file already gone → FileNotFoundError swallowed (lines 161-162)

def test_release_handles_already_deleted_lock(tmp_path):
    initialize_data_dir(tmp_path)
    lock = IngestLock.acquire(
        tmp_path, operation_id="op-a", command="kb ingest",
        force=False, resume=False,
    )
    (tmp_path / ".kb" / "ingest.lock").unlink()
    lock.release()  # should not raise


# IngestCheckpoint.append_decision: integration_decisions not a list (line 220)

def test_append_decision_resets_non_list_decisions(tmp_path):
    initialize_data_dir(tmp_path)
    cp = IngestCheckpoint(tmp_path, operation_id="op-x")
    cp.start_operation([])
    # Corrupt the state so integration_decisions is not a list
    state = load_state(tmp_path)
    state["ingest"]["integration_decisions"] = "bad"
    write_state(tmp_path, state)
    cp.append_decision({"verdict": "unrelated"})
    state2 = load_state(tmp_path)
    assert state2["ingest"]["integration_decisions"] == [{"verdict": "unrelated"}]


# IngestCheckpoint.record_written_file: files_written not a list (line 230)

def test_record_written_file_resets_non_list_files_written(tmp_path):
    initialize_data_dir(tmp_path)
    cp = IngestCheckpoint(tmp_path, operation_id="op-y")
    cp.start_operation([])
    state = load_state(tmp_path)
    state["ingest"]["files_written"] = "corrupted"
    write_state(tmp_path, state)
    cp.record_written_file(tmp_path / "topics" / "a" / "note.md")
    state2 = load_state(tmp_path)
    assert len(state2["ingest"]["files_written"]) == 1


# IngestCheckpoint.record_written_file: duplicate file not added (line 232->234)

def test_record_written_file_does_not_duplicate(tmp_path):
    initialize_data_dir(tmp_path)
    cp = IngestCheckpoint(tmp_path, operation_id="op-z")
    cp.start_operation([])
    note_path = tmp_path / "topics" / "a" / "note.md"
    cp.record_written_file(note_path)
    cp.record_written_file(note_path)  # second call should not duplicate
    state = load_state(tmp_path)
    assert len(state["ingest"]["files_written"]) == 1


# IngestCheckpoint._current: checkpoint missing → raises IngestError (line 270)

def test_checkpoint_current_raises_when_missing(tmp_path):
    initialize_data_dir(tmp_path)
    cp = IngestCheckpoint(tmp_path, operation_id="nonexistent-op")
    with pytest.raises(IngestError, match="checkpoint for operation"):
        cp._current()


# _read_json_file: file not found → returns None (lines 285-286)

def test_read_json_file_returns_none_for_missing(tmp_path):
    result = _read_json_file(tmp_path / "nonexistent.json")
    assert result is None


# _read_json_file: bad JSON → returns None (lines 285-286)

def test_read_json_file_returns_none_for_corrupt(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{{not json", encoding="utf-8")
    result = _read_json_file(path)
    assert result is None


# _lock_is_active: different hostname → True (line 293)

def test_lock_is_active_different_hostname():
    result = _lock_is_active({"hostname": "other-machine.example.com", "pid": 12345})
    assert result is True


# _lock_is_active: non-integer pid → False (lines 296-297)

def test_lock_is_active_non_integer_pid():
    result = _lock_is_active({"hostname": socket.gethostname(), "pid": "not-a-number"})
    assert result is False


# _lock_is_active: zero pid → False (line 299)

def test_lock_is_active_zero_pid():
    result = _lock_is_active({"hostname": socket.gethostname(), "pid": 0})
    assert result is False


# _lock_is_active: PermissionError from os.kill → True (process exists, not ours) (lines 304-305)

def test_lock_is_active_permission_error_means_active():
    with patch("kb_librarian.ingest_recovery.os.kill", side_effect=PermissionError("not owner")):
        result = _lock_is_active({"hostname": socket.gethostname(), "pid": 12345})
    assert result is True
