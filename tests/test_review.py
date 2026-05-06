from __future__ import annotations

import json
import re

import pytest

from kb_librarian.review import (
    ReviewStateError,
    accept_review_item,
    add_review_item,
    collect_review_summary,
    defer_review_item,
    explain_review_item,
    reject_review_item,
    review_state_path,
    validate_review_item,
)


def test_review_item_schema_validation_requires_core_fields():
    with pytest.raises(ReviewStateError):
        validate_review_item({"id": "classification-2026-05-04-001"})


def test_add_review_item_allocates_stable_queue_ids_and_deduplicates(tmp_path):
    first = add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate A",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate A", "confidence": "low"},
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-a",
    )
    second = add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate B",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate B", "confidence": "low"},
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-b",
    )
    repeated = add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate A",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate A", "confidence": "low"},
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-a",
    )

    assert first == "classification-2026-05-04-001"
    assert second == "classification-2026-05-04-002"
    assert repeated is None

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert len(state["items"]) == 2
    rendered = (tmp_path / "review" / "pending-classification.md").read_text(encoding="utf-8")
    assert "## item: classification-2026-05-04-001" in rendered
    assert "- confidence: low" in rendered


def test_collect_review_summary_migrates_phase_1_surfaces_idempotently(tmp_path):
    (tmp_path / "review").mkdir(parents=True)
    (tmp_path / ".kb").mkdir()
    (tmp_path / "review" / "pending-classification.md").write_text(
        "# Pending Classification\n\n"
        "## item: classification-a\n\n"
        "Source: `raw/a.md`\n"
        "Reason: low confidence\n\n"
        "- title: Candidate A\n"
        "- summary: Needs a topic.\n"
        "- suggested_topic: agent-systems\n"
        "- suggested_type: technique\n"
        "- confidence: low\n\n",
        encoding="utf-8",
    )
    (tmp_path / "review" / "pending-merge.md").write_text(
        "# Pending Merge\n\n"
        "## item: merge-2026-05-04-deadbeef\n\n"
        "Source: `raw/merge.md`\n"
        "Source hash: `abc123`\n"
        "Target note IDs: 2026-05-04-existing\n"
        "Rationale: adds detail\n"
        "Suggested action: review and merge manually.\n"
        "- fingerprint: merge-fingerprint\n"
        "- candidate_title: Merge Candidate\n"
        "- candidate_body:\n"
        "```markdown\n"
        "New body.\n"
        "```\n\n",
        encoding="utf-8",
    )
    (tmp_path / "review" / "disputes.md").write_text(
        "# Disputes\n\n"
        "## item: dispute-20260504-123000\n\n"
        "Target note IDs: 2026-05-04-existing\n"
        "Rationale: contradicts prior guidance\n"
        "- candidate_title: Disputed Candidate\n\n",
        encoding="utf-8",
    )
    (tmp_path / ".kb" / "ingested.json").write_text(
        json.dumps(
            [
                {
                    "hash": "abc",
                    "source_name": "dup.md",
                    "status": "duplicate",
                    "processed_at": "2026-05-05T09:00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".kb" / "errors.log").write_text(
        "2026-05-05T09:00:01 Unsupported ingest file extension for /tmp/example.pdf\n",
        encoding="utf-8",
    )

    counts, items = collect_review_summary(tmp_path, max_items=10)
    first_state = review_state_path(tmp_path).read_text(encoding="utf-8")
    collect_review_summary(tmp_path, max_items=10)
    second_state = review_state_path(tmp_path).read_text(encoding="utf-8")

    assert sum(counts.values()) == 5
    assert [item.kind for item in items[:3]] == ["dispute", "merge", "classification"]
    assert re.search(r"classification-\d{4}-\d{2}-\d{2}-001", first_state)
    assert "merge-2026-05-04-001" in first_state
    assert "dispute-2026-05-04-001" in first_state
    assert first_state == second_state

    pending_merge = (tmp_path / "review" / "pending-merge.md").read_text(encoding="utf-8")
    assert "Generated from review/review-items.json" in pending_merge
    assert "candidate_title: Merge Candidate" in pending_merge
    assert (tmp_path / "review" / "search-misses.md").is_file()


def test_explain_review_item_and_unknown_id(tmp_path):
    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate A",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate A", "confidence": "low"},
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-a",
    )

    explained = explain_review_item(tmp_path, "classification-2026-05-04-001")
    assert "Review item: classification-2026-05-04-001" in explained
    assert "status: pending" in explained

    with pytest.raises(ReviewStateError):
        explain_review_item(tmp_path, "missing-id")


def test_deferred_items_hidden_until_due(tmp_path):
    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate A",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate A", "confidence": "low"},
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-a",
    )
    item_id = "classification-2026-05-04-001"

    deferred = defer_review_item(tmp_path, item_id, days=30)
    assert deferred.changed
    assert "Deferred until" in deferred.message

    counts, items = collect_review_summary(tmp_path, max_items=10)
    assert sum(counts.values()) == 0
    assert items == []

    counts_all, items_all = collect_review_summary(tmp_path, max_items=10, include_deferred=True)
    assert sum(counts_all.values()) == 1
    assert len(items_all) == 1


def test_reject_archives_item_and_is_idempotent(tmp_path):
    add_review_item(
        tmp_path,
        queue="merge",
        title="Candidate Merge",
        target_notes=["2026-05-04-existing"],
        proposed_action="review and merge manually",
        payload={
            "candidate_title": "Candidate Merge",
            "candidate_body": "body",
            "source": "raw/merge.md",
            "source_hash": "abc",
        },
        priority="medium",
        created="2026-05-04",
        fingerprint="merge-a",
    )

    first = reject_review_item(tmp_path, "merge-2026-05-04-001")
    second = reject_review_item(tmp_path, "merge-2026-05-04-001")

    assert first.changed is True
    assert second.changed is False
    rejected_path = tmp_path / "review" / "rejected" / "merge-2026-05-04-001.md"
    assert rejected_path.is_file()
    assert "## item: merge-2026-05-04-001" in rejected_path.read_text(encoding="utf-8")


def test_accept_classification_creates_note(tmp_path):
    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate A",
        target_notes=[],
        proposed_action="review classification manually",
        payload={
            "title": "Candidate A",
            "summary": "Candidate A summary.",
            "source": "raw/a.md",
            "source_hash": "abc123",
        },
        priority="medium",
        created="2026-05-04",
        fingerprint="fingerprint-a",
    )

    accepted = accept_review_item(
        tmp_path,
        "classification-2026-05-04-001",
        topic="agent-systems",
        knowledge_type="technique",
    )
    assert accepted.changed is True
    assert "creating note" in accepted.message

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    item = state["items"][0]
    assert item["status"] == "accepted"
    note_files = list((tmp_path / "topics" / "agent-systems").glob("*.md"))
    assert len(note_files) == 1
    text = note_files[0].read_text(encoding="utf-8")
    assert "knowledge_type: technique" in text
    assert "Candidate A summary." in text


def test_accept_merge_requires_append_flag_and_is_idempotent(tmp_path):
    topic_dir = tmp_path / "topics" / "agent-systems"
    topic_dir.mkdir(parents=True)
    note_path = topic_dir / "2026-05-04-existing.md"
    note_path.write_text(
        "---\n"
        "id: 2026-05-04-existing\n"
        "title: Existing Note\n"
        "summary: Existing summary.\n"
        "topic: agent-systems\n"
        "created: 2026-05-04\n"
        "updated: 2026-05-04\n"
        "knowledge_type: technique\n"
        "status: active\n"
        "confidence: medium\n"
        "retrieval_phrases:\n"
        "- existing note\n"
        "tags:\n"
        "- agent-systems\n"
        "---\n"
        "\n"
        "Original body.\n",
        encoding="utf-8",
    )
    add_review_item(
        tmp_path,
        queue="merge",
        title="Candidate Merge",
        target_notes=["2026-05-04-existing"],
        proposed_action="review and merge manually",
        payload={
            "candidate_title": "Candidate Merge",
            "candidate_body": "Merged body line.",
            "source": "raw/merge.md",
            "source_hash": "abc",
        },
        priority="medium",
        created="2026-05-04",
        fingerprint="merge-a",
    )

    with pytest.raises(ReviewStateError):
        accept_review_item(tmp_path, "merge-2026-05-04-001")

    accepted = accept_review_item(tmp_path, "merge-2026-05-04-001", append_body=True)
    accepted_again = accept_review_item(tmp_path, "merge-2026-05-04-001", append_body=True)
    text = note_path.read_text(encoding="utf-8")
    assert accepted.changed is True
    assert accepted_again.changed is False
    assert text.count("Accepted Merge (merge-2026-05-04-001)") == 1


def test_accept_dispute_marks_reviewed(tmp_path):
    topic_dir = tmp_path / "topics" / "agent-systems"
    topic_dir.mkdir(parents=True)
    note_path = topic_dir / "2026-05-04-existing.md"
    note_path.write_text(
        "---\n"
        "id: 2026-05-04-existing\n"
        "title: Existing Note\n"
        "summary: Existing summary.\n"
        "topic: agent-systems\n"
        "created: 2026-05-04\n"
        "updated: 2026-05-04\n"
        "knowledge_type: heuristic\n"
        "status: disputed\n"
        "confidence: medium\n"
        "retrieval_phrases:\n"
        "- existing note\n"
        "tags:\n"
        "- agent-systems\n"
        "---\n"
        "\n"
        "Original body.\n",
        encoding="utf-8",
    )
    add_review_item(
        tmp_path,
        queue="dispute",
        title="Dispute Candidate",
        target_notes=["2026-05-04-existing"],
        proposed_action="review contradiction manually",
        payload={"candidate_title": "Dispute Candidate"},
        priority="high",
        created="2026-05-04",
        fingerprint="dispute-a",
    )

    accepted = accept_review_item(tmp_path, "dispute-2026-05-04-001")
    text = note_path.read_text(encoding="utf-8")
    assert accepted.changed is True
    assert "reviewed_by_user: true" in text
    assert "review_item_id: dispute-2026-05-04-001" in text


def test_accept_unsupported_queue_raises(tmp_path):
    add_review_item(
        tmp_path,
        queue="duplicate",
        title="dup.md",
        target_notes=[],
        proposed_action="inspect duplicate source",
        payload={"source_path": "raw/dup.md"},
        priority="low",
        created="2026-05-04",
        fingerprint="dup-a",
    )
    with pytest.raises(ReviewStateError):
        accept_review_item(tmp_path, "duplicate-2026-05-04-001")
