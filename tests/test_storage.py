from __future__ import annotations

from pathlib import Path

import pytest

from kb_librarian.errors import DuplicateNoteIdError
from kb_librarian.notes import Note
from kb_librarian.storage import (
    NoteRecord,
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    normalize_topic_for_path,
)


def _frontmatter(note_id: str, topic: str) -> dict[str, object]:
    return {
        "id": note_id,
        "title": "Title",
        "summary": "Summary",
        "topic": topic,
        "created": "2026-05-05",
        "updated": "2026-05-05",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": [],
        "tags": [],
    }


def test_topic_normalization_and_canonical_path(tmp_path):
    assert normalize_topic_for_path(" Agent Systems / Design ") == "agent-systems-design"

    path = canonical_note_path(tmp_path, "Agent Systems / Design", "2026-05-05-cli-contract")
    assert path == tmp_path / "topics" / "agent-systems-design" / "2026-05-05-cli-contract.md"


def test_ensure_topic_layout_creates_scope_file(tmp_path):
    created = ensure_topic_layout(tmp_path, "Agent Systems")
    assert tmp_path.joinpath("topics", "agent-systems").is_dir()
    assert tmp_path.joinpath("topics", "agent-systems", "scope.txt").is_file()
    assert created


def test_duplicate_note_id_detection():
    record_one = NoteRecord(
        path=canonical_note_path(
            Path("/tmp"),
            "one",
            "2026-05-05-duplicate",
        ),
        topic_path="one",
        note=Note(_frontmatter("2026-05-05-duplicate", "one"), "body\n"),
    )
    record_two = NoteRecord(
        path=canonical_note_path(
            Path("/tmp"),
            "two",
            "2026-05-05-duplicate",
        ),
        topic_path="two",
        note=Note(_frontmatter("2026-05-05-duplicate", "two"), "body\n"),
    )

    with pytest.raises(DuplicateNoteIdError, match="2026-05-05-duplicate"):
        ensure_unique_note_ids([record_one, record_two])
