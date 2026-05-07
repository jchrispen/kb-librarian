from __future__ import annotations

import json
from pathlib import Path

import pytest

from kb_librarian.config import default_config, write_config_file
from kb_librarian.errors import ProviderError
from kb_librarian.ingest import ingest, ingest_report_payload, parse_ingest_file, render_report
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.providers import MockProvider
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


def _write_minimal_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\n"
            b"endobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for item in objects:
        offsets.append(len(payload))
        payload.extend(item)
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(payload))


def test_parse_ingest_file_normalizes_text(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("# Heading\r\n\r\nBody\r\n", encoding="utf-8")

    parsed = parse_ingest_file(source)

    assert parsed.text == "# Heading\n\nBody\n"
    assert len(parsed.digest) == 64
    assert parsed.source_metadata == {"format": "md"}


def test_parse_ingest_file_extracts_html_text_and_metadata(tmp_path):
    source = tmp_path / "capture.html"
    source.write_text(
        "<!doctype html><html><head>"
        "<title>HTML ingest parsing</title>"
        "<link rel='canonical' href='https://example.test/capture'>"
        "</head><body>"
        "<nav>navigation should be ignored</nav>"
        "<h1>HTML ingest parsing</h1>"
        "<p>Visible guidance for coding agents.</p>"
        "<p hidden>hidden material</p>"
        "<a href='/details'>Details page</a>"
        "<script>ignored()</script>"
        "</body></html>",
        encoding="utf-8",
    )

    parsed = parse_ingest_file(source)

    assert "# HTML ingest parsing" in parsed.text
    assert "Visible guidance for coding agents." in parsed.text
    assert "navigation should be ignored" not in parsed.text
    assert "hidden material" not in parsed.text
    assert parsed.source_metadata["format"] == "html"
    assert parsed.source_metadata["title"] == "HTML ingest parsing"
    assert parsed.source_metadata["source_url"] == "https://example.test/capture"
    assert parsed.source_metadata["links"] == [
        {"text": "Details page", "href": "https://example.test/details"}
    ]


def test_parse_ingest_file_extracts_pdf_text_and_page_metadata(tmp_path):
    pytest.importorskip("pypdf")
    source = tmp_path / "capture.pdf"
    _write_minimal_pdf(source, "PDF ingest parsing keeps page metadata for agents.")

    parsed = parse_ingest_file(source)

    assert "[PDF page 1]" in parsed.text
    assert "PDF ingest parsing keeps page metadata for agents." in parsed.text
    assert parsed.source_metadata["format"] == "pdf"
    assert parsed.source_metadata["page_count"] == 1
    assert parsed.source_metadata["pages"] == [1]


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
    assert report.classification_items[0].startswith("classification-")
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
    assert len(second_report.duplicate_items) == 1
    assert second_report.duplicate_items[0].startswith("duplicate-")
    assert second_report.processed_files == 0
    assert "duplicates" in second_report.archived_paths[0].parts
    entries = json.loads((tmp_path / ".kb" / "ingested.json").read_text(encoding="utf-8"))
    assert entries[-1]["status"] == "duplicate"


def test_ingest_unsupported_file_logs_and_leaves_raw_file(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "capture.bin"
    source.write_text("not a pdf for this test", encoding="utf-8")

    report = ingest(tmp_path, config=config)

    assert report.unsupported_files == 1
    assert len(report.unsupported_file_items) == 1
    assert report.unsupported_file_items[0].startswith("unsupported-")
    assert report.errors == 0
    assert source.exists()
    assert "Unsupported ingest file extension" in (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")


def test_ingest_raw_html_preserves_source_metadata_in_note(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "agent-context.html"
    source.write_text(
        "<html><head>"
        "<title>Agent context from HTML</title>"
        "<meta property='og:url' content='https://example.test/agent-context'>"
        "</head><body>"
        "<h1>Agent context from HTML</h1>"
        "<p>Prefer task-shaped context for coding agents instead of broad browsing.</p>"
        "<a href='https://example.test/ref'>Reference</a>"
        "</body></html>",
        encoding="utf-8",
    )

    report = ingest(tmp_path, config=config)

    assert report.processed_files == 1
    assert len(report.created_notes) == 1
    note_path = next((tmp_path / "topics").rglob(f"{report.created_notes[0]}.md"))
    note = read_note(note_path)
    source_entry = note.frontmatter["sources"][0]
    assert source_entry["ref"].endswith("raw/agent-context.html")
    assert source_entry["format"] == "html"
    assert source_entry["title"] == "Agent context from HTML"
    assert source_entry["source_url"] == "https://example.test/agent-context"
    assert source_entry["links"] == [{"href": "https://example.test/ref", "text": "Reference"}]


def test_ingest_raw_pdf_preserves_page_metadata_in_note(tmp_path):
    pytest.importorskip("pypdf")
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "agent-context.pdf"
    _write_minimal_pdf(source, "PDF agent context parsing helps coding agents preserve provenance.")

    report = ingest(tmp_path, config=config)

    assert report.processed_files == 1
    assert len(report.created_notes) == 1
    note_path = next((tmp_path / "topics").rglob(f"{report.created_notes[0]}.md"))
    note = read_note(note_path)
    source_entry = note.frontmatter["sources"][0]
    assert source_entry["ref"].endswith("raw/agent-context.pdf")
    assert source_entry["format"] == "pdf"
    assert source_entry["page_count"] == 1
    assert source_entry["pages"] == [1]


def test_ingest_parser_failure_logs_review_item_and_leaves_raw_file(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "broken.pdf"
    source.write_text("not actually a pdf", encoding="utf-8")

    report = ingest(tmp_path, config=config)

    assert report.errors == 1
    assert report.processed_files == 0
    assert len(report.parser_failure_items) == 1
    assert report.parser_failure_items[0].startswith("parser-")
    assert source.exists()
    assert "broken.pdf" in (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    review = (tmp_path / "review" / "parser-failures.md").read_text(encoding="utf-8")
    assert "broken.pdf" in review
    assert "Suggested action: fix source extraction or replace the raw file." in review


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
    assert first.merge_proposals[0].startswith("merge-")
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
    assert len(report.dispute_items) == 1
    assert report.dispute_items[0].startswith("dispute-")
    disputed = read_note(note_path)
    assert disputed.frontmatter["status"] == "disputed"
    assert disputed.frontmatter["disputes"]
    disputes_doc = (tmp_path / "review" / "disputes.md").read_text(encoding="utf-8")
    assert "Target note IDs: 2026-05-04-cli-context-retrieval" in disputes_doc


def test_ingest_report_payload_and_human_output_align(tmp_path):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    source = tmp_path / "raw" / "ambiguous.md"
    source.write_text("# Ambiguous idea\n\nThis ambiguous agent note needs classification.\n", encoding="utf-8")

    report = ingest(tmp_path, config=config)
    payload = ingest_report_payload(report)
    human = render_report(report)

    assert payload["classification_items"]["count"] == 1
    assert payload["classification_items"]["review_item_ids"] == report.classification_items
    assert payload["review_item_ids"] == report.classification_items
    assert f"classification_items: {payload['classification_items']['count']}" in human
    assert "classification_review_item_ids:" in human
    assert report.classification_items[0] in human


def test_ingest_retries_transient_provider_failure_and_logs_attempts(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    config["providers"]["retry"] = {
        "max_attempts": 3,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "jitter_seconds": 0.0,
    }
    source = tmp_path / "raw" / "retry.md"
    source.write_text(
        "# Retryable provider call\n\nPrefer task-shaped context for coding agents.\n",
        encoding="utf-8",
    )

    class FlakyProvider(MockProvider):
        def __init__(self) -> None:
            self.extract_calls = 0

        def extract_candidates(self, **kwargs):  # type: ignore[override]
            self.extract_calls += 1
            if self.extract_calls == 1:
                raise ProviderError("service unavailable")
            return super().extract_candidates(**kwargs)

    flaky = FlakyProvider()
    monkeypatch.setattr("kb_librarian.ingest.provider_from_config", lambda *args, **kwargs: flaky)

    report = ingest(tmp_path, config=config)

    assert report.errors == 0
    assert report.processed_files == 1
    assert len(report.created_notes) == 1
    assert flaky.extract_calls == 2
    errors_log = (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    assert "stage=provider-retry" in errors_log
    assert "phase=extract" in errors_log


def test_ingest_final_provider_failure_is_resumable(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = configure_mock_provider(tmp_path)
    config["providers"]["retry"] = {
        "max_attempts": 2,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "jitter_seconds": 0.0,
    }
    source = tmp_path / "raw" / "resume-after-provider-failure.md"
    source.write_text(
        "# Resume after provider failure\n\nPrefer task-shaped context for coding agents.\n",
        encoding="utf-8",
    )

    class FailingProvider(MockProvider):
        def extract_candidates(self, **kwargs):  # type: ignore[override]
            raise ProviderError("service unavailable")

    monkeypatch.setattr("kb_librarian.ingest.provider_from_config", lambda *args, **kwargs: FailingProvider())
    failed = ingest(tmp_path, config=config)

    assert failed.errors == 1
    assert failed.processed_files == 0
    assert source.exists()
    state = json.loads((tmp_path / ".kb" / "state.json").read_text(encoding="utf-8"))
    assert state["ingest"]["status"] == "error"
    errors_log = (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    assert "stage=provider-final" in errors_log

    monkeypatch.setattr("kb_librarian.ingest.provider_from_config", lambda *args, **kwargs: MockProvider())
    resumed = ingest(tmp_path, config=config, resume=True)

    assert resumed.errors == 0
    assert resumed.processed_files == 1
    assert len(resumed.created_notes) == 1
    assert not source.exists()
