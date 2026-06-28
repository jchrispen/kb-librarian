from __future__ import annotations

import json
from pathlib import Path

import pytest

from kb_librarian.config import default_config
from kb_librarian.errors import IngestError, ProviderError
from kb_librarian.ingest import (
    IngestReport,
    ParsedInput,
    _append_source_to_note,
    _archive_if_raw_file,
    _candidate_matches,
    _coerce_string_list,
    _dedupe_path,
    _ensure_trailing_newline,
    _excerpt,
    _find_existing_note_for_source,
    _ingest_one,
    _load_ingested,
    _log_error,
    _mark_disputed,
    _needs_classification_review,
    _parent_topic,
    _provider_call,
    _record_ingest,
    _refuse_unresolved_checkpoint,
    _resume_files,
    _source_entry,
    _target_records,
    ingest,
    ingest_report_payload,
    render_report,
)
from kb_librarian.ingest_recovery import IngestCheckpoint, write_state
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.provider_retry import FailureClassification, RetryEvent
from kb_librarian.providers import (
    CandidateNote,
    ClassificationResult,
    ExtractionResult,
    IntegrationResult,
    ProviderFallbackEvent,
)
from kb_librarian.storage import canonical_note_path, ensure_topic_layout, load_note_records


def _note(
    data_dir: Path,
    *,
    note_id: str = "2026-01-01-coverage-note",
    title: str = "Coverage Note",
    topic: str = "agent-systems",
    body: str = "## Core idea\n\nCoverage body.\n",
    sources: object | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    ensure_topic_layout(data_dir, topic)
    frontmatter: dict[str, object] = {
        "id": note_id,
        "title": title,
        "summary": "Coverage summary.",
        "topic": topic,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": ["coverage phrase"],
        "tags": ["agent-systems"],
        "sources": [] if sources is None else sources,
        "disputes": [],
    }
    if extra:
        frontmatter.update(extra)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, Note(frontmatter=frontmatter, body=body))
    return path


def _candidate(**overrides: object) -> CandidateNote:
    values = {
        "title": "Coverage Note",
        "summary": "Coverage candidate summary.",
        "knowledge_type": "technique",
        "body": "Body references 2026-01-01-coverage-note.",
        "retrieval_phrases": ["coverage phrase"],
        "tags": ["agent-systems"],
        "confidence": "high",
        "utility_score": "high",
    }
    values.update(overrides)
    return CandidateNote(**values)


def test_render_report_includes_all_optional_sections():
    report = IngestReport(
        operation_id="ingest-extra",
        resumed=True,
        processed_files=2,
        created_notes=["created-1"],
        source_appends=["source-1"],
        merge_proposals=["merge-1"],
        disputes=["dispute-note", "dispute-note"],
        dispute_items=["dispute-item-1"],
        classification_items=["classification-1"],
        duplicate_items=["duplicate-1"],
        unsupported_file_items=["unsupported-1"],
        parser_failure_items=["parser-1"],
        review_paths=[Path("review/z.md"), Path("review/a.md")],
        skipped_candidates=1,
        duplicates=1,
        unsupported_files=1,
        errors=1,
        warnings=["Possible updated version: changed.md"],
        archived_paths=[Path("raw/processed/2026-06/changed.md")],
    )

    payload = ingest_report_payload(report)
    rendered = render_report(report)

    assert payload["resumed"] is True
    assert payload["disputes"]["count"] == 1
    assert payload["review_paths"] == ["review/a.md", "review/z.md"]
    for expected in [
        "created_note_ids:",
        "source_appended_note_ids:",
        "merge_review_item_ids:",
        "disputed_note_ids:",
        "dispute_review_item_ids:",
        "classification_review_item_ids:",
        "duplicate_review_item_ids:",
        "unsupported_file_review_item_ids:",
        "parser_failure_review_item_ids:",
        "review_paths:",
        "archived_paths:",
        "warnings:",
        "recovery:",
    ]:
        assert expected in rendered


def test_resume_and_unresolved_checkpoint_error_branches(tmp_path):
    initialize_data_dir(tmp_path)
    write_state(
        tmp_path,
        {
            "ingest": {
                "operation_id": "ingest-stuck",
                "status": "running",
                "current_raw_file": str(tmp_path / "raw" / "missing.md"),
                "pending_files": ["", str(tmp_path / "raw" / "missing.md")],
                "last_completed_stage": "",
            }
        },
    )

    with pytest.raises(IngestError, match="operation_id=ingest-stuck"):
        _refuse_unresolved_checkpoint(tmp_path, force=False)
    _refuse_unresolved_checkpoint(tmp_path, force=True)
    with pytest.raises(IngestError, match="raw file is missing"):
        _resume_files(tmp_path)

    write_state(tmp_path, {"ingest": {"status": "finished"}})
    with pytest.raises(IngestError, match="No interrupted ingest checkpoint"):
        _resume_files(tmp_path)


def test_ingest_without_raw_dir_and_fatal_checkpoint_failure(tmp_path, monkeypatch):
    no_raw_dir = tmp_path / "no-raw"
    no_raw_dir.mkdir()
    config = default_config(no_raw_dir)

    empty = ingest(no_raw_dir, config=config)

    assert empty.processed_files == 0
    assert empty.errors == 0

    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    source = tmp_path / "raw"
    (source / "fatal.md").write_text("# Fatal\n\nFatal path.\n", encoding="utf-8")

    def fail_parse(path: Path) -> ParsedInput:
        raise KeyboardInterrupt("stop now")

    monkeypatch.setattr("kb_librarian.ingest.parse_ingest_file", fail_parse)
    with pytest.raises(KeyboardInterrupt):
        ingest(tmp_path, config=config, force=True)
    state = json.loads((tmp_path / ".kb" / "state.json").read_text(encoding="utf-8"))
    assert state["ingest"]["status"] == "error"
    assert state["ingest"]["last_completed_stage"] == "fatal"


def test_provider_call_logs_retry_final_failure_and_fallback(tmp_path, monkeypatch):
    parsed = ParsedInput(tmp_path / "raw.md", "body", "hash")

    def fake_policy(config, phase, **kwargs):  # noqa: ANN001, ANN003
        error = ProviderError("temporary outage")
        classification = FailureClassification("transient_message", True, "timeout")
        retry = RetryEvent("ingest:classify", 1, 3, 0.125, classification, error)
        final = RetryEvent("ingest:classify", 3, 3, 0.0, classification, error)
        fallback = ProviderFallbackEvent(
            "ingest:classify",
            "primary",
            "model-a",
            "secondary",
            "model-b",
            "provider_error",
            "non_transient",
            ProviderError("bad credentials"),
        )
        kwargs["on_retry"](retry)
        kwargs["on_final_failure"](final)
        kwargs["on_fallback"](fallback)
        return kwargs["call"]("provider", type("Route", (), {"model": "model-a"})())

    monkeypatch.setattr("kb_librarian.ingest.call_with_provider_policy", fake_policy)

    result = _provider_call(
        data_dir=tmp_path,
        parsed=parsed,
        operation_id="ingest-extra",
        phase="classify",
        config={},
        env={},
        retry_policy=object(),
        candidate_title="Candidate",
        call=lambda provider, route: f"{provider}:{route.model}",
    )

    log = (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    assert result == "provider:model-a"
    assert "Retrying provider phase=classify candidate='Candidate'" in log
    assert "Provider phase=classify candidate='Candidate' stopped" in log
    assert "Falling back provider phase=classify candidate='Candidate'" in log


def test_checkpointed_ingest_one_records_classification_review(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    source = tmp_path / "raw" / "review.md"
    source.write_text("# Needs review\n\nLow confidence classification.\n", encoding="utf-8")
    parsed = ParsedInput(source, source.read_text(encoding="utf-8"), "digest")
    checkpoint = IngestCheckpoint(tmp_path, operation_id="ingest-extra")
    checkpoint.start_operation([source])
    checkpoint.start_file(source)
    report = IngestReport()

    def fake_provider_call(**kwargs):  # noqa: ANN003
        if kwargs["phase"] == "extract":
            return ExtractionResult([_candidate(title="Needs review")])
        if kwargs["phase"] == "classify":
            return ClassificationResult("", "technique", "high", reason="missing topic")
        raise AssertionError(kwargs["phase"])

    monkeypatch.setattr("kb_librarian.ingest._provider_call", fake_provider_call)

    _ingest_one(tmp_path, config=default_config(tmp_path), parsed=parsed, report=report, env={}, checkpoint=checkpoint)

    assert len(report.classification_items) == 1
    assert report.review_paths == [tmp_path / "review" / "pending-classification.md"]
    state = json.loads((tmp_path / ".kb" / "state.json").read_text(encoding="utf-8"))
    assert state["ingest"]["candidate_ids"]
    assert str(tmp_path / "review" / "pending-classification.md") in state["ingest"]["files_written"]


def test_target_record_validation_errors(tmp_path):
    initialize_data_dir(tmp_path)
    _note(tmp_path)
    records = load_note_records(tmp_path, validate=True)

    with pytest.raises(ProviderError, match="not present in candidate matches"):
        _target_records([], records, IntegrationResult("identical", ["2026-01-01-coverage-note"]))
    with pytest.raises(ProviderError, match="does not exist"):
        _target_records(
            [{"note_id": "2026-01-01-missing"}],
            records,
            IntegrationResult("identical", ["2026-01-01-missing"]),
        )
    with pytest.raises(ProviderError, match="requires at least one target record"):
        _target_records([], records, IntegrationResult("adds_nuance", []))
    assert _target_records([], records, IntegrationResult("unrelated", [])) == []


def test_candidate_matches_cover_parent_explicit_lexical_and_filter_branches(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _note(tmp_path, note_id="2026-01-01-coverage-note", title="Different title", topic="agent-systems")
    _note(
        tmp_path,
        note_id="2026-01-02-lexical-only",
        title="Lexical only",
        topic="other-topic",
        extra={"tags": ["other"], "retrieval_phrases": ["other phrase"]},
    )
    (tmp_path / ".kb" / "fts.sqlite").write_text("", encoding="utf-8")
    records = load_note_records(tmp_path, validate=True)
    candidate = _candidate(
        title="Coverage candidate",
        body="See 2026-01-01-coverage-note for the parent topic.",
    )
    classification = ClassificationResult("agent-systems/context", "technique", "high")

    monkeypatch.setattr("kb_librarian.ingest.tokenize_query", lambda query: ["coverage"])
    monkeypatch.setattr(
        "kb_librarian.ingest.query_candidates",
        lambda *args, **kwargs: [
            {"id": "2026-01-01-missing"},
            {"id": "2026-01-01-coverage-note", "score": 0},
            {"id": "2026-01-02-lexical-only", "score": 9},
        ],
    )
    monkeypatch.setattr(
        "kb_librarian.ingest.score_document",
        lambda match, **kwargs: 0.0 if match["id"] == "2026-01-01-coverage-note" else 9.5,
    )

    matches = _candidate_matches(
        tmp_path,
        config={"retrieval": {}},
        records=records,
        candidate=candidate,
        classification=classification,
        cap=10,
    )

    by_id = {match["note_id"]: match for match in matches}
    assert {"parent_topic", "tags", "retrieval_phrases", "explicit_note_id"} <= set(
        by_id["2026-01-01-coverage-note"]["reasons"]
    )
    assert by_id["2026-01-02-lexical-only"]["reasons"] == ["lexical"]
    assert "2026-01-01-missing" not in by_id


def test_source_append_and_existing_source_helpers_cover_nonlist_and_duplicate_branches(tmp_path):
    initialize_data_dir(tmp_path)
    note_path = _note(
        tmp_path,
        sources="not a list",
        extra={"retrieval_phrases": ["coverage phrase"]},
    )
    parsed = ParsedInput(
        tmp_path / "raw" / "source.md",
        "text",
        "hash-1",
        {"format": "md", "empty": "", "none": None, "links": [], "meta": {"ok": True}},
    )

    assert _source_entry(parsed) == {
        "type": "ingest",
        "ref": parsed.path.as_posix(),
        "hash": "hash-1",
        "format": "md",
        "meta": {"ok": True},
    }
    assert _append_source_to_note(note_path, parsed=parsed) is True
    updated = read_note(note_path)
    assert updated.frontmatter["sources"][0]["hash"] == "hash-1"
    assert _append_source_to_note(note_path, parsed=parsed) is False

    records = load_note_records(tmp_path, validate=True)
    assert _find_existing_note_for_source(records, source_hash="missing", candidate_title="Coverage Note") is None
    assert (
        _find_existing_note_for_source(records, source_hash="hash-1", candidate_title=" coverage note ").note_id
        == "2026-01-01-coverage-note"
    )


def test_source_helpers_skip_non_mapping_sources_and_missing_ingest_state(tmp_path):
    assert _load_ingested(tmp_path) == []

    initialize_data_dir(tmp_path)
    no_sources = _note(
        tmp_path,
        note_id="2026-01-01-no-sources",
        title="Coverage Note",
        sources="not a list",
    )
    mixed_sources = _note(
        tmp_path,
        note_id="2026-01-02-mixed-sources",
        title="Other Note",
        sources=["bad", {"hash": "old"}],
    )
    parsed = ParsedInput(tmp_path / "raw" / "mixed.md", "text", "new-hash")

    records = load_note_records(tmp_path, validate=True)

    assert _find_existing_note_for_source(records, source_hash="new-hash", candidate_title="Coverage Note") is None
    assert _append_source_to_note(mixed_sources, parsed=parsed) is True
    assert read_note(no_sources).frontmatter["sources"] == "not a list"


def test_mark_disputed_handles_nonlist_and_idempotent_existing_dispute(tmp_path):
    initialize_data_dir(tmp_path)
    first_path = _note(
        tmp_path,
        note_id="2026-01-01-first",
        title="First",
        extra={"disputes": "bad"},
    )
    second_path = _note(
        tmp_path,
        note_id="2026-01-02-second",
        title="Second",
        extra={"status": "disputed"},
    )
    records = load_note_records(tmp_path, validate=True)
    parsed = ParsedInput(tmp_path / "raw" / "dispute.md", "text", "hash")
    candidate = _candidate(title="Dispute candidate", body="conflicting body")

    changed = _mark_disputed(records, parsed=parsed, candidate=candidate, rationale="conflict")
    repeat = _mark_disputed(load_note_records(tmp_path, validate=True), parsed=parsed, candidate=candidate, rationale="conflict")

    assert changed == ["2026-01-01-first", "2026-01-02-second"]
    assert repeat == []
    assert read_note(first_path).frontmatter["disputes"][0]["rationale"] == "conflict"
    assert read_note(second_path).frontmatter["status"] == "disputed"


def test_needs_classification_review_config_and_blank_field_branches():
    candidate = _candidate(confidence="low")

    assert _needs_classification_review({}, candidate, ClassificationResult("topic", "technique", "high")) is True
    assert (
        _needs_classification_review(
            {"ingest": {"low_confidence_goes_to_review": False}},
            candidate,
            ClassificationResult("topic", "technique", "high"),
        )
        is False
    )
    assert (
        _needs_classification_review(
            {"ingest": {"low_confidence_goes_to_review": False}},
            _candidate(),
            ClassificationResult(" ", "technique", "high"),
        )
        is True
    )
    assert (
        _needs_classification_review(
            {"ingest": {"low_confidence_goes_to_review": False}},
            _candidate(),
            ClassificationResult("topic", "", "high"),
        )
        is True
    )


def test_ingested_state_recording_validation_and_deduplication(tmp_path):
    initialize_data_dir(tmp_path)
    parsed = ParsedInput(tmp_path / "raw" / "item.md", "text", "hash", {"format": "md"})

    assert _load_ingested(tmp_path) == []
    _record_ingest(tmp_path, parsed, status="success", note_ids=["note-1"], error="ignored")
    _record_ingest(tmp_path, parsed, status="success", note_ids=["note-1"], error="ignored")
    entries = _load_ingested(tmp_path)
    assert len(entries) == 1
    assert entries[0]["source_metadata"] == {"format": "md"}
    assert entries[0]["error"] == "ignored"

    path = tmp_path / ".kb" / "ingested.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(IngestError, match="must be a list"):
        _load_ingested(tmp_path)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(IngestError, match="Malformed ingested state"):
        _load_ingested(tmp_path)
    path.write_text('[{"ok": true}, "skip", 3]', encoding="utf-8")
    assert _load_ingested(tmp_path) == [{"ok": True}]


def test_archive_and_small_text_helpers_cover_edge_branches(tmp_path):
    initialize_data_dir(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    assert _archive_if_raw_file(tmp_path, outside, duplicate=False) is None

    processed = tmp_path / "raw" / "processed" / "2026-06" / "done.md"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text("done", encoding="utf-8")
    assert _archive_if_raw_file(tmp_path, processed, duplicate=False) is None

    duplicate_name = tmp_path / "name.md"
    duplicate_name.write_text("one", encoding="utf-8")
    duplicate_name.with_name("name-2.md").write_text("two", encoding="utf-8")
    assert _dedupe_path(duplicate_name).name == "name-3.md"

    _log_error(tmp_path, "plain")
    _log_error(tmp_path, "detailed", operation_id="op", stage="stage", file=outside)
    log = (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    assert " plain\n" in log
    assert "operation_id=op stage=stage file=" in log

    assert _ensure_trailing_newline("already\n") == "already\n"
    assert _ensure_trailing_newline("missing") == "missing\n"
    assert _excerpt("short text", limit=20) == "short text"
    assert _excerpt("word " * 80, limit=20).endswith("...")
    assert _parent_topic("single") is None
    assert _parent_topic("Parent / Child / Leaf") == "parent/child"
    assert _coerce_string_list("not-list") == []
    assert _coerce_string_list([" keep ", "", 42, "also"]) == ["keep", "also"]
