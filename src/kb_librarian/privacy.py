"""Privacy controls for provider-bound payloads."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from kb_librarian.errors import ProviderError
from kb_librarian.storage import normalize_topic_for_path


CLOUD_PROVIDERS = {"anthropic", "codex"}
REDACTION_TOKEN = "[REDACTED]"


def is_cloud_provider(provider_name: str) -> bool:
    return provider_name.strip().lower() in CLOUD_PROVIDERS


def enforce_provider_privacy(
    config: Mapping[str, Any],
    *,
    provider_name: str,
    operation: str,
    topics: Iterable[str] = (),
) -> None:
    """Raise when a provider attempt violates explicit privacy policy."""

    if not is_cloud_provider(provider_name):
        return
    privacy = _privacy_config(config)
    if privacy.get("cloud_llm_allowed") is False:
        raise ProviderError(
            f"Privacy policy blocks cloud provider {provider_name!r} for operation {operation!r}. "
            "Set privacy.cloud_llm_allowed to true or route the operation to a local provider."
        )

    blocked_topics = _normalized_string_set(privacy.get("blocked_topics", []))
    matched = sorted(
        {
            normalized
            for topic in topics
            for normalized in [normalize_topic_for_path(str(topic))]
            if normalized in blocked_topics
        }
    )
    if matched:
        raise ProviderError(
            f"Privacy policy blocks cloud provider {provider_name!r} for operation {operation!r} "
            "because topic(s) are blocked: "
            + ", ".join(matched)
            + ". Route the operation to a local provider or remove the topic from privacy.blocked_topics."
        )


def redact_text(config: Mapping[str, Any], text: str) -> str:
    """Apply configured redaction regexes to one provider-bound string."""

    redacted = text
    for pattern in _redaction_patterns(config):
        redacted = pattern.sub(REDACTION_TOKEN, redacted)
    return redacted


def redact_payload(config: Mapping[str, Any], payload: Any) -> Any:
    """Recursively redact strings in provider-bound payload data."""

    if isinstance(payload, str):
        return redact_text(config, payload)
    if isinstance(payload, list):
        return [redact_payload(config, item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_payload(config, item) for item in payload)
    if isinstance(payload, dict):
        return {key: redact_payload(config, value) for key, value in payload.items()}
    return payload


def _privacy_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    privacy = config.get("privacy")
    return privacy if isinstance(privacy, Mapping) else {}


def _redaction_patterns(config: Mapping[str, Any]) -> list[re.Pattern[str]]:
    privacy = _privacy_config(config)
    raw_patterns = privacy.get("redact_patterns", [])
    if not isinstance(raw_patterns, list):
        return []
    patterns: list[re.Pattern[str]] = []
    for item in raw_patterns:
        if not isinstance(item, str) or not item:
            continue
        try:
            patterns.append(re.compile(item))
        except re.error:
            continue
    return patterns


def _normalized_string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {normalize_topic_for_path(item) for item in value if isinstance(item, str) and item.strip()}
