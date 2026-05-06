from __future__ import annotations

import json
import re

import pytest

from kb_librarian.review import (
    ReviewStateError,
    add_review_item,
    collect_review_summary,
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
