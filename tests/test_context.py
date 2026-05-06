from __future__ import annotations

from datetime import date
from pathlib import Path

from kb_librarian.config import default_config
from kb_librarian.context import build_context
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note


def _configure_mock_synthesis(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    config["operations"]["synthesize"] = {"provider": "mock", "model": "mock-synthesize"}
    return config


def _seed_note(
    data_dir: Path,
    *,
    note_id: str,
    title: str,
    summary: str,
    knowledge_type: str,
    status: str,
    confidence: str,
    topic: str = "agent-systems",
    retrieval_phrases: list[str] | None = None,
    staleness_risk: str | None = None,
) -> None:
    today = date.today().isoformat()
    frontmatter = {
        "id": note_id,
        "title": title,
        "summary": summary,
        "topic": topic,
        "created": today,
        "updated": today,
        "knowledge_type": knowledge_type,
        "status": status,
        "confidence": confidence,
        "retrieval_phrases": retrieval_phrases or [title.lower()],
        "tags": ["agent", "context"],
    }
    if staleness_risk is not None:
        frontmatter["staleness_risk"] = staleness_risk
    note = Note(
        frontmatter=frontmatter,
        body="## Core idea\n\nUse compact task context for agents.\n",
    )
    path = data_dir / "topics" / topic / f"{note_id}.md"
    write_note(path, note)


def test_build_context_filters_archived_and_prefers_mode_types(tmp_path):
    initialize_data_dir(tmp_path)
    _seed_note(
        tmp_path,
        note_id="2026-05-05-coding-technique",
        title="CLI coding retrieval flow",
        summary="Technique for coding task context.",
        knowledge_type="technique",
        status="active",
        confidence="high",
        retrieval_phrases=["coding task context", "retrieval flow"],
    )
    _seed_note(
        tmp_path,
        note_id="2026-05-05-architecture-decision",
        title="Architecture boundary decision",
        summary="Decision about retrieval architecture.",
        knowledge_type="decision",
        status="active",
        confidence="high",
        retrieval_phrases=["retrieval architecture", "boundary decision"],
    )
    _seed_note(
        tmp_path,
        note_id="2026-05-05-archived-hit",
        title="CLI coding retrieval flow",
        summary="Archived duplicate.",
        knowledge_type="technique",
        status="archived",
        confidence="high",
    )
    reindex_data_dir(tmp_path)

    result = build_context(
        tmp_path,
        config=_configure_mock_synthesis(tmp_path),
        task="coding retrieval flow",
        mode="coding",
        budget=900,
    )

    selected_ids = [item.note_id for item in result.selected_notes]
    assert "2026-05-05-archived-hit" not in selected_ids
    assert selected_ids
    assert selected_ids[0] == "2026-05-05-coding-technique"
    assert "## Directly relevant techniques" in result.synthesis_markdown


def test_build_context_applies_budget_trimming(tmp_path):
    initialize_data_dir(tmp_path)
    for index in range(1, 7):
        _seed_note(
            tmp_path,
            note_id=f"2026-05-05-architecture-note-{index}",
            title=f"Architecture review pattern {index}",
            summary="Useful architecture review guidance for agent context retrieval.",
            knowledge_type="pattern",
            status="active",
            confidence="high",
            topic="architecture",
            retrieval_phrases=["architecture review", "context retrieval"],
        )
    reindex_data_dir(tmp_path)

    result = build_context(
        tmp_path,
        config=_configure_mock_synthesis(tmp_path),
        task="architecture review context retrieval",
        mode="architecture",
        budget=260,
    )

    assert len(result.selected_notes) <= 2
    for item in result.selected_notes:
        assert len(item.excerpt.split()) < 120


def test_build_context_no_results_returns_guidance(tmp_path):
    initialize_data_dir(tmp_path)
    _seed_note(
        tmp_path,
        note_id="2026-05-05-agent-technique",
        title="Agent retrieval contract",
        summary="Use task-shaped retrieval.",
        knowledge_type="technique",
        status="active",
        confidence="high",
    )
    reindex_data_dir(tmp_path)

    result = build_context(
        tmp_path,
        config=_configure_mock_synthesis(tmp_path),
        task="unrelated quantum gardening phrase",
        mode="research",
        budget=800,
    )

    assert result.selected_notes == []
    assert result.message is not None
    assert "kb search" in result.message
