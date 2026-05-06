"""Task-shaped context retrieval and rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from kb_librarian.indexing import reindex_data_dir
from kb_librarian.providers import LLMProvider, operation_route, provider_from_config
from kb_librarian.search_index import query_candidates, tokenize_query
from kb_librarian.storage import NoteRecord, load_note_records

CONTEXT_MODES = ("coding", "architecture", "debugging", "writing", "research", "review")
VALID_CONTEXT_MODES = set(CONTEXT_MODES)

MODE_TYPE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "coding": ("technique", "pattern", "anti-pattern", "heuristic"),
    "architecture": ("decision", "pattern", "heuristic", "anti-pattern"),
    "debugging": ("technique", "anti-pattern", "fact", "open-question"),
    "writing": ("decision", "heuristic", "pattern", "technique"),
    "research": ("fact", "decision", "heuristic", "pattern"),
    "review": ("anti-pattern", "heuristic", "open-question", "decision"),
}

TRUST_RISK_STATUSES = {"disputed", "needs-review", "superseded"}


@dataclass(frozen=True)
class ContextSelection:
    note_id: str
    title: str
    summary: str
    topic: str
    knowledge_type: str
    status: str
    confidence: str
    updated: str
    path: str
    excerpt: str
    retrieval_phrases: list[str]
    tags: list[str]
    score: float
    reasons: list[str]
    trust_flags: list[str]


@dataclass(frozen=True)
class ContextResult:
    task: str
    mode: str
    budget: int
    synthesis_markdown: str
    selected_notes: list[ContextSelection]
    citations: list[dict[str, str]]
    message: str | None = None


def build_context(
    data_dir: Path,
    *,
    config: Mapping[str, Any],
    task: str,
    mode: str,
    budget: int,
    env: Mapping[str, str] | None = None,
) -> ContextResult:
    records = load_note_records(data_dir, validate=True)
    if not records:
        return ContextResult(
            task=task,
            mode=mode,
            budget=budget,
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No useful notes found for this task. Try `kb search` for precise lookup.",
        )

    fts_path = data_dir / ".kb" / "fts.sqlite"
    if not fts_path.exists():
        reindex_data_dir(data_dir)

    candidates = query_candidates(fts_path, query=task, topic=None, knowledge_type=None)
    if not candidates:
        return ContextResult(
            task=task,
            mode=mode,
            budget=budget,
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No useful notes found for this task. Try `kb search` for precise lookup.",
        )

    by_id = {record.note_id: record for record in records}
    usage_counts = _load_usage_counts(data_dir)
    tokens = tokenize_query(task)

    scored: list[ContextSelection] = []
    for candidate in candidates:
        note_id = str(candidate.get("id", "")).strip()
        if not note_id or note_id not in by_id:
            continue
        record = by_id[note_id]
        fm = record.note.frontmatter
        status = str(fm.get("status", "")).strip()
        if status == "archived":
            continue

        selection = _score_record(
            task=task,
            tokens=tokens,
            mode=mode,
            record=record,
            data_dir=data_dir,
            usage_count=usage_counts.get(note_id, 0),
        )
        if selection is None:
            continue
        scored.append(selection)

    if not scored:
        return ContextResult(
            task=task,
            mode=mode,
            budget=budget,
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No useful notes found for this task. Try `kb search` for precise lookup.",
        )

    scored.sort(key=lambda item: (-item.score, item.note_id))
    selected = _apply_context_budget(scored, budget)
    if not selected:
        return ContextResult(
            task=task,
            mode=mode,
            budget=budget,
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No useful notes found for this task. Try `kb search` for precise lookup.",
        )

    route = operation_route(config, "synthesize")
    provider: LLMProvider = provider_from_config(config, route.provider, env=env)
    synthesis = provider.synthesize_context(
        task=task,
        mode=mode,
        budget=budget,
        model=route.model,
        selected_notes=[_selection_payload(item) for item in selected],
    )
    citations = [
        {
            "note_id": item.note_id,
            "path": item.path,
            "status": item.status,
            "confidence": item.confidence,
        }
        for item in selected
    ]

    return ContextResult(
        task=task,
        mode=mode,
        budget=budget,
        synthesis_markdown=synthesis.strip(),
        selected_notes=selected,
        citations=citations,
    )


def _score_record(
    *,
    task: str,
    tokens: list[str],
    mode: str,
    record: NoteRecord,
    data_dir: Path,
    usage_count: int,
) -> ContextSelection | None:
    fm = record.note.frontmatter

    note_id = str(fm.get("id", "")).strip()
    title = str(fm.get("title", "")).strip()
    summary = str(fm.get("summary", "")).strip()
    topic = str(fm.get("topic", "")).strip()
    knowledge_type = str(fm.get("knowledge_type", "")).strip()
    status = str(fm.get("status", "")).strip()
    confidence = str(fm.get("confidence", "")).strip()
    updated = str(fm.get("updated", "")).strip()
    staleness_risk = str(fm.get("staleness_risk", "")).strip()

    tags = _coerce_string_list(fm.get("tags", []))
    retrieval_phrases = _coerce_string_list(fm.get("retrieval_phrases", []))
    excerpt = _excerpt(record.note.body)

    title_l = title.lower()
    summary_l = summary.lower()
    note_id_l = note_id.lower()
    topic_l = topic.lower()
    tags_l = [item.lower() for item in tags]
    phrases_l = [item.lower() for item in retrieval_phrases]
    body_l = record.note.body.lower()
    task_l = task.strip().lower()

    score = 0.0
    reasons: list[str] = []
    relevance_score = 0.0

    if task_l and (task_l == note_id_l or task_l == title_l):
        score += 600.0
        relevance_score += 600.0
        reasons.append("exact_id_or_title")

    if task_l and task_l in title_l:
        score += 220.0
        relevance_score += 220.0
        reasons.append("title_phrase")

    title_hits = _token_hits(tokens, title_l)
    if title_hits:
        bump = 16.0 * float(title_hits)
        score += bump
        relevance_score += bump
        reasons.append("title")

    summary_hits = _token_hits(tokens, summary_l)
    if summary_hits:
        bump = 10.0 * float(summary_hits)
        score += bump
        relevance_score += bump
        reasons.append("summary")

    phrase_hits = _token_hits(tokens, "\n".join(phrases_l))
    if phrase_hits:
        bump = 9.0 * float(phrase_hits)
        score += bump
        relevance_score += bump
        reasons.append("retrieval_phrase")

    tag_hits = _token_hits(tokens, "\n".join(tags_l)) + _token_hits(tokens, topic_l)
    if tag_hits:
        bump = 8.0 * float(tag_hits)
        score += bump
        relevance_score += bump
        reasons.append("tag_or_topic")

    type_boost = _type_preference_boost(mode, knowledge_type)
    if type_boost:
        score += type_boost
        relevance_score += type_boost
        reasons.append("note_type_fit")

    body_hits = _token_hits(tokens, body_l)
    if body_hits:
        bump = min(40.0, 2.5 * float(body_hits))
        score += bump
        relevance_score += bump
        reasons.append("body")

    if usage_count > 0:
        boost = min(20.0, float(usage_count) * 2.0)
        score += boost
        reasons.append("usage")

    recency = _recency_score(updated)
    score += recency
    if recency:
        reasons.append("recency")

    if staleness_risk == "high":
        score -= 12.0
        reasons.append("staleness_high")
    elif staleness_risk == "medium":
        score -= 5.0
        reasons.append("staleness_medium")

    score += _status_score(status)
    score += _confidence_score(confidence)

    trust_flags = _trust_flags(status=status, confidence=confidence, staleness_risk=staleness_risk)
    if trust_flags and relevance_score < 70.0:
        return None

    if relevance_score <= 0:
        return None

    return ContextSelection(
        note_id=note_id,
        title=title,
        summary=summary,
        topic=topic,
        knowledge_type=knowledge_type,
        status=status,
        confidence=confidence,
        updated=updated,
        path=record.path.relative_to(data_dir).as_posix(),
        excerpt=excerpt,
        retrieval_phrases=retrieval_phrases,
        tags=tags,
        score=round(score, 2),
        reasons=sorted(set(reasons)),
        trust_flags=trust_flags,
    )


def _selection_payload(item: ContextSelection) -> dict[str, Any]:
    return {
        "note_id": item.note_id,
        "title": item.title,
        "summary": item.summary,
        "topic": item.topic,
        "knowledge_type": item.knowledge_type,
        "status": item.status,
        "confidence": item.confidence,
        "updated": item.updated,
        "path": item.path,
        "excerpt": item.excerpt,
        "retrieval_phrases": item.retrieval_phrases,
        "tags": item.tags,
        "score": item.score,
        "trust_flags": item.trust_flags,
    }


def _apply_context_budget(items: list[ContextSelection], budget: int) -> list[ContextSelection]:
    if budget <= 0:
        return items[:1]

    source_budget = max(120, int(budget * 0.6))
    max_notes = max(1, min(8, budget // 120))
    selected: list[ContextSelection] = []
    used = 0
    for item in items:
        token_cost = _estimate_item_tokens(item)
        if used + token_cost > source_budget:
            continue
        selected.append(_trim_item(item, max_excerpt_tokens=max(30, source_budget - used - 20)))
        used += token_cost
        if len(selected) >= max_notes:
            break
    return selected


def _trim_item(item: ContextSelection, *, max_excerpt_tokens: int) -> ContextSelection:
    excerpt = _trim_to_tokens(item.excerpt, max_excerpt_tokens)
    return ContextSelection(
        note_id=item.note_id,
        title=item.title,
        summary=item.summary,
        topic=item.topic,
        knowledge_type=item.knowledge_type,
        status=item.status,
        confidence=item.confidence,
        updated=item.updated,
        path=item.path,
        excerpt=excerpt,
        retrieval_phrases=item.retrieval_phrases,
        tags=item.tags,
        score=item.score,
        reasons=item.reasons,
        trust_flags=item.trust_flags,
    )


def _estimate_item_tokens(item: ContextSelection) -> int:
    text = " ".join(
        [
            item.title,
            item.summary,
            item.excerpt,
            " ".join(item.retrieval_phrases),
            " ".join(item.tags),
        ]
    )
    return max(1, len(text.split()) + 24)


def _trim_to_tokens(text: str, budget: int) -> str:
    words = text.split()
    if len(words) <= budget:
        return text
    return " ".join(words[: max(1, budget - 1)]) + " ..."


def _token_hits(tokens: list[str], text: str) -> int:
    hits = 0
    for token in tokens:
        if token and token in text:
            hits += text.count(token)
    return hits


def _type_preference_boost(mode: str, knowledge_type: str) -> float:
    preferred = MODE_TYPE_PREFERENCES.get(mode, ())
    for index, value in enumerate(preferred):
        if knowledge_type == value:
            return (4 - index) * 7.0
    return 0.0


def _status_score(status: str) -> float:
    if status == "active":
        return 6.0
    if status == "superseded":
        return -4.0
    if status == "needs-review":
        return -6.0
    if status == "disputed":
        return -12.0
    return 0.0


def _confidence_score(confidence: str) -> float:
    if confidence == "high":
        return 5.0
    if confidence == "medium":
        return 2.0
    if confidence == "low":
        return -7.0
    return 0.0


def _trust_flags(*, status: str, confidence: str, staleness_risk: str) -> list[str]:
    flags: list[str] = []
    if status in TRUST_RISK_STATUSES:
        flags.append(f"status:{status}")
    if confidence == "low":
        flags.append("confidence:low")
    if staleness_risk in {"medium", "high"}:
        flags.append(f"staleness:{staleness_risk}")
    return flags


def _recency_score(updated: str) -> float:
    try:
        updated_date = date.fromisoformat(updated)
    except ValueError:
        return 0.0
    age_days = (date.today() - updated_date).days
    if age_days < 0:
        return 0.0
    if age_days <= 30:
        return 8.0
    if age_days <= 180:
        return 4.0
    if age_days > 365:
        return -4.0
    return 0.0


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


def _excerpt(text: str, limit: int = 320) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _load_usage_counts(data_dir: Path) -> dict[str, int]:
    usage_path = data_dir / ".kb" / "usage.log"
    if not usage_path.exists():
        return {}

    counts: dict[str, int] = {}
    try:
        lines = usage_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for line in lines:
        for token in line.split():
            if len(token) >= 11 and token[4:5] == "-" and token[7:8] == "-":
                counts[token] = counts.get(token, 0) + 1
    return counts
