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

    def integration_verdict(self, **kwargs: object) -> str:
        """Return a future integration verdict."""

    def synthesize_context(self, **kwargs: object) -> str:
        """Return future task-shaped context."""


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

    def integration_verdict(self, **kwargs: object) -> str:
        del kwargs
        return "unrelated"

    def synthesize_context(self, **kwargs: object) -> str:
        del kwargs
        return ""


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

    def integration_verdict(self, **kwargs: object) -> str:
        del kwargs
        raise ProviderError("Integration verdicts are out of scope for Phase 01c.")

    def synthesize_context(self, **kwargs: object) -> str:
        del kwargs
        raise ProviderError("Context synthesis is out of scope for Phase 01c.")

    def _messages_json(self, *, model: str, prompt: str) -> Any:
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
        except urllib.error.URLError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        try:
            envelope = json.loads(raw)
            text_parts = [
                item.get("text", "")
                for item in envelope.get("content", [])
                if isinstance(item, Mapping) and item.get("type") == "text"
            ]
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ProviderError("Anthropic response was not valid Messages API JSON.") from exc
        if not text_parts:
            raise ProviderError("Anthropic response did not contain text content.")
        return parse_json_response("\n".join(text_parts))


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
