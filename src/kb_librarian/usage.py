"""Structured usage and search-miss feedback logging."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from kb_librarian.errors import KBLibrarianError
from kb_librarian.review import upsert_search_miss_review_item
from kb_librarian.search_index import tokenize_query

SCHEMA_VERSION = 1
USAGE_LOG = ".kb/usage.log"
SEARCH_MISSES_LOG = ".kb/search-misses.log"
LOW_SCORE_MISS_THRESHOLD = 5.0
SEARCH_MISS_REPEAT_THRESHOLD = 3

_DURATION_RE = re.compile(r"^(\d+)([mhdw])$")


@dataclass(frozen=True)
class UsageSummary:
    window: str
    note_id: str | None
    retrieval_count: int
    logged_use_count: int
    search_miss_count: int | None
    retrievals_by_command: dict[str, int]
    retrieved_notes: dict[str, int]
    used_notes: dict[str, int]
    suspect_flags: list[str]


def log_retrieval(
    data_dir: Path,
    *,
    command: str,
    query: str,
    returned_note_ids: Iterable[str],
    result_count: int,
    budget: int | None = None,
    mode: str | None = None,
    filters: Mapping[str, Any] | None = None,
    top_score: float | None = None,
    synthesis_succeeded: bool | None = None,
    task: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append one retrieval event without storing note bodies."""

    note_ids = [str(note_id) for note_id in returned_note_ids if str(note_id).strip()]
    record = {
        "schema_version": SCHEMA_VERSION,
        "event": "retrieval",
        "timestamp": timestamp or _now_iso(),
        "command": command,
        "query": query,
        "task": task,
        "mode": mode,
        "budget": budget,
        "filters": _clean_mapping(filters or {}),
        "returned_note_ids": note_ids,
        "result_count": int(result_count),
        "top_score": _round_score(top_score),
        "synthesis_succeeded": synthesis_succeeded,
    }
    _append_jsonl(data_dir / USAGE_LOG, record)
    refresh_usage_stats(data_dir)
    return record


def log_note_use(
    data_dir: Path,
    *,
    note_id: str,
    task: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append one explicit note-use event."""

    record = {
        "schema_version": SCHEMA_VERSION,
        "event": "note-use",
        "timestamp": timestamp or _now_iso(),
        "note_id": note_id,
        "task": task,
    }
    _append_jsonl(data_dir / USAGE_LOG, record)
    refresh_usage_stats(data_dir)
    return record


def maybe_log_search_miss(
    data_dir: Path,
    *,
    command: str,
    query: str,
    result_count: int,
    top_score: float | None,
    filters: Mapping[str, Any] | None = None,
    task: str | None = None,
    report_miss: bool = False,
    timestamp: str | None = None,
) -> str | None:
    """Record a poor retrieval signal when results are empty, weak, or explicitly reported."""

    reason = _miss_reason(
        result_count=result_count,
        top_score=top_score,
        report_miss=report_miss,
    )
    if reason is None:
        return None
    return log_search_miss(
        data_dir,
        command=command,
        query=query,
        result_count=result_count,
        top_score=top_score,
        filters=filters,
        task=task,
        reason=reason,
        high_value=bool(report_miss),
        timestamp=timestamp,
    )


def log_search_miss(
    data_dir: Path,
    *,
    command: str,
    query: str,
    result_count: int,
    top_score: float | None,
    filters: Mapping[str, Any] | None = None,
    task: str | None = None,
    reason: str,
    high_value: bool = False,
    timestamp: str | None = None,
) -> str | None:
    """Append one search-miss event and promote repeated/high-value groups to review."""

    record = {
        "schema_version": SCHEMA_VERSION,
        "event": "search-miss",
        "timestamp": timestamp or _now_iso(),
        "command": command,
        "query": query,
        "task": task,
        "filters": _clean_mapping(filters or {}),
        "result_count": int(result_count),
        "top_score": _round_score(top_score),
        "reason": reason,
        "high_value": bool(high_value),
    }
    _append_jsonl(data_dir / SEARCH_MISSES_LOG, record)
    promoted = promote_search_misses(data_dir)
    refresh_usage_stats(data_dir)
    return promoted[-1] if promoted else None


def promote_search_misses(data_dir: Path) -> list[str]:
    """Promote repeated or high-value miss groups into durable review items."""

    events = [
        event
        for event in read_search_miss_events(data_dir)
        if str(event.get("query", "")).strip()
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(_miss_group_key(event), []).append(event)

    promoted: list[str] = []
    for group_events in grouped.values():
        group_events.sort(key=lambda item: str(item.get("timestamp", "")))
        if not _should_promote_group(group_events):
            continue
        item_id = _upsert_search_miss_item(data_dir, group_events)
        if item_id is not None:
            promoted.append(item_id)
    return promoted


def summarize_usage(
    data_dir: Path,
    *,
    since: str | None = None,
    note_id: str | None = None,
    now: datetime | None = None,
) -> UsageSummary:
    cutoff = parse_since(since, now=now)
    events = [
        event
        for event in read_usage_events(data_dir)
        if _event_in_window(event, cutoff=cutoff)
    ]

    retrieval_events: list[dict[str, Any]] = []
    note_use_events: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") == "retrieval":
            if note_id is None or note_id in _returned_note_ids(event):
                retrieval_events.append(event)
            continue
        if event.get("event") == "note-use":
            if note_id is None or event.get("note_id") == note_id:
                note_use_events.append(event)

    retrieved_counter: Counter[str] = Counter()
    command_counter: Counter[str] = Counter()
    for event in retrieval_events:
        command_counter[str(event.get("command") or "unknown")] += 1
        for returned_id in _returned_note_ids(event):
            if note_id is None or returned_id == note_id:
                retrieved_counter[returned_id] += 1

    used_counter: Counter[str] = Counter()
    for event in note_use_events:
        used_id = str(event.get("note_id", "")).strip()
        if used_id:
            used_counter[used_id] += 1

    search_miss_count: int | None = None
    if note_id is None:
        search_miss_count = sum(
            1
            for event in read_search_miss_events(data_dir)
            if _event_in_window(event, cutoff=cutoff)
        )

    return UsageSummary(
        window=_window_label(since),
        note_id=note_id,
        retrieval_count=len(retrieval_events),
        logged_use_count=len(note_use_events),
        search_miss_count=search_miss_count,
        retrievals_by_command=_sorted_counter(command_counter),
        retrieved_notes=_sorted_counter(retrieved_counter),
        used_notes=_sorted_counter(used_counter),
        suspect_flags=[],
    )


def render_usage_summary(summary: UsageSummary) -> str:
    lines = [
        "Usage summary",
        f"window: {summary.window}",
    ]
    if summary.note_id:
        lines.append(f"note: {summary.note_id}")
    lines.extend(
        [
            f"retrievals: {summary.retrieval_count}",
            f"logged uses: {summary.logged_use_count}",
        ]
    )
    if summary.search_miss_count is not None:
        lines.append(f"search misses: {summary.search_miss_count}")

    _extend_count_block(lines, "retrievals by command", summary.retrievals_by_command)
    _extend_count_block(lines, "top retrieved notes", summary.retrieved_notes)
    _extend_count_block(lines, "logged note uses", summary.used_notes)

    if summary.suspect_flags:
        lines.append("suspect flags:")
        for flag in summary.suspect_flags:
            lines.append(f"- {flag}")
    else:
        lines.append("suspect flags: none")
    return "\n".join(lines) + "\n"


def parse_since(value: str | None, *, now: datetime | None = None) -> datetime | None:
    if value is None or not value.strip():
        return None

    normalized = value.strip().lower()
    match = _DURATION_RE.match(normalized)
    anchor = now or datetime.now()
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            return anchor - timedelta(minutes=amount)
        if unit == "h":
            return anchor - timedelta(hours=amount)
        if unit == "d":
            return anchor - timedelta(days=amount)
        if unit == "w":
            return anchor - timedelta(weeks=amount)

    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        pass
    try:
        parsed_date = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise KBLibrarianError("--since must be a duration like 7d, 24h, 30m, 2w, or an ISO date.") from exc
    return datetime.combine(parsed_date, datetime.min.time())


def read_usage_events(data_dir: Path) -> list[dict[str, Any]]:
    return _read_jsonl(data_dir / USAGE_LOG)


def read_search_miss_events(data_dir: Path) -> list[dict[str, Any]]:
    return _read_jsonl(data_dir / SEARCH_MISSES_LOG)


def note_usage_counts(data_dir: Path) -> dict[str, int]:
    """Return retrieval/use counts by note ID for ranking boosts."""

    counts: Counter[str] = Counter()
    for event in read_usage_events(data_dir):
        if event.get("event") == "retrieval":
            for note_id in _returned_note_ids(event):
                counts[note_id] += 1
        elif event.get("event") == "note-use":
            note_id = str(event.get("note_id", "")).strip()
            if note_id:
                counts[note_id] += 1
    return dict(counts)


def usage_stats_payload(data_dir: Path) -> dict[str, Any]:
    events = read_usage_events(data_dir)
    miss_events = read_search_miss_events(data_dir)
    retrievals = [event for event in events if event.get("event") == "retrieval"]
    logged_uses = [event for event in events if event.get("event") == "note-use"]
    command_counter: Counter[str] = Counter(
        str(event.get("command") or "unknown") for event in retrievals
    )
    last_seen = ""
    for event in [*events, *miss_events]:
        timestamp = str(event.get("timestamp") or "")
        if timestamp > last_seen:
            last_seen = timestamp
    return {
        "retrievals": len(retrievals),
        "logged_uses": len(logged_uses),
        "search_misses": len(miss_events),
        "retrievals_by_command": dict(sorted(command_counter.items())),
        "last_event_at": last_seen or None,
    }


def refresh_usage_stats(data_dir: Path) -> None:
    stats_path = data_dir / ".kb" / "stats.json"
    try:
        loaded = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    loaded["usage"] = usage_stats_payload(data_dir)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(loaded, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _upsert_search_miss_item(data_dir: Path, events: list[dict[str, Any]]) -> str | None:
    first = events[0]
    latest = events[-1]
    query = str(latest.get("query") or first.get("query") or "").strip()
    normalized_query = _normalized_query(query)
    filters = _clean_mapping(latest.get("filters") if isinstance(latest.get("filters"), Mapping) else {})
    reason_counter = Counter(str(event.get("reason") or "unknown") for event in events)
    high_value = any(bool(event.get("high_value")) for event in events)
    payload = {
        "query": query,
        "normalized_query": normalized_query,
        "misses": len(events),
        "first_seen": str(first.get("timestamp") or ""),
        "last_seen": str(latest.get("timestamp") or ""),
        "commands": sorted({str(event.get("command") or "unknown") for event in events}),
        "filters": filters,
        "reasons": dict(sorted(reason_counter.items())),
        "latest_result_count": latest.get("result_count"),
        "latest_top_score": latest.get("top_score"),
        "high_value": high_value,
        "examples": [
            {
                "timestamp": event.get("timestamp"),
                "command": event.get("command"),
                "reason": event.get("reason"),
                "result_count": event.get("result_count"),
                "top_score": event.get("top_score"),
                "task": event.get("task"),
            }
            for event in events[-3:]
        ],
    }
    title = f'"{query}" missed useful results {len(events)} time(s)'
    fingerprint = _search_miss_fingerprint(
        normalized_query=normalized_query,
        filters=filters,
    )
    return upsert_search_miss_review_item(
        data_dir,
        title=title,
        payload=payload,
        priority="high" if high_value or len(events) >= 5 else "medium",
        created=str(first.get("timestamp") or date.today().isoformat()),
        fingerprint=fingerprint,
    )


def _should_promote_group(events: list[dict[str, Any]]) -> bool:
    if len(events) >= SEARCH_MISS_REPEAT_THRESHOLD:
        return True
    return any(bool(event.get("high_value")) for event in events)


def _miss_group_key(event: Mapping[str, Any]) -> str:
    query = _normalized_query(str(event.get("query") or ""))
    filters = event.get("filters") if isinstance(event.get("filters"), Mapping) else {}
    return json.dumps(
        {
            "normalized_query": query,
            "filters": _clean_mapping(filters),
        },
        sort_keys=True,
    )


def _search_miss_fingerprint(*, normalized_query: str, filters: Mapping[str, Any]) -> str:
    material = json.dumps(
        {
            "kind": "searchmiss",
            "normalized_query": normalized_query,
            "filters": _clean_mapping(filters),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"searchmiss:{digest}"


def _normalized_query(query: str) -> str:
    tokens = tokenize_query(query)
    if tokens:
        return " ".join(tokens)
    return " ".join(query.lower().split())


def _miss_reason(*, result_count: int, top_score: float | None, report_miss: bool) -> str | None:
    if report_miss:
        return "reported-poor-result"
    if result_count <= 0:
        return "zero-results"
    if top_score is not None and top_score < LOW_SCORE_MISS_THRESHOLD:
        return "low-score"
    return None


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _event_in_window(event: Mapping[str, Any], *, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    timestamp = _parse_timestamp(str(event.get("timestamp") or ""))
    if timestamp is None:
        return False
    return _comparable_datetime(timestamp) >= _comparable_datetime(cutoff)


def _parse_timestamp(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _comparable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _returned_note_ids(event: Mapping[str, Any]) -> list[str]:
    value = event.get("returned_note_ids")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _clean_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, (str, int, float, bool)):
            cleaned[str(key)] = item
        elif isinstance(item, list):
            cleaned[str(key)] = [str(part) for part in item if str(part).strip()]
        else:
            cleaned[str(key)] = str(item)
    return cleaned


def _round_score(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _extend_count_block(lines: list[str], title: str, counts: Mapping[str, int]) -> None:
    if not counts:
        return
    lines.append(f"{title}:")
    for key, value in counts.items():
        lines.append(f"- {key}: {value}")


def _window_label(since: str | None) -> str:
    return f"last {since.strip()}" if since and since.strip() else "all time"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
