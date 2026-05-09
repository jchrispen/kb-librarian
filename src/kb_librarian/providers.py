"""Provider protocol and configured adapters."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, TypeVar
from urllib.parse import urlparse, urlunparse

from kb_librarian.errors import ProviderError
from kb_librarian.notes import CONFIDENCE_LEVELS, KNOWLEDGE_TYPES
from kb_librarian.provider_retry import (
    RetryEvent,
    RetryPolicy,
    call_with_retry,
    classify_provider_failure,
)
from kb_librarian.provider_seams import (
    BACKEND_LM_STUDIO,
    BACKEND_OLLAMA,
    BACKEND_VLLM,
    ProviderSeam,
    provider_seam_supported_for_runtime,
    resolve_provider_seam,
)
from kb_librarian.privacy import enforce_provider_privacy
from kb_librarian.storage import normalize_topic_for_path


T = TypeVar("T")

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
class ProviderFallbackEvent:
    operation: str
    provider: str
    model: str
    next_provider: str
    next_model: str
    classification_kind: str
    classification_detail: str
    error: ProviderError


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


@dataclass(frozen=True)
class LocalProviderStatus:
    reachable: bool
    models: list[str]
    message: str


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
    return operation_routes(config, operation)[0]


def operation_routes(config: Mapping[str, Any], operation: str) -> list[OperationRoute]:
    operations = config.get("operations")
    if not isinstance(operations, Mapping):
        raise ProviderError("Config section operations must be a mapping.")
    raw = operations.get(operation)
    if not isinstance(raw, Mapping):
        raise ProviderError(f"Config operation {operation!r} must be a mapping.")

    provider = _operation_provider(config, raw)
    model = raw.get("model")
    if not isinstance(provider, str) or not provider.strip():
        raise ProviderError(
            f"Config operation {operation!r} requires a provider or providers.policy.default_provider."
        )
    if not isinstance(model, str) or not model.strip():
        raise ProviderError(f"Config operation {operation!r} requires a model.")
    primary = OperationRoute(provider=provider.strip(), model=model.strip())
    return [primary, *_fallback_routes(config, operation, model=model.strip())]


def call_with_provider_policy(
    config: Mapping[str, Any],
    operation: str,
    *,
    operation_name: str,
    retry_policy: RetryPolicy,
    call: Callable[[LLMProvider, OperationRoute], T],
    env: Mapping[str, str] | None = None,
    provider_factory: Callable[..., LLMProvider] | None = None,
    privacy_topics: Iterable[str] = (),
    on_retry: Callable[[RetryEvent], None] | None = None,
    on_final_failure: Callable[[RetryEvent], None] | None = None,
    on_fallback: Callable[[ProviderFallbackEvent], None] | None = None,
) -> T:
    """Run a provider-backed call through deterministic selection and fallback policy."""

    routes = operation_routes(config, operation)
    factory = provider_from_config if provider_factory is None else provider_factory
    attempted: list[OperationRoute] = []
    last_error: ProviderError | None = None
    last_kind = "not_attempted"
    last_detail = "no_attempts"

    for index, route in enumerate(routes):
        attempted.append(route)
        try:
            enforce_provider_privacy(
                config,
                provider_name=route.provider,
                operation=operation_name,
                topics=privacy_topics,
            )
            provider = factory(config, route.provider, env=env)
            return call_with_retry(
                _policy_attempt_name(operation_name, route),
                lambda: call(provider, route),
                policy=retry_policy,
                on_retry=on_retry,
                on_final_failure=on_final_failure,
            )
        except ProviderError as error:
            last_error = error
            classification = classify_provider_failure(error)
            last_kind = classification.kind
            last_detail = classification.detail
            has_fallback = index < len(routes) - 1
            if classification.transient and has_fallback:
                next_route = routes[index + 1]
                if on_fallback is not None:
                    on_fallback(
                        ProviderFallbackEvent(
                            operation=operation_name,
                            provider=route.provider,
                            model=route.model,
                            next_provider=next_route.provider,
                            next_model=next_route.model,
                            classification_kind=classification.kind,
                            classification_detail=classification.detail,
                            error=error,
                        )
                    )
                continue
            break

    attempted_text = ", ".join(f"{route.provider}({route.model})" for route in attempted)
    stop_reason = f"{last_kind}:{last_detail}"
    if last_error is None:
        raise ProviderError(
            f"Provider policy for operation {operation_name!r} had no provider attempts "
            f"(stop_reason={stop_reason})."
        )
    raise ProviderError(
        f"Provider policy for operation {operation_name!r} stopped after provider attempts "
        f"[{attempted_text}] (stop_reason={stop_reason}). Last error: {last_error}"
    ) from last_error


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
        seam = _provider_seam(provider_name, provider_config)
        _ensure_runtime_support(seam)
        return MockProvider()
    if provider_name == "anthropic":
        seam = _provider_seam(provider_name, provider_config)
        _ensure_runtime_support(seam)
        environ = os.environ if env is None else env
        api_key_env = seam.api_key_env or "ANTHROPIC_API_KEY"
        api_key = environ.get(api_key_env)
        if not api_key:
            raise ProviderError(
                f"Missing Anthropic credentials for credential_source {seam.diagnostic_credential_source!r}. "
                f"Set environment variable {api_key_env} "
                "or switch providers.anthropic.credential_source back to 'api_key_env'."
            )
        return AnthropicProvider(api_key=api_key)
    if provider_name == "codex":
        seam = _provider_seam(provider_name, provider_config)
        _ensure_runtime_support(seam)
        base_url = provider_config.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ProviderError("Config key providers.codex.base_url is required.")
        environ = os.environ if env is None else env
        api_key_env = seam.api_key_env or "OPENAI_API_KEY"
        api_key = environ.get(api_key_env)
        if not api_key:
            raise ProviderError(
                f"Missing Codex credentials for credential_source {seam.diagnostic_credential_source!r}. "
                f"Set environment variable {api_key_env} "
                "or switch providers.codex.credential_source back to 'api_key_env'."
            )
        timeout = _provider_timeout_seconds(provider_config.get("timeout_seconds"), default=120.0)
        organization = provider_config.get("organization")
        if organization is not None and not isinstance(organization, str):
            raise ProviderError("Config key providers.codex.organization must be a string when present.")
        project = provider_config.get("project")
        if project is not None and not isinstance(project, str):
            raise ProviderError("Config key providers.codex.project must be a string when present.")
        return CodexProvider(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout,
            organization=organization.strip() if isinstance(organization, str) else None,
            project=project.strip() if isinstance(project, str) else None,
        )
    if provider_name == "local":
        seam = _provider_seam(provider_name, provider_config)
        _ensure_runtime_support(seam)
        base_url = provider_config.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ProviderError("Config key providers.local.base_url is required.")
        timeout = _provider_timeout_seconds(provider_config.get("timeout_seconds"), default=120.0)
        if seam.backend == BACKEND_OLLAMA:
            return LocalOllamaProvider(base_url=base_url, timeout_seconds=timeout)
        return LocalOpenAICompatibleProvider(
            backend=seam.backend,
            base_url=base_url,
            timeout_seconds=timeout,
        )

    raise ProviderError(f"Unsupported provider {provider_name!r}.")


def _operation_provider(config: Mapping[str, Any], route: Mapping[str, Any]) -> str | None:
    configured = route.get("provider")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        return None
    policy = providers.get("policy")
    if not isinstance(policy, Mapping):
        return None
    default_provider = policy.get("default_provider")
    if isinstance(default_provider, str) and default_provider.strip():
        return default_provider.strip()
    return None


def _fallback_routes(config: Mapping[str, Any], operation: str, *, model: str) -> list[OperationRoute]:
    providers = config.get("providers")
    if not isinstance(providers, Mapping):
        return []
    policy = providers.get("policy")
    if not isinstance(policy, Mapping):
        return []
    fallback = policy.get("fallback")
    if not isinstance(fallback, Mapping):
        return []
    entries = fallback.get(operation, [])
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ProviderError(f"Config providers.policy.fallback.{operation} must be a list.")

    routes: list[OperationRoute] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            provider = entry.strip()
            fallback_model = model
        elif isinstance(entry, Mapping):
            provider_value = entry.get("provider")
            provider = provider_value.strip() if isinstance(provider_value, str) else ""
            model_value = entry.get("model")
            fallback_model = model_value.strip() if isinstance(model_value, str) and model_value.strip() else model
        else:
            provider = ""
            fallback_model = model
        if not provider:
            raise ProviderError(
                f"Config providers.policy.fallback.{operation}[{index}] must be a provider string "
                "or a mapping with provider."
            )
        routes.append(OperationRoute(provider=provider, model=fallback_model))
    return routes


def _policy_attempt_name(operation_name: str, route: OperationRoute) -> str:
    return f"{operation_name}[provider={route.provider}]"


def local_provider_status(
    provider_config: Mapping[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> LocalProviderStatus:
    """Return local backend reachability and model names for diagnostics."""

    try:
        seam = resolve_provider_seam(
            "local",
            provider_config,
            error_factory=ProviderError,
            error_prefix="providers.local",
        )
    except ProviderError as exc:
        return LocalProviderStatus(False, [], f"Local provider configuration error: {exc}")
    base_url = provider_config.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return LocalProviderStatus(False, [], "Local provider base_url is not configured.")
    timeout = timeout_seconds
    if timeout is None:
        timeout = _provider_timeout_seconds(provider_config.get("timeout_seconds"), default=5.0)
    if seam.backend == BACKEND_OLLAMA:
        return _ollama_local_provider_status(base_url, timeout)
    if seam.backend in {BACKEND_VLLM, BACKEND_LM_STUDIO}:
        return _openai_compatible_local_provider_status(seam.backend, base_url, timeout)
    return LocalProviderStatus(False, [], f"Unsupported local backend {seam.backend!r}.")


def _ollama_local_provider_status(base_url: str, timeout: float) -> LocalProviderStatus:
    url = _join_url(base_url, "/api/tags")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return LocalProviderStatus(False, [], f"Ollama tags request failed with HTTP {exc.code}.")
    except urllib.error.URLError as exc:
        return LocalProviderStatus(False, [], f"Ollama backend is unreachable at {base_url}: {exc.reason}.")
    except TimeoutError:
        return LocalProviderStatus(False, [], f"Ollama backend timed out at {base_url}.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return LocalProviderStatus(False, [], "Ollama tags response was not valid JSON.")
    models = _ollama_model_names(payload)
    return LocalProviderStatus(True, models, f"Ollama backend is reachable at {base_url}.")


def _openai_compatible_local_provider_status(backend: str, base_url: str, timeout: float) -> LocalProviderStatus:
    label = _local_backend_label(backend)
    request = urllib.request.Request(_openai_compatible_url(base_url, "/models"), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return LocalProviderStatus(False, [], f"{label} models request failed with HTTP {exc.code}.")
    except urllib.error.URLError as exc:
        return LocalProviderStatus(False, [], f"{label} backend is unreachable at {base_url}: {exc.reason}.")
    except TimeoutError:
        return LocalProviderStatus(False, [], f"{label} backend timed out at {base_url}.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return LocalProviderStatus(False, [], f"{label} models response was not valid JSON.")
    models = _openai_model_names(payload)
    return LocalProviderStatus(True, models, f"{label} backend is reachable at {base_url}.")


def provider_runtime_support(provider_name: str, provider_config: Mapping[str, Any]) -> tuple[ProviderSeam, str | None]:
    """Return the resolved seam plus an implementation-gap message when unsupported."""

    seam = _provider_seam(provider_name, provider_config)
    supported, message = provider_seam_supported_for_runtime(seam)
    return seam, None if supported else message


def _provider_seam(provider_name: str, provider_config: Mapping[str, Any]) -> ProviderSeam:
    try:
        return resolve_provider_seam(
            provider_name,
            provider_config,
            error_factory=ProviderError,
            error_prefix=f"providers.{provider_name}",
        )
    except Exception as exc:
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError(str(exc)) from exc


def _ensure_runtime_support(seam: ProviderSeam) -> None:
    supported, message = provider_seam_supported_for_runtime(seam)
    if not supported:
        raise ProviderError(message or f"Provider {seam.provider_name!r} is not supported.")


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


class LocalOllamaProvider:
    """Ollama local HTTP API adapter using the provider structured response contract."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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
        payload = self._generate_json(model=model, prompt=prompt)
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
        prompt = (
            "Classify this KB candidate. Return only JSON with keys topic, knowledge_type, "
            "confidence, and reason. Topic should be a concise kebab-case compatible topic "
            "name. Confidence must be high, medium, or low.\n\n"
            f"Source path: {source_path.as_posix()}\n"
            f"Candidate: {json.dumps(candidate_to_payload(candidate), sort_keys=True)}\n\n"
            f"Document excerpt:\n{text[:6000]}"
        )
        return validate_classification_payload(self._generate_json(model=model, prompt=prompt))

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
        prompt = (
            "You are integrating a candidate KB note into an existing artifact. "
            "Return only JSON with keys verdict, target_note_ids, and rationale. "
            "Allowed verdict values: identical, adds_nuance, contradicts, unrelated. "
            "Choose target_note_ids from the provided matches. Return [] when verdict is unrelated.\n\n"
            f"Source path: {source_path.as_posix()}\n"
            f"Candidate: {json.dumps(candidate_to_payload(candidate), sort_keys=True)}\n"
            f"Classification: {json.dumps(classification.__dict__, sort_keys=True)}\n"
            f"Matches: {json.dumps(matches, sort_keys=True)}\n\n"
            f"Document excerpt:\n{text[:6000]}"
        )
        return validate_integration_payload(self._generate_json(model=model, prompt=prompt))

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
        return self._generate_text(model=model, prompt=prompt).strip()

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
        return self._generate_text(model=model, prompt=prompt).strip()

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
        payload = self._generate_json(model=model, prompt=prompt)
        if not isinstance(payload, Mapping):
            raise ProviderError("Compaction response must be a JSON object.")
        return payload

    def _generate_json(self, *, model: str, prompt: str) -> Any:
        text = self._ollama_generate(model=model, prompt=prompt, json_format=True)
        return parse_json_response(text)

    def _generate_text(self, *, model: str, prompt: str) -> str:
        return self._ollama_generate(model=model, prompt=prompt, json_format=False)

    def _ollama_generate(self, *, model: str, prompt: str, json_format: bool) -> str:
        if not model:
            raise ProviderError("Local provider operation requires a model.")
        body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if json_format:
            body["format"] = "json"
        request = urllib.request.Request(
            _join_url(self.base_url, "/api/generate"),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                response_body = exc.read().decode("utf-8")
            except Exception:
                response_body = ""
            body_excerpt = response_body.strip().replace("\n", " ")[:240]
            message = f"Local Ollama request failed with HTTP {exc.code}"
            if body_excerpt:
                message += f": {body_excerpt}"
            message += ". Start Ollama, pull the configured model, or switch the operation route."
            raise ProviderError(message) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                "Local Ollama request failed: "
                f"{exc}. Start Ollama at {self.base_url}, pull model {model!r}, "
                "or switch the operation route."
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                f"Local Ollama request timed out after {self.timeout_seconds:g}s. "
                "Increase providers.local.timeout_seconds or switch the operation route."
            ) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("Local Ollama response was not valid JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ProviderError("Local Ollama response was not a JSON object.")
        if payload.get("error"):
            raise ProviderError(
                "Local Ollama returned an error: "
                f"{payload['error']}. Pull model {model!r} or switch the operation route."
            )
        response = payload.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ProviderError("Local Ollama response did not contain text content.")
        return response


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


class CodexProvider(AnthropicProvider):
    """Codex-compatible Responses API adapter using structured JSON prompts."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 120.0,
        organization: str | None = None,
        project: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.organization = organization
        self.project = project

    def _messages_json(self, *, model: str, prompt: str) -> Any:
        envelope = self._responses_envelope(model=model, prompt=prompt, json_format=True)
        return parse_json_response(_codex_envelope_text(envelope))

    def _messages_text(self, *, model: str, prompt: str) -> str:
        envelope = self._responses_envelope(model=model, prompt=prompt, json_format=False)
        return _codex_envelope_text(envelope)

    def _responses_envelope(self, *, model: str, prompt: str, json_format: bool) -> Any:
        if not model:
            raise ProviderError("Codex provider operation requires a model.")
        body: dict[str, Any] = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": 4096,
        }
        if json_format:
            body["text"] = {"format": {"type": "json_object"}}
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        if self.project:
            headers["OpenAI-Project"] = self.project
        request = urllib.request.Request(
            _join_url(self.base_url, "/responses"),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _codex_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                "Codex provider request failed: "
                f"{exc}. Check providers.codex.base_url, network access, or switch the operation route."
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                f"Codex provider request timed out after {self.timeout_seconds:g}s. "
                "Increase providers.codex.timeout_seconds or switch the operation route."
            ) from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ProviderError("Codex provider response was not valid Responses API JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ProviderError("Codex provider response was not a JSON object.")
        response_error = payload.get("error")
        if response_error:
            raise ProviderError(f"Codex provider response returned an error: {_provider_error_text(response_error)}")
        return payload


class LocalOpenAICompatibleProvider(CodexProvider):
    """OpenAI-compatible local adapter for vLLM and LM Studio."""

    def __init__(self, *, backend: str, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.backend = backend
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.organization = None
        self.project = None

    def _responses_envelope(self, *, model: str, prompt: str, json_format: bool) -> Any:
        if not model:
            raise ProviderError("Local provider operation requires a model.")
        body: dict[str, Any] = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "max_output_tokens": 4096,
        }
        if json_format:
            body["text"] = {"format": {"type": "json_object"}}
        request = urllib.request.Request(
            _openai_compatible_url(self.base_url, "/responses"),
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _local_openai_compatible_http_error(self.backend, exc) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(_local_openai_compatible_transport_message(self.backend, exc, self.base_url, model)) from exc
        except TimeoutError as exc:
            raise ProviderError(_local_openai_compatible_timeout_message(self.backend, self.timeout_seconds)) from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ProviderError(f"{_local_backend_label(self.backend)} response was not valid Responses API JSON.") from exc
        if not isinstance(payload, Mapping):
            raise ProviderError(f"{_local_backend_label(self.backend)} response was not a JSON object.")
        response_error = payload.get("error")
        if response_error:
            raise ProviderError(
                f"{_local_backend_label(self.backend)} response returned an error: "
                f"{_provider_error_text(response_error)}"
            )
        return payload


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


def _codex_envelope_text(envelope: Any) -> str:
    if not isinstance(envelope, Mapping):
        raise ProviderError("Codex provider response was not a JSON object.")

    direct_output_text = envelope.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text.strip()

    text_parts: list[str] = []
    refusals: list[str] = []
    output = envelope.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if not isinstance(content_item, Mapping):
                    continue
                item_type = content_item.get("type")
                if item_type == "output_text" and isinstance(content_item.get("text"), str):
                    text = content_item["text"].strip()
                    if text:
                        text_parts.append(text)
                elif item_type == "refusal" and isinstance(content_item.get("refusal"), str):
                    refusal = content_item["refusal"].strip()
                    if refusal:
                        refusals.append(refusal)

    if text_parts:
        return "\n".join(text_parts)
    if refusals:
        raise ProviderError("Codex provider refused request: " + " ".join(refusals))

    incomplete_details = envelope.get("incomplete_details")
    if isinstance(incomplete_details, Mapping):
        reason = incomplete_details.get("reason")
        if isinstance(reason, str) and reason.strip():
            raise ProviderError(f"Codex provider response was incomplete: {reason.strip()}.")
    raise ProviderError("Codex provider response did not contain text content.")


def _codex_http_error(exc: urllib.error.HTTPError) -> ProviderError:
    try:
        response_body = exc.read().decode("utf-8")
    except Exception:
        response_body = ""
    body_excerpt = _provider_error_text(response_body).strip().replace("\n", " ")[:240]
    message = f"Codex provider request failed with HTTP {exc.code}"
    if body_excerpt:
        message += f": {body_excerpt}"
    if exc.code in {401, 403}:
        message += ". Check providers.codex.api_key_env and the configured API key."
    elif exc.code == 404:
        message += ". Check providers.codex.base_url and the configured model."
    elif exc.code == 429:
        message += ". The provider rate limited the request; retry policy may retry this operation."
    elif 500 <= int(exc.code) <= 599:
        message += ". The provider returned a server error; retry policy may retry this operation."
    return ProviderError(message)


def _provider_error_text(error_payload: Any) -> str:
    if isinstance(error_payload, str):
        try:
            decoded = json.loads(error_payload)
        except json.JSONDecodeError:
            return error_payload
        return _provider_error_text(decoded)
    if isinstance(error_payload, Mapping):
        nested_error = error_payload.get("error")
        if isinstance(nested_error, Mapping):
            return _provider_error_text(nested_error)
        message = error_payload.get("message")
        code = error_payload.get("code")
        if isinstance(message, str) and message.strip():
            if isinstance(code, str) and code.strip():
                return f"{code.strip()}: {message.strip()}"
            return message.strip()
        return json.dumps(dict(error_payload), sort_keys=True)
    return str(error_payload)


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


def _provider_timeout_seconds(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return default


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _openai_compatible_url(base_url: str, path: str) -> str:
    parsed = urlparse(base_url.rstrip("/"))
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/v1"):
        full_path = f"{base_path}/{path.lstrip('/')}"
    elif base_path:
        full_path = f"{base_path}/v1/{path.lstrip('/')}"
    else:
        full_path = f"/v1/{path.lstrip('/')}"
    return urlunparse(parsed._replace(path=full_path, params="", query="", fragment=""))


def _ollama_model_names(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        model = item.get("model")
        for value in (name, model):
            if isinstance(value, str) and value.strip() and value.strip() not in names:
                names.append(value.strip())
    return sorted(names)


def _openai_model_names(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() not in names:
            names.append(model_id.strip())
    return sorted(names)


def _local_backend_label(backend: str) -> str:
    return {
        BACKEND_OLLAMA: "Ollama",
        BACKEND_VLLM: "vLLM",
        BACKEND_LM_STUDIO: "LM Studio",
    }.get(backend, backend)


def _local_openai_compatible_http_error(backend: str, exc: urllib.error.HTTPError) -> ProviderError:
    try:
        response_body = exc.read().decode("utf-8")
    except Exception:
        response_body = ""
    label = _local_backend_label(backend)
    body_excerpt = _provider_error_text(response_body).strip().replace("\n", " ")[:240]
    message = f"Local {label} request failed with HTTP {exc.code}"
    if body_excerpt:
        message += f": {body_excerpt}"
    if backend == BACKEND_LM_STUDIO:
        message += ". Load the configured model in LM Studio, confirm the local server is running, or switch the operation route."
    elif backend == BACKEND_VLLM:
        message += ". Start vLLM with the configured model and an OpenAI-compatible /v1 endpoint, or switch the operation route."
    else:
        message += ". Check the configured local backend and operation route."
    return ProviderError(message)


def _local_openai_compatible_transport_message(backend: str, exc: urllib.error.URLError, base_url: str, model: str) -> str:
    label = _local_backend_label(backend)
    if backend == BACKEND_LM_STUDIO:
        return (
            f"Local {label} request failed: {exc}. Start the LM Studio local server at {base_url}, "
            f"load model {model!r}, or switch the operation route."
        )
    if backend == BACKEND_VLLM:
        return (
            f"Local {label} request failed: {exc}. Start vLLM at {base_url} with model {model!r}, "
            "or switch the operation route."
        )
    return f"Local {label} request failed: {exc}."


def _local_openai_compatible_timeout_message(backend: str, timeout_seconds: float) -> str:
    label = _local_backend_label(backend)
    return (
        f"Local {label} request timed out after {timeout_seconds:g}s. "
        "Increase providers.local.timeout_seconds or switch the operation route."
    )


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
