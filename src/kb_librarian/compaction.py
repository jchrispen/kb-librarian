"""Compaction cluster detection and proposal drafting."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.errors import KBLibrarianError
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.mutations import atomic_write_note, require_clean_worktree
from kb_librarian.notes import Note, generate_note_id
from kb_librarian.provider_retry import RetryEvent, retry_policy_from_config
from kb_librarian.privacy import redact_payload
from kb_librarian.providers import (
    CompactionDraft,
    ProviderFallbackEvent,
    call_with_provider_policy,
    provider_from_config,
    validate_compaction_payload,
)
from kb_librarian.review import (
    ensure_review_state,
    queue_compaction_cluster_review_item,
    queue_compaction_proposal_review_item,
)
from kb_librarian.storage import (
    NoteRecord,
    canonical_note_path,
    ensure_unique_note_ids,
    existing_note_ids,
    load_note_records,
    normalize_topic_for_path,
)
from kb_librarian.usage import note_usage_counts


MIN_CLUSTER_NOTES = 2
PAIR_REASON_THRESHOLD = 2
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "use",
    "when",
    "with",
}


@dataclass(frozen=True)
class CompactionCluster:
    cluster_id: str
    note_ids: list[str]
    reason: str
    evidence: list[str]
    suggested_canonical_title: str
    risk: str
    fingerprint: str


@dataclass(frozen=True)
class CompactionScanResult:
    clusters: list[CompactionCluster]
    review_item_ids: list[str]


@dataclass(frozen=True)
class CompactionProposalResult:
    review_item_id: str
    cluster_id: str
    source_note_ids: list[str]
    draft: CompactionDraft
    created: bool


@dataclass(frozen=True)
class CompactionApplyResult:
    canonical_note_id: str
    canonical_path: Path
    superseded_note_ids: list[str]
    archived_note_ids: list[str]
    deleted_note_ids: list[str]
    kept_note_ids: list[str]


def scan_compaction_clusters(data_dir: Path, *, config: Mapping[str, Any]) -> CompactionScanResult:
    """Detect overlapping note clusters and queue durable review items."""

    review_config = config.get("review", {})
    if isinstance(review_config, Mapping) and bool(review_config.get("hygiene_fenced", False)):
        return CompactionScanResult(clusters=[], review_item_ids=[])

    records = _eligible_records(load_note_records(data_dir, validate=True))
    ensure_unique_note_ids(records)
    threshold = _duplicate_cluster_threshold(config)
    if len(records) < threshold:
        return CompactionScanResult(clusters=[], review_item_ids=[])

    clusters = detect_compaction_clusters(
        records,
        threshold=threshold,
        backlinks=_load_backlinks(data_dir),
        usage_pairs=_load_retrieval_pairs(data_dir),
        merge_pairs=_load_merge_pairs(data_dir),
    )
    cooldown_days = int(review_config.get("compaction_cooldown_days", 30)) if isinstance(review_config, Mapping) else 30
    item_ids: list[str] = []
    for cluster in clusters:
        queued = queue_compaction_cluster_review_item(
            data_dir,
            cluster_id=cluster.cluster_id,
            source_note_ids=cluster.note_ids,
            title=f"Compaction candidate: {cluster.suggested_canonical_title}",
            reason=cluster.reason,
            evidence=cluster.evidence,
            suggested_canonical_title=cluster.suggested_canonical_title,
            risk=cluster.risk,
            cluster_fingerprint=cluster.fingerprint,
            cooldown_days=cooldown_days,
        )
        if queued is not None:
            item_ids.append(queued)
    return CompactionScanResult(clusters=clusters, review_item_ids=item_ids)


def detect_compaction_clusters(
    records: list[NoteRecord],
    *,
    threshold: int,
    backlinks: Mapping[str, list[str]] | None = None,
    usage_pairs: Mapping[tuple[str, str], int] | None = None,
    merge_pairs: Mapping[tuple[str, str], int] | None = None,
) -> list[CompactionCluster]:
    """Return deterministic duplicate or overlap clusters for note records."""

    threshold = max(MIN_CLUSTER_NOTES, threshold)
    backlinks = backlinks or {}
    usage_pairs = usage_pairs or {}
    merge_pairs = merge_pairs or {}
    by_id = {record.note_id: record for record in records}
    adjacency: dict[str, set[str]] = {record.note_id: set() for record in records}
    edge_evidence: dict[tuple[str, str], list[str]] = {}

    ordered = sorted(records, key=lambda item: item.note_id)
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            reasons = _pair_reasons(
                left,
                right,
                backlinks=backlinks,
                usage_pairs=usage_pairs,
                merge_pairs=merge_pairs,
            )
            if len(reasons) < PAIR_REASON_THRESHOLD:
                continue
            adjacency[left.note_id].add(right.note_id)
            adjacency[right.note_id].add(left.note_id)
            edge_evidence[_pair_key(left.note_id, right.note_id)] = reasons

    visited: set[str] = set()
    clusters: list[CompactionCluster] = []
    for note_id in sorted(adjacency):
        if note_id in visited:
            continue
        stack = [note_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(adjacency.get(current, set()) - component, reverse=True))
        visited.update(component)
        if len(component) < threshold:
            continue

        component_records = [by_id[item] for item in sorted(component)]
        evidence = _component_evidence(component_records, edge_evidence)
        note_ids = [record.note_id for record in component_records]
        fingerprint = _cluster_fingerprint(note_ids)
        clusters.append(
            CompactionCluster(
                cluster_id=f"cluster-{fingerprint[:12]}",
                note_ids=note_ids,
                reason=_cluster_reason(evidence),
                evidence=evidence,
                suggested_canonical_title=_suggested_title(component_records),
                risk=_risk(component_records),
                fingerprint=fingerprint,
            )
        )
    clusters.sort(key=lambda item: (item.note_ids[0], item.cluster_id))
    return clusters


def draft_compaction_proposal(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    target: str,
) -> CompactionProposalResult:
    """Resolve a topic or cluster target, ask the provider for a draft, and queue it."""

    review_config = config.get("review", {})
    if isinstance(review_config, Mapping) and bool(review_config.get("hygiene_fenced", False)):
        raise KBLibrarianError(
            "Compaction proposals are disabled: review.hygiene_fenced is true for this KB."
        )

    records = _eligible_records(load_note_records(data_dir, validate=True))
    ensure_unique_note_ids(records)
    source_records, cluster_id = resolve_compaction_target(data_dir, records, target)
    source_note_ids = [record.note_id for record in source_records]
    source_topics = [str(record.note.frontmatter.get("topic", "")) for record in source_records]
    if len(source_records) < MIN_CLUSTER_NOTES:
        raise KBLibrarianError("Compaction requires at least two source notes.")

    retry_policy = retry_policy_from_config(config)
    source_payload = redact_payload(
        config,
        [_provider_note_payload(record, data_dir=data_dir) for record in source_records],
    )
    payload = call_with_provider_policy(
        config,
        "compact",
        operation_name="compact:synthesize",
        retry_policy=retry_policy,
        call=lambda provider, route: provider.synthesize_compaction(
            source_notes=source_payload,
            cluster_id=cluster_id,
            model=route.model,
        ),
        provider_factory=provider_from_config,
        privacy_topics=source_topics,
        on_retry=lambda event: _log_provider_event(data_dir, phase="compact", event=event, cluster_id=cluster_id),
        on_final_failure=lambda event: _log_provider_event(
            data_dir,
            phase="compact-final",
            event=event,
            cluster_id=cluster_id,
        ),
        on_fallback=lambda event: _log_provider_fallback_event(
            data_dir,
            phase="compact",
            event=event,
            cluster_id=cluster_id,
        ),
    )
    draft = validate_compaction_payload(payload)

    cluster_fingerprint = _cluster_fingerprint(source_note_ids)
    queued = queue_compaction_proposal_review_item(
        data_dir,
        cluster_id=cluster_id,
        source_note_ids=source_note_ids,
        title=f"Compaction proposal: {draft.frontmatter['title']}",
        draft=draft,
        evidence=[_record_evidence(record, data_dir=data_dir) for record in source_records],
        cluster_fingerprint=cluster_fingerprint,
    )
    if queued is None:
        existing = _existing_compaction_proposal_id(data_dir, cluster_id=cluster_id, source_note_ids=source_note_ids)
        if existing is None:
            raise KBLibrarianError("Compaction proposal already exists but could not be resolved from review state.")
        return CompactionProposalResult(
            review_item_id=existing,
            cluster_id=cluster_id,
            source_note_ids=source_note_ids,
            draft=draft,
            created=False,
        )
    return CompactionProposalResult(
        review_item_id=queued,
        cluster_id=cluster_id,
        source_note_ids=source_note_ids,
        draft=draft,
        created=True,
    )


def apply_compaction_review_item(
    data_dir: Path,
    item: Mapping[str, Any],
    *,
    force: bool = False,
) -> CompactionApplyResult:
    """Apply an accepted compaction proposal and rebuild generated indexes."""

    payload = item.get("payload", {})
    if not isinstance(payload, Mapping) or payload.get("kind") != "proposal":
        raise KBLibrarianError("Only compaction proposal review items can be accepted.")

    require_clean_worktree(
        data_dir,
        force=force,
        operation=f"Accepting compaction item {item.get('id')}",
    )

    records = _eligible_or_supersedable_records(load_note_records(data_dir, validate=True))
    ensure_unique_note_ids(records)
    by_id = {record.note_id: record for record in records}
    source_note_ids = _payload_note_ids(payload, item)
    if len(source_note_ids) < MIN_CLUSTER_NOTES:
        raise KBLibrarianError("Compaction proposal requires at least two source notes.")

    missing = [note_id for note_id in source_note_ids if note_id not in by_id]
    if missing:
        raise KBLibrarianError(f"Compaction proposal references missing notes: {', '.join(missing)}")

    canonical = _canonical_note_from_payload(
        payload,
        source_note_ids=source_note_ids,
        existing_ids=existing_note_ids(records),
    )
    canonical_path = canonical_note_path(
        data_dir,
        str(canonical.frontmatter["topic"]),
        str(canonical.frontmatter["id"]),
    )
    if canonical_path.exists():
        raise KBLibrarianError(f"Canonical compaction destination already exists: {canonical_path}")

    atomic_write_note(canonical_path, canonical)
    usage_counts = note_usage_counts(data_dir)
    superseded: list[str] = []
    archived: list[str] = []
    deleted: list[str] = []
    kept: list[str] = []
    dispositions = _dispositions_by_note_id(payload)

    for source_note_id in source_note_ids:
        record = by_id[source_note_id]
        recommendation = dispositions.get(source_note_id, {}).get("recommendation", "supersede")
        recommendation = str(recommendation).strip().lower()
        if recommendation in {"keep", "review"}:
            kept.append(source_note_id)
            continue
        if recommendation == "delete" and _can_delete_source_note(canonical, source_note_id, usage_counts):
            record.path.unlink()
            deleted.append(source_note_id)
            continue
        status = "archived" if recommendation in {"archive", "delete"} else "superseded"
        _mark_source_note(
            record,
            status=status,
            superseded_by=str(canonical.frontmatter["id"]),
        )
        if status == "archived":
            archived.append(source_note_id)
        else:
            superseded.append(source_note_id)

    reindex_data_dir(data_dir)
    return CompactionApplyResult(
        canonical_note_id=str(canonical.frontmatter["id"]),
        canonical_path=canonical_path,
        superseded_note_ids=superseded,
        archived_note_ids=archived,
        deleted_note_ids=deleted,
        kept_note_ids=kept,
    )


def _log_provider_event(
    data_dir: Path,
    *,
    phase: str,
    event: RetryEvent,
    cluster_id: str,
) -> None:
    path = data_dir / ".kb" / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    message = (
        f"{stamp} stage=provider-{phase} op={event.operation} "
        f"attempt={event.attempt}/{event.max_attempts} "
        f"transient={event.classification.transient} "
        f"reason={event.classification.kind}:{event.classification.detail} "
        f"delay={event.delay_seconds:.3f}s cluster_id={cluster_id!r} error={event.error}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message)


def _log_provider_fallback_event(
    data_dir: Path,
    *,
    phase: str,
    event: ProviderFallbackEvent,
    cluster_id: str,
) -> None:
    path = data_dir / ".kb" / "errors.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    message = (
        f"{stamp} stage=provider-{phase}-fallback op={event.operation} "
        f"from={event.provider} model={event.model} "
        f"to={event.next_provider} next_model={event.next_model} "
        f"reason={event.classification_kind}:{event.classification_detail} "
        f"cluster_id={cluster_id!r} error={event.error}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message)


def resolve_compaction_target(data_dir: Path, records: list[NoteRecord], target: str) -> tuple[list[NoteRecord], str]:
    """Resolve a topic, compaction review item ID, or cluster ID to notes."""

    normalized_target = target.strip()
    if not normalized_target:
        raise KBLibrarianError("`kb compact` requires a topic, cluster ID, or compaction review item ID.")

    by_id = {record.note_id: record for record in records}
    state = ensure_review_state(data_dir, render=True)
    for item in state.get("items", []):
        if not isinstance(item, dict) or item.get("queue") != "compaction":
            continue
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            continue
        item_id = str(item.get("id", ""))
        cluster_id = str(payload.get("cluster_id", ""))
        if normalized_target not in {item_id, cluster_id}:
            continue
        note_ids = _payload_note_ids(payload, item)
        missing = [note_id for note_id in note_ids if note_id not in by_id]
        if missing:
            raise KBLibrarianError(f"Compaction target references missing note IDs: {', '.join(missing)}")
        selected = [by_id[note_id] for note_id in note_ids]
        if len(selected) < MIN_CLUSTER_NOTES:
            raise KBLibrarianError("Compaction target has insufficient material; at least two notes are required.")
        return selected, cluster_id or _topic_cluster_id(selected)

    topic = normalize_topic_for_path(normalized_target)
    topic_matches = [
        record
        for record in records
        if record.topic_path == topic or normalize_topic_for_path(str(record.note.frontmatter.get("topic", ""))) == topic
    ]
    if not topic_matches:
        raise KBLibrarianError(f"No compaction cluster or topic matched {target!r}.")
    if len(topic_matches) < MIN_CLUSTER_NOTES:
        raise KBLibrarianError(
            f"Topic {target!r} has insufficient material for compaction; at least two notes are required."
        )
    return sorted(topic_matches, key=lambda item: item.note_id), _topic_cluster_id(topic_matches)


def _eligible_records(records: Iterable[NoteRecord]) -> list[NoteRecord]:
    eligible: list[NoteRecord] = []
    for record in records:
        status = str(record.note.frontmatter.get("status", ""))
        if status in {"archived", "superseded"}:
            continue
        eligible.append(record)
    return sorted(eligible, key=lambda item: item.note_id)


def _eligible_or_supersedable_records(records: Iterable[NoteRecord]) -> list[NoteRecord]:
    eligible: list[NoteRecord] = []
    for record in records:
        status = str(record.note.frontmatter.get("status", ""))
        if status == "archived":
            continue
        eligible.append(record)
    return sorted(eligible, key=lambda item: item.note_id)


def _canonical_note_from_payload(
    payload: Mapping[str, Any],
    *,
    source_note_ids: list[str],
    existing_ids: set[str],
) -> Note:
    draft_frontmatter = payload.get("frontmatter")
    if not isinstance(draft_frontmatter, Mapping):
        raise KBLibrarianError("Compaction proposal is missing proposed frontmatter.")
    body = str(payload.get("body", "")).strip()
    if not body:
        raise KBLibrarianError("Compaction proposal is missing proposed body markdown.")

    today = date.today().isoformat()
    title = str(draft_frontmatter.get("title") or "Canonical compacted note").strip()
    topic = str(draft_frontmatter.get("topic") or "general").strip()
    knowledge_type = str(draft_frontmatter.get("knowledge_type") or "technique").strip()
    note_id = generate_note_id(title, today, existing_ids=existing_ids)
    sources = _coerce_sources(draft_frontmatter.get("sources"))
    sources.append(
        {
            "type": "compaction",
            "ref": "review/pending-compaction.md",
            "source_note_ids": source_note_ids,
        }
    )
    frontmatter: dict[str, Any] = {
        **dict(draft_frontmatter),
        "id": note_id,
        "title": title,
        "summary": str(draft_frontmatter.get("summary") or title).strip() or title,
        "topic": topic,
        "created": today,
        "updated": today,
        "knowledge_type": knowledge_type,
        "status": "active",
        "confidence": str(draft_frontmatter.get("confidence") or "medium").strip() or "medium",
        "basis": _coerce_string_list(draft_frontmatter.get("basis")) or ["accepted compaction review"],
        "sources": sources,
        "retrieval_phrases": _coerce_string_list(draft_frontmatter.get("retrieval_phrases")) or [title.lower()],
        "agent_use": _coerce_string_list(draft_frontmatter.get("agent_use")),
        "applies_when": _coerce_string_list(draft_frontmatter.get("applies_when")),
        "does_not_apply_when": _coerce_string_list(draft_frontmatter.get("does_not_apply_when")),
        "failure_modes": _coerce_string_list(draft_frontmatter.get("failure_modes")),
        "staleness_risk": str(draft_frontmatter.get("staleness_risk") or "medium").strip() or "medium",
        "reviewed_by_user": True,
        "disputes": _coerce_string_list(draft_frontmatter.get("disputes")),
        "tags": _coerce_string_list(draft_frontmatter.get("tags")) or [normalize_topic_for_path(topic)],
    }
    note = Note(frontmatter=frontmatter, body=body.rstrip("\n") + "\n")
    note.validate()
    return note


def _coerce_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sources: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            sources.append(dict(item))
    return sources


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dispositions_by_note_id(payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    raw = payload.get("dispositions")
    if not isinstance(raw, list):
        return {}
    dispositions: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        note_id = str(item.get("note_id", "")).strip()
        if not note_id:
            continue
        dispositions[note_id] = {
            "recommendation": str(item.get("recommendation", "")).strip().lower(),
            "rationale": str(item.get("rationale", "")).strip(),
        }
    return dispositions


def _can_delete_source_note(note: Note, source_note_id: str, usage_counts: Mapping[str, int]) -> bool:
    if int(usage_counts.get(source_note_id, 0)) > 0:
        return False
    rendered = note.to_markdown()
    return source_note_id not in rendered


def _mark_source_note(record: NoteRecord, *, status: str, superseded_by: str) -> None:
    frontmatter = dict(record.note.frontmatter)
    frontmatter["status"] = status
    frontmatter["updated"] = date.today().isoformat()
    frontmatter["superseded_by"] = superseded_by
    if status == "superseded":
        frontmatter["reviewed_by_user"] = True
    note = Note(frontmatter=frontmatter, body=record.note.body)
    note.validate()
    atomic_write_note(record.path, note)


def _duplicate_cluster_threshold(config: Mapping[str, Any]) -> int:
    review = config.get("review", {})
    raw = review.get("duplicate_cluster_threshold", 4) if isinstance(review, Mapping) else 4
    try:
        return max(MIN_CLUSTER_NOTES, int(raw))
    except (TypeError, ValueError):
        return 4


def _pair_reasons(
    left: NoteRecord,
    right: NoteRecord,
    *,
    backlinks: Mapping[str, list[str]],
    usage_pairs: Mapping[tuple[str, str], int],
    merge_pairs: Mapping[tuple[str, str], int],
) -> list[str]:
    reasons: list[str] = []
    left_fm = left.note.frontmatter
    right_fm = right.note.frontmatter

    title_score = _jaccard(_tokens(str(left_fm.get("title", ""))), _tokens(str(right_fm.get("title", ""))))
    if _normalize_title(str(left_fm.get("title", ""))) == _normalize_title(str(right_fm.get("title", ""))):
        reasons.append("same normalized title")
    elif title_score >= 0.45:
        reasons.append(f"title token overlap {title_score:.2f}")

    phrase_score = _jaccard(
        _list_tokens(left_fm.get("retrieval_phrases")),
        _list_tokens(right_fm.get("retrieval_phrases")),
    )
    if phrase_score >= 0.35:
        reasons.append(f"retrieval phrase overlap {phrase_score:.2f}")

    tag_overlap = sorted(_string_set(left_fm.get("tags")) & _string_set(right_fm.get("tags")))
    if len(tag_overlap) >= 2:
        reasons.append(f"shared tags: {', '.join(tag_overlap[:4])}")

    body_score = _jaccard(_tokens(left.note.body), _tokens(right.note.body))
    if body_score >= 0.18:
        reasons.append(f"body term overlap {body_score:.2f}")

    pair = _pair_key(left.note_id, right.note_id)
    if _linked(left.note_id, right.note_id, backlinks):
        reasons.append("backlink proximity")
    if usage_pairs.get(pair, 0) >= 2:
        reasons.append(f"repeated retrieval overlap {usage_pairs[pair]}")
    if merge_pairs.get(pair, 0) >= 1:
        reasons.append("repeated adds_nuance review overlap")

    return reasons


def _component_evidence(records: list[NoteRecord], edge_evidence: Mapping[tuple[str, str], list[str]]) -> list[str]:
    evidence: list[str] = []
    note_ids = [record.note_id for record in records]
    for left_index, left in enumerate(note_ids):
        for right in note_ids[left_index + 1 :]:
            reasons = edge_evidence.get(_pair_key(left, right), [])
            if reasons:
                evidence.append(f"{left} + {right}: {', '.join(reasons[:4])}")
    if not evidence:
        evidence = [_record_evidence(record, data_dir=None) for record in records]
    return evidence[:12]


def _record_evidence(record: NoteRecord, *, data_dir: Path | None) -> str:
    fm = record.note.frontmatter
    path = record.path
    if data_dir is not None:
        try:
            path = record.path.relative_to(data_dir)
        except ValueError:
            pass
    return f"{record.note_id}: {fm.get('title', '')} ({path.as_posix()})"


def _cluster_reason(evidence: list[str]) -> str:
    if not evidence:
        return "overlapping note signals"
    reason_counts: dict[str, int] = {}
    for item in evidence:
        _, _, reason_text = item.partition(": ")
        for part in reason_text.split(", "):
            key = part.split(" 0.", maxsplit=1)[0].strip()
            if key:
                reason_counts[key] = reason_counts.get(key, 0) + 1
    if not reason_counts:
        return "overlapping note signals"
    ordered = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    return "; ".join(f"{key} ({count})" for key, count in ordered[:3])


def _suggested_title(records: list[NoteRecord]) -> str:
    titles = [str(record.note.frontmatter.get("title", "")).strip() for record in records]
    titles = [title for title in titles if title]
    if not titles:
        return "Canonical compacted note"
    titles.sort(key=lambda item: (len(item), item.lower()))
    return titles[0]


def _risk(records: list[NoteRecord]) -> str:
    statuses = {str(record.note.frontmatter.get("status", "")) for record in records}
    types = {str(record.note.frontmatter.get("knowledge_type", "")) for record in records}
    confidences = {str(record.note.frontmatter.get("confidence", "")) for record in records}
    if "disputed" in statuses or "fact" in types:
        return "high"
    if len(types) > 1 or "low" in confidences or len(records) >= 6:
        return "medium"
    return "low"


def _provider_note_payload(record: NoteRecord, *, data_dir: Path) -> dict[str, Any]:
    fm = record.note.frontmatter
    try:
        path = record.path.relative_to(data_dir).as_posix()
    except ValueError:
        path = record.path.as_posix()
    return {
        "note_id": record.note_id,
        "path": path,
        "frontmatter": fm,
        "body": record.note.body,
    }


def _existing_compaction_proposal_id(data_dir: Path, *, cluster_id: str, source_note_ids: list[str]) -> str | None:
    wanted = sorted(source_note_ids)
    state = ensure_review_state(data_dir, render=True)
    for item in state.get("items", []):
        if not isinstance(item, dict) or item.get("queue") != "compaction":
            continue
        payload = item.get("payload", {})
        if not isinstance(payload, dict) or payload.get("kind") != "proposal":
            continue
        if payload.get("cluster_id") == cluster_id and sorted(_payload_note_ids(payload, item)) == wanted:
            return str(item.get("id", ""))
    return None


def _payload_note_ids(payload: Mapping[str, Any], item: Mapping[str, Any]) -> list[str]:
    raw = payload.get("source_note_ids")
    if not isinstance(raw, list):
        raw = payload.get("cluster_note_ids")
    if not isinstance(raw, list):
        raw = item.get("target_notes", [])
    return sorted({str(note_id).strip() for note_id in raw if str(note_id).strip()})


def _topic_cluster_id(records: Iterable[NoteRecord]) -> str:
    note_ids = [record.note_id for record in records]
    return f"cluster-{_cluster_fingerprint(note_ids)[:12]}"


def _cluster_fingerprint(note_ids: Iterable[str]) -> str:
    material = json.dumps({"source_note_ids": sorted(note_ids)}, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _load_backlinks(data_dir: Path) -> dict[str, list[str]]:
    path = data_dir / ".kb" / "backlinks.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    loaded: dict[str, list[str]] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            loaded[str(key)] = [str(item) for item in value if str(item).strip()]
    return loaded


def _load_retrieval_pairs(data_dir: Path) -> dict[tuple[str, str], int]:
    path = data_dir / ".kb" / "usage.log"
    pairs: dict[tuple[str, str], int] = {}
    if not path.exists():
        return pairs
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("event") != "retrieval":
            continue
        note_ids = event.get("returned_note_ids")
        if not isinstance(note_ids, list) or len(note_ids) < 2:
            continue
        clean = sorted({str(note_id) for note_id in note_ids if str(note_id).strip()})
        for left_index, left in enumerate(clean):
            for right in clean[left_index + 1 :]:
                pair = _pair_key(left, right)
                pairs[pair] = pairs.get(pair, 0) + 1
    return pairs


def _load_merge_pairs(data_dir: Path) -> dict[tuple[str, str], int]:
    pairs: dict[tuple[str, str], int] = {}
    state = ensure_review_state(data_dir, render=False)
    for item in state.get("items", []):
        if not isinstance(item, dict) or item.get("queue") != "merge":
            continue
        target_notes = item.get("target_notes", [])
        if not isinstance(target_notes, list) or len(target_notes) < 2:
            continue
        clean = sorted({str(note_id) for note_id in target_notes if str(note_id).strip()})
        for left_index, left in enumerate(clean):
            for right in clean[left_index + 1 :]:
                pair = _pair_key(left, right)
                pairs[pair] = pairs.get(pair, 0) + 1
    return pairs


def _linked(left: str, right: str, backlinks: Mapping[str, list[str]]) -> bool:
    left_sources = set(backlinks.get(left, []))
    right_sources = set(backlinks.get(right, []))
    return right in left_sources or left in right_sources or bool(left_sources & right_sources)


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def _normalize_title(value: str) -> str:
    return " ".join(_tokens(value))


def _list_tokens(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return _tokens(" ".join(str(item) for item in value))


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {normalize_topic_for_path(str(item)) for item in value if str(item).strip()}


def _tokens(value: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(value.lower()) if token not in STOPWORDS and len(token) > 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
