"""Provider protocol and configured adapters."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from kb_librarian.errors import ProviderError
from kb_librarian.notes import CONFIDENCE_LEVELS, KNOWLEDGE_TYPES
from kb_librarian.storage import normalize_topic_for_path


UTILITY_SCORES = {"high", "medium", "low"}
INTEGRATION_VERDICTS = {"identical", "adds_nuance", "contradicts", "unrelated"}


@dataclass(frozen=True)
class CandidateNote:
    title: str
    summary: str
    knowledge_type: str | None
    body: str
    retrieval_phrases: list[str]
    tags: list[str]
    confidence: str
    utility_score: str
    topic: str | None = None
    agent_use: list[str] | None = None
    applies_when: list[str] | None = None
    does_not_apply_when: list[str] | None = None
    failure_modes: list[str] | None = None
    claims: list[str] | None = None


@dataclass(frozen=True)
class ExtractionResult:
    candidates: list[CandidateNote]


@dataclass(frozen=True)
class ClassificationResult:
    topic: str
    knowledge_type: str
    confidence: str
    reason: str = ""


@dataclass(frozen=True)
class OperationRoute:
    provider: str
    model: str


@dataclass(frozen=True)
class IntegrationResult:
    verdict: str
    target_note_ids: list[str]
    rationale: str = ""


@dataclass(frozen=True)
class CompactionDisposition:
    note_id: str
    recommendation: str
    rationale: str


@dataclass(frozen=True)
class CompactionDraft:
    frontmatter: dict[str, Any]
    body: str
    source_note_ids: list[str]
    dispositions: list[CompactionDisposition]
    diff_summary: str


class LLMProvider(Protocol):
    """Provider operations needed across Phase 1 milestones."""

    def extract_candidates(
        self,
        *,
        text: str,
        source_path: Path,
        max_notes: int,
        model: str,
    ) -> ExtractionResult:
        """Extract durable candidate notes from one parsed document."""

    def classify_candidate(
        self,
        *,
        candidate: CandidateNote,
        text: str,
        source_path: Path,
        model: str,
    ) -> ClassificationResult:
        """Classify a candidate into topic and note type."""

    def integration_verdict(
        self,
        *,
        candidate: CandidateNote,
        classification: ClassificationResult,
        matches: list[dict[str, Any]],
        text: str,
        source_path: Path,
        model: str,
    ) -> IntegrationResult:
        """Return a verdict describing how to integrate a candidate."""

    def synthesize_context(self, **kwargs: object) -> str:
        """Return future task-shaped context."""

    def synthesize_exploration(self, **kwargs: object) -> str:
        """Return broad ideation context."""

    def synthesize_compaction(self, **kwargs: object) -> Mapping[str, Any]:
        """Return a reviewable canonical note draft and disposition plan."""


def operation_route(config: Mapping[str, Any], operation: str) -> OperationRoute:
    operations = config.get("operations")
    if not isinstance(operations, Mapping):
        raise ProviderError("Config section operations must be a mapping.")
    raw = operations.get(operation)
    if not isinstance(raw, Mapping):
        raise ProviderError(f"Config operation {operation!r} must be a mapping.")

    provider = raw.get("provider")
    model = raw.get("model")
    if not isinstance(provider, str) or not provider.strip():
        raise ProviderError(f"Config operation {operation!r} requires a provider.")
    if not isinstance(model, str) or not model.strip():
        raise ProviderError(f"Config operation {operation!r} requires a model.")
    return OperationRoute(provider=provider.strip(), model=model.strip())


def provider_from_config(
    config: Mapping[str, Any],
    provider_name: str,
    *,
    env: Mapping[str, str] | None = None,
) -> LLMProvider:
    providers = config.get("providers")
    if not isinstance(providers, Mapping) or provider_name not in providers:
        raise ProviderError(f"Provider {provider_name!r} is not configured.")

    provider_config = providers[provider_name]
    if not isinstance(provider_config, Mapping):
        raise ProviderError(f"Config section providers.{provider_name} must be a mapping.")

    if provider_name == "mock":
        return MockProvider()
    if provider_name == "anthropic":
        api_key_env = provider_config.get("api_key_env")
        if not isinstance(api_key_env, str) or not api_key_env.strip():
            raise ProviderError("Config key providers.anthropic.api_key_env is required.")
        environ = os.environ if env is None else env
        api_key = environ.get(api_key_env)
        if not api_key:
            raise ProviderError(
                f"Missing Anthropic API key. Set environment variable {api_key_env} "
                "or configure ingest to use the mock provider."
            )
        return AnthropicProvider(api_key=api_key)

    raise ProviderError(f"Unsupported provider {provider_name!r}.")


class MockProvider:
    """Deterministic provider used for tests and offline smoke checks."""

    def extract_candidates(
        self,
        *,
        text: str,
        source_path: Path,
        max_notes: int,
        model: str,
    ) -> ExtractionResult:
        del model
        if _has_no_durable_note_signal(text) or len(text.strip()) < 20:
            return ExtractionResult(candidates=[])

        title = _first_heading_or_line(text) or source_path.stem.replace("-", " ").title()
        summary = _first_paragraph(text, exclude_heading=True) or title
        knowledge_type = _guess_knowledge_type(text)
        phrases = _retrieval_phrases(title, summary)
        tags = [normalize_topic_for_path(item) for item in phrases[:3]]
        body = _candidate_body(knowledge_type, text)
        confidence = "low" if "ambiguous" in text.lower() else "high"
        utility_score = "medium" if "low utility" in text.lower() else "high"
        topic = _guess_topic(text)

        candidate = CandidateNote(
            title=title[:120],
            summary=summary[:240],
            knowledge_type=knowledge_type,
            topic=topic,
            body=body,
            retrieval_phrases=phrases,
            tags=tags,
            confidence=confidence,
            utility_score=utility_score,
            agent_use=["coding agent setup"] if "agent" in text.lower() else [],
            applies_when=[],
            does_not_apply_when=[],
            failure_modes=[],
            claims=[],
        )
        return ExtractionResult(candidates=[candidate][:max_notes])

    def classify_candidate(
        self,
        *,
        candidate: CandidateNote,
        text: str,
        source_path: Path,
        model: str,
    ) -> ClassificationResult:
        del source_path, model
        confidence = candidate.confidence
        topic = candidate.topic or _guess_topic(text) or "general"
        knowledge_type = candidate.knowledge_type or _guess_knowledge_type(text)
        reason = "deterministic mock classification"
        if "ambiguous" in f"{candidate.title} {candidate.summary} {text}".lower():
            confidence = "low"
            reason = "mock detected ambiguous classification signal"
        return ClassificationResult(
            topic=topic,
            knowledge_type=knowledge_type,
            confidence=confidence,
            reason=reason,
        )

    def integration_verdict(
        self,
        *,
        candidate: CandidateNote,
        classification: ClassificationResult,
        matches: list[dict[str, Any]],
        text: str,
        source_path: Path,
        model: str,
    ) -> IntegrationResult:
        del classification, source_path, model
        target_note_ids = _clean_string_list([item.get("note_id", "") for item in matches], "matches.note_id")
        if target_note_ids:
            target_note_ids = target_note_ids[:3]
        lowered = f"{candidate.title}\n{candidate.summary}\n{candidate.body}\n{text}".lower()
        if target_note_ids and any(token in lowered for token in ("identical", "duplicate", "same as")):
            return IntegrationResult(
                verdict="identical",
                target_note_ids=[target_note_ids[0]],
                rationale="mock keyword match for identical",
            )
        if target_note_ids and any(token in lowered for token in ("adds nuance", "additional nuance", "extends")):
            return IntegrationResult(
                verdict="adds_nuance",
                target_note_ids=[target_note_ids[0]],
                rationale="mock keyword match for adds_nuance",
            )
        if target_note_ids and any(token in lowered for token in ("contradict", "conflict", "opposes")):
            return IntegrationResult(
                verdict="contradicts",
                target_note_ids=[target_note_ids[0]],
                rationale="mock keyword match for contradicts",
            )
        return IntegrationResult(
            verdict="unrelated",
            target_note_ids=[],
            rationale="mock default unrelated verdict",
        )

    def synthesize_context(self, **kwargs: object) -> str:
        task = str(kwargs.get("task", "")).strip()
        mode = str(kwargs.get("mode", "")).strip() or "coding"
        selected_notes = kwargs.get("selected_notes")
        if not isinstance(selected_notes, list):
            selected_notes = []

        lines = [
            "## Directly relevant techniques",
        ]
        if selected_notes:
            for note in selected_notes[:3]:
                if not isinstance(note, Mapping):
                    continue
                note_id = str(note.get("note_id", "")).strip()
                summary = str(note.get("summary", "")).strip()
                if note_id and summary:
                    lines.append(f"- [{note_id}] {summary}")
        else:
            lines.append("- No selected notes.")

        lines.extend(
            [
                "",
                "## Applicable heuristics",
            ]
        )
        if selected_notes:
            for note in selected_notes[:3]:
                if not isinstance(note, Mapping):
                    continue
                note_id = str(note.get("note_id", "")).strip()
                title = str(note.get("title", "")).strip()
                if note_id and title:
                    lines.append(f"- [{note_id}] Apply {title.lower()} for {mode} work.")
        else:
            lines.append("- No applicable heuristics found.")

        lines.extend(
            [
                "",
                "## Warnings / failure modes",
            ]
        )
        warned = False
        for note in selected_notes:
            if not isinstance(note, Mapping):
                continue
            note_id = str(note.get("note_id", "")).strip()
            trust_flags = note.get("trust_flags", [])
            if isinstance(trust_flags, list) and trust_flags:
                lines.append(f"- [{note_id}] Treat with caution: {', '.join(str(item) for item in trust_flags)}.")
                warned = True
        if not warned:
            lines.append("- No elevated trust risks in selected notes.")

        lines.extend(
            [
                "",
                "## Suggested agent behavior",
                f"- Use the selected notes to execute: {task or 'current task'}.",
                "- Keep claims grounded to cited source notes.",
            ]
        )
        return "\n".join(lines).strip()

    def synthesize_exploration(self, **kwargs: object) -> str:
        problem = str(kwargs.get("problem", "")).strip()
        selected_notes = kwargs.get("selected_notes")
        if not isinstance(selected_notes, list):
            selected_notes = []

        direct_notes = [note for note in selected_notes if isinstance(note, Mapping)]
        lines = ["## Directly relevant concepts"]
        if direct_notes:
            for note in direct_notes[:3]:
                note_id = str(note.get("note_id", "")).strip()
                summary = str(note.get("summary", "")).strip()
                if note_id and summary:
                    lines.append(f"- [{note_id}] {summary}")
        else:
            lines.append("- No directly relevant concepts found.")

        lines.extend(["", "## Adjacent patterns"])
        for note in direct_notes[:4]:
            note_id = str(note.get("note_id", "")).strip()
            title = str(note.get("title", "")).strip()
            if note_id and title:
                lines.append(f"- [{note_id}] Consider adjacent use of {title.lower()}.")
        if len(lines) >= 2 and lines[-1] == "## Adjacent patterns":
            lines.append("- No adjacent patterns found.")

        lines.extend(["", "## Tensions / tradeoffs"])
        if direct_notes:
            lines.append("- Balance broad recall against grounding; keep claims tied to cited notes.")
        else:
            lines.append("- No tradeoffs identified from selected notes.")

        lines.extend(["", "## Possible analogies"])
        if direct_notes:
            lines.append("- Treat retrieved notes as reusable concept modules for the current problem.")
        else:
            lines.append("- No analogies identified from selected notes.")

        lines.extend(["", "## Anti-patterns to avoid"])
        warned = False
        for note in direct_notes:
            note_id = str(note.get("note_id", "")).strip()
            trust_flags = note.get("trust_flags", [])
            if isinstance(trust_flags, list) and trust_flags:
                lines.append(f"- [{note_id}] Avoid treating this as settled: {', '.join(str(item) for item in trust_flags)}.")
                warned = True
        if not warned:
            lines.append("- Avoid inventing connections not supported by source notes.")

        lines.extend(
            [
                "",
                "## Open questions",
                f"- What constraint matters most for: {problem or 'this exploration'}?",
            ]
        )
        return "\n".join(lines).strip()

    def synthesize_compaction(self, **kwargs: object) -> Mapping[str, Any]:
        source_notes = kwargs.get("source_notes")
        if not isinstance(source_notes, list):
            source_notes = []
        clean_notes = [note for note in source_notes if isinstance(note, Mapping)]
        first = clean_notes[0] if clean_notes else {}
        first_fm = first.get("frontmatter", {}) if isinstance(first, Mapping) else {}
        if not isinstance(first_fm, Mapping):
            first_fm = {}

        title = str(first_fm.get("title") or "Canonical compacted note").strip()
        summary = str(first_fm.get("summary") or f"Compacted proposal for {title}.").strip()
        topic = str(first_fm.get("topic") or "general").strip()
        knowledge_type = str(first_fm.get("knowledge_type") or "technique").strip()
        tags: list[str] = []
        retrieval_phrases: list[str] = []
        source_note_ids: list[str] = []
        body_lines = ["## Core idea", ""]
        for note in clean_notes:
            note_id = str(note.get("note_id", "")).strip()
            if not note_id:
                continue
            source_note_ids.append(note_id)
            fm = note.get("frontmatter", {})
            if isinstance(fm, Mapping):
                note_summary = str(fm.get("summary", "")).strip()
                note_title = str(fm.get("title", "")).strip()
                if note_summary:
                    body_lines.append(f"- [{note_id}] {note_summary}")
                elif note_title:
                    body_lines.append(f"- [{note_id}] {note_title}")
                tags.extend(str(item) for item in fm.get("tags", []) if isinstance(item, str))
                retrieval_phrases.extend(
                    str(item) for item in fm.get("retrieval_phrases", []) if isinstance(item, str)
                )

        if not source_note_ids:
            source_note_ids = ["unknown-note"]
            body_lines.append("- No source notes were provided.")

        body_lines.extend(
            [
                "",
                "## Source Notes",
                *[f"- {note_id}" for note_id in source_note_ids],
                "",
                "## Review Notes",
                "- This mock proposal preserves the original notes until review acceptance.",
            ]
        )
        return {
            "frontmatter": {
                "title": title,
                "summary": summary,
                "topic": topic,
                "knowledge_type": knowledge_type,
                "confidence": "medium",
                "retrieval_phrases": retrieval_phrases[:8] or [title.lower()],
                "tags": sorted(set(tags))[:8] or [normalize_topic_for_path(topic)],
            },
            "body": "\n".join(body_lines).strip() + "\n",
            "source_note_ids": source_note_ids,
            "dispositions": [
                {
                    "note_id": note_id,
                    "recommendation": "supersede",
                    "rationale": "content folded into the proposed canonical note",
                }
                for note_id in source_note_ids
            ],
            "diff_summary": (
                f"Drafts one canonical note from {len(source_note_ids)} source notes and recommends supersession."
            ),
        }


class AnthropicProvider:
    """Anthropic Messages API adapter using structured JSON prompts."""

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    def extract_candidates(
        self,
        *,
        text: str,
        source_path: Path,
        max_notes: int,
        model: str,
    ) -> ExtractionResult:
        prompt = (
            "Extract durable KB Librarian candidate notes from this markdown/text document. "
            "Return only JSON with shape {\"candidates\": [...]}. Each candidate must include "
            "title, summary, knowledge_type, body, retrieval_phrases, tags, confidence, "
            "utility_score, and may include topic, agent_use, applies_when, "
            "does_not_apply_when, failure_modes, claims. Prefer zero candidates over weak notes. "
            f"Return at most {max_notes} candidates.\n\n"
            f"Source path: {source_path.as_posix()}\n\n{text}"
        )
        payload = self._messages_json(model=model, prompt=prompt)
        result = validate_extraction_payload(payload)
        return ExtractionResult(candidates=result.candidates[:max_notes])

    def classify_candidate(
        self,
        *,
        candidate: CandidateNote,
        text: str,
        source_path: Path,
        model: str,
    ) -> ClassificationResult:
        candidate_json = json.dumps(candidate_to_payload(candidate), sort_keys=True)
        prompt = (
            "Classify this KB candidate. Return only JSON with keys topic, knowledge_type, "
            "confidence, and reason. Topic should be a concise kebab-case compatible topic "
            "name. Confidence must be high, medium, or low.\n\n"
            f"Source path: {source_path.as_posix()}\n"
            f"Candidate: {candidate_json}\n\n"
            f"Document excerpt:\n{text[:6000]}"
        )
        payload = self._messages_json(model=model, prompt=prompt)
        return validate_classification_payload(payload)

    def integration_verdict(
        self,
        *,
        candidate: CandidateNote,
        classification: ClassificationResult,
        matches: list[dict[str, Any]],
        text: str,
        source_path: Path,
        model: str,
    ) -> IntegrationResult:
        candidate_json = json.dumps(candidate_to_payload(candidate), sort_keys=True)
        classification_json = json.dumps(
            {
                "topic": classification.topic,
                "knowledge_type": classification.knowledge_type,
                "confidence": classification.confidence,
                "reason": classification.reason,
            },
            sort_keys=True,
        )
        matches_json = json.dumps(matches, sort_keys=True)
        prompt = (
            "You are integrating a candidate KB note into an existing artifact. "
            "Return only JSON with keys verdict, target_note_ids, and rationale. "
            "Allowed verdict values: identical, adds_nuance, contradicts, unrelated. "
            "Choose target_note_ids from the provided matches. Return [] when verdict is unrelated.\n\n"
            f"Source path: {source_path.as_posix()}\n"
            f"Candidate: {candidate_json}\n"
            f"Classification: {classification_json}\n"
            f"Matches: {matches_json}\n\n"
            f"Document excerpt:\n{text[:6000]}"
        )
        payload = self._messages_json(model=model, prompt=prompt)
        return validate_integration_payload(payload)

    def synthesize_context(self, **kwargs: object) -> str:
        task = str(kwargs.get("task", "")).strip()
        mode = str(kwargs.get("mode", "coding")).strip() or "coding"
        budget = int(kwargs.get("budget", 1800))
        model = str(kwargs.get("model", "")).strip()
        selected_notes = kwargs.get("selected_notes")
        if not isinstance(selected_notes, list):
            selected_notes = []

        prompt = (
            "Synthesize compact KB context in markdown with exactly these sections:\n"
            "## Directly relevant techniques\n"
            "## Applicable heuristics\n"
            "## Warnings / failure modes\n"
            "## Suggested agent behavior\n\n"
            "Ground every claim in the selected notes and cite note IDs in square brackets like [2026-...]. "
            "Do not invent facts outside selected notes.\n\n"
            f"Task: {task}\n"
            f"Mode: {mode}\n"
            f"Budget tokens: {budget}\n\n"
            f"Selected notes JSON:\n{json.dumps(selected_notes, sort_keys=True)}"
        )
        return self._messages_text(model=model, prompt=prompt).strip()

    def synthesize_exploration(self, **kwargs: object) -> str:
        problem = str(kwargs.get("problem", "")).strip()
        budget = int(kwargs.get("budget", 3000))
        model = str(kwargs.get("model", "")).strip()
        selected_notes = kwargs.get("selected_notes")
        if not isinstance(selected_notes, list):
            selected_notes = []

        prompt = (
            "Synthesize broad KB exploration in markdown with exactly these sections:\n"
            "## Directly relevant concepts\n"
            "## Adjacent patterns\n"
            "## Tensions / tradeoffs\n"
            "## Possible analogies\n"
            "## Anti-patterns to avoid\n"
            "## Open questions\n\n"
            "Ground every claim in the selected notes and cite note IDs in square brackets like [2026-...]. "
            "Use adjacent concepts only when the selected notes support them. "
            "Do not invent facts outside selected notes.\n\n"
            f"Problem: {problem}\n"
            f"Budget tokens: {budget}\n\n"
            f"Selected notes JSON:\n{json.dumps(selected_notes, sort_keys=True)}"
        )
        return self._messages_text(model=model, prompt=prompt).strip()

    def synthesize_compaction(self, **kwargs: object) -> Mapping[str, Any]:
        model = str(kwargs.get("model", "")).strip()
        cluster_id = str(kwargs.get("cluster_id", "")).strip()
        source_notes = kwargs.get("source_notes")
        if not isinstance(source_notes, list):
            source_notes = []
        prompt = (
            "Draft a review-gated KB compaction proposal. Return only JSON with keys:\n"
            "frontmatter, body, source_note_ids, dispositions, diff_summary.\n\n"
            "frontmatter must include title, summary, topic, knowledge_type, confidence, "
            "retrieval_phrases, and tags. Do not include an id; the reviewer will assign one later. "
            "body must be markdown for the proposed canonical note and cite source note IDs in square brackets. "
            "dispositions must be a list of objects with note_id, recommendation, and rationale; "
            "recommendation should be one of supersede, delete, keep, or review. "
            "diff_summary must explain the user-visible change in plain language. "
            "Do not apply changes to notes.\n\n"
            f"Cluster ID: {cluster_id}\n"
            f"Source notes JSON:\n{json.dumps(source_notes, sort_keys=True)}"
        )
        payload = self._messages_json(model=model, prompt=prompt)
        if not isinstance(payload, Mapping):
            raise ProviderError("Compaction response must be a JSON object.")
        return payload

    def _messages_json(self, *, model: str, prompt: str) -> Any:
        envelope = self._messages_envelope(model=model, prompt=prompt)
        text = _envelope_text(envelope)
        return parse_json_response(text)

    def _messages_text(self, *, model: str, prompt: str) -> str:
        envelope = self._messages_envelope(model=model, prompt=prompt)
        return _envelope_text(envelope)

    def _messages_envelope(self, *, model: str, prompt: str) -> Any:
        body = json.dumps(
            {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                response_body = exc.read().decode("utf-8")
            except Exception:
                response_body = ""
            body_excerpt = response_body.strip().replace("\n", " ")[:240]
            message = f"Anthropic request failed with HTTP {exc.code}"
            if body_excerpt:
                message += f": {body_excerpt}"
            raise ProviderError(message) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ProviderError("Anthropic response was not valid Messages API JSON.") from exc


def _envelope_text(envelope: Any) -> str:
    if not isinstance(envelope, Mapping):
        raise ProviderError("Anthropic response was not a JSON object.")
    text_parts = [
        item.get("text", "")
        for item in envelope.get("content", [])
        if isinstance(item, Mapping) and item.get("type") == "text"
    ]
    if not text_parts:
        raise ProviderError("Anthropic response did not contain text content.")
    return "\n".join(str(part) for part in text_parts if str(part).strip())


def parse_json_response(text: str) -> Any:
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Provider response was not valid JSON: {exc}") from exc


def validate_extraction_payload(payload: Any) -> ExtractionResult:
    if not isinstance(payload, Mapping):
        raise ProviderError("Extraction response must be a JSON object.")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ProviderError("Extraction response must include a candidates list.")
    candidates = [validate_candidate_payload(item) for item in raw_candidates]
    return ExtractionResult(candidates=candidates)


def validate_candidate_payload(payload: Any) -> CandidateNote:
    if not isinstance(payload, Mapping):
        raise ProviderError("Candidate must be a JSON object.")
    title = _required_string(payload, "title")
    summary = _required_string(payload, "summary")
    body = _required_string(payload, "body")
    knowledge_type = _optional_string(payload, "knowledge_type")
    if knowledge_type is not None and knowledge_type not in KNOWLEDGE_TYPES:
        raise ProviderError(f"Candidate knowledge_type {knowledge_type!r} is invalid.")
    confidence = _required_enum(payload, "confidence", CONFIDENCE_LEVELS)
    utility_score = _required_enum(payload, "utility_score", UTILITY_SCORES)
    topic = _optional_string(payload, "topic")
    return CandidateNote(
        title=title,
        summary=summary,
        knowledge_type=knowledge_type,
        topic=topic,
        body=body,
        retrieval_phrases=_required_string_list(payload, "retrieval_phrases"),
        tags=_required_string_list(payload, "tags"),
        confidence=confidence,
        utility_score=utility_score,
        agent_use=_optional_string_list(payload, "agent_use"),
        applies_when=_optional_string_list(payload, "applies_when"),
        does_not_apply_when=_optional_string_list(payload, "does_not_apply_when"),
        failure_modes=_optional_string_list(payload, "failure_modes"),
        claims=_optional_string_list(payload, "claims"),
    )


def validate_classification_payload(payload: Any) -> ClassificationResult:
    if not isinstance(payload, Mapping):
        raise ProviderError("Classification response must be a JSON object.")
    topic = _required_string(payload, "topic")
    knowledge_type = _required_enum(payload, "knowledge_type", KNOWLEDGE_TYPES)
    confidence = _required_enum(payload, "confidence", CONFIDENCE_LEVELS)
    reason = _optional_string(payload, "reason") or ""
    return ClassificationResult(
        topic=topic,
        knowledge_type=knowledge_type,
        confidence=confidence,
        reason=reason,
    )


def validate_integration_payload(payload: Any) -> IntegrationResult:
    if not isinstance(payload, Mapping):
        raise ProviderError("Integration response must be a JSON object.")
    verdict = _required_enum(payload, "verdict", INTEGRATION_VERDICTS)
    target_note_ids = _required_string_list(payload, "target_note_ids")
    rationale = _optional_string(payload, "rationale") or ""
    if verdict == "unrelated" and target_note_ids:
        raise ProviderError("Integration response with verdict unrelated must not include target_note_ids.")
    if verdict != "unrelated" and not target_note_ids:
        raise ProviderError(f"Integration response with verdict {verdict!r} requires at least one target note ID.")
    return IntegrationResult(
        verdict=verdict,
        target_note_ids=target_note_ids,
        rationale=rationale,
    )


def validate_compaction_payload(payload: Any) -> CompactionDraft:
    if not isinstance(payload, Mapping):
        raise ProviderError("Compaction response must be a JSON object.")
    frontmatter = payload.get("frontmatter")
    if not isinstance(frontmatter, Mapping):
        raise ProviderError("Compaction response field frontmatter must be an object.")
    compact_frontmatter: dict[str, Any] = {
        "title": _required_string(frontmatter, "title"),
        "summary": _required_string(frontmatter, "summary"),
        "topic": _required_string(frontmatter, "topic"),
        "knowledge_type": _required_enum(frontmatter, "knowledge_type", KNOWLEDGE_TYPES),
        "confidence": _required_enum(frontmatter, "confidence", CONFIDENCE_LEVELS),
        "retrieval_phrases": _required_string_list(frontmatter, "retrieval_phrases"),
        "tags": _required_string_list(frontmatter, "tags"),
    }
    for optional_field in (
        "basis",
        "sources",
        "agent_use",
        "applies_when",
        "does_not_apply_when",
        "failure_modes",
        "staleness_risk",
    ):
        if optional_field in frontmatter:
            compact_frontmatter[optional_field] = frontmatter[optional_field]

    body = _required_string(payload, "body")
    source_note_ids = _required_string_list(payload, "source_note_ids")
    raw_dispositions = payload.get("dispositions")
    if not isinstance(raw_dispositions, list) or not raw_dispositions:
        raise ProviderError("Compaction response field dispositions must be a non-empty list.")
    dispositions: list[CompactionDisposition] = []
    allowed_recommendations = {"supersede", "delete", "keep", "review"}
    seen_note_ids: set[str] = set()
    for index, item in enumerate(raw_dispositions):
        if not isinstance(item, Mapping):
            raise ProviderError(f"Compaction disposition {index} must be an object.")
        note_id = _required_string(item, "note_id")
        recommendation = _required_enum(item, "recommendation", allowed_recommendations)
        rationale = _required_string(item, "rationale")
        dispositions.append(
            CompactionDisposition(note_id=note_id, recommendation=recommendation, rationale=rationale)
        )
        seen_note_ids.add(note_id)
    missing = sorted(set(source_note_ids) - seen_note_ids)
    if missing:
        raise ProviderError(
            "Compaction response dispositions must include every source note ID; missing: "
            + ", ".join(missing)
        )
    diff_summary = _required_string(payload, "diff_summary")
    return CompactionDraft(
        frontmatter=compact_frontmatter,
        body=body,
        source_note_ids=source_note_ids,
        dispositions=dispositions,
        diff_summary=diff_summary,
    )


def candidate_to_payload(candidate: CandidateNote) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "summary": candidate.summary,
        "knowledge_type": candidate.knowledge_type,
        "topic": candidate.topic,
        "body": candidate.body,
        "retrieval_phrases": candidate.retrieval_phrases,
        "tags": candidate.tags,
        "confidence": candidate.confidence,
        "utility_score": candidate.utility_score,
        "agent_use": candidate.agent_use or [],
        "applies_when": candidate.applies_when or [],
        "does_not_apply_when": candidate.does_not_apply_when or [],
        "failure_modes": candidate.failure_modes or [],
        "claims": candidate.claims or [],
    }


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(f"Provider response field {field} must be a non-empty string.")
    return value.strip()


def _optional_string(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderError(f"Provider response field {field} must be a string when present.")
    stripped = value.strip()
    return stripped or None


def _required_enum(payload: Mapping[str, Any], field: str, allowed: set[str]) -> str:
    value = _required_string(payload, field)
    if value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ProviderError(f"Provider response field {field} must be one of: {allowed_text}")
    return value


def _required_string_list(payload: Mapping[str, Any], field: str) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ProviderError(f"Provider response field {field} must be a list of strings.")
    return _clean_string_list(value, field)


def _optional_string_list(payload: Mapping[str, Any], field: str) -> list[str]:
    value = payload.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderError(f"Provider response field {field} must be a list of strings when present.")
    return _clean_string_list(value, field)


def _clean_string_list(value: list[Any], field: str) -> list[str]:
    cleaned: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ProviderError(f"Provider response field {field}[{index}] must be a string.")
        stripped = item.strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _has_no_durable_note_signal(text: str) -> bool:
    lowered = text.lower()
    return "no durable notes worth creating" in lowered or "do not ingest" in lowered


def _first_heading_or_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
        return stripped
    return ""


def _first_paragraph(text: str, *, exclude_heading: bool) -> str:
    paragraphs = re.split(r"\n\s*\n", text.strip())
    for paragraph in paragraphs:
        compact = " ".join(line.strip() for line in paragraph.splitlines()).strip()
        if not compact:
            continue
        if exclude_heading and compact.startswith("#"):
            continue
        return compact
    return ""


def _guess_knowledge_type(text: str) -> str:
    lowered = text.lower()
    if "anti-pattern" in lowered or "avoid " in lowered:
        return "anti-pattern"
    if "decision" in lowered or "decided" in lowered:
        return "decision"
    if "open question" in lowered or "unresolved" in lowered:
        return "open-question"
    if "pattern" in lowered:
        return "pattern"
    if "heuristic" in lowered or "prefer " in lowered:
        return "heuristic"
    if "sqlite" in lowered or "supports" in lowered:
        return "fact"
    return "technique"


def _guess_topic(text: str) -> str:
    lowered = text.lower()
    if "agent" in lowered or "context" in lowered or "cli" in lowered or "kb" in lowered:
        return "agent-systems"
    if "python" in lowered or "pytest" in lowered:
        return "python"
    return "general"


def _retrieval_phrases(title: str, summary: str) -> list[str]:
    phrases: list[str] = []
    for text in (title, summary):
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text.lower())
        if not words:
            continue
        phrase = " ".join(words[:5])
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases or ["durable knowledge"]


def _candidate_body(knowledge_type: str, text: str) -> str:
    heading = {
        "fact": "## Claim",
        "technique": "## Core idea",
        "heuristic": "## Judgment",
        "pattern": "## Core idea",
        "anti-pattern": "## Failure mode",
        "decision": "## Decision",
        "open-question": "## Question",
    }[knowledge_type]
    return f"{heading}\n\n{text.strip()}\n"
