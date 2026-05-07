"""Durable review state and rendered queue summaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.errors import KBLibrarianError
from kb_librarian.notes import KNOWLEDGE_TYPES, Note, body_template, generate_note_id, read_note, write_note
from kb_librarian.storage import (
    canonical_note_path,
    ensure_topic_layout,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
)


STATE_VERSION = 1
PENDING_STATUSES = {"pending", "deferred"}
RESOLVED_STATUSES = {"accepted", "rejected"}
STATUSES = PENDING_STATUSES | RESOLVED_STATUSES
PRIORITIES = {"high", "medium", "low"}
ITEM_RE = re.compile(r"^##\s+item:\s+(.+?)\s*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class QueueDefinition:
    queue: str
    count_key: str
    id_prefix: str
    file_name: str | None
    file_title: str | None
    priority: str
    proposed_action: str
    order: int


QUEUE_DEFINITIONS: dict[str, QueueDefinition] = {
    "dispute": QueueDefinition(
        queue="dispute",
        count_key="dispute",
        id_prefix="dispute",
        file_name="disputes.md",
        file_title="Disputes",
        priority="high",
        proposed_action="review contradiction manually",
        order=10,
    ),
    "merge": QueueDefinition(
        queue="merge",
        count_key="merge",
        id_prefix="merge",
        file_name="pending-merge.md",
        file_title="Pending Merge",
        priority="medium",
        proposed_action="review and merge manually",
        order=20,
    ),
    "classification": QueueDefinition(
        queue="classification",
        count_key="classification",
        id_prefix="classification",
        file_name="pending-classification.md",
        file_title="Pending Classification",
        priority="medium",
        proposed_action="review classification manually",
        order=30,
    ),
    "searchmiss": QueueDefinition(
        queue="searchmiss",
        count_key="searchmiss",
        id_prefix="searchmiss",
        file_name="search-misses.md",
        file_title="Search Misses",
        priority="medium",
        proposed_action="review missed search result",
        order=40,
    ),
    "compaction": QueueDefinition(
        queue="compaction",
        count_key="compaction",
        id_prefix="compaction",
        file_name="pending-compaction.md",
        file_title="Pending Compaction",
        priority="medium",
        proposed_action="draft or review compaction proposal",
        order=45,
    ),
    "topic": QueueDefinition(
        queue="topic",
        count_key="topic",
        id_prefix="topic",
        file_name="pending-topic.md",
        file_title="Pending Topic Reorganization",
        priority="medium",
        proposed_action="review topic reorganization proposal",
        order=46,
    ),
    "stale": QueueDefinition(
        queue="stale",
        count_key="stale",
        id_prefix="stale",
        file_name="stale.md",
        file_title="Stale Notes",
        priority="medium",
        proposed_action="reverify or refresh note",
        order=47,
    ),
    "orphan": QueueDefinition(
        queue="orphan",
        count_key="orphan",
        id_prefix="orphan",
        file_name="orphans.md",
        file_title="Orphan Notes",
        priority="medium",
        proposed_action="link, merge, retopic, archive, or keep with rationale",
        order=48,
    ),
    "low_utility": QueueDefinition(
        queue="low_utility",
        count_key="low_utility",
        id_prefix="lowutility",
        file_name="low-utility.md",
        file_title="Low Utility",
        priority="medium",
        proposed_action="improve note utility or resolve disputed signal",
        order=49,
    ),
    "duplicate": QueueDefinition(
        queue="duplicate",
        count_key="duplicate",
        id_prefix="duplicate",
        file_name=None,
        file_title=None,
        priority="low",
        proposed_action="inspect duplicate source",
        order=50,
    ),
    "unsupported_file": QueueDefinition(
        queue="unsupported_file",
        count_key="unsupported_file",
        id_prefix="unsupported",
        file_name=None,
        file_title=None,
        priority="low",
        proposed_action="convert or remove unsupported raw file",
        order=60,
    ),
}


@dataclass(frozen=True)
class ReviewSummaryItem:
    kind: str
    item_id: str
    summary: str


@dataclass(frozen=True)
class ReviewMutationResult:
    item_id: str
    action: str
    changed: bool
    message: str


class ReviewStateError(KBLibrarianError):
    """Raised when durable review state is malformed."""


def review_state_path(data_dir: Path) -> Path:
    return data_dir / "review" / "review-items.json"


def ensure_review_state(data_dir: Path, *, render: bool = True) -> dict[str, Any]:
    """Load durable review state, bootstrapping it from Phase 1 surfaces if missing."""

    path = review_state_path(data_dir)
    if path.exists():
        state = _load_state(path)
    else:
        state = {"version": STATE_VERSION, "items": []}
        for imported in _legacy_markdown_items(data_dir):
            _append_item(
                state,
                queue=str(imported["queue"]),
                title=str(imported["title"]),
                target_notes=list(imported["target_notes"]),
                proposed_action=str(imported["proposed_action"]),
                payload=dict(imported["payload"]),
                priority=str(imported["priority"]),
                created=str(imported["created"]),
                history=list(imported["history"]),
                fingerprint=str(imported["fingerprint"]),
            )
        for imported in _duplicate_items_from_ingest_log(data_dir):
            _append_item(state, **imported)
        for imported in _unsupported_items_from_error_log(data_dir):
            _append_item(state, **imported)
        _write_state(path, state)

    if render:
        render_review_queues(data_dir, state=state)
    return state


def add_review_item(
    data_dir: Path,
    *,
    queue: str,
    title: str,
    target_notes: list[str] | None,
    proposed_action: str | None,
    payload: Mapping[str, Any],
    priority: str | None = None,
    created: str | None = None,
    fingerprint: str | None = None,
) -> str | None:
    """Append a pending review item unless an equivalent item already exists."""

    state = ensure_review_state(data_dir, render=False)
    definition = _definition(queue)
    payload_dict = dict(payload)
    item_fingerprint = fingerprint or _payload_fingerprint(
        queue=queue,
        title=title,
        target_notes=target_notes or [],
        proposed_action=proposed_action or definition.proposed_action,
        payload=payload_dict,
    )
    if _find_existing_item(state, item_fingerprint) is not None:
        return None

    item_id = _append_item(
        state,
        queue=queue,
        title=title,
        target_notes=target_notes or [],
        proposed_action=proposed_action or definition.proposed_action,
        payload={**payload_dict, "fingerprint": item_fingerprint},
        priority=priority or definition.priority,
        created=created or date.today().isoformat(),
        history=[
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": "created",
                "source": "api",
            }
        ],
        fingerprint=item_fingerprint,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return item_id


def upsert_search_miss_review_item(
    data_dir: Path,
    *,
    title: str,
    payload: Mapping[str, Any],
    priority: str,
    created: str,
    fingerprint: str,
) -> str | None:
    """Create or refresh a pending search-miss review item."""

    definition = QUEUE_DEFINITIONS["searchmiss"]
    payload_dict = dict(payload)
    state = ensure_review_state(data_dir, render=False)
    existing = _find_existing_item(state, fingerprint)
    if existing is not None:
        if str(existing.get("status")) in RESOLVED_STATUSES:
            return None
        existing["title"] = title.strip() or str(existing["title"])
        existing["priority"] = priority
        existing["payload"] = {**payload_dict, "fingerprint": fingerprint}
        existing["updated"] = date.today().isoformat()
        history = existing.get("history")
        if isinstance(history, list):
            history.append(
                {
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "action": "updated",
                    "source": ".kb/search-misses.log",
                }
            )
        _write_state(review_state_path(data_dir), state)
        render_review_queues(data_dir, state=state)
        return str(existing["id"])

    item_id = _append_item(
        state,
        queue="searchmiss",
        title=title,
        target_notes=[],
        proposed_action=definition.proposed_action,
        payload={**payload_dict, "fingerprint": fingerprint},
        priority=priority,
        created=created,
        history=[
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": "created",
                "source": ".kb/search-misses.log",
            }
        ],
        fingerprint=fingerprint,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return item_id


def upsert_hygiene_review_item(
    data_dir: Path,
    *,
    queue: str,
    title: str,
    target_notes: list[str],
    payload: Mapping[str, Any],
    priority: str,
    created: str,
    fingerprint: str,
    source: str,
    return_existing: bool = False,
) -> str | None:
    """Create or refresh a stale/orphan/low-utility item keyed by fingerprint."""

    definition = _definition(queue)
    payload_dict = dict(payload)
    state = ensure_review_state(data_dir, render=False)
    existing = _find_existing_item(state, fingerprint)
    if existing is not None:
        existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
        existing_clean = dict(existing_payload) if isinstance(existing_payload, dict) else {}
        existing_clean.pop("fingerprint", None)
        incoming_clean = dict(payload_dict)

        changed = (
            existing_clean != incoming_clean
            or str(existing.get("title", "")) != (title.strip() or str(existing.get("title", "")))
            or sorted(str(note_id) for note_id in existing.get("target_notes", []) if str(note_id).strip())
            != sorted(str(note_id) for note_id in target_notes if str(note_id).strip())
            or str(existing.get("priority")) != priority
        )
        if not changed:
            return str(existing["id"]) if return_existing else None

        status = str(existing.get("status"))
        if status in RESOLVED_STATUSES:
            existing["status"] = "pending"
            action = "reopened"
        else:
            action = "updated"

        existing["title"] = title.strip() or str(existing.get("title", "(untitled review item)"))
        existing["priority"] = priority
        existing["target_notes"] = sorted({str(note_id) for note_id in target_notes if str(note_id).strip()})
        existing["proposed_action"] = definition.proposed_action
        existing["payload"] = {**payload_dict, "fingerprint": fingerprint}
        existing["updated"] = date.today().isoformat()
        history = existing.get("history")
        if not isinstance(history, list):
            history = []
            existing["history"] = history
        history.append(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "source": source,
            }
        )
        _write_state(review_state_path(data_dir), state)
        render_review_queues(data_dir, state=state)
        return str(existing["id"])

    item_id = _append_item(
        state,
        queue=queue,
        title=title,
        target_notes=target_notes,
        proposed_action=definition.proposed_action,
        payload={**payload_dict, "fingerprint": fingerprint},
        priority=priority,
        created=created,
        history=[
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": "created",
                "source": source,
            }
        ],
        fingerprint=fingerprint,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return item_id


def queue_duplicate_review_item(
    data_dir: Path,
    *,
    source_name: str,
    source_path: Path,
    source_hash: str,
    archived_path: Path | None,
) -> str | None:
    payload: dict[str, Any] = {
        "source_name": source_name,
        "source_path": source_path.as_posix(),
        "source_hash": source_hash,
    }
    if archived_path is not None:
        payload["archived_path"] = archived_path.as_posix()
    return add_review_item(
        data_dir,
        queue="duplicate",
        title=source_name,
        target_notes=[],
        proposed_action=QUEUE_DEFINITIONS["duplicate"].proposed_action,
        payload=payload,
        priority="low",
        fingerprint=_payload_fingerprint(
            queue="duplicate",
            title=source_name,
            target_notes=[],
            proposed_action=QUEUE_DEFINITIONS["duplicate"].proposed_action,
            payload=payload,
        ),
    )


def queue_unsupported_file_review_item(data_dir: Path, *, source_path: Path) -> str | None:
    payload = {"source_path": source_path.as_posix()}
    return add_review_item(
        data_dir,
        queue="unsupported_file",
        title=source_path.name,
        target_notes=[],
        proposed_action=QUEUE_DEFINITIONS["unsupported_file"].proposed_action,
        payload=payload,
        priority="low",
        fingerprint=_payload_fingerprint(
            queue="unsupported_file",
            title=source_path.name,
            target_notes=[],
            proposed_action=QUEUE_DEFINITIONS["unsupported_file"].proposed_action,
            payload=payload,
        ),
    )


def queue_compaction_cluster_review_item(
    data_dir: Path,
    *,
    cluster_id: str,
    source_note_ids: list[str],
    title: str,
    reason: str,
    evidence: list[str],
    suggested_canonical_title: str,
    risk: str,
    cluster_fingerprint: str,
    cooldown_days: int,
) -> str | None:
    payload: dict[str, Any] = {
        "kind": "cluster",
        "cluster_id": cluster_id,
        "cluster_note_ids": sorted(source_note_ids),
        "source_note_ids": sorted(source_note_ids),
        "reason": reason,
        "evidence": list(evidence),
        "suggested_canonical_title": suggested_canonical_title,
        "risk": risk,
        "cluster_fingerprint": cluster_fingerprint,
    }
    fingerprint = f"compaction-cluster:{cluster_fingerprint}"
    state = ensure_review_state(data_dir, render=False)
    existing = _find_existing_item(state, fingerprint)
    if existing is not None:
        status = str(existing.get("status"))
        if status != "rejected":
            return None
        if not _cooldown_elapsed(str(existing.get("updated") or existing.get("created")), cooldown_days):
            return None
        existing["status"] = "pending"
        existing["priority"] = _compaction_priority(risk, proposal=False)
        existing["title"] = title
        existing["target_notes"] = sorted(source_note_ids)
        existing["proposed_action"] = QUEUE_DEFINITIONS["compaction"].proposed_action
        existing["payload"] = {**payload, "fingerprint": fingerprint}
        existing["updated"] = date.today().isoformat()
        history = existing.get("history")
        if not isinstance(history, list):
            history = []
            existing["history"] = history
        history.append(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": "reopened",
                "source": "cluster-scan",
                "note": f"Cooldown elapsed after {cooldown_days} day(s).",
            }
        )
        _write_state(review_state_path(data_dir), state)
        render_review_queues(data_dir, state=state)
        return str(existing["id"])

    item_id = _append_item(
        state,
        queue="compaction",
        title=title,
        target_notes=sorted(source_note_ids),
        proposed_action=QUEUE_DEFINITIONS["compaction"].proposed_action,
        payload={**payload, "fingerprint": fingerprint},
        priority=_compaction_priority(risk, proposal=False),
        created=date.today().isoformat(),
        history=[
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "action": "created",
                "source": "cluster-scan",
            }
        ],
        fingerprint=fingerprint,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return item_id


def queue_compaction_proposal_review_item(
    data_dir: Path,
    *,
    cluster_id: str,
    source_note_ids: list[str],
    title: str,
    draft: Any,
    evidence: list[str],
    cluster_fingerprint: str,
) -> str | None:
    dispositions = [
        {
            "note_id": str(item.note_id),
            "recommendation": str(item.recommendation),
            "rationale": str(item.rationale),
        }
        for item in getattr(draft, "dispositions", [])
    ]
    payload: dict[str, Any] = {
        "kind": "proposal",
        "cluster_id": cluster_id,
        "source_note_ids": sorted(source_note_ids),
        "cluster_fingerprint": cluster_fingerprint,
        "evidence": list(evidence),
        "frontmatter": dict(getattr(draft, "frontmatter")),
        "body": str(getattr(draft, "body")),
        "dispositions": dispositions,
        "diff_summary": str(getattr(draft, "diff_summary")),
        "suggested_canonical_title": str(getattr(draft, "frontmatter", {}).get("title", "")),
        "risk": "medium",
    }
    fingerprint = f"compaction-proposal:{cluster_fingerprint}"
    return add_review_item(
        data_dir,
        queue="compaction",
        title=title,
        target_notes=sorted(source_note_ids),
        proposed_action="review compaction proposal",
        payload=payload,
        priority="medium",
        fingerprint=fingerprint,
    )


def explain_review_item(data_dir: Path, item_id: str) -> str:
    state = ensure_review_state(data_dir, render=True)
    item = _find_item_by_id(state, item_id)
    note_paths = _target_note_paths(data_dir, item)
    payload = item.get("payload", {})
    payload_text = json.dumps(payload, indent=2, sort_keys=True)
    lines = [
        f"Review item: {item['id']}",
        f"queue: {item['queue']}",
        f"status: {item['status']}",
        f"priority: {item['priority']}",
        f"created: {item['created']}",
        f"updated: {item['updated']}",
    ]
    defer_until = item.get("defer_until")
    if isinstance(defer_until, str) and defer_until.strip():
        lines.append(f"defer_until: {defer_until}")
    lines.append(f"proposed_action: {item['proposed_action']}")
    lines.append(f"title: {item['title']}")
    lines.append("target_notes:")
    if note_paths:
        lines.extend([f"- {entry}" for entry in note_paths])
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "payload:",
            payload_text,
            "",
            "history:",
        ]
    )
    history = item.get("history", [])
    if isinstance(history, list) and history:
        for entry in history:
            lines.append(f"- {json.dumps(entry, sort_keys=True)}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def accept_review_item(
    data_dir: Path,
    item_id: str,
    *,
    topic: str | None = None,
    knowledge_type: str | None = None,
    note_id: str | None = None,
    append_body: bool = False,
    resolution_note: str | None = None,
    force: bool = False,
) -> ReviewMutationResult:
    state = ensure_review_state(data_dir, render=False)
    item = _find_item_by_id(state, item_id)
    status = str(item["status"])
    if status == "accepted":
        return ReviewMutationResult(item_id=item_id, action="accept", changed=False, message="Review item already accepted.")
    if status == "rejected":
        raise ReviewStateError(f"Review item {item_id} is rejected and cannot be accepted.")

    message = _apply_accept_action(
        data_dir,
        item,
        topic=topic,
        knowledge_type=knowledge_type,
        note_id=note_id,
        append_body=append_body,
        resolution_note=resolution_note,
        force=force,
    )
    _transition_item(
        item,
        to_status="accepted",
        action="accepted",
        note=message,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return ReviewMutationResult(item_id=item_id, action="accept", changed=True, message=message)


def reject_review_item(data_dir: Path, item_id: str, *, reason: str | None = None) -> ReviewMutationResult:
    state = ensure_review_state(data_dir, render=False)
    item = _find_item_by_id(state, item_id)
    status = str(item["status"])
    if status == "rejected":
        return ReviewMutationResult(item_id=item_id, action="reject", changed=False, message="Review item already rejected.")
    if status == "accepted":
        raise ReviewStateError(f"Review item {item_id} is accepted and cannot be rejected.")

    _transition_item(
        item,
        to_status="rejected",
        action="rejected",
        note=reason or "Rejected via CLI review command.",
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return ReviewMutationResult(
        item_id=item_id,
        action="reject",
        changed=True,
        message=reason or "Review item rejected.",
    )


def defer_review_item(data_dir: Path, item_id: str, *, days: int) -> ReviewMutationResult:
    if days <= 0:
        raise ReviewStateError("--days must be a positive integer.")
    state = ensure_review_state(data_dir, render=False)
    item = _find_item_by_id(state, item_id)
    status = str(item["status"])
    if status == "accepted":
        raise ReviewStateError(f"Review item {item_id} is accepted and cannot be deferred.")
    if status == "rejected":
        raise ReviewStateError(f"Review item {item_id} is rejected and cannot be deferred.")

    due = (date.today() + timedelta(days=days)).isoformat()
    if status == "deferred" and str(item.get("defer_until", "")) == due:
        return ReviewMutationResult(
            item_id=item_id,
            action="defer",
            changed=False,
            message=f"Review item already deferred until {due}.",
        )

    _transition_item(
        item,
        to_status="deferred",
        action="deferred",
        note=f"Deferred for {days} day(s).",
        defer_until=due,
    )
    _write_state(review_state_path(data_dir), state)
    render_review_queues(data_dir, state=state)
    return ReviewMutationResult(item_id=item_id, action="defer", changed=True, message=f"Deferred until {due}.")


def validate_review_item(item: Mapping[str, Any]) -> None:
    required = {
        "id",
        "queue",
        "status",
        "priority",
        "title",
        "created",
        "updated",
        "target_notes",
        "proposed_action",
        "payload",
        "history",
    }
    missing = sorted(required - set(item))
    if missing:
        raise ReviewStateError(f"Review item is missing required fields: {', '.join(missing)}")
    queue = item.get("queue")
    if not isinstance(queue, str) or queue not in QUEUE_DEFINITIONS:
        raise ReviewStateError(f"Review item has unsupported queue: {queue!r}")
    status = item.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise ReviewStateError(f"Review item has unsupported status: {status!r}")
    priority = item.get("priority")
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise ReviewStateError(f"Review item has unsupported priority: {priority!r}")
    if not isinstance(item.get("target_notes"), list):
        raise ReviewStateError("Review item target_notes must be a list.")
    if not isinstance(item.get("payload"), dict):
        raise ReviewStateError("Review item payload must be an object.")
    if not isinstance(item.get("history"), list):
        raise ReviewStateError("Review item history must be a list.")
    for key in ("id", "title", "created", "updated", "proposed_action"):
        if not isinstance(item.get(key), str):
            raise ReviewStateError(f"Review item {key} must be a string.")
    defer_until = item.get("defer_until")
    if defer_until is not None and not isinstance(defer_until, str):
        raise ReviewStateError("Review item defer_until must be an ISO date string when present.")


def render_review_queues(data_dir: Path, *, state: Mapping[str, Any] | None = None) -> None:
    state = state or ensure_review_state(data_dir, render=False)
    items = _state_items(state)
    for definition in QUEUE_DEFINITIONS.values():
        if definition.file_name is None:
            continue
        queue_items = [
            item
            for item in items
            if item["queue"] == definition.queue and _is_visible_pending_item(item)
        ]
        text = _render_queue_file(definition, sorted(queue_items, key=_queue_file_sort_key))
        path = data_dir / "review" / definition.file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _render_rejected_items(data_dir, items)


def collect_review_summary(
    data_dir: Path,
    *,
    max_items: int,
    include_deferred: bool = False,
) -> tuple[dict[str, int], list[ReviewSummaryItem]]:
    state = ensure_review_state(data_dir, render=True)
    pending = [
        item
        for item in _state_items(state)
        if _is_visible_pending_item(item, include_deferred=include_deferred)
    ]
    counts = {definition.count_key: 0 for definition in QUEUE_DEFINITIONS.values()}
    for item in pending:
        definition = _definition(str(item["queue"]))
        counts[definition.count_key] += 1

    ordered = sorted(pending, key=_summary_sort_key)
    if max_items > 0:
        ordered = ordered[:max_items]
    return counts, [_summary_item(item) for item in ordered]


def render_review_summary(
    counts: Mapping[str, int],
    items: Iterable[ReviewSummaryItem],
    *,
    max_items: int,
) -> str:
    total = int(sum(counts.values()))
    lines = [
        f"Review items: {total}",
        "",
        f"classification: {counts.get('classification', 0)}",
        f"merge: {counts.get('merge', 0)}",
        f"dispute: {counts.get('dispute', 0)}",
        f"searchmiss: {counts.get('searchmiss', 0)}",
        f"compaction: {counts.get('compaction', 0)}",
        f"topic: {counts.get('topic', 0)}",
        f"stale: {counts.get('stale', 0)}",
        f"orphan: {counts.get('orphan', 0)}",
        f"low_utility: {counts.get('low_utility', 0)}",
        f"duplicate: {counts.get('duplicate', 0)}",
        f"unsupported_file: {counts.get('unsupported_file', 0)}",
    ]

    materialized = list(items)
    if materialized:
        lines.extend(["", f"Showing up to {max_items} items:"])
        for item in materialized:
            lines.append(f"- [{item.kind}] {item.item_id} - {item.summary}")
        suggested = min(3, len(materialized))
        lines.extend(["", f"Suggested time: choose {suggested} item(s) now."])
    return "\n".join(lines) + "\n"


def _definition(queue: str) -> QueueDefinition:
    try:
        return QUEUE_DEFINITIONS[queue]
    except KeyError as exc:
        raise ReviewStateError(f"Unsupported review queue: {queue!r}") from exc


def _load_state(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReviewStateError(f"Malformed review state at {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReviewStateError(f"Review state at {path} must be an object.")
    if loaded.get("version") != STATE_VERSION:
        raise ReviewStateError(f"Review state at {path} has unsupported version {loaded.get('version')!r}.")
    _state_items(loaded)
    return loaded


def _state_items(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = state.get("items")
    if not isinstance(items, list):
        raise ReviewStateError("Review state items must be a list.")
    validated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ReviewStateError("Review state items must contain only objects.")
        validate_review_item(item)
        validated.append(item)
    return validated


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    _state_items(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_item(
    state: dict[str, Any],
    *,
    queue: str,
    title: str,
    target_notes: list[str],
    proposed_action: str,
    payload: dict[str, Any],
    priority: str,
    created: str,
    history: list[dict[str, Any]],
    fingerprint: str,
) -> str:
    if _find_existing_item(state, fingerprint) is not None:
        existing = _find_existing_item(state, fingerprint)
        return str(existing["id"]) if existing else ""
    definition = _definition(queue)
    created_date = _date_part(created)
    item_id = _allocate_id(state, prefix=definition.id_prefix, created_date=created_date)
    item = {
        "id": item_id,
        "queue": queue,
        "status": "pending",
        "priority": priority,
        "title": title.strip() or "(untitled review item)",
        "created": created_date,
        "updated": created_date,
        "target_notes": sorted({str(note_id) for note_id in target_notes if str(note_id).strip()}),
        "proposed_action": proposed_action,
        "payload": {**payload, "fingerprint": fingerprint},
        "history": history,
    }
    validate_review_item(item)
    state.setdefault("items", []).append(item)
    return item_id


def _find_existing_item(state: Mapping[str, Any], fingerprint: str) -> dict[str, Any] | None:
    for item in _state_items(state):
        payload = item.get("payload", {})
        if isinstance(payload, dict) and payload.get("fingerprint") == fingerprint:
            return item
    return None


def _allocate_id(state: Mapping[str, Any], *, prefix: str, created_date: str) -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}-{re.escape(created_date)}-(\d{{3}})$")
    used: set[int] = set()
    for item in _state_items(state):
        match = pattern.match(str(item.get("id", "")))
        if match:
            used.add(int(match.group(1)))
    counter = 1
    while counter in used:
        counter += 1
    return f"{prefix}-{created_date}-{counter:03d}"


def _payload_fingerprint(
    *,
    queue: str,
    title: str,
    target_notes: list[str],
    proposed_action: str,
    payload: Mapping[str, Any],
) -> str:
    material = {
        "queue": queue,
        "title": title.strip(),
        "target_notes": sorted(set(target_notes)),
        "proposed_action": proposed_action,
        "payload": payload,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _legacy_markdown_items(data_dir: Path) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []
    sources = (
        ("classification", data_dir / "review" / "pending-classification.md"),
        ("merge", data_dir / "review" / "pending-merge.md"),
        ("dispute", data_dir / "review" / "disputes.md"),
    )
    for queue, path in sources:
        imports.extend(_legacy_items_from_file(queue, path, data_dir=data_dir))
    return imports


def _legacy_items_from_file(queue: str, path: Path, *, data_dir: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    matches = list(ITEM_RE.finditer(text))
    if not matches:
        return []

    definition = _definition(queue)
    imported: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        source_item_id = match.group(1).strip()
        fields = _fields_from_legacy_block(block)
        title = _legacy_title(queue, fields, block)
        created = _legacy_created(source_item_id, path)
        target_notes = _target_notes_from_fields(fields)
        fingerprint = fields.get("fingerprint") or _payload_fingerprint(
            queue=queue,
            title=title,
            target_notes=target_notes,
            proposed_action=definition.proposed_action,
            payload={"source_markdown": block, "source_item_id": source_item_id},
        )
        payload = {
            **fields,
            "source_item_id": source_item_id,
            "source_markdown": block,
        }
        imported.append(
            {
                "queue": queue,
                "title": title,
                "target_notes": target_notes,
                "proposed_action": definition.proposed_action,
                "payload": payload,
                "priority": definition.priority,
                "created": created,
                "history": [
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "action": "imported",
                        "source": _relative_path(data_dir, path),
                        "source_item_id": source_item_id,
                    }
                ],
                "fingerprint": fingerprint,
            }
        )
    return imported


def _fields_from_legacy_block(block: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for label, key in (
        ("Source", "source"),
        ("Source hash", "source_hash"),
        ("Target note IDs", "target_note_ids"),
        ("Rationale", "rationale"),
        ("Reason", "reason"),
        ("Suggested action", "suggested_action"),
    ):
        value = _line_field(block, label)
        if value:
            fields[key] = value
    for key in ("fingerprint", "candidate_title", "title", "summary", "suggested_topic", "suggested_type", "confidence"):
        value = _bullet_field(block, key)
        if value:
            fields[key] = value
    body = _code_block_after(block, "- candidate_body:")
    if body:
        fields["candidate_body"] = body
    return fields


def _line_field(block: str, label: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.+?)\s*$", flags=re.MULTILINE)
    match = pattern.search(block)
    if not match:
        return None
    return _strip_markdown_value(match.group(1))


def _bullet_field(block: str, key: str) -> str | None:
    pattern = re.compile(rf"^-\s+{re.escape(key)}:\s*(.+?)\s*$", flags=re.MULTILINE)
    match = pattern.search(block)
    if not match:
        return None
    return _strip_markdown_value(match.group(1))


def _strip_markdown_value(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        return stripped[1:-1]
    return stripped


def _code_block_after(block: str, marker: str) -> str | None:
    index = block.find(marker)
    if index < 0:
        return None
    remainder = block[index + len(marker) :]
    match = re.search(r"```(?:\w+)?\n(.*?)\n```", remainder, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1)


def _legacy_title(queue: str, fields: Mapping[str, Any], block: str) -> str:
    for key in ("candidate_title", "title", "reason", "rationale"):
        value = str(fields.get(key, "")).strip()
        if value:
            return value
    return _summary_from_block(block, (r"^-\s+title:\s+(.+)$", r"^-\s+candidate_title:\s+(.+)$", r"^Reason:\s+(.+)$")) or queue


def _target_notes_from_fields(fields: Mapping[str, Any]) -> list[str]:
    value = fields.get("target_note_ids")
    if not isinstance(value, str) or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _legacy_created(source_item_id: str, path: Path) -> str:
    date_match = re.search(r"(20\d{2})-?(\d{2})-?(\d{2})", source_item_id)
    if date_match:
        return f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    except OSError:
        return date.today().isoformat()


def _duplicate_items_from_ingest_log(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / ".kb" / "ingested.json"
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []

    imported: list[dict[str, Any]] = []
    definition = QUEUE_DEFINITIONS["duplicate"]
    for entry in loaded:
        if not isinstance(entry, dict) or entry.get("status") != "duplicate":
            continue
        source_name = str(entry.get("source_name") or entry.get("source_path") or "duplicate source")
        processed_at = str(entry.get("processed_at") or date.today().isoformat())
        payload = dict(entry)
        fingerprint = _payload_fingerprint(
            queue="duplicate",
            title=source_name,
            target_notes=[],
            proposed_action=definition.proposed_action,
            payload=payload,
        )
        imported.append(
            {
                "queue": "duplicate",
                "title": source_name,
                "target_notes": [],
                "proposed_action": definition.proposed_action,
                "payload": payload,
                "priority": definition.priority,
                "created": _date_part(processed_at),
                "history": [{"at": processed_at, "action": "imported", "source": ".kb/ingested.json"}],
                "fingerprint": fingerprint,
            }
        )
    return imported


def _unsupported_items_from_error_log(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / ".kb" / "errors.log"
    if not path.exists():
        return []
    imported: list[dict[str, Any]] = []
    definition = QUEUE_DEFINITIONS["unsupported_file"]
    marker = "Unsupported ingest file extension for "
    for line in path.read_text(encoding="utf-8").splitlines():
        if marker not in line:
            continue
        stamp, _, remainder = line.partition(" ")
        source = remainder.split(marker, maxsplit=1)[-1].strip()
        payload = {"source_path": source, "log_line": line}
        fingerprint = _payload_fingerprint(
            queue="unsupported_file",
            title=Path(source).name or source,
            target_notes=[],
            proposed_action=definition.proposed_action,
            payload=payload,
        )
        imported.append(
            {
                "queue": "unsupported_file",
                "title": Path(source).name or source,
                "target_notes": [],
                "proposed_action": definition.proposed_action,
                "payload": payload,
                "priority": definition.priority,
                "created": _date_part(stamp),
                "history": [{"at": stamp, "action": "imported", "source": ".kb/errors.log"}],
                "fingerprint": fingerprint,
            }
        )
    return imported


def _render_queue_file(definition: QueueDefinition, items: list[dict[str, Any]]) -> str:
    lines = [
        f"# {definition.file_title}",
        "",
        "<!-- Generated from review/review-items.json. Do not edit this file as the source of truth. -->",
        "",
    ]
    for item in items:
        lines.extend(_render_item_markdown(item))
    return "\n".join(lines).rstrip() + "\n"


def _render_item_markdown(item: Mapping[str, Any]) -> list[str]:
    queue = str(item["queue"])
    payload = item["payload"] if isinstance(item["payload"], dict) else {}
    lines = [
        f"## item: {item['id']}",
        "",
    ]
    source = payload.get("source") or payload.get("source_path")
    if source:
        lines.append(f"Source: `{source}`")
    source_hash = payload.get("source_hash")
    if source_hash:
        lines.append(f"Source hash: `{source_hash}`")
    if item.get("target_notes"):
        lines.append(f"Target note IDs: {', '.join(str(note_id) for note_id in item['target_notes'])}")
    rationale = payload.get("rationale")
    reason = payload.get("reason")
    if rationale:
        lines.append(f"Rationale: {rationale}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append(f"Suggested action: {item['proposed_action']}.")

    if queue == "classification":
        _extend_if_present(lines, "- title", payload.get("title") or item.get("title"))
        _extend_if_present(lines, "- summary", payload.get("summary"))
        _extend_if_present(lines, "- suggested_topic", payload.get("suggested_topic"))
        _extend_if_present(lines, "- suggested_type", payload.get("suggested_type"))
        _extend_if_present(lines, "- confidence", payload.get("confidence"))
    elif queue in {"merge", "dispute"}:
        _extend_if_present(lines, "- fingerprint", payload.get("fingerprint"))
        _extend_if_present(lines, "- candidate_title", payload.get("candidate_title") or item.get("title"))
        body = payload.get("candidate_body")
        if isinstance(body, str) and body.strip():
            lines.extend(["- candidate_body:", "```markdown", body.rstrip("\n"), "```"])
    elif queue == "searchmiss":
        _extend_if_present(lines, "- query", payload.get("query") or item.get("title"))
        _extend_if_present(lines, "- misses", payload.get("misses"))
        _extend_if_present(lines, "- first_seen", payload.get("first_seen"))
        _extend_if_present(lines, "- last_seen", payload.get("last_seen"))
        commands = payload.get("commands")
        if isinstance(commands, list) and commands:
            _extend_if_present(lines, "- commands", ", ".join(str(command) for command in commands))
        reasons = payload.get("reasons")
        if isinstance(reasons, dict) and reasons:
            rendered = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
            _extend_if_present(lines, "- reasons", rendered)
    elif queue == "compaction":
        _extend_if_present(lines, "- kind", payload.get("kind"))
        _extend_if_present(lines, "- cluster_id", payload.get("cluster_id"))
        _extend_if_present(lines, "- suggested_canonical_title", payload.get("suggested_canonical_title"))
        _extend_if_present(lines, "- risk", payload.get("risk"))
        _extend_if_present(lines, "- reason", payload.get("reason"))
        evidence = payload.get("evidence")
        if isinstance(evidence, list) and evidence:
            lines.append("- evidence:")
            lines.extend(f"  - {item}" for item in evidence[:10])
        diff_summary = payload.get("diff_summary")
        if isinstance(diff_summary, str) and diff_summary.strip():
            _extend_if_present(lines, "- diff_summary", diff_summary)
        dispositions = payload.get("dispositions")
        if isinstance(dispositions, list) and dispositions:
            lines.append("- dispositions:")
            for disposition in dispositions:
                if not isinstance(disposition, Mapping):
                    continue
                note_id = disposition.get("note_id")
                recommendation = disposition.get("recommendation")
                rationale = disposition.get("rationale")
                lines.append(f"  - {note_id}: {recommendation} - {rationale}")
        frontmatter = payload.get("frontmatter")
        if isinstance(frontmatter, Mapping) and frontmatter:
            lines.extend(["- proposed_frontmatter:", "```yaml"])
            lines.append(json.dumps(frontmatter, indent=2, sort_keys=True))
            lines.append("```")
        body = payload.get("body")
        if isinstance(body, str) and body.strip():
            lines.extend(["- proposed_body:", "```markdown", body.rstrip("\n"), "```"])
    elif queue == "topic":
        _extend_if_present(lines, "- kind", payload.get("kind"))
        _extend_if_present(lines, "- source_topic", payload.get("source_topic"))
        source_topics = payload.get("source_topics")
        if isinstance(source_topics, list) and source_topics:
            _extend_if_present(lines, "- source_topics", ", ".join(str(topic) for topic in source_topics))
        target_topics = payload.get("target_topics")
        if isinstance(target_topics, list) and target_topics:
            _extend_if_present(lines, "- target_topics", ", ".join(str(topic) for topic in target_topics))
        _extend_if_present(lines, "- target_topic", payload.get("target_topic"))
        moves = payload.get("moves")
        if isinstance(moves, list) and moves:
            lines.append("- moves:")
            for move in moves[:25]:
                if not isinstance(move, Mapping):
                    continue
                lines.append(
                    "  - {note_id}: {from_topic} -> {to_topic} ({reason})".format(
                        note_id=move.get("note_id"),
                        from_topic=move.get("from_topic"),
                        to_topic=move.get("to_topic"),
                        reason=move.get("reason", ""),
                    )
                )
    elif queue == "stale":
        _extend_if_present(lines, "- note_id", payload.get("note_id"))
        _extend_if_present(lines, "- updated", payload.get("updated"))
        _extend_if_present(lines, "- stale_after_days", payload.get("stale_after_days"))
        _extend_if_present(lines, "- age_days", payload.get("age_days"))
        _extend_if_present(lines, "- knowledge_type", payload.get("knowledge_type"))
        _extend_if_present(lines, "- staleness_risk", payload.get("staleness_risk"))
        _extend_if_present(lines, "- confidence", payload.get("confidence"))
        _extend_if_present(lines, "- status", payload.get("status"))
        _extend_if_present(lines, "- source_quality", payload.get("source_quality"))
        _extend_if_present(lines, "- reason", payload.get("reason"))
        evidence = payload.get("evidence")
        if isinstance(evidence, list) and evidence:
            lines.append("- evidence:")
            lines.extend(f"  - {entry}" for entry in evidence[:10])
    elif queue == "orphan":
        _extend_if_present(lines, "- note_id", payload.get("note_id"))
        _extend_if_present(lines, "- updated", payload.get("updated"))
        _extend_if_present(lines, "- orphan_after_days", payload.get("orphan_after_days"))
        _extend_if_present(lines, "- age_days", payload.get("age_days"))
        _extend_if_present(lines, "- inbound_backlinks", payload.get("inbound_backlinks"))
        _extend_if_present(lines, "- outbound_refs", payload.get("outbound_refs"))
        _extend_if_present(lines, "- recent_retrievals", payload.get("recent_retrievals"))
        _extend_if_present(lines, "- recent_uses", payload.get("recent_uses"))
        _extend_if_present(lines, "- topic_neighbors", payload.get("topic_neighbors"))
        _extend_if_present(lines, "- reason", payload.get("reason"))
        suggested_actions = payload.get("suggested_actions")
        if isinstance(suggested_actions, list) and suggested_actions:
            lines.append("- suggested_actions:")
            lines.extend(f"  - {action}" for action in suggested_actions[:10])
    elif queue == "low_utility":
        _extend_if_present(lines, "- note_id", payload.get("note_id"))
        _extend_if_present(lines, "- retrievals", payload.get("retrievals"))
        _extend_if_present(lines, "- logged_uses", payload.get("logged_uses"))
        _extend_if_present(lines, "- use_ratio", payload.get("use_ratio"))
        _extend_if_present(lines, "- suspect_count", payload.get("suspect_count"))
        _extend_if_present(lines, "- correction_count", payload.get("correction_count"))
        _extend_if_present(lines, "- weak_retrievals", payload.get("weak_retrievals"))
        _extend_if_present(lines, "- reason", payload.get("reason"))
        reasons = payload.get("reasons")
        if isinstance(reasons, list) and reasons:
            lines.append("- reasons:")
            lines.extend(f"  - {entry}" for entry in reasons[:12])
        evidence = payload.get("evidence")
        if isinstance(evidence, list) and evidence:
            lines.append("- evidence:")
            lines.extend(f"  - {entry}" for entry in evidence[:10])

    lines.extend(["", ""])
    return lines


def _extend_if_present(lines: list[str], label: str, value: Any) -> None:
    if value is not None and str(value).strip():
        lines.append(f"{label}: {value}")


def _summary_item(item: Mapping[str, Any]) -> ReviewSummaryItem:
    queue = str(item["queue"])
    definition = _definition(queue)
    return ReviewSummaryItem(kind=definition.count_key, item_id=str(item["id"]), summary=_item_summary(item))


def _item_summary(item: Mapping[str, Any]) -> str:
    payload = item.get("payload", {})
    if isinstance(payload, dict):
        for key in (
            "suggested_canonical_title",
            "candidate_title",
            "title",
            "source_name",
            "source_path",
            "query",
            "reason",
            "rationale",
            "source_topic",
            "target_topic",
        ):
            value = str(payload.get(key, "")).strip()
            if value:
                return value
    return str(item.get("title", "")).strip() or "(no summary)"


def _summary_sort_key(item: Mapping[str, Any]) -> tuple[int, int, str, str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(str(item.get("priority")), 9)
    queue_order = _definition(str(item["queue"])).order
    return (priority_rank, queue_order, str(item["created"]), str(item["id"]))


def _queue_file_sort_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (str(item["created"]), str(item["id"]))


def _date_part(value: str) -> str:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    compact = re.search(r"(20\d{2})(\d{2})(\d{2})", value)
    if compact:
        return f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}"
    return date.today().isoformat()


def _cooldown_elapsed(updated: str, days: int) -> bool:
    try:
        updated_date = date.fromisoformat(_date_part(updated))
    except ValueError:
        return False
    return (date.today() - updated_date).days >= max(0, days)


def _compaction_priority(risk: str, *, proposal: bool) -> str:
    if proposal:
        return "medium"
    if risk == "high":
        return "high"
    if risk == "low":
        return "low"
    return "medium"


def _summary_from_block(block: str, patterns: tuple[str, ...]) -> str:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    for pattern in patterns:
        regex = re.compile(pattern)
        for line in lines:
            captured = regex.match(line)
            if captured:
                value = captured.group(1).strip()
                if value:
                    return value
    for line in lines:
        if not line.startswith("```"):
            return line
    return "(no summary)"


def _relative_path(data_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(data_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _find_item_by_id(state: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    for item in _state_items(state):
        if str(item.get("id")) == item_id:
            return item
    raise ReviewStateError(f"Unknown review item ID: {item_id}")


def _target_note_paths(data_dir: Path, item: Mapping[str, Any]) -> list[str]:
    by_id = {record.note_id: record.path for record in load_note_records(data_dir, validate=True)}
    note_ids = item.get("target_notes", [])
    if not isinstance(note_ids, list):
        return []
    rendered: list[str] = []
    for note_id in note_ids:
        text_id = str(note_id)
        path = by_id.get(text_id)
        rendered.append(f"{text_id} -> {path.as_posix()}" if path else f"{text_id} -> (missing)")
    return rendered


def _transition_item(
    item: dict[str, Any],
    *,
    to_status: str,
    action: str,
    note: str,
    defer_until: str | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    item["status"] = to_status
    item["updated"] = date.today().isoformat()
    if defer_until:
        item["defer_until"] = defer_until
    else:
        item.pop("defer_until", None)

    history = item.get("history")
    if not isinstance(history, list):
        history = []
        item["history"] = history
    history.append(
        {
            "at": now,
            "action": action,
            "source": "cli",
            "note": note,
        }
    )


def _is_visible_pending_item(item: Mapping[str, Any], *, include_deferred: bool = False) -> bool:
    status = str(item.get("status"))
    if status == "pending":
        return True
    if status != "deferred":
        return False
    if include_deferred:
        return True
    due = str(item.get("defer_until", "")).strip()
    if not due:
        return True
    return _date_part(due) <= date.today().isoformat()


def _render_rejected_items(data_dir: Path, items: Iterable[Mapping[str, Any]]) -> None:
    rejected_dir = data_dir / "review" / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    for item in items:
        if str(item.get("status")) != "rejected":
            continue
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            continue
        expected.add(f"{item_id}.md")
        text = "\n".join(_render_item_markdown(item)).rstrip() + "\n"
        (rejected_dir / f"{item_id}.md").write_text(text, encoding="utf-8")

    for path in rejected_dir.glob("*.md"):
        if path.name not in expected:
            path.unlink()


def _apply_accept_action(
    data_dir: Path,
    item: dict[str, Any],
    *,
    topic: str | None,
    knowledge_type: str | None,
    note_id: str | None,
    append_body: bool,
    resolution_note: str | None,
    force: bool,
) -> str:
    queue = str(item["queue"])
    if queue == "classification":
        return _accept_classification_item(
            data_dir,
            item,
            topic=topic,
            knowledge_type=knowledge_type,
            note_id=note_id,
        )
    if queue == "merge":
        return _accept_merge_item(data_dir, item, append_body=append_body)
    if queue == "dispute":
        return _accept_dispute_item(data_dir, item)
    if queue == "searchmiss":
        return _accept_searchmiss_item(item, resolution_note=resolution_note)
    if queue == "compaction":
        return _accept_compaction_item(data_dir, item, force=force)
    if queue == "topic":
        return _accept_topic_item(data_dir, item, force=force)
    raise ReviewStateError(
        f"Accept is not supported for queue {queue!r} in this milestone. Use reject/defer instead."
    )


def _accept_classification_item(
    data_dir: Path,
    item: dict[str, Any],
    *,
    topic: str | None,
    knowledge_type: str | None,
    note_id: str | None,
) -> str:
    payload = item.get("payload", {})
    source_path = str(payload.get("source", "")).strip() if isinstance(payload, dict) else ""
    source_hash = str(payload.get("source_hash", "")).strip() if isinstance(payload, dict) else ""

    if note_id:
        appended = _append_source_by_note_id(
            data_dir,
            note_id=note_id,
            source_path=source_path,
            source_hash=source_hash,
        )
        if appended:
            return f"Accepted by appending source to note {note_id}."
        return f"Accepted; source already present on note {note_id}."

    selected_topic = (topic or "").strip()
    selected_type = (knowledge_type or "").strip()
    if not selected_topic:
        raise ReviewStateError("classification accept requires --topic when --note-id is not supplied.")
    if selected_type not in KNOWLEDGE_TYPES:
        allowed = ", ".join(sorted(KNOWLEDGE_TYPES))
        raise ReviewStateError(f"classification accept requires --type in: {allowed}")

    note_id_created = _create_note_from_classification(
        data_dir,
        payload=payload if isinstance(payload, dict) else {},
        topic=selected_topic,
        knowledge_type=selected_type,
    )
    return f"Accepted by creating note {note_id_created} in topic {selected_topic}."


def _accept_merge_item(data_dir: Path, item: dict[str, Any], *, append_body: bool) -> str:
    if not append_body:
        raise ReviewStateError("merge accept requires --append-body to apply candidate body text.")
    payload = item.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    candidate_body = str(payload.get("candidate_body", "")).strip()
    if not candidate_body:
        raise ReviewStateError("merge accept cannot proceed because candidate body is missing from review payload.")
    source_path = str(payload.get("source", "")).strip()
    source_hash = str(payload.get("source_hash", "")).strip()

    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    by_id = {record.note_id: record.path for record in records}
    target_ids = item.get("target_notes", [])
    if not isinstance(target_ids, list) or not target_ids:
        raise ReviewStateError("merge accept requires at least one target note.")

    changed = 0
    for target_id_raw in target_ids:
        target_id = str(target_id_raw)
        path = by_id.get(target_id)
        if path is None:
            raise ReviewStateError(f"Target note {target_id!r} was not found for merge accept.")
        if _append_merge_body_to_note(path, item_id=str(item["id"]), candidate_body=candidate_body):
            changed += 1
        _append_source_to_note(path, source_path=source_path, source_hash=source_hash)
    return f"Accepted merge into {len(target_ids)} target note(s); body appended on {changed} note(s)."


def _accept_dispute_item(data_dir: Path, item: dict[str, Any]) -> str:
    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    by_id = {record.note_id: record.path for record in records}
    target_ids = item.get("target_notes", [])
    if not isinstance(target_ids, list) or not target_ids:
        raise ReviewStateError("dispute accept requires at least one target note.")

    updated = 0
    for target_id_raw in target_ids:
        target_id = str(target_id_raw)
        path = by_id.get(target_id)
        if path is None:
            raise ReviewStateError(f"Target note {target_id!r} was not found for dispute accept.")
        if _acknowledge_dispute_on_note(path, item_id=str(item["id"])):
            updated += 1
    return f"Accepted dispute acknowledgement on {updated} note(s)."


def _accept_searchmiss_item(item: dict[str, Any], *, resolution_note: str | None) -> str:
    text = (resolution_note or "").strip()
    if not text:
        raise ReviewStateError("searchmiss accept requires --resolution-note.")
    payload = item.get("payload")
    if not isinstance(payload, dict):
        payload = {}
        item["payload"] = payload
    payload["resolution_note"] = text
    payload["resolved_at"] = datetime.now().isoformat(timespec="seconds")
    return "Accepted search-miss item with a resolution note."


def _accept_compaction_item(data_dir: Path, item: dict[str, Any], *, force: bool) -> str:
    from kb_librarian.compaction import apply_compaction_review_item

    result = apply_compaction_review_item(data_dir, item, force=force)
    return (
        f"Accepted compaction into note {result.canonical_note_id}; "
        f"superseded={len(result.superseded_note_ids)}, "
        f"archived={len(result.archived_note_ids)}, "
        f"deleted={len(result.deleted_note_ids)}, "
        f"kept={len(result.kept_note_ids)}."
    )


def _accept_topic_item(data_dir: Path, item: dict[str, Any], *, force: bool) -> str:
    from kb_librarian.topic_mutations import apply_topic_review_item

    result = apply_topic_review_item(data_dir, item, force=force)
    return f"Accepted topic proposal; moved {len(result.moved_notes)} note(s)."


def _create_note_from_classification(
    data_dir: Path,
    *,
    payload: Mapping[str, Any],
    topic: str,
    knowledge_type: str,
) -> str:
    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    existing_ids = existing_note_ids(records)

    title = str(payload.get("title") or "Captured note").strip() or "Captured note"
    summary = str(payload.get("summary") or title).strip() or title
    today = date.today().isoformat()
    note_id = generate_note_id(title, today, existing_ids=existing_ids)
    source_path = str(payload.get("source", "")).strip()
    source_hash = str(payload.get("source_hash", "")).strip()
    retrieval_phrases = [title.lower()]
    frontmatter: dict[str, Any] = {
        "id": note_id,
        "title": title,
        "summary": summary,
        "topic": topic,
        "created": today,
        "updated": today,
        "knowledge_type": knowledge_type,
        "status": "active",
        "confidence": "medium",
        "basis": ["reviewed classification"],
        "sources": [],
        "retrieval_phrases": retrieval_phrases,
        "agent_use": ["review"],
        "applies_when": [],
        "does_not_apply_when": [],
        "failure_modes": [],
        "staleness_risk": "medium",
        "reviewed_by_user": True,
        "disputes": [],
        "tags": [topic],
    }
    if source_path or source_hash:
        source: dict[str, Any] = {"type": "ingest"}
        if source_path:
            source["ref"] = source_path
        if source_hash:
            source["hash"] = source_hash
        frontmatter["sources"] = [source]

    ensure_topic_layout(data_dir, topic)
    path = canonical_note_path(data_dir, topic, note_id)
    note = Note(frontmatter=frontmatter, body=body_template(knowledge_type))
    note.validate()
    write_note(path, note)
    return note_id


def _append_source_by_note_id(
    data_dir: Path,
    *,
    note_id: str,
    source_path: str,
    source_hash: str,
) -> bool:
    records = load_note_records(data_dir, validate=True)
    ensure_unique_note_ids(records)
    by_id = {record.note_id: record.path for record in records}
    target = by_id.get(note_id)
    if target is None:
        raise ReviewStateError(f"Note ID {note_id!r} not found for source append.")
    return _append_source_to_note(target, source_path=source_path, source_hash=source_hash)


def _append_source_to_note(path: Path, *, source_path: str, source_hash: str) -> bool:
    if not source_path and not source_hash:
        return False
    note = read_note(path)
    frontmatter = dict(note.frontmatter)
    existing = frontmatter.get("sources")
    if not isinstance(existing, list):
        existing = []
    for source in existing:
        if not isinstance(source, Mapping):
            continue
        if source_hash and str(source.get("hash", "")) == source_hash:
            return False
        if source_path and str(source.get("ref", "")) == source_path:
            return False

    entry: dict[str, Any] = {"type": "ingest"}
    if source_path:
        entry["ref"] = source_path
    if source_hash:
        entry["hash"] = source_hash
    frontmatter["sources"] = [*existing, entry]
    frontmatter["updated"] = date.today().isoformat()
    updated = Note(frontmatter=frontmatter, body=note.body)
    updated.validate()
    write_note(path, updated)
    return True


def _append_merge_body_to_note(path: Path, *, item_id: str, candidate_body: str) -> bool:
    note = read_note(path)
    marker = f"<!-- review-merge:{item_id} -->"
    if marker in note.body:
        return False
    body = note.body.rstrip("\n")
    merged = (
        f"{body}\n\n{marker}\n"
        f"## Accepted Merge ({item_id})\n\n"
        f"{candidate_body.rstrip()}\n"
    )
    frontmatter = dict(note.frontmatter)
    frontmatter["updated"] = date.today().isoformat()
    updated = Note(frontmatter=frontmatter, body=merged)
    updated.validate()
    write_note(path, updated)
    return True


def _acknowledge_dispute_on_note(path: Path, *, item_id: str) -> bool:
    note = read_note(path)
    frontmatter = dict(note.frontmatter)
    disputes = frontmatter.get("disputes")
    if not isinstance(disputes, list):
        disputes = []

    changed = False
    for entry in disputes:
        if not isinstance(entry, dict):
            continue
        if entry.get("review_item_id") == item_id:
            if "acknowledged_at" not in entry:
                entry["acknowledged_at"] = datetime.now().isoformat(timespec="seconds")
                changed = True
            if not entry.get("acknowledged"):
                entry["acknowledged"] = True
                changed = True
            break
    else:
        disputes.append(
            {
                "review_item_id": item_id,
                "acknowledged": True,
                "acknowledged_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        changed = True

    if not frontmatter.get("reviewed_by_user"):
        frontmatter["reviewed_by_user"] = True
        changed = True
    if changed:
        frontmatter["disputes"] = disputes
        frontmatter["updated"] = date.today().isoformat()
        updated = Note(frontmatter=frontmatter, body=note.body)
        updated.validate()
        write_note(path, updated)
    return changed
