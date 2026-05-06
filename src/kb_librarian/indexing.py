"""Generated index builders for KB notes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.search_index import build_lexical_index
from kb_librarian.storage import NOTE_ID_REFERENCE_PATTERN, NoteRecord, ensure_unique_note_ids, load_note_records
from kb_librarian.usage import usage_stats_payload


@dataclass(frozen=True)
class ReindexResult:
    artifacts: list[Path]
    note_count: int
    topic_count: int
    index_backend: str
    compaction_clusters: int = 0
    compaction_review_items: list[str] | None = None
    stale_review_items: list[str] | None = None
    orphan_review_items: list[str] | None = None
    low_utility_review_items: list[str] | None = None


def reindex_data_dir(
    data_dir: Path,
    *,
    config: Mapping[str, Any] | None = None,
    scan_clusters: bool = False,
) -> ReindexResult:
    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    grouped = _group_by_topic(records)
    for topic_dir in sorted((data_dir / "topics").glob("*")):
        if topic_dir.is_dir():
            grouped.setdefault(topic_dir.name, [])

    artifacts: list[Path] = []
    top_index = data_dir / "INDEX.md"
    _write_if_changed(top_index, _render_top_level_index(data_dir, grouped))
    artifacts.append(top_index)

    for topic, topic_records in sorted(grouped.items()):
        topic_index_path = data_dir / "topics" / topic / "INDEX.md"
        _write_if_changed(topic_index_path, _render_topic_index(topic, topic_records))
        artifacts.append(topic_index_path)

    backlinks_path = data_dir / ".kb" / "backlinks.json"
    backlinks_payload = _build_backlinks(records)
    _write_json_if_changed(backlinks_path, backlinks_payload)
    artifacts.append(backlinks_path)

    stats_path = data_dir / ".kb" / "stats.json"
    stats_payload = {
        "notes": len(records),
        "topics": len(grouped),
        "usage": usage_stats_payload(data_dir),
    }
    _write_json_if_changed(stats_path, stats_payload)
    artifacts.append(stats_path)

    documents = [index_document(record) for record in records]
    fts_path = data_dir / ".kb" / "fts.sqlite"
    backend = build_lexical_index(fts_path, documents)
    artifacts.append(fts_path)

    manifest_path = data_dir / ".kb" / "index-manifest.json"
    manifest_payload = _build_manifest(records, grouped, backend=backend)
    _write_json_if_changed(manifest_path, manifest_payload)
    artifacts.append(manifest_path)

    compaction_clusters = 0
    compaction_review_items: list[str] = []
    stale_review_items: list[str] = []
    orphan_review_items: list[str] = []
    low_utility_review_items: list[str] = []
    if scan_clusters:
        if config is None:
            raise ValueError("config is required when scan_clusters=True")
        from kb_librarian.compaction import scan_compaction_clusters
        from kb_librarian.hygiene import scan_hygiene_queues

        scan = scan_compaction_clusters(data_dir, config=config)
        compaction_clusters = len(scan.clusters)
        compaction_review_items = scan.review_item_ids
        hygiene = scan_hygiene_queues(data_dir, config=config)
        stale_review_items = hygiene.stale_item_ids
        orphan_review_items = hygiene.orphan_item_ids
        low_utility_review_items = hygiene.low_utility_item_ids

    return ReindexResult(
        artifacts=artifacts,
        note_count=len(records),
        topic_count=len(grouped),
        index_backend=backend,
        compaction_clusters=compaction_clusters,
        compaction_review_items=compaction_review_items,
        stale_review_items=stale_review_items,
        orphan_review_items=orphan_review_items,
        low_utility_review_items=low_utility_review_items,
    )


def index_document(record: NoteRecord) -> dict[str, str]:
    frontmatter = record.note.frontmatter
    return {
        "id": str(frontmatter["id"]),
        "path": record.path.as_posix(),
        "title": str(frontmatter["title"]),
        "summary": str(frontmatter["summary"]),
        "topic": str(frontmatter["topic"]),
        "knowledge_type": str(frontmatter["knowledge_type"]),
        "tags": _join_string_list(frontmatter.get("tags")),
        "retrieval_phrases": _join_string_list(frontmatter.get("retrieval_phrases")),
        "agent_use": _join_string_list(frontmatter.get("agent_use")),
        "applies_when": _join_string_list(frontmatter.get("applies_when")),
        "does_not_apply_when": _join_string_list(frontmatter.get("does_not_apply_when")),
        "body": record.note.body,
        "status": str(frontmatter["status"]),
        "confidence": str(frontmatter["confidence"]),
        "updated": str(frontmatter["updated"]),
    }


def _group_by_topic(records: Iterable[NoteRecord]) -> dict[str, list[NoteRecord]]:
    grouped: dict[str, list[NoteRecord]] = {}
    for record in records:
        grouped.setdefault(record.topic_path, []).append(record)

    for topic_records in grouped.values():
        topic_records.sort(key=lambda item: item.note_id)
    return grouped


def _render_top_level_index(data_dir: Path, grouped: Mapping[str, list[NoteRecord]]) -> str:
    lines = [
        "# Knowledge Base",
        "",
        "This is an agent context artifact. Prefer `kb context`, `kb explore`, or `kb search` for retrieval.",
        "",
    ]
    if not grouped:
        lines.append("- No topics indexed yet.")
        lines.append("")
        return "\n".join(lines)

    for topic, records in sorted(grouped.items()):
        topic_dir = data_dir / "topics" / topic
        description = _topic_scope_summary(topic_dir)
        lines.append(
            f"- [{topic}](topics/{topic}/) — {description} ({len(records)} notes)"
        )
    lines.append("")
    return "\n".join(lines)


def _render_topic_index(topic: str, records: list[NoteRecord]) -> str:
    lines = [
        f"# Topic Index: {topic}",
        "",
        "| ID | Title | Summary | Type | Confidence | Status | Updated |",
        "|---|---|---|---|---|---|---|",
    ]
    for record in records:
        fm = record.note.frontmatter
        note_id = str(fm["id"])
        title = _escape_table_cell(str(fm["title"]))
        summary = _escape_table_cell(str(fm["summary"]))
        knowledge_type = str(fm["knowledge_type"])
        confidence = str(fm["confidence"])
        status = str(fm["status"])
        updated = str(fm["updated"])
        lines.append(
            f"| [{note_id}]({note_id}.md) | {title} | {summary} | {knowledge_type} | {confidence} | {status} | {updated} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_backlinks(records: Iterable[NoteRecord]) -> dict[str, list[str]]:
    backlinks: dict[str, set[str]] = {}
    for record in records:
        source_id = record.note_id
        for ref_id in _extract_referenced_ids(record):
            if ref_id == source_id:
                continue
            backlinks.setdefault(ref_id, set()).add(source_id)

    serialized: dict[str, list[str]] = {}
    for target_id in sorted(backlinks):
        serialized[target_id] = sorted(backlinks[target_id])
    return serialized


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


def _build_manifest(
    records: list[NoteRecord],
    grouped: Mapping[str, list[NoteRecord]],
    *,
    backend: str,
) -> dict[str, Any]:
    updated_dates = [date.fromisoformat(str(record.note.frontmatter["updated"])) for record in records]
    if updated_dates:
        build_time = f"{max(updated_dates).isoformat()}T00:00:00Z"
    else:
        build_time = "1970-01-01T00:00:00Z"

    indexed_files = sorted(record.path.as_posix() for record in records)
    return {
        "version": 1,
        "build_time": build_time,
        "note_count": len(records),
        "topic_count": len(grouped),
        "index_backend": backend,
        "indexed_files": indexed_files,
    }


def _join_string_list(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    string_values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return "\n".join(string_values)


def _topic_scope_summary(topic_dir: Path) -> str:
    scope_path = topic_dir / "scope.txt"
    if not scope_path.exists():
        return "topic notes"
    for line in scope_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            return line.strip()
    return "topic notes"


def _escape_table_cell(value: str) -> str:
    compact = " ".join(value.splitlines()).strip()
    return compact.replace("|", "\\|")


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    path.write_text(content, encoding="utf-8")
    return True


def _write_json_if_changed(path: Path, payload: Any) -> bool:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return _write_if_changed(path, rendered)
