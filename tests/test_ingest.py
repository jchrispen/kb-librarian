from __future__ import annotations

import json
from pathlib import Path

from kb_librarian.config import default_config, write_config_file
from kb_librarian.ingest import ingest, parse_ingest_file
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.storage import canonical_note_path, ensure_topic_layout


def mock_config(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "mock-extract"}
    config["operations"]["classify"] = {"provider": "mock", "model": "mock-classify"}
    config["operations"]["integrate"] = {"provider": "mock", "model": "mock-integrate"}
    return config


def configure_mock_provider(data_dir: Path) -> dict[str, object]:
    config = mock_config(data_dir)
    write_config_file(data_dir / ".kb" / "config.yaml", config)
    return config


def _seed_note(data_dir: Path, *, topic: str, note_id: str, title: str, body: str) -> Path:
    ensure_topic_layout(data_dir, topic)
    note = Note(
        frontmatter={
            "id": note_id,
            "title": title,
            "summary": "Existing note summary.",
            "topic": topic,
            "created": "2026-05-04",
            "updated": "2026-05-04",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "sources": [{"type": "ingest", "ref": "raw/seed.md", "hash": "seed-hash"}],
            "retrieval_phrases": ["task-shaped context", "cli context retrieval"],
            "tags": ["agent-systems"],
            "disputes": [],
        },
        body=body,
    )
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, note)
    return path


def test_parse_ingest_file_normalizes_text(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("# Heading\r\n\r\nBody\r\n", encoding="utf-8")

    parsed = parse_ingest_file(source)

    assert parsed.text == "# Heading\n\nBody\n"
    assert len(parsed.digest) == 64


def test_ingest_raw_markdown_with_mock_provider_creates_note_and_archives(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "agent-context.md"
    source.write_text(
        "# CLI context retrieval\n\nPrefer task-shaped context for coding agents instead of broad browsing.\n",
        encoding="utf-8",
    )

    report = ingest(tmp_path, config=config)

    assert report.processed_files == 1
    assert len(report.created_notes) == 1
    assert report.errors == 0
    assert not source.exists()
    assert report.archived_paths[0].parts[-4:-1] == ("raw", "processed", report.archived_paths[0].parent.name)
    note_path = next((tmp_path / "topics").rglob(f"{report.created_notes[0]}.md"))
    note = read_note(note_path)
    assert note.frontmatter["topic"] == "agent-systems"
    assert note.frontmatter["sources"][0]["ref"].endswith("raw/agent-context.md")
    assert (tmp_path / ".kb" / "fts.sqlite").is_file()


def test_ingest_ambiguous_candidate_goes_to_pending_classification(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "ambiguous.md"
    source.write_text("# Ambiguous idea\n\nThis ambiguous agent note needs classification.\n", encoding="utf-8")

    report = ingest(tmp_path, config=config)

    assert report.created_notes == []
    assert len(report.classification_items) == 1
    review = (tmp_path / "review" / "pending-classification.md").read_text(encoding="utf-8")
    assert "Ambiguous idea" in review
    assert "confidence: low" in review
    assert not source.exists()


def test_ingest_duplicate_success_hash_archives_to_duplicates(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    payload = "# CLI context retrieval\n\nPrefer task-shaped context for coding agents.\n"
    first = tmp_path / "raw" / "item.md"
    first.write_text(payload, encoding="utf-8")
    first_report = ingest(tmp_path, config=config)
    assert first_report.processed_files == 1

    second = tmp_path / "raw" / "item.md"
    second.write_text(payload, encoding="utf-8")
    second_report = ingest(tmp_path, config=config)

    assert second_report.duplicates == 1
    assert second_report.processed_files == 0
    assert "duplicates" in second_report.archived_paths[0].parts
    entries = json.loads((tmp_path / ".kb" / "ingested.json").read_text(encoding="utf-8"))
    assert entries[-1]["status"] == "duplicate"


def test_ingest_unsupported_file_logs_and_leaves_raw_file(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "capture.pdf"
    source.write_text("not a pdf for this test", encoding="utf-8")

    report = ingest(tmp_path, config=config)

    assert report.unsupported_files == 1
    assert report.errors == 0
    assert source.exists()
    assert "Unsupported ingest file extension" in (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")


def test_ingest_identical_appends_source_without_rewriting_body(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    note_path = _seed_note(
        tmp_path,
        topic="agent-systems",
        note_id="2026-05-04-cli-context-retrieval",
        title="CLI context retrieval",
        body="## Core idea\n\nOriginal body stays unchanged.\n",
    )
    source = tmp_path / "raw" / "identical.md"
    source.write_text(
        "# CLI context retrieval\n\nThis is identical guidance for agents.\n",
        encoding="utf-8",
    )

    report = ingest(tmp_path, config=config)

    assert report.created_notes == []
    assert report.source_appends == ["2026-05-04-cli-context-retrieval"]
    updated = read_note(note_path)
    assert "Original body stays unchanged." in updated.body
    assert len(updated.frontmatter["sources"]) == 2

    source = tmp_path / "raw" / "identical.md"
    source.write_text(
        "# CLI context retrieval\n\nThis is identical guidance for agents.\n",
        encoding="utf-8",
    )
    repeat = ingest(tmp_path, config=config, force=True)
    assert repeat.source_appends == []
    repeated_note = read_note(note_path)
    assert len(repeated_note.frontmatter["sources"]) == 2


def test_ingest_adds_nuance_queues_merge_idempotently(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    _seed_note(
        tmp_path,
        topic="agent-systems",
        note_id="2026-05-04-cli-context-retrieval",
        title="CLI context retrieval",
        body="## Core idea\n\nOriginal note.\n",
    )
    source = tmp_path / "raw" / "nuance.md"
    source.write_text(
        "# CLI context retrieval\n\nThis adds nuance to the existing guidance.\n",
        encoding="utf-8",
    )

    first = ingest(tmp_path, config=config)
    assert len(first.merge_proposals) == 1
    pending_merge = (tmp_path / "review" / "pending-merge.md").read_text(encoding="utf-8")
    assert "candidate_title: CLI context retrieval" in pending_merge

    source = tmp_path / "raw" / "nuance.md"
    source.write_text(
        "# CLI context retrieval\n\nThis adds nuance to the existing guidance.\n",
        encoding="utf-8",
    )
    second = ingest(tmp_path, config=config, force=True)
    assert second.merge_proposals == []


def test_ingest_contradicts_marks_note_disputed_and_writes_review(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    note_path = _seed_note(
        tmp_path,
        topic="python",
        note_id="2026-05-04-cli-context-retrieval",
        title="CLI context retrieval",
        body="## Core idea\n\nPrefer compact retrieval.\n",
    )
    source = tmp_path / "raw" / "conflict.md"
    source.write_text(
        "# CLI context retrieval\n\nThis contradicts the prior guidance.\n",
        encoding="utf-8",
    )

    report = ingest(tmp_path, config=config)

    assert report.disputes == ["2026-05-04-cli-context-retrieval"]
    disputed = read_note(note_path)
    assert disputed.frontmatter["status"] == "disputed"
    assert disputed.frontmatter["disputes"]
    disputes_doc = (tmp_path / "review" / "disputes.md").read_text(encoding="utf-8")
    assert "Target note IDs: 2026-05-04-cli-context-retrieval" in disputes_doc
