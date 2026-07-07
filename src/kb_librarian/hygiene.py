"""Hygiene signal detection and suspect-flag workflows."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from kb_librarian.notes import Note
from kb_librarian.review import ReviewStateError, upsert_hygiene_review_item
from kb_librarian.storage import NOTE_ID_REFERENCE_PATTERN, NoteRecord, load_note_records
from kb_librarian.usage import log_suspect_flag, read_usage_events

_CORRECTION_TERMS = ("wrong", "incorrect", "inaccurate", "outdated", "correction", "fix")
_DISPUTE_TERMS = ("contradict", "conflict", "dispute", "false", "wrong")


@dataclass(frozen=True)
class HygieneScanResult:
    stale_item_ids: list[str]
    orphan_item_ids: list[str]
    low_utility_item_ids: list[str]

    @property
    def item_ids(self) -> list[str]:
        return [*self.stale_item_ids, *self.orphan_item_ids, *self.low_utility_item_ids]


@dataclass(frozen=True)
class FlagSuspectResult:
    queue: str
    item_id: str
    created_or_updated: bool
    suspect_count: int


def scan_hygiene_queues(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    today: date | None = None,
) -> HygieneScanResult:
    review_config = config.get("review", {}) if isinstance(config.get("review"), Mapping) else {}
    if bool(review_config.get("hygiene_fenced", False)):
        # ponytail: hygiene fence only covers scan-driven queues (stale/orphan/low_utility here,
        # compaction in compaction.py); flag-suspect and classification/parser_failure are untouched.
        return HygieneScanResult(stale_item_ids=[], orphan_item_ids=[], low_utility_item_ids=[])

    records = load_note_records(data_dir, validate=True)
    anchor = today or date.today()
    stale_after_days = int(review_config.get("stale_after_days", 180))
    orphan_after_days = int(review_config.get("orphan_after_days", 30))

    backlinks = _load_backlinks(data_dir)
    usage = _usage_signals(data_dir=data_dir, now=anchor)
    by_topic = _records_by_topic(records)

    stale_item_ids: list[str] = []
    orphan_item_ids: list[str] = []
    low_utility_item_ids: list[str] = []

    for record in records:
        stale_item = _maybe_upsert_stale(
            data_dir=data_dir,
            record=record,
            stale_after_days=stale_after_days,
            today=anchor,
        )
        if stale_item:
            stale_item_ids.append(stale_item)

        orphan_item = _maybe_upsert_orphan(
            data_dir=data_dir,
            record=record,
            backlinks=backlinks,
            usage=usage,
            by_topic=by_topic,
            orphan_after_days=orphan_after_days,
            today=anchor,
        )
        if orphan_item:
            orphan_item_ids.append(orphan_item)

        low_utility_item = _maybe_upsert_low_utility(
            data_dir=data_dir,
            record=record,
            usage=usage,
            today=anchor,
            reason_hint=None,
        )
        if low_utility_item:
            low_utility_item_ids.append(low_utility_item)

    return HygieneScanResult(
        stale_item_ids=stale_item_ids,
        orphan_item_ids=orphan_item_ids,
        low_utility_item_ids=low_utility_item_ids,
    )


def flag_suspect_note(
    data_dir: Path,
    *,
    note_id: str,
    reason: str,
    timestamp: str | None = None,
) -> FlagSuspectResult:
    if not reason.strip():
        raise ReviewStateError("flag-suspect requires a non-empty reason.")
    log_suspect_flag(data_dir, note_id=note_id, reason=reason, timestamp=timestamp)

    records = load_note_records(data_dir, validate=True)
    by_id = {record.note_id: record for record in records}
    if note_id not in by_id:
        raise ReviewStateError(f"Unknown note ID: {note_id}")

    usage = _usage_signals(data_dir=data_dir, now=date.today())
    record = by_id[note_id]
    queue = "dispute" if _is_dispute_reason(reason) else "low_utility"
    item_id = _upsert_suspect_item(
        data_dir=data_dir,
        record=record,
        usage=usage,
        reason=reason,
        queue=queue,
    )
    return FlagSuspectResult(
        queue=queue,
        item_id=item_id,
        created_or_updated=bool(item_id),
        suspect_count=usage.suspect_counts.get(note_id, 0),
    )


@dataclass(frozen=True)
class _UsageSignals:
    retrieval_counts: dict[str, int]
    logged_use_counts: dict[str, int]
    suspect_counts: dict[str, int]
    suspect_reasons: dict[str, list[str]]
    correction_counts: dict[str, int]
    weak_retrieval_counts: dict[str, int]
    recent_retrieval_counts: dict[str, int]
    recent_use_counts: dict[str, int]
    first_seen: dict[str, str]
    last_seen: dict[str, str]


def _usage_signals(data_dir: Path, *, now: date) -> _UsageSignals:
    retrieval_counts: Counter[str] = Counter()
    logged_use_counts: Counter[str] = Counter()
    suspect_counts: Counter[str] = Counter()
    correction_counts: Counter[str] = Counter()
    weak_retrieval_counts: Counter[str] = Counter()
    recent_retrieval_counts: Counter[str] = Counter()
    recent_use_counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    suspect_reasons: dict[str, list[str]] = {}

    recent_cutoff = now - timedelta(days=30)
    for event in read_usage_events(data_dir):
        event_type = str(event.get("event") or "")
        stamp = _event_date(event)
        if event_type == "retrieval":
            for note_id in _event_note_ids(event):
                retrieval_counts[note_id] += 1
                if _is_weak_retrieval(event):
                    weak_retrieval_counts[note_id] += 1
                _update_seen_windows(first_seen, last_seen, note_id=note_id, stamp=stamp)
                if stamp and stamp >= recent_cutoff:
                    recent_retrieval_counts[note_id] += 1
            continue

        if event_type == "note-use":
            note_id = str(event.get("note_id") or "").strip()
            if not note_id:
                continue
            logged_use_counts[note_id] += 1
            _update_seen_windows(first_seen, last_seen, note_id=note_id, stamp=stamp)
            if stamp and stamp >= recent_cutoff:
                recent_use_counts[note_id] += 1
            continue

        if event_type == "suspect-flag":
            note_id = str(event.get("note_id") or "").strip()
            if not note_id:
                continue
            suspect_counts[note_id] += 1
            reason = str(event.get("reason") or "").strip()
            if reason:
                reasons = suspect_reasons.setdefault(note_id, [])
                if reason not in reasons:
                    reasons.append(reason)
                if _looks_like_correction(reason):
                    correction_counts[note_id] += 1
            _update_seen_windows(first_seen, last_seen, note_id=note_id, stamp=stamp)

    return _UsageSignals(
        retrieval_counts=dict(retrieval_counts),
        logged_use_counts=dict(logged_use_counts),
        suspect_counts=dict(suspect_counts),
        suspect_reasons=suspect_reasons,
        correction_counts=dict(correction_counts),
        weak_retrieval_counts=dict(weak_retrieval_counts),
        recent_retrieval_counts=dict(recent_retrieval_counts),
        recent_use_counts=dict(recent_use_counts),
        first_seen=first_seen,
        last_seen=last_seen,
    )


def _maybe_upsert_stale(
    *,
    data_dir: Path,
    record: NoteRecord,
    stale_after_days: int,
    today: date,
) -> str | None:
    fm = record.note.frontmatter
    status = str(fm.get("status") or "")
    if status == "archived":
        return None
    updated = _parse_date(str(fm.get("updated") or ""))
    if updated is None:
        return None
    age_days = max(0, (today - updated).days)
    threshold = _stale_threshold_days(record.note, stale_after_days)
    if age_days < threshold:
        return None

    rounded_age_days = int(age_days / 7) * 7
    note_id = record.note_id
    knowledge_type = str(fm.get("knowledge_type") or "")
    staleness_risk = str(fm.get("staleness_risk") or "unknown")
    confidence = str(fm.get("confidence") or "")
    source_quality = _source_quality(record.note)
    reason = f"Updated {age_days} days ago (threshold {threshold} days)."
    evidence = [
        f"updated={updated.isoformat()}",
        f"stale_on={(updated + timedelta(days=threshold)).isoformat()}",
        f"knowledge_type={knowledge_type}",
        f"staleness_risk={staleness_risk}",
        f"confidence={confidence}",
        f"source_quality={source_quality}",
    ]
    payload = {
        "note_id": note_id,
        "path": record.path.as_posix(),
        "updated": updated.isoformat(),
        "age_days": rounded_age_days,
        "stale_after_days": threshold,
        "knowledge_type": knowledge_type,
        "staleness_risk": staleness_risk,
        "confidence": confidence,
        "status": status,
        "source_quality": source_quality,
        "reason": reason,
        "evidence": evidence,
    }
    priority = _stale_priority(note=record.note)
    return upsert_hygiene_review_item(
        data_dir,
        queue="stale",
        title=f"{note_id} may be stale",
        target_notes=[note_id],
        payload=payload,
        priority=priority,
        created=today.isoformat(),
        fingerprint=f"stale:{note_id}",
        source="hygiene-scan",
    )


def _maybe_upsert_orphan(
    *,
    data_dir: Path,
    record: NoteRecord,
    backlinks: Mapping[str, list[str]],
    usage: _UsageSignals,
    by_topic: Mapping[str, list[NoteRecord]],
    orphan_after_days: int,
    today: date,
) -> str | None:
    fm = record.note.frontmatter
    status = str(fm.get("status") or "")
    if status in {"archived", "superseded"}:
        return None
    updated = _parse_date(str(fm.get("updated") or ""))
    if updated is None:
        return None
    age_days = max(0, (today - updated).days)
    if age_days < orphan_after_days:
        return None

    note_id = record.note_id
    inbound = len(backlinks.get(note_id, []))
    outbound = len(_outbound_references(record))
    topic_neighbors = _topic_neighbor_count(record, by_topic=by_topic)
    recent_retrievals = usage.recent_retrieval_counts.get(note_id, 0)
    recent_uses = usage.recent_use_counts.get(note_id, 0)
    if inbound > 0 or outbound > 0 or topic_neighbors > 0 or recent_retrievals > 0 or recent_uses > 0:
        return None

    payload = {
        "note_id": note_id,
        "path": record.path.as_posix(),
        "updated": updated.isoformat(),
        "age_days": int(age_days / 7) * 7,
        "orphan_after_days": orphan_after_days,
        "inbound_backlinks": inbound,
        "outbound_refs": outbound,
        "recent_retrievals": recent_retrievals,
        "recent_uses": recent_uses,
        "topic_neighbors": topic_neighbors,
        "reason": "No backlinks, related references, recent usage, or nearby topic context.",
        "suggested_actions": [
            "add links to/from related notes",
            "merge with a nearby note",
            "retopic to a more active area",
            "archive if obsolete",
            "leave as-is with rationale",
        ],
    }
    return upsert_hygiene_review_item(
        data_dir,
        queue="orphan",
        title=f"{note_id} appears isolated",
        target_notes=[note_id],
        payload=payload,
        priority="medium",
        created=today.isoformat(),
        fingerprint=f"orphan:{note_id}",
        source="hygiene-scan",
    )


def _maybe_upsert_low_utility(
    *,
    data_dir: Path,
    record: NoteRecord,
    usage: _UsageSignals,
    today: date,
    reason_hint: str | None,
) -> str | None:
    note_id = record.note_id
    retrievals = usage.retrieval_counts.get(note_id, 0)
    logged_uses = usage.logged_use_counts.get(note_id, 0)
    suspects = usage.suspect_counts.get(note_id, 0)
    corrections = usage.correction_counts.get(note_id, 0)
    weak_retrievals = usage.weak_retrieval_counts.get(note_id, 0)
    use_ratio = float(logged_uses) / float(retrievals) if retrievals > 0 else 0.0

    reasons: list[str] = []
    if retrievals >= 3 and logged_uses == 0:
        reasons.append("retrieved repeatedly without logged use")
    if retrievals >= 5 and use_ratio < 0.25:
        reasons.append("low retrieval-to-use conversion")
    if weak_retrievals >= 3:
        reasons.append("often appears in weak retrieval contexts")
    if suspects > 0:
        reasons.append("explicit suspect flags recorded")
    if corrections >= 2:
        reasons.append("multiple correction-style suspect reports")
    if reason_hint:
        reasons.append(reason_hint)
    if not reasons:
        return None

    deduped_reasons = _dedupe_preserve_order(reasons + usage.suspect_reasons.get(note_id, []))
    payload = {
        "note_id": note_id,
        "path": record.path.as_posix(),
        "retrievals": retrievals,
        "logged_uses": logged_uses,
        "use_ratio": round(use_ratio, 2),
        "suspect_count": suspects,
        "correction_count": corrections,
        "weak_retrievals": weak_retrievals,
        "first_seen": usage.first_seen.get(note_id),
        "last_seen": usage.last_seen.get(note_id),
        "reason": deduped_reasons[0],
        "reasons": deduped_reasons[:12],
        "evidence": [
            f"retrievals={retrievals}",
            f"logged_uses={logged_uses}",
            f"use_ratio={round(use_ratio, 2)}",
            f"suspect_count={suspects}",
            f"correction_count={corrections}",
            f"weak_retrievals={weak_retrievals}",
        ],
    }
    priority = "high" if suspects > 0 or corrections >= 2 else "medium"
    return upsert_hygiene_review_item(
        data_dir,
        queue="low_utility",
        title=f"{note_id} may be low utility",
        target_notes=[note_id],
        payload=payload,
        priority=priority,
        created=today.isoformat(),
        fingerprint=f"lowutility:{note_id}",
        source="hygiene-scan",
    )


def _upsert_suspect_item(
    *,
    data_dir: Path,
    record: NoteRecord,
    usage: _UsageSignals,
    reason: str,
    queue: str,
) -> str:
    note_id = record.note_id
    if queue == "dispute":
        payload = {
            "candidate_title": f"Suspect flag for {note_id}",
            "reason": reason,
            "note_id": note_id,
            "suspect_count": usage.suspect_counts.get(note_id, 0),
            "reasons": usage.suspect_reasons.get(note_id, [])[:12],
        }
        item_id = upsert_hygiene_review_item(
            data_dir,
            queue="dispute",
            title=f"Suspect claim reported for {note_id}",
            target_notes=[note_id],
            payload=payload,
            priority="high",
            created=date.today().isoformat(),
            fingerprint=f"suspect-dispute:{note_id}",
            source="kb-flag-suspect",
            return_existing=True,
        )
        if item_id:
            return item_id
        raise ReviewStateError("Could not upsert dispute review item for suspect flag.")

    low_utility_item = _maybe_upsert_low_utility(
        data_dir=data_dir,
        record=record,
        usage=usage,
        today=date.today(),
        reason_hint=f"suspect flag: {reason.strip()}",
    )
    if low_utility_item:
        return low_utility_item
    existing_item = upsert_hygiene_review_item(
        data_dir,
        queue="low_utility",
        title=f"{note_id} may be low utility",
        target_notes=[note_id],
        payload={
            "note_id": note_id,
            "path": record.path.as_posix(),
            "retrievals": usage.retrieval_counts.get(note_id, 0),
            "logged_uses": usage.logged_use_counts.get(note_id, 0),
            "use_ratio": 0.0,
            "suspect_count": usage.suspect_counts.get(note_id, 0),
            "correction_count": usage.correction_counts.get(note_id, 0),
            "weak_retrievals": usage.weak_retrieval_counts.get(note_id, 0),
            "reason": f"suspect flag: {reason.strip()}",
            "reasons": usage.suspect_reasons.get(note_id, [])[:12],
            "evidence": [f"suspect flag: {reason.strip()}"],
        },
        priority="high",
        created=date.today().isoformat(),
        fingerprint=f"lowutility:{note_id}",
        source="kb-flag-suspect",
        return_existing=True,
    )
    if existing_item:
        return existing_item
    raise ReviewStateError("Could not upsert low-utility review item for suspect flag.")


def _load_backlinks(data_dir: Path) -> dict[str, list[str]]:
    path = data_dir / ".kb" / "backlinks.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            result[str(key)] = [str(item) for item in value if str(item).strip()]
    return result


def _event_note_ids(event: Mapping[str, Any]) -> list[str]:
    ids = event.get("returned_note_ids")
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if str(item).strip()]


def _event_date(event: Mapping[str, Any]) -> date | None:
    stamp = str(event.get("timestamp") or "").strip()
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed.date()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _stale_threshold_days(note: Note, stale_after_days: int) -> int:
    fm = note.frontmatter
    risk = str(fm.get("staleness_risk") or "medium")
    knowledge_type = str(fm.get("knowledge_type") or "")
    confidence = str(fm.get("confidence") or "medium")
    factor = 1.0
    if knowledge_type == "fact":
        if risk == "high":
            factor = 0.5
        elif risk == "medium":
            factor = 0.75
        else:
            factor = 1.0
    else:
        if risk == "high":
            factor = 0.9
        elif risk == "low":
            factor = 1.2
    if confidence == "low":
        factor *= 0.9
    return max(14, int(stale_after_days * factor))


def _stale_priority(*, note: Note) -> str:
    fm = note.frontmatter
    knowledge_type = str(fm.get("knowledge_type") or "")
    risk = str(fm.get("staleness_risk") or "")
    if knowledge_type == "fact" and risk in {"medium", "high"}:
        return "high"
    if risk == "high":
        return "high"
    return "medium"


def _source_quality(note: Note) -> str:
    sources = note.frontmatter.get("sources")
    if not isinstance(sources, list) or not sources:
        return "low"
    score = 0
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        kind = str(source.get("type") or "").lower()
        if kind in {"web", "paper", "docs", "book"}:
            score += 2
        elif kind in {"conversation", "note"}:
            score += 1
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def _records_by_topic(records: list[NoteRecord]) -> dict[str, list[NoteRecord]]:
    grouped: dict[str, list[NoteRecord]] = {}
    for record in records:
        grouped.setdefault(record.topic_path, []).append(record)
    return grouped


def _outbound_references(record: NoteRecord) -> set[str]:
    refs = set(NOTE_ID_REFERENCE_PATTERN.findall(record.note.body))
    for value in record.note.frontmatter.values():
        _extract_references_from_value(value, refs)
    refs.discard(record.note_id)
    return refs


def _extract_references_from_value(value: Any, refs: set[str]) -> None:
    if isinstance(value, str):
        refs.update(NOTE_ID_REFERENCE_PATTERN.findall(value))
        return
    if isinstance(value, list):
        for entry in value:
            _extract_references_from_value(entry, refs)
        return
    if isinstance(value, Mapping):
        for entry in value.values():
            _extract_references_from_value(entry, refs)


def _topic_neighbor_count(record: NoteRecord, *, by_topic: Mapping[str, list[NoteRecord]]) -> int:
    neighbors = [candidate for candidate in by_topic.get(record.topic_path, []) if candidate.note_id != record.note_id]
    if not neighbors:
        return 0
    tags = _string_list(record.note.frontmatter.get("tags"))
    if not tags:
        return len(neighbors)
    shared = 0
    for candidate in neighbors:
        other_tags = _string_list(candidate.note.frontmatter.get("tags"))
        if set(tags) & set(other_tags):
            shared += 1
    return shared


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_weak_retrieval(event: Mapping[str, Any]) -> bool:
    top_score = event.get("top_score")
    result_count = event.get("result_count")
    try:
        if top_score is not None and float(top_score) < 5.0:
            return True
    except (TypeError, ValueError):
        pass
    try:
        return int(result_count) <= 1
    except (TypeError, ValueError):
        return False


def _update_seen_windows(
    first_seen: dict[str, str],
    last_seen: dict[str, str],
    *,
    note_id: str,
    stamp: date | None,
) -> None:
    if stamp is None:
        return
    text = stamp.isoformat()
    if note_id not in first_seen or first_seen[note_id] > text:
        first_seen[note_id] = text
    if note_id not in last_seen or last_seen[note_id] < text:
        last_seen[note_id] = text


def _looks_like_correction(reason: str) -> bool:
    text = reason.lower()
    return any(term in text for term in _CORRECTION_TERMS)


def _is_dispute_reason(reason: str) -> bool:
    text = reason.lower()
    return any(term in text for term in _DISPUTE_TERMS)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value.strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
