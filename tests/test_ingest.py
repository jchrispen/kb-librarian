from __future__ import annotations

import json
from pathlib import Path

from kb_librarian.config import default_config, write_config_file
from kb_librarian.ingest import ingest, parse_ingest_file
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import read_note


def mock_config(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "mock-extract"}
    config["operations"]["classify"] = {"provider": "mock", "model": "mock-classify"}
    return config


def configure_mock_provider(data_dir: Path) -> dict[str, object]:
    config = mock_config(data_dir)
    write_config_file(data_dir / ".kb" / "config.yaml", config)
    return config


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
