"""Note storage and repository traversal helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kb_librarian.atomic import atomic_write_text
from kb_librarian.errors import DuplicateNoteIdError, NoteParseError, NoteValidationError
from kb_librarian.notes import Note, read_note

TOPIC_SEGMENT_SAFE_PATTERN = re.compile(r"[^a-z0-9]+")
TOPIC_SEPARATOR_PATTERN = re.compile(r"[\\/]+")
NOTE_ID_REFERENCE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\b")

TOPIC_SCOPE_TEMPLATE = (
    "Describe what this topic is for and what it is not for.\n"
    "This file is intentionally user-editable.\n"
)


@dataclass(frozen=True)
class NoteRecord:
    """One canonical note plus its repository path metadata."""

    path: Path
    topic_path: str
    note: Note

    @property
    def note_id(self) -> str:
        return str(self.note.frontmatter["id"])


def normalize_topic_for_path(topic: str) -> str:
    segments: list[str] = []
    for raw_segment in TOPIC_SEPARATOR_PATTERN.split(topic.strip()):
        normalized_segment = TOPIC_SEGMENT_SAFE_PATTERN.sub("-", raw_segment.strip().lower()).strip("-")
        if normalized_segment:
            segments.append(normalized_segment)
    return "/".join(segments) if segments else "untitled-topic"


def topic_dir_path(data_dir: Path, topic: str) -> Path:
    normalized = normalize_topic_for_path(topic)
    segments = [segment for segment in normalized.split("/") if segment]
    return data_dir / "topics" / Path(*segments)


def topic_scope_path(data_dir: Path, topic: str) -> Path:
    return topic_dir_path(data_dir, topic) / "scope.txt"


def canonical_note_path(data_dir: Path, topic: str, note_id: str) -> Path:
    return topic_dir_path(data_dir, topic) / f"{note_id}.md"


def ensure_topic_layout(data_dir: Path, topic: str) -> list[Path]:
    created: list[Path] = []
    normalized_topic = normalize_topic_for_path(topic)
    topic_segments = [segment for segment in normalized_topic.split("/") if segment]
    for depth in range(1, len(topic_segments) + 1):
        topic_dir = data_dir / "topics" / Path(*topic_segments[:depth])
        if not topic_dir.exists():
            topic_dir.mkdir(parents=True, exist_ok=True)
            created.append(topic_dir)
        else:
            topic_dir.mkdir(parents=True, exist_ok=True)

        scope_path = topic_dir / "scope.txt"
        if not scope_path.exists():
            atomic_write_text(scope_path, TOPIC_SCOPE_TEMPLATE)
            created.append(scope_path)
    return created


def iter_note_files(data_dir: Path) -> list[Path]:
    topics_root = data_dir / "topics"
    if not topics_root.exists():
        return []

    files: list[Path] = []
    for path in sorted(topics_root.rglob("*.md")):
        if _is_generated_index_file(path):
            continue
        files.append(path)
    return files


def load_note_records(data_dir: Path, *, validate: bool = True) -> list[NoteRecord]:
    records: list[NoteRecord] = []
    topics_root = data_dir / "topics"
    for note_path in iter_note_files(data_dir):
        try:
            note = read_note(note_path, validate=validate)
        except (NoteParseError, NoteValidationError) as exc:
            raise type(exc)(f"{exc} (file: {note_path})") from exc

        topic_dir = note_path.parent
        topic_rel = topic_dir.relative_to(topics_root).as_posix()
        records.append(NoteRecord(path=note_path, topic_path=topic_rel, note=note))
    return records


def ensure_unique_note_ids(records: Iterable[NoteRecord]) -> None:
    by_id: dict[str, list[Path]] = {}
    for record in records:
        by_id.setdefault(record.note_id, []).append(record.path)

    duplicates = {note_id: paths for note_id, paths in by_id.items() if len(paths) > 1}
    if not duplicates:
        return

    lines = ["Duplicate note IDs detected:"]
    for note_id in sorted(duplicates):
        listed = ", ".join(str(path) for path in sorted(duplicates[note_id]))
        lines.append(f"- {note_id}: {listed}")
    raise DuplicateNoteIdError("\n".join(lines))


def existing_note_ids(records: Iterable[NoteRecord]) -> set[str]:
    return {record.note_id for record in records}


def _is_generated_index_file(path: Path) -> bool:
    if path.name == "INDEX.md":
        return True
    if not path.name.startswith("INDEX-") or path.suffix != ".md":
        return False
    return path.stem.removeprefix("INDEX-").isdigit()
