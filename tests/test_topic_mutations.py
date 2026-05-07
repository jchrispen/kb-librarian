from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from kb_librarian.errors import KBLibrarianError
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.review import accept_review_item, review_state_path
from kb_librarian.storage import canonical_note_path, load_note_records
from kb_librarian.topic_mutations import (
    TopicMove,
    _validate_move_conflicts,
    promote_topics,
    queue_topic_merge_proposal,
    queue_topic_split_proposal,
    rename_topic,
)


def _write_note(
    data_dir: Path,
    *,
    note_id: str,
    title: str,
    topic: str,
    summary: str = "Summary text.",
) -> None:
    today = date.today().isoformat()
    note = Note(
        {
            "id": note_id,
            "title": title,
            "summary": summary,
            "topic": topic,
            "created": today,
            "updated": today,
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": [title.lower()],
            "tags": [topic],
        },
        "## Core idea\n\nMove this note safely.\n",
    )
    write_note(canonical_note_path(data_dir, topic, note_id), note)


def test_rename_topic_moves_subtree_frontmatter_and_indexes(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-05-07-agent-root",
        title="Agent root",
        topic="agent-systems",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-07-agent-child",
        title="Agent child",
        topic="agent-systems/retrieval",
    )

    result = rename_topic(tmp_path, old_topic="agent-systems", new_topic="coding/agents")

    assert len(result.moved_notes) == 2
    root_path = tmp_path / "topics" / "coding" / "agents" / "2026-05-07-agent-root.md"
    child_path = tmp_path / "topics" / "coding" / "agents" / "retrieval" / "2026-05-07-agent-child.md"
    assert root_path.is_file()
    assert child_path.is_file()
    assert not (tmp_path / "topics" / "agent-systems").exists()
    assert read_note(root_path).frontmatter["topic"] == "coding/agents"
    assert read_note(child_path).frontmatter["topic"] == "coding/agents/retrieval"
    assert (tmp_path / ".kb" / "fts.sqlite").is_file()


def test_promote_topic_moves_under_parent(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-05-07-retrieval-note",
        title="Retrieval note",
        topic="retrieval",
    )

    result = promote_topics(tmp_path, topics=["retrieval"], parent="agent-systems")

    assert len(result.moved_notes) == 1
    moved = tmp_path / "topics" / "agent-systems" / "retrieval" / "2026-05-07-retrieval-note.md"
    assert moved.is_file()
    assert read_note(moved).frontmatter["topic"] == "agent-systems/retrieval"


def test_topic_split_proposal_is_review_gated_and_accept_moves_notes(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-05-07-retrieval-flow",
        title="Retrieval flow",
        topic="mixed",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-07-review-checklist",
        title="Review checklist",
        topic="mixed",
    )

    proposal = queue_topic_split_proposal(
        tmp_path,
        topic="mixed",
        target_topics=["agent-systems/retrieval", "agent-systems/review"],
    )

    assert proposal.created is True
    assert proposal.review_item_id == "topic-" + date.today().isoformat() + "-001"
    assert (tmp_path / "topics" / "mixed" / "2026-05-07-retrieval-flow.md").is_file()
    rendered = (tmp_path / "review" / "pending-topic.md").read_text(encoding="utf-8")
    assert "## item: " + proposal.review_item_id in rendered
    assert "- kind: split" in rendered

    accepted = accept_review_item(tmp_path, proposal.review_item_id)

    assert accepted.changed is True
    assert "moved 2 note" in accepted.message
    assert (tmp_path / "topics" / "agent-systems" / "retrieval" / "2026-05-07-retrieval-flow.md").is_file()
    assert (tmp_path / "topics" / "agent-systems" / "review" / "2026-05-07-review-checklist.md").is_file()
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["items"][0]["status"] == "accepted"


def test_topic_merge_proposal_accept_moves_notes(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-05-07-alpha-note",
        title="Alpha note",
        topic="alpha",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-07-beta-note",
        title="Beta note",
        topic="beta",
    )

    proposal = queue_topic_merge_proposal(
        tmp_path,
        left_topic="alpha",
        right_topic="beta",
        target_topic="combined",
    )
    accept_review_item(tmp_path, proposal.review_item_id)

    records = load_note_records(tmp_path)
    assert {record.topic_path for record in records} == {"combined"}
    assert sorted(record.note_id for record in records) == [
        "2026-05-07-alpha-note",
        "2026-05-07-beta-note",
    ]


def test_topic_move_conflict_detection_reports_destination_path():
    destination = Path("/tmp/kb/topics/new/2026-05-07-a.md")
    moves = [
        TopicMove(
            note_id="2026-05-07-a",
            old_topic="old-a",
            new_topic="new",
            source_path=Path("/tmp/kb/topics/old-a/2026-05-07-a.md"),
            destination_path=destination,
        ),
        TopicMove(
            note_id="2026-05-07-b",
            old_topic="old-b",
            new_topic="new",
            source_path=Path("/tmp/kb/topics/old-b/2026-05-07-b.md"),
            destination_path=destination,
        ),
    ]

    with pytest.raises(KBLibrarianError, match="Multiple notes would move"):
        _validate_move_conflicts(moves)


def test_topic_mutation_requires_clean_git_worktree_unless_forced(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not available")

    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-05-07-git-note",
        title="Git note",
        topic="git-topic",
    )
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "initial kb")
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(KBLibrarianError, match="clean git worktree"):
        rename_topic(tmp_path, old_topic="git-topic", new_topic="renamed")

    result = rename_topic(tmp_path, old_topic="git-topic", new_topic="renamed", force=True)

    assert len(result.moved_notes) == 1
    assert (tmp_path / "topics" / "renamed" / "2026-05-07-git-note.md").is_file()


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)
