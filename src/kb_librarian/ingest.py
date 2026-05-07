"""Markdown/text ingest pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from kb_librarian.atomic import atomic_write_json
from kb_librarian.errors import IngestError, NoteValidationError, ProviderError
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.ingest_recovery import (
    IngestCheckpoint,
    IngestLock,
    current_ingest_checkpoint,
    new_operation_id,
    resumable_ingest_checkpoint,
)
from kb_librarian.notes import Note, generate_note_id, read_note, write_note
from kb_librarian.parsers import parse_html, parse_pdf
from kb_librarian.provider_retry import RetryEvent, retry_policy_from_config
from kb_librarian.providers import (
    CandidateNote,
    ClassificationResult,
    IntegrationResult,
    ProviderFallbackEvent,
    call_with_provider_policy,
    provider_from_config,
)
from kb_librarian.review import (
    add_review_item,
    queue_duplicate_review_item,
    queue_parser_failure_review_item,
    queue_unsupported_file_review_item,
)
from kb_librarian.search_index import query_candidates, score_document, tokenize_query
from kb_librarian.storage import (
    NOTE_ID_REFERENCE_PATTERN,
    NoteRecord,
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
    normalize_topic_for_path,
)


SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf", ".html", ".htm"}
MATCH_CAP = 12


@dataclass(frozen=True)
class ParsedInput:
    path: Path
    text: str
    digest: str
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestReport:
    operation_id: str | None = None
    resumed: bool = False
    processed_files: int = 0
    created_notes: list[str] = field(default_factory=list)
    source_appends: list[str] = field(default_factory=list)
    merge_proposals: list[str] = field(default_factory=list)
    disputes: list[str] = field(default_factory=list)
    dispute_items: list[str] = field(default_factory=list)
    classification_items: list[str] = field(default_factory=list)
    duplicate_items: list[str] = field(default_factory=list)
    unsupported_file_items: list[str] = field(default_factory=list)
    parser_failure_items: list[str] = field(default_factory=list)
    review_paths: list[Path] = field(default_factory=list)
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
    resume: bool = False,
    env: Mapping[str, str] | None = None,
) -> IngestReport:
    del quiet
    if resume and file_path is not None:
        raise IngestError("`kb ingest --resume` cannot be combined with an explicit file path.")

    operation_id = _operation_id_for_run(data_dir, resume=resume)
    command = "kb ingest --resume" if resume else "kb ingest"
    lock = IngestLock.acquire(
        data_dir,
        operation_id=operation_id,
        command=command,
        force=force,
        resume=resume,
    )
    report = IngestReport()
    report.operation_id = operation_id
    report.resumed = resume
    checkpoint = IngestCheckpoint(data_dir, operation_id=operation_id)
    checkpoint_started = False

    try:
        if resume:
            files = _resume_files(data_dir)
            checkpoint.start_operation(files, resumed=True)
            checkpoint_started = True
        else:
            _refuse_unresolved_checkpoint(data_dir, force=force)
            files = _selected_files(data_dir, file_path)
            checkpoint.start_operation(files, resumed=False)
            checkpoint_started = True

        for path in files:
            lock.update_current_file(path)
            checkpoint.start_file(path)
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                report.unsupported_files += 1
                _log_error(
                    data_dir,
                    f"Unsupported ingest file extension for {path}",
                    operation_id=operation_id,
                    stage="unsupported-file",
                    file=path,
                )
                item_id = queue_unsupported_file_review_item(data_dir, source_path=path)
                if item_id:
                    report.unsupported_file_items.append(item_id)
                checkpoint.update("unsupported-file", parse_status="unsupported")
                continue

            try:
                parsed = parse_ingest_file(path)
                checkpoint.update("parsed", current_hash=parsed.digest, parse_status="success")
                duplicate_path = _handle_duplicate(data_dir, parsed, force=force)
                if duplicate_path is not None:
                    report.duplicates += 1
                    report.archived_paths.append(duplicate_path)
                    item_id = queue_duplicate_review_item(
                        data_dir,
                        source_name=parsed.path.name,
                        source_path=parsed.path,
                        source_hash=parsed.digest,
                        archived_path=duplicate_path,
                    )
                    if item_id:
                        report.duplicate_items.append(item_id)
                    checkpoint.complete_file(archive_target=duplicate_path)
                    continue
                if _has_filename_with_different_hash(data_dir, parsed):
                    report.warnings.append(f"Possible updated version: {path.name}")

                before_note_count = len(report.created_notes)
                _ingest_one(data_dir, config=config, parsed=parsed, report=report, env=env, checkpoint=checkpoint)
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
                checkpoint.complete_file(archive_target=archive_path)
                report.processed_files += 1
            except (IngestError, NoteValidationError, ProviderError, OSError) as exc:
                report.errors += 1
                checkpoint.fail(stage="error", error=str(exc))
                _log_error(data_dir, str(exc), operation_id=operation_id, stage="error", file=path)
                if isinstance(exc, IngestError) and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    item_id = queue_parser_failure_review_item(data_dir, source_path=path, error=str(exc))
                    if item_id:
                        report.parser_failure_items.append(item_id)
                    report.review_paths.append(data_dir / "review" / "parser-failures.md")
                if path.exists():
                    digest = _sha256(path)
                    _record_ingest(
                        data_dir,
                        ParsedInput(path=path, text="", digest=digest),
                        status="error",
                        error=str(exc),
                    )
        checkpoint.finish(errors=report.errors)
        lock.update_current_file(None)
    except BaseException as exc:
        if checkpoint_started:
            checkpoint.fail(stage="fatal", error=str(exc))
        raise
    finally:
        lock.release()

    return report


def parse_ingest_file(path: Path) -> ParsedInput:
    suffix = path.suffix.lower()
    source_metadata: dict[str, Any] = {"format": suffix.lstrip(".") or "text"}
    if suffix == ".pdf":
        text, source_metadata = parse_pdf(path)
    elif suffix in {".html", ".htm"}:
        text, source_metadata = parse_html(path)
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise IngestError(f"Could not decode {path} as UTF-8 text.") from exc
        text = _normalize_document_text(raw)
    if not text.strip():
        raise IngestError(f"Ingest file {path} is empty.")
    return ParsedInput(path=path, text=text, digest=_sha256(path), source_metadata=source_metadata)


def _operation_id_for_run(data_dir: Path, *, resume: bool) -> str:
    if not resume:
        return new_operation_id()
    checkpoint = resumable_ingest_checkpoint(data_dir)
    if checkpoint is None:
        raise IngestError("No interrupted ingest checkpoint was found; run `kb ingest` normally.")
    operation_id = str(checkpoint.get("operation_id", "")).strip()
    return operation_id or new_operation_id()


def _refuse_unresolved_checkpoint(data_dir: Path, *, force: bool) -> None:
    if force:
        return
    checkpoint = resumable_ingest_checkpoint(data_dir)
    if checkpoint is None:
        return
    operation_id = str(checkpoint.get("operation_id", "unknown"))
    current = checkpoint.get("current_raw_file") or "(none recorded)"
    stage = checkpoint.get("last_completed_stage") or "unknown"
    raise IngestError(
        "Interrupted ingest checkpoint exists "
        f"(operation_id={operation_id}, current_raw_file={current}, stage={stage}). "
        "Run `kb ingest --resume` to continue it, or `kb ingest --force` to start over."
    )


def _resume_files(data_dir: Path) -> list[Path]:
    checkpoint = current_ingest_checkpoint(data_dir)
    if checkpoint is None or str(checkpoint.get("status")) not in {"running", "error", "interrupted"}:
        raise IngestError("No interrupted ingest checkpoint was found; run `kb ingest` normally.")

    current = checkpoint.get("current_raw_file")
    candidates: list[Path] = []
    if isinstance(current, str) and current.strip():
        candidates.append(Path(current))
    pending = checkpoint.get("pending_files")
    if isinstance(pending, list):
        candidates.extend(Path(str(path)) for path in pending if str(path).strip())

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            deduped.append(path)

    if deduped:
        return deduped

    archive_target = checkpoint.get("archive_target")
    if isinstance(archive_target, str) and archive_target.strip() and Path(archive_target).exists():
        return []

    raise IngestError(
        "Interrupted ingest checkpoint cannot be resumed because the raw file is missing. "
        "Run `kb doctor` and inspect `.kb/state.json` plus `.kb/errors.log`."
    )


def render_report(report: IngestReport) -> str:
    payload = ingest_report_payload(report)
    lines = [
        "Ingest report:",
        f"operation_id: {payload['operation_id']}",
        f"processed_files: {payload['processed_files']}",
        f"created_notes: {payload['created_notes']['count']}",
        f"source_appends: {payload['source_appends']['count']}",
        f"merge_proposals: {payload['merge_proposals']['count']}",
        f"disputes: {payload['disputes']['count']}",
        f"classification_items: {payload['classification_items']['count']}",
        f"skipped_candidates: {payload['skipped_candidates']}",
        f"duplicates: {payload['duplicates']['count']}",
        f"unsupported_files: {payload['unsupported_files']['count']}",
        f"parser_failures: {payload['parser_failures']['count']}",
        f"errors: {payload['errors']}",
    ]
    if payload["created_notes"]["note_ids"]:
        lines.append("created_note_ids:")
        lines.extend(f"- {note_id}" for note_id in payload["created_notes"]["note_ids"])
    if payload["source_appends"]["note_ids"]:
        lines.append("source_appended_note_ids:")
        lines.extend(f"- {note_id}" for note_id in payload["source_appends"]["note_ids"])
    if payload["merge_proposals"]["review_item_ids"]:
        lines.append("merge_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["merge_proposals"]["review_item_ids"])
    if payload["disputes"]["note_ids"]:
        lines.append("disputed_note_ids:")
        lines.extend(f"- {note_id}" for note_id in payload["disputes"]["note_ids"])
    if payload["disputes"]["review_item_ids"]:
        lines.append("dispute_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["disputes"]["review_item_ids"])
    if payload["classification_items"]["review_item_ids"]:
        lines.append("classification_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["classification_items"]["review_item_ids"])
    if payload["duplicates"]["review_item_ids"]:
        lines.append("duplicate_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["duplicates"]["review_item_ids"])
    if payload["unsupported_files"]["review_item_ids"]:
        lines.append("unsupported_file_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["unsupported_files"]["review_item_ids"])
    if payload["parser_failures"]["review_item_ids"]:
        lines.append("parser_failure_review_item_ids:")
        lines.extend(f"- {item_id}" for item_id in payload["parser_failures"]["review_item_ids"])
    if payload["review_paths"]:
        lines.append("review_paths:")
        for path in payload["review_paths"]:
            lines.append(f"- {path}")
    if payload["archived_paths"]:
        lines.append("archived_paths:")
        for path in payload["archived_paths"]:
            lines.append(f"- {path}")
    if payload["warnings"]:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    if report.errors:
        lines.append("recovery:")
        lines.append("- Rerun with `kb ingest --resume` after fixing the logged error.")
        lines.append("- Run `kb doctor` if the checkpoint or lock state looks inconsistent.")
    return "\n".join(lines) + "\n"


def ingest_report_payload(report: IngestReport) -> dict[str, Any]:
    """Return stable JSON-compatible report fields used by human and JSON output."""

    review_item_ids = [
        *report.merge_proposals,
        *report.dispute_items,
        *report.classification_items,
        *report.duplicate_items,
        *report.unsupported_file_items,
        *report.parser_failure_items,
    ]
    return {
        "operation_id": report.operation_id,
        "resumed": report.resumed,
        "processed_files": report.processed_files,
        "created_notes": {
            "count": len(report.created_notes),
            "note_ids": list(report.created_notes),
        },
        "source_appends": {
            "count": len(report.source_appends),
            "note_ids": list(report.source_appends),
        },
        "merge_proposals": {
            "count": len(report.merge_proposals),
            "review_item_ids": list(report.merge_proposals),
        },
        "disputes": {
            "count": len(set(report.disputes)),
            "note_ids": sorted(set(report.disputes)),
            "review_item_ids": list(report.dispute_items),
        },
        "classification_items": {
            "count": len(report.classification_items),
            "review_item_ids": list(report.classification_items),
        },
        "skipped_candidates": report.skipped_candidates,
        "duplicates": {
            "count": report.duplicates,
            "review_item_ids": list(report.duplicate_items),
        },
        "unsupported_files": {
            "count": report.unsupported_files,
            "review_item_ids": list(report.unsupported_file_items),
        },
        "parser_failures": {
            "count": len(report.parser_failure_items),
            "review_item_ids": list(report.parser_failure_items),
        },
        "errors": report.errors,
        "review_item_ids": review_item_ids,
        "review_paths": sorted({path.as_posix() for path in report.review_paths}),
        "archived_paths": [path.as_posix() for path in report.archived_paths],
        "warnings": list(report.warnings),
    }


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
    checkpoint: IngestCheckpoint | None = None,
) -> None:
    retry_policy = retry_policy_from_config(config)
    operation_id = checkpoint.operation_id if checkpoint is not None else None

    ingest_config = config.get("ingest", {})
    max_notes = int(ingest_config.get("max_notes_per_doc", 7))
    extraction = _provider_call(
        data_dir=data_dir,
        parsed=parsed,
        operation_id=operation_id,
        phase="extract",
        config=config,
        env=env,
        retry_policy=retry_policy,
        call=lambda provider, route: provider.extract_candidates(
            text=parsed.text,
            source_path=parsed.path,
            max_notes=max_notes,
            model=route.model,
        ),
    )
    if checkpoint is not None:
        checkpoint.update(
            "extracted",
            candidate_ids=[_candidate_checkpoint_id(candidate) for candidate in extraction.candidates[:max_notes]],
        )

    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    existing_ids = existing_note_ids(records)

    for candidate in extraction.candidates[:max_notes]:
        if candidate.utility_score == "low":
            report.skipped_candidates += 1
            continue
        classification = _provider_call(
            data_dir=data_dir,
            parsed=parsed,
            operation_id=operation_id,
            phase="classify",
            config=config,
            env=env,
            retry_policy=retry_policy,
            candidate_title=candidate.title,
            call=lambda provider, route: provider.classify_candidate(
                candidate=candidate,
                text=parsed.text,
                source_path=parsed.path,
                model=route.model,
            ),
        )
        if checkpoint is not None:
            checkpoint.update(
                "classified",
                parse_status="success",
                current_candidate=_candidate_checkpoint_id(candidate),
            )
        if _needs_classification_review(config, candidate, classification):
            item_id = _append_classification_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                classification=classification,
            )
            if item_id:
                report.classification_items.append(item_id)
            report.review_paths.append(data_dir / "review" / "pending-classification.md")
            if checkpoint is not None:
                checkpoint.record_written_file(data_dir / "review" / "review-items.json")
                checkpoint.record_written_file(data_dir / "review" / "pending-classification.md")
            continue

        matches = _candidate_matches(
            data_dir,
            config=config,
            records=records,
            candidate=candidate,
            classification=classification,
            cap=MATCH_CAP,
        )

        try:
            integration = _provider_call(
                data_dir=data_dir,
                parsed=parsed,
                operation_id=operation_id,
                phase="integrate",
                config=config,
                env=env,
                retry_policy=retry_policy,
                candidate_title=candidate.title,
                call=lambda provider, route: provider.integration_verdict(
                    candidate=candidate,
                    classification=classification,
                    matches=matches,
                    text=parsed.text,
                    source_path=parsed.path,
                    model=route.model,
                ),
            )
            target_records = _target_records(matches, records, integration)
            if checkpoint is not None:
                checkpoint.append_decision(
                    {
                        "candidate_id": _candidate_checkpoint_id(candidate),
                        "candidate_title": candidate.title,
                        "verdict": integration.verdict,
                        "target_note_ids": list(integration.target_note_ids),
                    }
                )
        except ProviderError as exc:
            report.errors += 1
            item_id = _append_classification_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                classification=classification,
                reason=f"integration verdict error: {exc}",
            )
            review_path = data_dir / "review" / "pending-classification.md"
            if item_id:
                report.classification_items.append(item_id)
            report.review_paths.append(review_path)
            if checkpoint is not None:
                checkpoint.record_written_file(data_dir / "review" / "review-items.json")
                checkpoint.record_written_file(review_path)
            continue

        mutated = False
        if integration.verdict == "unrelated":
            existing_source_note = _find_existing_note_for_source(
                records,
                source_hash=parsed.digest,
                candidate_title=candidate.title,
            )
            if existing_source_note is not None:
                existing_ids.add(existing_source_note.note_id)
                continue
            note_id = generate_note_id(candidate.title, date.today(), existing_ids=existing_ids)
            existing_ids.add(note_id)
            note = _candidate_to_note(
                candidate,
                classification=classification,
                note_id=note_id,
                parsed=parsed,
            )
            ensure_topic_layout(data_dir, classification.topic)
            path = canonical_note_path(data_dir, classification.topic, note_id)
            write_note(path, note)
            if checkpoint is not None:
                checkpoint.record_written_file(path)
            report.created_notes.append(note_id)
            mutated = True
        elif integration.verdict == "identical":
            for record in target_records:
                if _append_source_to_note(record.path, parsed=parsed):
                    if checkpoint is not None:
                        checkpoint.record_written_file(record.path)
                    report.source_appends.append(record.note_id)
                    mutated = True
        elif integration.verdict == "adds_nuance":
            queued = _append_merge_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                target_note_ids=[record.note_id for record in target_records],
                rationale=integration.rationale,
            )
            if queued:
                report.merge_proposals.append(queued)
            report.review_paths.append(data_dir / "review" / "pending-merge.md")
            if checkpoint is not None:
                checkpoint.record_written_file(data_dir / "review" / "review-items.json")
                checkpoint.record_written_file(data_dir / "review" / "pending-merge.md")
        elif integration.verdict == "contradicts":
            disputed_ids = _mark_disputed(
                target_records,
                parsed=parsed,
                candidate=candidate,
                rationale=integration.rationale,
            )
            report.disputes.extend(disputed_ids)
            if disputed_ids:
                mutated = True
            item_id = _append_dispute_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                target_note_ids=[record.note_id for record in target_records],
                rationale=integration.rationale,
            )
            if item_id:
                report.dispute_items.append(item_id)
            report.review_paths.append(data_dir / "review" / "disputes.md")
            if checkpoint is not None:
                checkpoint.record_written_file(data_dir / "review" / "review-items.json")
                checkpoint.record_written_file(data_dir / "review" / "disputes.md")
        else:
            report.errors += 1
            item_id = _append_classification_review(
                data_dir,
                parsed=parsed,
                candidate=candidate,
                classification=classification,
                reason=f"unknown integration verdict: {integration.verdict}",
            )
            if item_id:
                report.classification_items.append(item_id)
            report.review_paths.append(data_dir / "review" / "pending-classification.md")
            if checkpoint is not None:
                checkpoint.record_written_file(data_dir / "review" / "review-items.json")
                checkpoint.record_written_file(data_dir / "review" / "pending-classification.md")

        if mutated:
            reindex_data_dir(data_dir)
            if checkpoint is not None:
                checkpoint.update("indexed")
            records = load_note_records(data_dir, validate=True)
            ensure_unique_note_ids(records)
            existing_ids = existing_note_ids(records)


def _target_records(
    matches: list[dict[str, Any]],
    records: list[NoteRecord],
    integration: IntegrationResult,
) -> list[NoteRecord]:
    by_id = {record.note_id: record for record in records}
    match_ids = {str(item.get("note_id", "")) for item in matches}
    target_records: list[NoteRecord] = []
    for note_id in integration.target_note_ids:
        if integration.verdict != "unrelated" and note_id not in match_ids:
            raise ProviderError(
                f"Integration verdict returned target note ID {note_id!r} not present in candidate matches."
            )
        record = by_id.get(note_id)
        if record is None:
            raise ProviderError(f"Integration verdict target note ID {note_id!r} does not exist.")
        target_records.append(record)

    if integration.verdict != "unrelated" and not target_records:
        raise ProviderError(f"Integration verdict {integration.verdict!r} requires at least one target record.")
    return target_records


def _provider_call(
    *,
    data_dir: Path,
    parsed: ParsedInput,
    operation_id: str | None,
    phase: str,
    config: Mapping[str, Any],
    env: Mapping[str, str] | None,
    retry_policy: Any,
    call: Any,
    candidate_title: str | None = None,
) -> Any:
    detail = f"candidate={candidate_title!r} " if candidate_title else ""

    def on_retry(event: RetryEvent) -> None:
        _log_error(
            data_dir,
            (
                f"Retrying provider phase={phase} {detail}"
                f"attempt={event.attempt}/{event.max_attempts} "
                f"delay={event.delay_seconds:.3f}s reason={event.classification.kind}:{event.classification.detail} "
                f"error={event.error}"
            ),
            operation_id=operation_id,
            stage="provider-retry",
            file=parsed.path,
        )

    def on_final_failure(event: RetryEvent) -> None:
        _log_error(
            data_dir,
            (
                f"Provider phase={phase} {detail}stopped "
                f"attempt={event.attempt}/{event.max_attempts} "
                f"transient={event.classification.transient} "
                f"reason={event.classification.kind}:{event.classification.detail} "
                f"error={event.error}"
            ),
            operation_id=operation_id,
            stage="provider-final",
            file=parsed.path,
        )

    def on_fallback(event: ProviderFallbackEvent) -> None:
        _log_error(
            data_dir,
            (
                f"Falling back provider phase={phase} {detail}"
                f"from={event.provider} model={event.model} "
                f"to={event.next_provider} next_model={event.next_model} "
                f"reason={event.classification_kind}:{event.classification_detail} "
                f"error={event.error}"
            ),
            operation_id=operation_id,
            stage="provider-fallback",
            file=parsed.path,
        )

    return call_with_provider_policy(
        config,
        phase,
        operation_name=f"ingest:{phase}",
        retry_policy=retry_policy,
        call=call,
        env=env,
        provider_factory=provider_from_config,
        on_retry=on_retry,
        on_final_failure=on_final_failure,
        on_fallback=on_fallback,
    )


def _candidate_checkpoint_id(candidate: CandidateNote) -> str:
    payload = {
        "title": candidate.title,
        "summary": candidate.summary,
        "body": candidate.body,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


def _find_existing_note_for_source(
    records: list[NoteRecord],
    *,
    source_hash: str,
    candidate_title: str,
) -> NoteRecord | None:
    normalized_title = candidate_title.strip().lower()
    for record in records:
        if str(record.note.frontmatter.get("title", "")).strip().lower() != normalized_title:
            continue
        sources = record.note.frontmatter.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if isinstance(source, Mapping) and source.get("hash") == source_hash:
                return record
    return None


def _candidate_matches(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    records: list[NoteRecord],
    candidate: CandidateNote,
    classification: ClassificationResult,
    cap: int,
) -> list[dict[str, Any]]:
    retrieval = config.get("retrieval", {})
    by_id = {record.note_id: record for record in records}
    score_by_id: dict[str, float] = {}
    reasons_by_id: dict[str, set[str]] = {}

    candidate_topic = normalize_topic_for_path(classification.topic)
    parent_topic = _parent_topic(classification.topic)
    candidate_tags = {normalize_topic_for_path(tag) for tag in candidate.tags}
    candidate_phrases = {item.strip().lower() for item in candidate.retrieval_phrases if item.strip()}
    explicit_ids = set(NOTE_ID_REFERENCE_PATTERN.findall(f"{candidate.title}\n{candidate.summary}\n{candidate.body}"))

    for record in records:
        fm = record.note.frontmatter
        note_id = record.note_id
        note_topic = normalize_topic_for_path(str(fm.get("topic", "")))
        note_tags = {normalize_topic_for_path(str(item)) for item in _coerce_string_list(fm.get("tags", []))}
        note_phrases = {item.strip().lower() for item in _coerce_string_list(fm.get("retrieval_phrases", [])) if item.strip()}

        if note_topic == candidate_topic:
            _bump_match(score_by_id, reasons_by_id, note_id, 65.0, "topic")
        if parent_topic and note_topic == parent_topic:
            _bump_match(score_by_id, reasons_by_id, note_id, 50.0, "parent_topic")

        shared_tags = candidate_tags & note_tags
        if shared_tags:
            _bump_match(score_by_id, reasons_by_id, note_id, min(30.0, 12.0 * len(shared_tags)), "tags")

        shared_phrases = candidate_phrases & note_phrases
        if shared_phrases:
            _bump_match(score_by_id, reasons_by_id, note_id, min(30.0, 10.0 * len(shared_phrases)), "retrieval_phrases")

        if note_id in explicit_ids:
            _bump_match(score_by_id, reasons_by_id, note_id, 90.0, "explicit_note_id")

        note_title = str(fm.get("title", "")).strip().lower()
        if note_title and note_title == candidate.title.strip().lower():
            _bump_match(score_by_id, reasons_by_id, note_id, 40.0, "title")

    fts_path = data_dir / ".kb" / "fts.sqlite"
    if records and not fts_path.exists():
        reindex_data_dir(data_dir)
    query = " ".join([candidate.title, candidate.summary, *candidate.retrieval_phrases, *candidate.tags]).strip()
    if query and fts_path.exists():
        tokens = tokenize_query(query)
        lexical = query_candidates(fts_path, query=query, topic=None, knowledge_type=None)
        for match in lexical[:30]:
            note_id = str(match.get("id", ""))
            if note_id not in by_id:
                continue
            lexical_score = score_document(match, query=query, tokens=tokens, weights=retrieval)
            if lexical_score <= 0:
                continue
            _bump_match(
                score_by_id,
                reasons_by_id,
                note_id,
                min(35.0, float(lexical_score)),
                "lexical",
            )

    ranked = sorted(
        score_by_id.items(),
        key=lambda item: (-item[1], item[0]),
    )

    rendered: list[dict[str, Any]] = []
    for note_id, score in ranked[:cap]:
        record = by_id[note_id]
        fm = record.note.frontmatter
        rendered.append(
            {
                "note_id": record.note_id,
                "topic": str(fm.get("topic", "")),
                "title": str(fm.get("title", "")),
                "summary": str(fm.get("summary", "")),
                "knowledge_type": str(fm.get("knowledge_type", "")),
                "status": str(fm.get("status", "")),
                "tags": _coerce_string_list(fm.get("tags", [])),
                "retrieval_phrases": _coerce_string_list(fm.get("retrieval_phrases", [])),
                "path": record.path.as_posix(),
                "score": round(score, 2),
                "reasons": sorted(reasons_by_id.get(note_id, set())),
                "excerpt": _excerpt(record.note.body),
            }
        )
    return rendered


def _candidate_to_note(
    candidate: CandidateNote,
    *,
    classification: ClassificationResult,
    note_id: str,
    parsed: ParsedInput,
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
        "sources": [_source_entry(parsed)],
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


def _source_entry(parsed: ParsedInput) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "ingest",
        "ref": parsed.path.as_posix(),
        "hash": parsed.digest,
    }
    for key, value in sorted(parsed.source_metadata.items()):
        if value in (None, "", [], {}):
            continue
        entry[key] = value
    return entry


def _append_source_to_note(path: Path, *, parsed: ParsedInput) -> bool:
    note = read_note(path)
    frontmatter = dict(note.frontmatter)
    existing_sources = frontmatter.get("sources")
    if not isinstance(existing_sources, list):
        existing_sources = []

    source_ref = parsed.path.as_posix()
    for item in existing_sources:
        if not isinstance(item, Mapping):
            continue
        if item.get("hash") == parsed.digest or item.get("ref") == source_ref:
            return False

    appended = _source_entry(parsed)
    frontmatter["sources"] = [*existing_sources, appended]
    frontmatter["updated"] = date.today().isoformat()
    updated = Note(frontmatter=frontmatter, body=note.body)
    updated.validate()
    write_note(path, updated)
    return True


def _append_merge_review(
    data_dir: Path,
    *,
    parsed: ParsedInput,
    candidate: CandidateNote,
    target_note_ids: list[str],
    rationale: str,
) -> str | None:
    fingerprint = _review_fingerprint(
        "merge",
        source_hash=parsed.digest,
        candidate_title=candidate.title,
        candidate_body=candidate.body,
        target_note_ids=target_note_ids,
    )
    return add_review_item(
        data_dir,
        queue="merge",
        title=candidate.title,
        target_notes=target_note_ids,
        proposed_action="review and merge manually",
        payload={
            "source": parsed.path.as_posix(),
            "source_hash": parsed.digest,
            "target_note_ids": ", ".join(target_note_ids),
            "rationale": rationale or "Provider reported adds_nuance.",
            "candidate_title": candidate.title,
            "candidate_body": _ensure_trailing_newline(candidate.body).rstrip("\n"),
        },
        priority="medium",
        fingerprint=fingerprint,
    )


def _append_dispute_review(
    data_dir: Path,
    *,
    parsed: ParsedInput,
    candidate: CandidateNote,
    target_note_ids: list[str],
    rationale: str,
) -> str | None:
    fingerprint = _review_fingerprint(
        "dispute",
        source_hash=parsed.digest,
        candidate_title=candidate.title,
        candidate_body=candidate.body,
        target_note_ids=target_note_ids,
    )
    return add_review_item(
        data_dir,
        queue="dispute",
        title=candidate.title,
        target_notes=target_note_ids,
        proposed_action="review contradiction manually",
        payload={
            "source": parsed.path.as_posix(),
            "source_hash": parsed.digest,
            "target_note_ids": ", ".join(target_note_ids),
            "rationale": rationale or "Provider reported contradiction.",
            "candidate_title": candidate.title,
            "candidate_body": _ensure_trailing_newline(candidate.body).rstrip("\n"),
        },
        priority="high",
        fingerprint=fingerprint,
    )


def _mark_disputed(
    records: list[NoteRecord],
    *,
    parsed: ParsedInput,
    candidate: CandidateNote,
    rationale: str,
) -> list[str]:
    fingerprint = _review_fingerprint(
        "dispute",
        source_hash=parsed.digest,
        candidate_title=candidate.title,
        candidate_body=candidate.body,
        target_note_ids=[record.note_id for record in records],
    )
    changed: list[str] = []
    today = date.today().isoformat()
    for record in records:
        note = read_note(record.path)
        frontmatter = dict(note.frontmatter)
        record_changed = False
        if frontmatter.get("status") != "disputed":
            frontmatter["status"] = "disputed"
            record_changed = True

        disputes = frontmatter.get("disputes")
        if not isinstance(disputes, list):
            disputes = []
        if not any(isinstance(item, Mapping) and item.get("fingerprint") == fingerprint for item in disputes):
            disputes.append(
                {
                    "fingerprint": fingerprint,
                    "reported_at": datetime.now().isoformat(timespec="seconds"),
                    "source_ref": parsed.path.as_posix(),
                    "source_hash": parsed.digest,
                    "candidate_title": candidate.title,
                    "target_note_ids": [item.note_id for item in records],
                    "rationale": rationale,
                }
            )
            record_changed = True
        frontmatter["disputes"] = disputes
        if not record_changed:
            continue
        frontmatter["updated"] = today

        updated = Note(frontmatter=frontmatter, body=note.body)
        updated.validate()
        write_note(record.path, updated)
        changed.append(record.note_id)
    return changed


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


def _append_classification_review(
    data_dir: Path,
    *,
    parsed: ParsedInput,
    candidate: CandidateNote,
    classification: ClassificationResult,
    reason: str | None = None,
) -> str | None:
    payload = {
        "source": parsed.path.as_posix(),
        "source_hash": parsed.digest,
        "reason": reason or classification.reason or "low confidence or ambiguous classification",
        "title": candidate.title,
        "summary": candidate.summary,
        "suggested_topic": classification.topic,
        "suggested_type": classification.knowledge_type,
        "confidence": classification.confidence,
    }
    fingerprint = _review_fingerprint(
        "classification",
        source_hash=parsed.digest,
        candidate_title=candidate.title,
        candidate_body=json.dumps(payload, sort_keys=True),
        target_note_ids=[],
    )
    return add_review_item(
        data_dir,
        queue="classification",
        title=candidate.title,
        target_notes=[],
        proposed_action="review classification manually",
        payload=payload,
        priority="medium",
        fingerprint=fingerprint,
    )


def _review_fingerprint(
    prefix: str,
    *,
    source_hash: str,
    candidate_title: str,
    candidate_body: str,
    target_note_ids: list[str],
) -> str:
    payload = {
        "prefix": prefix,
        "source_hash": source_hash,
        "candidate_title": candidate_title.strip(),
        "candidate_body": candidate_body.strip(),
        "target_note_ids": sorted(set(target_note_ids)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:16]


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
    atomic_write_json(path, entries)


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
    if parsed.source_metadata:
        entry["source_metadata"] = dict(parsed.source_metadata)
    if archived_path is not None:
        entry["archived_path"] = archived_path.as_posix()
    if error:
        entry["error"] = error
    if any(
        item.get("hash") == entry["hash"]
        and item.get("source_path") == entry["source_path"]
        and item.get("status") == entry["status"]
        for item in entries
    ):
        return
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


def _log_error(
    data_dir: Path,
    message: str,
    *,
    operation_id: str | None = None,
    stage: str | None = None,
    file: Path | None = None,
) -> None:
    path = data_dir / ".kb" / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    details = []
    if operation_id:
        details.append(f"operation_id={operation_id}")
    if stage:
        details.append(f"stage={stage}")
    if file is not None:
        details.append(f"file={file}")
    prefix = f"{' '.join(details)} " if details else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {prefix}{message}\n")


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _excerpt(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _parent_topic(topic: str) -> str | None:
    segments = [part.strip() for part in re.split(r"[\\/]+", topic) if part.strip()]
    if len(segments) < 2:
        return None
    return normalize_topic_for_path("/".join(segments[:-1]))


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            items.append(stripped)
    return items


def _bump_match(
    score_by_id: dict[str, float],
    reasons_by_id: dict[str, set[str]],
    note_id: str,
    score: float,
    reason: str,
) -> None:
    score_by_id[note_id] = score_by_id.get(note_id, 0.0) + score
    reasons_by_id.setdefault(note_id, set()).add(reason)
