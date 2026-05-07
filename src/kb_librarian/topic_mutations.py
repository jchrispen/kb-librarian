"""Topic reorganization planning, proposal, and application helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.errors import KBLibrarianError
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.mutations import atomic_write_note, require_clean_worktree
from kb_librarian.notes import Note, read_note
from kb_librarian.review import add_review_item, ensure_review_state
from kb_librarian.storage import (
    TOPIC_SCOPE_TEMPLATE,
    NoteRecord,
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    load_note_records,
    normalize_topic_for_path,
    topic_dir_path,
    topic_scope_path,
)


@dataclass(frozen=True)
class TopicMove:
    note_id: str
    old_topic: str
    new_topic: str
    source_path: Path
    destination_path: Path
    reason: str = ""


@dataclass(frozen=True)
class TopicMutationResult:
    action: str
    moved_notes: list[TopicMove]
    affected_topics: list[str]


@dataclass(frozen=True)
class TopicProposalResult:
    review_item_id: str
    kind: str
    created: bool
    moves: list[TopicMove]


def rename_topic(
    data_dir: Path,
    *,
    old_topic: str,
    new_topic: str,
    force: bool = False,
) -> TopicMutationResult:
    old_normalized = normalize_topic_for_path(old_topic)
    new_normalized = normalize_topic_for_path(new_topic)
    if old_normalized == new_normalized:
        raise KBLibrarianError("Topic rename requires different old and new topic names.")
    records = _load_unique_records(data_dir)
    moves = _plan_prefix_moves(
        data_dir,
        records,
        source_topic=old_normalized,
        target_topic=new_normalized,
        reason=f"rename {old_normalized} to {new_normalized}",
    )
    return _apply_topic_moves(
        data_dir,
        moves,
        action=f"Renaming topic {old_normalized} to {new_normalized}",
        force=force,
    )


def promote_topics(
    data_dir: Path,
    *,
    topics: Iterable[str],
    parent: str,
    force: bool = False,
) -> TopicMutationResult:
    parent_normalized = normalize_topic_for_path(parent)
    normalized_topics = [normalize_topic_for_path(topic) for topic in topics if str(topic).strip()]
    if not normalized_topics:
        raise KBLibrarianError("topic promote requires at least one topic.")
    _ensure_no_overlapping_topics(normalized_topics)
    records = _load_unique_records(data_dir)
    moves: list[TopicMove] = []
    for topic in normalized_topics:
        if topic == parent_normalized or parent_normalized.startswith(f"{topic}/"):
            raise KBLibrarianError(f"Cannot promote topic {topic!r} under itself or one of its descendants.")
        leaf = topic.rsplit("/", maxsplit=1)[-1]
        target_topic = f"{parent_normalized}/{leaf}"
        if target_topic == topic:
            raise KBLibrarianError(f"Topic {topic!r} is already under {parent_normalized!r}.")
        moves.extend(
            _plan_prefix_moves(
                data_dir,
                records,
                source_topic=topic,
                target_topic=target_topic,
                reason=f"promote {topic} under {parent_normalized}",
            )
        )
    return _apply_topic_moves(
        data_dir,
        moves,
        action=f"Promoting topic(s) under {parent_normalized}",
        force=force,
    )


def queue_topic_split_proposal(
    data_dir: Path,
    *,
    topic: str,
    target_topics: list[str],
) -> TopicProposalResult:
    source_topic = normalize_topic_for_path(topic)
    targets = _unique_normalized_topics(target_topics)
    if len(targets) < 2:
        raise KBLibrarianError("topic split requires at least two --into topics.")
    if source_topic in targets:
        raise KBLibrarianError("topic split target topics must differ from the source topic.")

    records = _load_unique_records(data_dir)
    source_records = [record for record in records if record.topic_path == source_topic]
    if not source_records:
        raise KBLibrarianError(f"Topic {source_topic!r} has no direct notes to split.")
    moves = _suggest_split_moves(data_dir, source_records, targets)
    payload = {
        "kind": "split",
        "source_topic": source_topic,
        "target_topics": targets,
        "moves": [_move_payload(data_dir, move) for move in moves],
        "fingerprint_material": _move_fingerprint_material(moves),
    }
    fingerprint = _proposal_fingerprint(payload)
    item_id = add_review_item(
        data_dir,
        queue="topic",
        title=f"Split topic {source_topic} into {', '.join(targets)}",
        target_notes=[move.note_id for move in moves],
        proposed_action="review topic split proposal",
        payload=payload,
        priority="medium",
        fingerprint=fingerprint,
    )
    if item_id is None:
        item_id = _existing_topic_proposal_id(data_dir, fingerprint)
        created = False
    else:
        created = True
    if item_id is None:
        raise KBLibrarianError("Topic split proposal already exists but could not be resolved.")
    return TopicProposalResult(review_item_id=item_id, kind="split", created=created, moves=moves)


def queue_topic_merge_proposal(
    data_dir: Path,
    *,
    left_topic: str,
    right_topic: str,
    target_topic: str,
) -> TopicProposalResult:
    left = normalize_topic_for_path(left_topic)
    right = normalize_topic_for_path(right_topic)
    target = normalize_topic_for_path(target_topic)
    if left == right:
        raise KBLibrarianError("topic merge requires two different source topics.")

    records = _load_unique_records(data_dir)
    source_records = [record for record in records if record.topic_path in {left, right}]
    if not source_records:
        raise KBLibrarianError(f"Topics {left!r} and {right!r} have no direct notes to merge.")
    moves = [
        TopicMove(
            note_id=record.note_id,
            old_topic=record.topic_path,
            new_topic=target,
            source_path=record.path,
            destination_path=canonical_note_path(data_dir, target, record.note_id),
            reason=f"merge {left} and {right} as {target}",
        )
        for record in sorted(source_records, key=lambda item: item.note_id)
    ]
    payload = {
        "kind": "merge",
        "source_topics": [left, right],
        "target_topic": target,
        "moves": [_move_payload(data_dir, move) for move in moves],
        "fingerprint_material": _move_fingerprint_material(moves),
    }
    fingerprint = _proposal_fingerprint(payload)
    item_id = add_review_item(
        data_dir,
        queue="topic",
        title=f"Merge topics {left} and {right} as {target}",
        target_notes=[move.note_id for move in moves],
        proposed_action="review topic merge proposal",
        payload=payload,
        priority="medium",
        fingerprint=fingerprint,
    )
    if item_id is None:
        item_id = _existing_topic_proposal_id(data_dir, fingerprint)
        created = False
    else:
        created = True
    if item_id is None:
        raise KBLibrarianError("Topic merge proposal already exists but could not be resolved.")
    return TopicProposalResult(review_item_id=item_id, kind="merge", created=created, moves=moves)


def apply_topic_review_item(
    data_dir: Path,
    item: Mapping[str, Any],
    *,
    force: bool = False,
) -> TopicMutationResult:
    payload = item.get("payload", {})
    if not isinstance(payload, Mapping) or payload.get("kind") not in {"split", "merge"}:
        raise KBLibrarianError("Only topic split or merge proposals can be accepted.")
    moves = _moves_from_payload(data_dir, payload)
    return _apply_topic_moves(
        data_dir,
        moves,
        action=f"Accepting topic proposal {item.get('id')}",
        force=force,
    )


def _load_unique_records(data_dir: Path) -> list[NoteRecord]:
    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    return records


def _plan_prefix_moves(
    data_dir: Path,
    records: list[NoteRecord],
    *,
    source_topic: str,
    target_topic: str,
    reason: str,
) -> list[TopicMove]:
    affected = [
        record
        for record in records
        if record.topic_path == source_topic or record.topic_path.startswith(f"{source_topic}/")
    ]
    if not affected:
        raise KBLibrarianError(f"Topic {source_topic!r} has no notes to move.")

    moves: list[TopicMove] = []
    for record in sorted(affected, key=lambda item: (item.topic_path, item.note_id)):
        suffix = record.topic_path.removeprefix(source_topic)
        new_topic = f"{target_topic}{suffix}"
        moves.append(
            TopicMove(
                note_id=record.note_id,
                old_topic=record.topic_path,
                new_topic=new_topic,
                source_path=record.path,
                destination_path=canonical_note_path(data_dir, new_topic, record.note_id),
                reason=reason,
            )
        )
    return moves


def _apply_topic_moves(
    data_dir: Path,
    moves: list[TopicMove],
    *,
    action: str,
    force: bool,
) -> TopicMutationResult:
    if not moves:
        raise KBLibrarianError("No topic moves were planned.")
    require_clean_worktree(data_dir, force=force, operation=action)
    _validate_move_conflicts(moves)
    source_topics = sorted({move.old_topic for move in moves})
    destination_topics = sorted({move.new_topic for move in moves})
    _copy_topic_scopes(data_dir, moves=moves)
    for topic in destination_topics:
        ensure_topic_layout(data_dir, topic)
    for move in moves:
        _move_one_note(move)
    _cleanup_old_topic_dirs(data_dir, source_topics)
    reindex_data_dir(data_dir)
    return TopicMutationResult(
        action=action,
        moved_notes=moves,
        affected_topics=sorted(set(source_topics) | set(destination_topics)),
    )


def _move_one_note(move: TopicMove) -> None:
    current = read_note(move.source_path, validate=True)
    frontmatter = dict(current.frontmatter)
    frontmatter["topic"] = move.new_topic
    frontmatter["updated"] = date.today().isoformat()
    updated = Note(frontmatter=frontmatter, body=current.body)
    updated.validate()
    if move.source_path == move.destination_path:
        atomic_write_note(move.source_path, updated)
        return
    atomic_write_note(move.destination_path, updated)
    move.source_path.unlink()


def _validate_move_conflicts(moves: list[TopicMove]) -> None:
    by_destination: dict[Path, Path] = {}
    for move in moves:
        existing_source = by_destination.get(move.destination_path)
        if existing_source is not None and existing_source != move.source_path:
            raise KBLibrarianError(f"Multiple notes would move to {move.destination_path}.")
        by_destination[move.destination_path] = move.source_path

    source_paths = {move.source_path for move in moves}
    conflicts = [
        move.destination_path
        for move in moves
        if move.destination_path.exists() and move.destination_path not in source_paths
    ]
    if conflicts:
        listed = ", ".join(path.as_posix() for path in sorted(conflicts))
        raise KBLibrarianError(f"Topic move destination path conflict: {listed}")


def _copy_topic_scopes(
    data_dir: Path,
    *,
    moves: list[TopicMove],
) -> None:
    topic_pairs = sorted({(move.old_topic, move.new_topic) for move in moves})
    for source_topic, destination_topic in topic_pairs:
        source = topic_scope_path(data_dir, source_topic)
        if not source.exists():
            continue
        destination = topic_scope_path(data_dir, destination_topic)
        ensure_topic_layout(data_dir, destination_topic)
        if destination.exists() and destination.read_text(encoding="utf-8") != TOPIC_SCOPE_TEMPLATE:
            continue
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _cleanup_old_topic_dirs(data_dir: Path, source_topics: list[str]) -> None:
    for topic in sorted(source_topics, key=lambda item: item.count("/"), reverse=True):
        directory = topic_dir_path(data_dir, topic)
        if not directory.exists():
            continue
        for path in sorted(directory.glob("INDEX*.md")):
            if path.name == "INDEX.md" or path.stem.removeprefix("INDEX-").isdigit():
                path.unlink()
        scope = directory / "scope.txt"
        if scope.exists() and _can_remove_topic_scope(directory):
            scope.unlink()
        _remove_empty_parents(directory, stop=data_dir / "topics")


def _can_remove_topic_scope(directory: Path) -> bool:
    for path in directory.iterdir():
        if path.name == "scope.txt":
            continue
        return False
    return True


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    current = path
    while current != stop and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _ensure_no_overlapping_topics(topics: list[str]) -> None:
    unique = sorted(set(topics))
    if len(unique) != len(topics):
        raise KBLibrarianError("topic promote received duplicate topics.")
    for left_index, left in enumerate(unique):
        for right in unique[left_index + 1 :]:
            if right.startswith(f"{left}/"):
                raise KBLibrarianError("topic promote cannot move overlapping parent and child topics together.")


def _unique_normalized_topics(topics: list[str]) -> list[str]:
    normalized = [normalize_topic_for_path(topic) for topic in topics if str(topic).strip()]
    unique = sorted(set(normalized))
    if len(unique) != len(normalized):
        raise KBLibrarianError("Topic targets must be unique after normalization.")
    return normalized


def _suggest_split_moves(data_dir: Path, records: list[NoteRecord], targets: list[str]) -> list[TopicMove]:
    moves: list[TopicMove] = []
    for index, record in enumerate(sorted(records, key=lambda item: item.note_id)):
        target = _best_split_target(record, targets, fallback=targets[index % len(targets)])
        moves.append(
            TopicMove(
                note_id=record.note_id,
                old_topic=record.topic_path,
                new_topic=target,
                source_path=record.path,
                destination_path=canonical_note_path(data_dir, target, record.note_id),
                reason=f"split score matched {target}",
            )
        )
    return moves


def _best_split_target(record: NoteRecord, targets: list[str], *, fallback: str) -> str:
    haystack = _record_text(record)
    scored: list[tuple[int, str]] = []
    for target in targets:
        tokens = [part for part in target.replace("/", "-").split("-") if part]
        score = sum(1 for token in tokens if token in haystack)
        scored.append((score, target))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1] if scored and scored[0][0] > 0 else fallback


def _record_text(record: NoteRecord) -> str:
    fm = record.note.frontmatter
    parts = [
        str(fm.get("title", "")),
        str(fm.get("summary", "")),
        " ".join(str(item) for item in fm.get("tags", []) if isinstance(item, str)),
        " ".join(str(item) for item in fm.get("retrieval_phrases", []) if isinstance(item, str)),
        record.note.body,
    ]
    return normalize_topic_for_path(" ".join(parts)).replace("/", "-")


def _move_payload(data_dir: Path, move: TopicMove) -> dict[str, Any]:
    return {
        "note_id": move.note_id,
        "from_topic": move.old_topic,
        "to_topic": move.new_topic,
        "from_path": _relative_path(data_dir, move.source_path),
        "to_path": _relative_path(data_dir, move.destination_path),
        "reason": move.reason,
    }


def _moves_from_payload(data_dir: Path, payload: Mapping[str, Any]) -> list[TopicMove]:
    raw_moves = payload.get("moves")
    if not isinstance(raw_moves, list) or not raw_moves:
        raise KBLibrarianError("Topic proposal has no note moves to apply.")
    records = _load_unique_records(data_dir)
    by_id = {record.note_id: record for record in records}
    moves: list[TopicMove] = []
    for raw in raw_moves:
        if not isinstance(raw, Mapping):
            continue
        note_id = str(raw.get("note_id", "")).strip()
        target_topic = normalize_topic_for_path(str(raw.get("to_topic", "")))
        record = by_id.get(note_id)
        if record is None:
            raise KBLibrarianError(f"Topic proposal references missing note {note_id!r}.")
        moves.append(
            TopicMove(
                note_id=note_id,
                old_topic=record.topic_path,
                new_topic=target_topic,
                source_path=record.path,
                destination_path=canonical_note_path(data_dir, target_topic, note_id),
                reason=str(raw.get("reason", "")).strip(),
            )
        )
    return moves


def _move_fingerprint_material(moves: list[TopicMove]) -> list[dict[str, str]]:
    return [
        {
            "note_id": move.note_id,
            "from_topic": move.old_topic,
            "to_topic": move.new_topic,
        }
        for move in sorted(moves, key=lambda item: item.note_id)
    ]


def _proposal_fingerprint(payload: Mapping[str, Any]) -> str:
    material = {
        "queue": "topic",
        "kind": payload.get("kind"),
        "source_topic": payload.get("source_topic"),
        "source_topics": payload.get("source_topics"),
        "target_topic": payload.get("target_topic"),
        "target_topics": payload.get("target_topics"),
        "moves": payload.get("fingerprint_material"),
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"topic:{digest}"


def _existing_topic_proposal_id(data_dir: Path, fingerprint: str) -> str | None:
    state = ensure_review_state(data_dir, render=True)
    for item in state.get("items", []):
        if not isinstance(item, dict) or item.get("queue") != "topic":
            continue
        payload = item.get("payload", {})
        if isinstance(payload, dict) and payload.get("fingerprint") == fingerprint:
            return str(item.get("id", ""))
    return None


def _relative_path(data_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return path.as_posix()
