"""Markdown/text ingest pipeline."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from kb_librarian.errors import IngestError, NoteValidationError, ProviderError
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.notes import Note, generate_note_id, write_note
from kb_librarian.providers import (
    CandidateNote,
    ClassificationResult,
    operation_route,
    provider_from_config,
)
from kb_librarian.search_index import query_candidates, score_document, tokenize_query
from kb_librarian.storage import (
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
    normalize_topic_for_path,
)


SUPPORTED_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class ParsedInput:
    path: Path
    text: str
    digest: str


@dataclass
class IngestReport:
    processed_files: int = 0
    created_notes: list[str] = field(default_factory=list)
    classification_items: list[Path] = field(default_factory=list)
    skipped_candidates: int = 0
    duplicates: int = 0
    unsupported_files: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)
    archived_paths: list[Path] = field(default_factory=list)


def ingest(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    file_path: str | Path | None = None,
    force: bool = False,
    quiet: bool = False,
    env: Mapping[str, str] | None = None,
) -> IngestReport:
    del quiet
    files = _selected_files(data_dir, file_path)
    report = IngestReport()

    for path in files:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            report.unsupported_files += 1
            _log_error(data_dir, f"Unsupported ingest file extension for {path}")
            continue

        try:
            parsed = parse_ingest_file(path)
            duplicate_path = _handle_duplicate(data_dir, parsed, force=force)
            if duplicate_path is not None:
                report.duplicates += 1
                report.archived_paths.append(duplicate_path)
                continue
            if _has_filename_with_different_hash(data_dir, parsed):
                report.warnings.append(f"Possible updated version: {path.name}")

            before_note_count = len(report.created_notes)
            _ingest_one(data_dir, config=config, parsed=parsed, report=report, env=env)
            archive_path = _archive_if_raw_file(data_dir, path, duplicate=False)
            if archive_path is not None:
                report.archived_paths.append(archive_path)
            created_for_file = report.created_notes[before_note_count:]
            _record_ingest(
                data_dir,
                parsed,
                status="success",
                note_ids=created_for_file,
                archived_path=archive_path,
            )
            report.processed_files += 1
        except (IngestError, NoteValidationError, ProviderError, OSError) as exc:
            report.errors += 1
            _log_error(data_dir, f"{path}: {exc}")
            if path.exists():
                digest = _sha256(path)
                _record_ingest(data_dir, ParsedInput(path=path, text="", digest=digest), status="error", error=str(exc))

    return report


def parse_ingest_file(path: Path) -> ParsedInput:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError(f"Could not decode {path} as UTF-8 text.") from exc
    text = _normalize_document_text(raw)
    if not text.strip():
        raise IngestError(f"Ingest file {path} is empty.")
    return ParsedInput(path=path, text=text, digest=_sha256(path))


def render_report(report: IngestReport) -> str:
    lines = [
        "Ingest report:",
        f"processed_files: {report.processed_files}",
        f"created_notes: {len(report.created_notes)}",
        f"classification_items: {len(report.classification_items)}",
        f"skipped_candidates: {report.skipped_candidates}",
        f"duplicates: {report.duplicates}",
        f"unsupported_files: {report.unsupported_files}",
        f"errors: {report.errors}",
    ]
    if report.created_notes:
        lines.append("note_ids:")
        lines.extend(f"- {note_id}" for note_id in report.created_notes)
    if report.classification_items:
        lines.append("review_paths:")
        for path in sorted(set(report.classification_items)):
            lines.append(f"- {path}")
    if report.archived_paths:
        lines.append("archived_paths:")
        for path in report.archived_paths:
            lines.append(f"- {path}")
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines) + "\n"


def _selected_files(data_dir: Path, file_path: str | Path | None) -> list[Path]:
    if file_path is not None:
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise IngestError(f"Ingest file {path} does not exist or is not a file.")
        return [path]

    raw_dir = data_dir / "raw"
    if not raw_dir.exists():
        return []
    processed_root = raw_dir / "processed"
    files = [
        path
        for path in sorted(raw_dir.iterdir())
        if path.is_file() and processed_root not in path.parents
    ]
    return files


def _ingest_one(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    parsed: ParsedInput,
    report: IngestReport,
    env: Mapping[str, str] | None,
) -> None:
    extract_route = operation_route(config, "extract")
    classify_route = operation_route(config, "classify")
    extractor = provider_from_config(config, extract_route.provider, env=env)
    classifier = provider_from_config(config, classify_route.provider, env=env)

    ingest_config = config.get("ingest", {})
    max_notes = int(ingest_config.get("max_notes_per_doc", 7))
    extraction = extractor.extract_candidates(
        text=parsed.text,
        source_path=parsed.path,
        max_notes=max_notes,
        model=extract_route.model,
    )

    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    existing_ids = existing_note_ids(records)

    for candidate in extraction.candidates[:max_notes]:
        if candidate.utility_score == "low":
            report.skipped_candidates += 1
            continue
        classification = classifier.classify_candidate(
            candidate=candidate,
            text=parsed.text,
            source_path=parsed.path,
            model=classify_route.model,
        )
        if _needs_classification_review(config, candidate, classification):
            _append_classification_review(data_dir, parsed=parsed, candidate=candidate, classification=classification)
            report.classification_items.append(data_dir / "review" / "pending-classification.md")
            continue
        if _has_existing_match(data_dir, config=config, candidate=candidate, classification=classification):
            _append_classification_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                classification=classification,
                reason="possible existing note match; integration verdict deferred to Phase 01d",
            )
            report.classification_items.append(data_dir / "review" / "pending-classification.md")
            continue

        note_id = generate_note_id(candidate.title, date.today(), existing_ids=existing_ids)
        existing_ids.add(note_id)
        note = _candidate_to_note(
            candidate,
            classification=classification,
            note_id=note_id,
            source_path=parsed.path,
        )
        ensure_topic_layout(data_dir, classification.topic)
        path = canonical_note_path(data_dir, classification.topic, note_id)
        write_note(path, note)
        reindex_data_dir(data_dir)
        report.created_notes.append(note_id)


def _candidate_to_note(
    candidate: CandidateNote,
    *,
    classification: ClassificationResult,
    note_id: str,
    source_path: Path,
) -> Note:
    today = date.today().isoformat()
    tags = candidate.tags or [normalize_topic_for_path(classification.topic)]
    frontmatter: dict[str, Any] = {
        "id": note_id,
        "title": candidate.title,
        "summary": candidate.summary,
        "topic": classification.topic,
        "created": today,
        "updated": today,
        "knowledge_type": classification.knowledge_type,
        "status": "active",
        "confidence": classification.confidence,
        "basis": ["ingested source"],
        "sources": [{"type": "ingest", "ref": source_path.as_posix()}],
        "retrieval_phrases": candidate.retrieval_phrases,
        "agent_use": candidate.agent_use or [],
        "applies_when": candidate.applies_when or [],
        "does_not_apply_when": candidate.does_not_apply_when or [],
        "failure_modes": candidate.failure_modes or [],
        "reviewed_by_user": False,
        "disputes": [],
        "tags": tags,
    }
    note = Note(frontmatter=frontmatter, body=_ensure_trailing_newline(candidate.body))
    note.validate()
    return note


def _needs_classification_review(
    config: Mapping[str, Any],
    candidate: CandidateNote,
    classification: ClassificationResult,
) -> bool:
    ingest_config = config.get("ingest", {})
    low_confidence_to_review = bool(ingest_config.get("low_confidence_goes_to_review", True))
    if low_confidence_to_review and (candidate.confidence == "low" or classification.confidence == "low"):
        return True
    if not classification.topic.strip() or not classification.knowledge_type.strip():
        return True
    return False


def _has_existing_match(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    candidate: CandidateNote,
    classification: ClassificationResult,
) -> bool:
    fts_path = data_dir / ".kb" / "fts.sqlite"
    if not fts_path.exists():
        reindex_data_dir(data_dir)
    query = " ".join([candidate.title, candidate.summary, *candidate.retrieval_phrases]).strip()
    if not query:
        return False
    matches = query_candidates(fts_path, query=query, topic=None, knowledge_type=None)
    tokens = tokenize_query(query)
    retrieval = config.get("retrieval", {})
    for match in matches[:20]:
        score = score_document(match, query=query, tokens=tokens, weights=retrieval)
        same_title = match["title"].strip().lower() == candidate.title.strip().lower()
        same_topic = match["topic"].strip().lower() == classification.topic.strip().lower()
        if same_title or (same_topic and score >= 20):
            return True
    return False


def _append_classification_review(
    data_dir: Path,
    *,
    parsed: ParsedInput,
    candidate: CandidateNote,
    classification: ClassificationResult,
    reason: str | None = None,
) -> None:
    review_path = data_dir / "review" / "pending-classification.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    if not review_path.exists():
        review_path.write_text("# Pending Classification\n\n", encoding="utf-8")
    item_id = f"classification-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    rendered = [
        f"## item: {item_id}",
        "",
        f"Source: `{parsed.path.as_posix()}`",
        f"Reason: {reason or classification.reason or 'low confidence or ambiguous classification'}",
        "",
        f"- title: {candidate.title}",
        f"- summary: {candidate.summary}",
        f"- suggested_topic: {classification.topic}",
        f"- suggested_type: {classification.knowledge_type}",
        f"- confidence: {classification.confidence}",
        "",
    ]
    with review_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(rendered))


def _normalize_document_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized + "\n" if normalized else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_ingested(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / ".kb" / "ingested.json"
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"Malformed ingested state at {path}: {exc}") from exc
    if not isinstance(loaded, list):
        raise IngestError(f"Ingested state at {path} must be a list.")
    return [item for item in loaded if isinstance(item, dict)]


def _write_ingested(data_dir: Path, entries: list[dict[str, Any]]) -> None:
    path = data_dir / ".kb" / "ingested.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_ingest(
    data_dir: Path,
    parsed: ParsedInput,
    *,
    status: str,
    note_ids: list[str] | None = None,
    archived_path: Path | None = None,
    error: str | None = None,
) -> None:
    entries = _load_ingested(data_dir)
    entry: dict[str, Any] = {
        "hash": parsed.digest,
        "source_path": parsed.path.as_posix(),
        "source_name": parsed.path.name,
        "status": status,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "note_ids": note_ids or [],
    }
    if archived_path is not None:
        entry["archived_path"] = archived_path.as_posix()
    if error:
        entry["error"] = error
    entries.append(entry)
    _write_ingested(data_dir, entries)


def _handle_duplicate(data_dir: Path, parsed: ParsedInput, *, force: bool) -> Path | None:
    if force:
        return None
    entries = _load_ingested(data_dir)
    if any(item.get("hash") == parsed.digest and item.get("status") == "success" for item in entries):
        archive_path = _archive_if_raw_file(data_dir, parsed.path, duplicate=True)
        _record_ingest(data_dir, parsed, status="duplicate", archived_path=archive_path)
        return archive_path or parsed.path
    return None


def _has_filename_with_different_hash(data_dir: Path, parsed: ParsedInput) -> bool:
    entries = _load_ingested(data_dir)
    return any(
        item.get("source_name") == parsed.path.name
        and item.get("hash") != parsed.digest
        and item.get("status") == "success"
        for item in entries
    )


def _archive_if_raw_file(data_dir: Path, path: Path, *, duplicate: bool) -> Path | None:
    try:
        path.relative_to(data_dir / "raw")
    except ValueError:
        return None
    if not path.exists() or (data_dir / "raw" / "processed") in path.parents:
        return None
    month = date.today().strftime("%Y-%m")
    destination_dir = data_dir / "raw" / "processed" / month
    if duplicate:
        destination_dir = destination_dir / "duplicates"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = _dedupe_path(destination_dir / path.name)
    shutil.move(str(path), str(destination))
    return destination


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _log_error(data_dir: Path, message: str) -> None:
    path = data_dir / ".kb" / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"
