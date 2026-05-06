"""Read-only KB health diagnostics and offline self-test."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.config import default_config, read_config_file, validate_config, write_config_file
from kb_librarian.errors import ConfigError, KBLibrarianError, NoteParseError, NoteValidationError
from kb_librarian.indexing import (
    _build_backlinks,
    group_records_for_indexing,
    render_index_pages,
    reindex_data_dir,
)
from kb_librarian.ingest import ingest
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import read_note
from kb_librarian.paths import DIRECTORIES, LOG_FILES, REVIEW_STATE_FILE, ROOT_FILES, config_path
from kb_librarian.review import STATE_VERSION, ReviewStateError, validate_review_item
from kb_librarian.search_index import load_backend, query_candidates
from kb_librarian.storage import NOTE_ID_REFERENCE_PATTERN, NoteRecord, iter_note_files

SEVERITIES = ("ok", "warn", "error")
SUPPORTED_PROVIDERS = {"anthropic", "mock"}


@dataclass(frozen=True)
class DoctorFinding:
    subsystem: str
    severity: str
    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class DoctorReport:
    data_dir: Path
    findings: list[DoctorFinding]

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warn")

    @property
    def ok_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "ok")


@dataclass(frozen=True)
class DoctorSelfTestReport:
    findings: list[DoctorFinding]

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "error")


def run_doctor(data_dir: str | Path, *, env: Mapping[str, str] | None = None) -> DoctorReport:
    root = Path(data_dir).expanduser()
    environ = os.environ if env is None else env
    findings: list[DoctorFinding] = []

    if not root.exists():
        findings.append(
            DoctorFinding(
                "Layout",
                "error",
                "data-dir-missing",
                f"Data directory does not exist: {root}",
                root,
            )
        )
        return DoctorReport(data_dir=root, findings=findings)
    if not root.is_dir():
        findings.append(
            DoctorFinding(
                "Layout",
                "error",
                "data-dir-not-directory",
                f"Data directory is not a directory: {root}",
                root,
            )
        )
        return DoctorReport(data_dir=root, findings=findings)

    _check_layout(root, findings)
    config = _check_config(root, findings)
    records = _check_notes(root, findings)
    note_ids = {record.note_id for record in records}
    if records:
        _check_note_links(records, note_ids, findings)
    else:
        findings.append(DoctorFinding("Notes", "ok", "no-notes", "No notes found."))

    _check_review_state(root, note_ids, findings)
    _check_markdown_indexes(root, records, config, findings)
    _check_backlinks(root, records, findings)
    _check_manifest(root, records, config, findings)
    _check_fts(root, records, findings)
    _check_raw_ingest_errors(root, findings)
    if config is not None:
        _check_provider_routes(config, environ, findings)

    return DoctorReport(data_dir=root, findings=findings)


def render_doctor_report(report: DoctorReport) -> str:
    lines = [f"KB Doctor: {report.data_dir}", ""]
    grouped: dict[str, list[DoctorFinding]] = {}
    for finding in report.findings:
        grouped.setdefault(finding.subsystem, []).append(finding)

    for subsystem in grouped:
        lines.append(subsystem)
        for finding in grouped[subsystem]:
            location = f" ({finding.path})" if finding.path is not None else ""
            lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}{location}")
        lines.append("")

    lines.append(
        f"Summary: ok={report.ok_count}, warn={report.warning_count}, error={report.error_count}"
    )
    return "\n".join(lines) + "\n"


def run_doctor_self_test() -> DoctorSelfTestReport:
    findings: list[DoctorFinding] = []
    try:
        with tempfile.TemporaryDirectory(prefix="kb-librarian-doctor-") as tmp:
            data_dir = Path(tmp) / "kb"
            initialize_data_dir(data_dir)
            findings.append(DoctorFinding("Self-Test", "ok", "init", "Initialized temporary KB."))

            config = default_config(data_dir)
            config["providers"]["mock"] = {}
            for operation in list(config["operations"]):
                config["operations"][operation] = {"provider": "mock", "model": f"mock-{operation}"}
            write_config_file(config_path(data_dir), config)
            findings.append(DoctorFinding("Self-Test", "ok", "mock-provider", "Configured mock provider routes."))

            source = data_dir / "raw" / "doctor-self-test.md"
            source.write_text(
                "# Doctor self test retrieval\n\n"
                "Use local mock provider flows to verify ingest, indexing, and search remain offline.\n",
                encoding="utf-8",
            )
            ingest_report = ingest(data_dir, config=config, file_path=source)
            if ingest_report.errors or not ingest_report.created_notes:
                findings.append(
                    DoctorFinding(
                        "Self-Test",
                        "error",
                        "ingest",
                        "Mock ingest did not create a note.",
                    )
                )
                return DoctorSelfTestReport(findings)
            findings.append(DoctorFinding("Self-Test", "ok", "ingest", "Mock ingest created a note."))

            reindex_result = reindex_data_dir(data_dir, config=config)
            if reindex_result.note_count != 1:
                findings.append(
                    DoctorFinding(
                        "Self-Test",
                        "error",
                        "reindex",
                        f"Expected 1 indexed note, found {reindex_result.note_count}.",
                    )
                )
                return DoctorSelfTestReport(findings)
            findings.append(DoctorFinding("Self-Test", "ok", "reindex", "Rebuilt local indexes."))

            candidates = query_candidates(
                data_dir / ".kb" / "fts.sqlite",
                query="doctor self test retrieval",
            )
            if not candidates:
                findings.append(
                    DoctorFinding(
                        "Self-Test",
                        "error",
                        "search",
                        "Search returned no candidates for the self-test note.",
                    )
                )
                return DoctorSelfTestReport(findings)
            findings.append(DoctorFinding("Self-Test", "ok", "search", "Search found the self-test note."))

            doctor_report = run_doctor(data_dir, env={})
            if doctor_report.error_count:
                findings.extend(
                    DoctorFinding(
                        "Self-Test",
                        "error",
                        "doctor",
                        finding.message,
                        finding.path,
                    )
                    for finding in doctor_report.findings
                    if finding.severity == "error"
                )
                return DoctorSelfTestReport(findings)
            findings.append(DoctorFinding("Self-Test", "ok", "doctor", "Doctor completed without errors."))
    except Exception as exc:  # pragma: no cover - defensive boundary for CLI diagnostics.
        findings.append(DoctorFinding("Self-Test", "error", "unexpected", f"Unexpected self-test failure: {exc}"))
    return DoctorSelfTestReport(findings)


def render_self_test_report(report: DoctorSelfTestReport) -> str:
    lines = ["KB Doctor self-test", ""]
    for finding in report.findings:
        location = f" ({finding.path})" if finding.path is not None else ""
        lines.append(f"[{finding.severity}] {finding.code}: {finding.message}{location}")
    lines.append("")
    outcome = "passed" if report.error_count == 0 else "failed"
    lines.append(f"Self-test {outcome}.")
    return "\n".join(lines) + "\n"


def _check_layout(data_dir: Path, findings: list[DoctorFinding]) -> None:
    missing_dirs = [data_dir / name for name in DIRECTORIES if not (data_dir / name).is_dir()]
    if missing_dirs:
        for path in missing_dirs:
            findings.append(
                DoctorFinding(
                    "Layout",
                    "error",
                    "directory-missing",
                    "Required directory is missing; run `kb init` or restore it.",
                    path,
                )
            )
    else:
        findings.append(DoctorFinding("Layout", "ok", "directories", "Required directories are present."))

    for name in ROOT_FILES:
        path = data_dir / name
        if not path.is_file():
            findings.append(
                DoctorFinding(
                    "Layout",
                    "warn",
                    "root-file-missing",
                    "Expected root file is missing; run `kb init` or `kb reindex`.",
                    path,
                )
            )

    for name in LOG_FILES:
        path = data_dir / name
        if not path.exists():
            findings.append(
                DoctorFinding(
                    "Layout",
                    "warn",
                    "log-file-missing",
                    "Expected log file is missing; run `kb init` to recreate it.",
                    path,
                )
            )


def _check_config(data_dir: Path, findings: list[DoctorFinding]) -> dict[str, Any] | None:
    path = config_path(data_dir)
    if not path.is_file():
        findings.append(
            DoctorFinding(
                "Config",
                "error",
                "config-missing",
                "Config file is missing; run `kb init`.",
                path,
            )
        )
        return None
    try:
        config = read_config_file(path)
        validate_config(config)
    except ConfigError as exc:
        findings.append(DoctorFinding("Config", "error", "config-invalid", str(exc), path))
        return None

    configured_dir = Path(str(config.get("data_dir", ""))).expanduser()
    if configured_dir != data_dir:
        findings.append(
            DoctorFinding(
                "Config",
                "warn",
                "data-dir-mismatch",
                f"Config data_dir points at {configured_dir}; current data dir is {data_dir}.",
                path,
            )
        )
    findings.append(DoctorFinding("Config", "ok", "config-valid", "Config file is valid.", path))
    return config


def _check_notes(data_dir: Path, findings: list[DoctorFinding]) -> list[NoteRecord]:
    records: list[NoteRecord] = []
    topics_root = data_dir / "topics"
    for path in iter_note_files(data_dir):
        try:
            note = read_note(path, validate=True)
        except (NoteParseError, NoteValidationError) as exc:
            findings.append(DoctorFinding("Notes", "error", "note-invalid", str(exc), path))
            continue
        records.append(
            NoteRecord(
                path=path,
                topic_path=path.parent.relative_to(topics_root).as_posix(),
                note=note,
            )
        )

    by_id: dict[str, list[Path]] = {}
    for record in records:
        by_id.setdefault(record.note_id, []).append(record.path)
    duplicates = {note_id: paths for note_id, paths in by_id.items() if len(paths) > 1}
    for note_id in sorted(duplicates):
        paths = ", ".join(str(path) for path in sorted(duplicates[note_id]))
        findings.append(
            DoctorFinding(
                "Notes",
                "error",
                "duplicate-note-id",
                f"Duplicate note ID {note_id}: {paths}",
            )
        )

    if records and not any(finding.subsystem == "Notes" and finding.severity == "error" for finding in findings):
        findings.append(DoctorFinding("Notes", "ok", "note-schema", f"Validated {len(records)} note(s)."))
    return records


def _check_note_links(
    records: Iterable[NoteRecord],
    note_ids: set[str],
    findings: list[DoctorFinding],
) -> None:
    broken = 0
    for record in records:
        for reference in sorted(_extract_referenced_ids(record)):
            if reference == record.note_id or reference in note_ids:
                continue
            broken += 1
            findings.append(
                DoctorFinding(
                    "Notes",
                    "error",
                    "broken-note-link",
                    f"Reference {reference} does not resolve to a note ID.",
                    record.path,
                )
            )
    if broken == 0:
        findings.append(DoctorFinding("Notes", "ok", "note-links", "Note ID references resolve."))


def _check_review_state(data_dir: Path, note_ids: set[str], findings: list[DoctorFinding]) -> None:
    path = data_dir / REVIEW_STATE_FILE
    if not path.is_file():
        findings.append(
            DoctorFinding(
                "Review",
                "error",
                "review-state-missing",
                "Durable review state is missing; run `kb init` or restore review history.",
                path,
            )
        )
        return
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ReviewStateError("Review state must be a JSON object.")
        if loaded.get("version") != STATE_VERSION:
            raise ReviewStateError(f"Review state has unsupported version {loaded.get('version')!r}.")
        items = loaded.get("items")
        if not isinstance(items, list):
            raise ReviewStateError("Review state items must be a list.")
        for item in items:
            if not isinstance(item, dict):
                raise ReviewStateError("Review state items must contain only objects.")
            validate_review_item(item)
            missing_targets = sorted(
                str(note_id)
                for note_id in item.get("target_notes", [])
                if str(note_id).strip() and str(note_id) not in note_ids
            )
            if missing_targets:
                findings.append(
                    DoctorFinding(
                        "Review",
                        "warn",
                        "review-target-missing",
                        f"Review item {item.get('id')} targets missing note IDs: {', '.join(missing_targets)}.",
                        path,
                    )
                )
    except (json.JSONDecodeError, OSError, ReviewStateError) as exc:
        findings.append(DoctorFinding("Review", "error", "review-state-unreadable", str(exc), path))
        return
    findings.append(DoctorFinding("Review", "ok", "review-state", "Review state is readable.", path))


def _check_markdown_indexes(
    data_dir: Path,
    records: list[NoteRecord],
    config: Mapping[str, Any] | None,
    findings: list[DoctorFinding],
) -> None:
    grouped = group_records_for_indexing(data_dir, records)
    expected_pages = render_index_pages(data_dir, grouped, config=config)
    warnings_before = _subsystem_count(findings, "Indexes", "warn")
    errors_before = _subsystem_count(findings, "Indexes", "error")
    expected_paths = {page.path for page in expected_pages}

    for page in expected_pages:
        if not page.path.is_file():
            findings.append(
                DoctorFinding(
                    "Indexes",
                    "warn",
                    "markdown-index-missing",
                    "Generated markdown index is missing; run `kb reindex`.",
                    page.path,
                )
            )
            continue
        try:
            current = page.path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(DoctorFinding("Indexes", "error", "markdown-index-unreadable", str(exc), page.path))
            continue
        if current != page.content:
            findings.append(
                DoctorFinding(
                    "Indexes",
                    "warn",
                    "markdown-index-stale",
                    "Generated markdown index differs from current notes; run `kb reindex`.",
                    page.path,
                )
            )

    for stale_path in _generated_index_page_files(data_dir):
        if stale_path not in expected_paths:
            findings.append(
                DoctorFinding(
                    "Indexes",
                    "warn",
                    "markdown-index-stale-page",
                    "Stale generated index page remains; run `kb reindex`.",
                    stale_path,
                )
            )

    if (
        _subsystem_count(findings, "Indexes", "warn") == warnings_before
        and _subsystem_count(findings, "Indexes", "error") == errors_before
    ):
        findings.append(DoctorFinding("Indexes", "ok", "markdown-indexes", "Markdown indexes are current."))


def _check_backlinks(data_dir: Path, records: list[NoteRecord], findings: list[DoctorFinding]) -> None:
    path = data_dir / ".kb" / "backlinks.json"
    if not path.is_file():
        findings.append(
            DoctorFinding(
                "Indexes",
                "warn",
                "backlinks-missing",
                "Backlinks artifact is missing; run `kb reindex`.",
                path,
            )
        )
        return
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(DoctorFinding("Indexes", "error", "backlinks-unreadable", str(exc), path))
        return
    expected = _build_backlinks(records)
    if current != expected:
        findings.append(
            DoctorFinding(
                "Indexes",
                "warn",
                "backlinks-stale",
                "Backlinks artifact differs from current notes; run `kb reindex`.",
                path,
            )
        )


def _check_manifest(
    data_dir: Path,
    records: list[NoteRecord],
    config: Mapping[str, Any] | None,
    findings: list[DoctorFinding],
) -> None:
    path = data_dir / ".kb" / "index-manifest.json"
    if not path.is_file():
        findings.append(
            DoctorFinding(
                "Indexes",
                "warn",
                "manifest-missing",
                "Index manifest is missing; run `kb reindex`.",
                path,
            )
        )
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(DoctorFinding("Indexes", "error", "manifest-unreadable", str(exc), path))
        return
    if not isinstance(manifest, dict):
        findings.append(DoctorFinding("Indexes", "error", "manifest-invalid", "Index manifest must be an object.", path))
        return

    grouped = group_records_for_indexing(data_dir, records)
    expected_files = sorted(record.path.as_posix() for record in records)
    expected_artifacts = sorted(
        page.path.relative_to(data_dir).as_posix() for page in render_index_pages(data_dir, grouped, config=config)
    )
    checks = {
        "note_count": len(records),
        "topic_count": len(grouped),
        "indexed_files": expected_files,
        "generated_artifacts": expected_artifacts,
    }
    for key, expected in checks.items():
        if manifest.get(key) != expected:
            findings.append(
                DoctorFinding(
                    "Indexes",
                    "warn",
                    "manifest-stale",
                    f"Manifest field {key} is stale or missing; run `kb reindex`.",
                    path,
                )
            )


def _check_fts(data_dir: Path, records: list[NoteRecord], findings: list[DoctorFinding]) -> None:
    path = data_dir / ".kb" / "fts.sqlite"
    if not path.is_file():
        if records:
            findings.append(
                DoctorFinding(
                    "Retrieval",
                    "error",
                    "fts-missing",
                    "Lexical index is missing; run `kb reindex` before relying on retrieval.",
                    path,
                )
            )
        else:
            findings.append(DoctorFinding("Retrieval", "ok", "fts-empty-kb", "No lexical index needed for an empty KB."))
        return
    try:
        backend = load_backend(path)
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT id, path FROM notes ORDER BY id, path").fetchall()
        finally:
            conn.close()
    except (KBLibrarianError, sqlite3.Error, OSError) as exc:
        findings.append(DoctorFinding("Retrieval", "error", "fts-unreadable", str(exc), path))
        return

    expected = sorted((record.note_id, record.path.as_posix()) for record in records)
    current = sorted((str(row[0]), str(row[1])) for row in rows)
    if current != expected:
        findings.append(
            DoctorFinding(
                "Retrieval",
                "error",
                "fts-stale",
                "Lexical index contents do not match note files; run `kb reindex`.",
                path,
            )
        )
        return
    findings.append(DoctorFinding("Retrieval", "ok", "fts-current", f"Lexical index is current using {backend}.", path))


def _check_raw_ingest_errors(data_dir: Path, findings: list[DoctorFinding]) -> None:
    path = data_dir / ".kb" / "errors.log"
    if not path.exists():
        findings.append(
            DoctorFinding(
                "Ingest",
                "warn",
                "errors-log-missing",
                "Ingest error log is missing; run `kb init` to recreate it.",
                path,
            )
        )
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(DoctorFinding("Ingest", "error", "errors-log-unreadable", str(exc), path))
        return
    if text.strip():
        findings.append(
            DoctorFinding(
                "Ingest",
                "warn",
                "raw-ingest-errors",
                "Ingest error log is not empty; inspect `.kb/errors.log` and pending raw files.",
                path,
            )
        )
    else:
        findings.append(DoctorFinding("Ingest", "ok", "raw-ingest-errors", "No recorded ingest errors.", path))


def _check_provider_routes(
    config: Mapping[str, Any],
    env: Mapping[str, str],
    findings: list[DoctorFinding],
) -> None:
    providers = config.get("providers")
    operations = config.get("operations")
    if not isinstance(providers, Mapping) or not isinstance(operations, Mapping):
        return

    warnings_before = _subsystem_count(findings, "Providers", "warn")
    errors_before = _subsystem_count(findings, "Providers", "error")
    routed_providers = sorted(
        {
            str(route.get("provider")).strip()
            for route in operations.values()
            if isinstance(route, Mapping) and str(route.get("provider", "")).strip()
        }
    )
    for provider_name in routed_providers:
        provider_config = providers.get(provider_name)
        if not isinstance(provider_config, Mapping):
            findings.append(
                DoctorFinding(
                    "Providers",
                    "error",
                    "provider-missing",
                    f"Provider {provider_name!r} is used by an operation but is not configured.",
                )
            )
            continue
        if provider_name not in SUPPORTED_PROVIDERS:
            findings.append(
                DoctorFinding(
                    "Providers",
                    "error",
                    "provider-unsupported",
                    f"Provider {provider_name!r} is configured but this CLI does not support it.",
                )
            )
            continue
        if provider_name == "anthropic":
            api_key_env = provider_config.get("api_key_env")
            if not isinstance(api_key_env, str) or not api_key_env.strip():
                findings.append(
                    DoctorFinding(
                        "Providers",
                        "error",
                        "provider-api-env-missing",
                        "Anthropic provider is missing api_key_env.",
                    )
                )
            elif not env.get(api_key_env):
                findings.append(
                    DoctorFinding(
                        "Providers",
                        "warn",
                        "provider-api-key-unset",
                        f"Environment variable {api_key_env} is not set for provider-backed commands.",
                    )
                )

    if (
        _subsystem_count(findings, "Providers", "warn") == warnings_before
        and _subsystem_count(findings, "Providers", "error") == errors_before
    ):
        findings.append(DoctorFinding("Providers", "ok", "provider-routes", "Provider routes are configured."))


def _extract_referenced_ids(record: NoteRecord) -> set[str]:
    ids = set(NOTE_ID_REFERENCE_PATTERN.findall(record.note.body))
    _extract_ids_from_value(record.note.frontmatter, into=ids)
    return ids


def _extract_ids_from_value(value: Any, *, into: set[str]) -> None:
    if isinstance(value, str):
        into.update(NOTE_ID_REFERENCE_PATTERN.findall(value))
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _extract_ids_from_value(nested, into=into)
        return
    if isinstance(value, list):
        for nested in value:
            _extract_ids_from_value(nested, into=into)


def _generated_index_page_files(data_dir: Path) -> list[Path]:
    pages = [path for path in data_dir.glob("INDEX-*.md") if _is_numbered_index_page(path)]
    topics_root = data_dir / "topics"
    if topics_root.exists():
        pages.extend(path for path in topics_root.rglob("INDEX-*.md") if _is_numbered_index_page(path))
    return sorted(pages)


def _is_numbered_index_page(path: Path) -> bool:
    stem = path.stem
    if not stem.startswith("INDEX-"):
        return False
    return stem.removeprefix("INDEX-").isdigit()


def _subsystem_count(findings: Iterable[DoctorFinding], subsystem: str, severity: str) -> int:
    return sum(1 for finding in findings if finding.subsystem == subsystem and finding.severity == severity)
