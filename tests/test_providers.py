from __future__ import annotations

import json
import io
from pathlib import Path
import urllib.error

import pytest

from kb_librarian.provider_retry import classify_provider_failure
from kb_librarian.errors import ProviderError
from kb_librarian.providers import (
    CodexProvider,
    LocalOllamaProvider,
    MockProvider,
    local_provider_status,
    parse_json_response,
    provider_from_config,
    validate_classification_payload,
    validate_extraction_payload,
    validate_integration_payload,
)


def test_parse_json_response_accepts_json_fence():
    payload = parse_json_response('```json\n{"candidates": []}\n```')

    assert payload == {"candidates": []}


def test_validate_extraction_payload_builds_candidate():
    result = validate_extraction_payload(
        {
            "candidates": [
                {
                    "title": "Use task-shaped context",
                    "summary": "Agents benefit from compact context for the current task.",
                    "knowledge_type": "heuristic",
                    "body": "## Judgment\n\nUse task-shaped context.\n",
                    "retrieval_phrases": ["task-shaped context"],
                    "tags": ["agent-context"],
                    "confidence": "high",
                    "utility_score": "high",
                }
            ]
        }
    )

    assert result.candidates[0].title == "Use task-shaped context"
    assert result.candidates[0].knowledge_type == "heuristic"


def test_validate_extraction_payload_rejects_invalid_candidate_type():
    with pytest.raises(ProviderError, match="knowledge_type"):
        validate_extraction_payload(
            {
                "candidates": [
                    {
                        "title": "Bad",
                        "summary": "Bad candidate.",
                        "knowledge_type": "article",
                        "body": "Body",
                        "retrieval_phrases": [],
                        "tags": [],
                        "confidence": "high",
                        "utility_score": "high",
                    }
                ]
            }
        )


def test_validate_classification_payload():
    result = validate_classification_payload(
        {
            "topic": "agent-systems",
            "knowledge_type": "technique",
            "confidence": "medium",
            "reason": "fits agent systems",
        }
    )

    assert result.topic == "agent-systems"
    assert result.knowledge_type == "technique"


def test_mock_provider_is_deterministic():
    provider = MockProvider()

    extraction = provider.extract_candidates(
        text="# CLI context\n\nPrefer task-shaped context for coding agents.",
        source_path=Path(__file__),
        max_notes=7,
        model="mock",
    )
    classification = provider.classify_candidate(
        candidate=extraction.candidates[0],
        text="# CLI context\n\nPrefer task-shaped context for coding agents.",
        source_path=Path(__file__),
        model="mock",
    )

    assert extraction.candidates[0].title == "CLI context"
    assert classification.topic == "agent-systems"
    assert classification.confidence == "high"


def test_mock_provider_synthesize_context_is_grounded():
    provider = MockProvider()

    result = provider.synthesize_context(
        task="review this architecture",
        mode="architecture",
        budget=1800,
        selected_notes=[
            {
                "note_id": "2026-05-04-agent-context-cli-contract",
                "title": "Use a CLI as the stable contract",
                "summary": "A stable CLI lets multiple agents access the same artifact.",
                "trust_flags": [],
            }
        ],
    )

    assert "## Directly relevant techniques" in result
    assert "[2026-05-04-agent-context-cli-contract]" in result
    assert "## Suggested agent behavior" in result


def test_mock_provider_synthesize_exploration_has_explore_sections():
    provider = MockProvider()

    result = provider.synthesize_exploration(
        problem="reduce token burn while preserving agent access",
        budget=3000,
        selected_notes=[
            {
                "note_id": "2026-05-04-token-budget-pattern",
                "title": "Token budget pattern",
                "summary": "Use retrieval budgets to preserve useful agent access.",
                "trust_flags": ["confidence:low"],
            }
        ],
    )

    assert "## Directly relevant concepts" in result
    assert "## Adjacent patterns" in result
    assert "## Tensions / tradeoffs" in result
    assert "## Open questions" in result
    assert "[2026-05-04-token-budget-pattern]" in result


def test_validate_integration_payload():
    result = validate_integration_payload(
        {
            "verdict": "identical",
            "target_note_ids": ["2026-05-04-agent-context-cli-contract"],
            "rationale": "same idea",
        }
    )

    assert result.verdict == "identical"
    assert result.target_note_ids == ["2026-05-04-agent-context-cli-contract"]


def test_validate_integration_payload_rejects_invalid_targets():
    with pytest.raises(ProviderError, match="requires at least one target note ID"):
        validate_integration_payload(
            {
                "verdict": "adds_nuance",
                "target_note_ids": [],
                "rationale": "missing target",
            }
        )


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_provider_from_config_builds_local_ollama_provider():
    provider = provider_from_config(
        {
            "providers": {
                "local": {
                    "backend": "ollama",
                    "base_url": "http://127.0.0.1:11434",
                    "timeout_seconds": 3,
                }
            }
        },
        "local",
    )

    assert isinstance(provider, LocalOllamaProvider)


def test_provider_from_config_builds_codex_provider():
    provider = provider_from_config(
        {
            "providers": {
                "codex": {
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "https://api.openai.com/v1",
                    "timeout_seconds": 3,
                }
            }
        },
        "codex",
        env={"OPENAI_API_KEY": "test-key"},
    )

    assert isinstance(provider, CodexProvider)


def test_provider_from_config_requires_codex_api_key():
    with pytest.raises(ProviderError, match="Missing Codex provider API key"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "api_key_env": "OPENAI_API_KEY",
                        "base_url": "https://api.openai.com/v1",
                    }
                }
            },
            "codex",
            env={},
        )


def test_codex_provider_maps_responses_structured_response(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        requests.append((request, json.loads(request.data.decode("utf-8")), timeout))
        return _FakeResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "candidates": [
                                            {
                                                "title": "Codex provider note",
                                                "summary": "Codex provider extracts structured notes.",
                                                "knowledge_type": "technique",
                                                "body": "## Core idea\n\nUse Codex provider routes.\n",
                                                "retrieval_phrases": ["codex provider"],
                                                "tags": ["codex"],
                                                "confidence": "high",
                                                "utility_score": "high",
                                            }
                                        ]
                                    }
                                ),
                            }
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = CodexProvider(api_key="test-key", base_url="https://example.test/v1/", timeout_seconds=11)
    result = provider.extract_candidates(
        text="# Codex provider\n\nUse Codex-compatible provider routes.",
        source_path=Path("raw/codex.md"),
        max_notes=3,
        model="gpt-5.1-codex",
    )

    request, body, timeout = requests[0]
    assert result.candidates[0].title == "Codex provider note"
    assert request.full_url == "https://example.test/v1/responses"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert body["model"] == "gpt-5.1-codex"
    assert body["text"]["format"]["type"] == "json_object"
    assert body["input"][0]["content"][0]["type"] == "input_text"
    assert timeout == 11


def test_codex_provider_http_errors_feed_retry_classification(monkeypatch):
    def fake_urlopen(request, timeout):  # noqa: ANN001
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":{"code":"rate_limit_exceeded","message":"slow down"}}'),
        )

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://example.test/v1")

    with pytest.raises(ProviderError) as exc_info:
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1-codex")

    assert "Codex provider request failed with HTTP 429" in str(exc_info.value)
    assert "rate limited" in str(exc_info.value)
    classification = classify_provider_failure(exc_info.value)
    assert classification.transient is True
    assert classification.detail == "http_status=429"


def test_local_ollama_provider_maps_structured_response(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        requests.append((request.full_url, json.loads(request.data.decode("utf-8")), timeout))
        return _FakeResponse(
            {
                "response": json.dumps(
                    {
                        "candidates": [
                            {
                                "title": "Local provider note",
                                "summary": "Local provider extracts structured notes.",
                                "knowledge_type": "technique",
                                "body": "## Core idea\n\nUse local providers.\n",
                                "retrieval_phrases": ["local provider"],
                                "tags": ["local"],
                                "confidence": "high",
                                "utility_score": "high",
                            }
                        ]
                    }
                )
            }
        )

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434/", timeout_seconds=9)
    result = provider.extract_candidates(
        text="# Local provider\n\nUse local providers for sensitive notes.",
        source_path=Path("raw/local.md"),
        max_notes=3,
        model="llama3.2",
    )

    assert result.candidates[0].title == "Local provider note"
    assert requests[0][0] == "http://localhost:11434/api/generate"
    assert requests[0][1]["format"] == "json"
    assert requests[0][1]["stream"] is False
    assert requests[0][2] == 9


def test_local_ollama_provider_maps_transport_error(monkeypatch):
    def fake_urlopen(request, timeout):  # noqa: ANN001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")

    with pytest.raises(ProviderError, match="Start Ollama"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_provider_status_reports_models(monkeypatch):
    def fake_urlopen(request, timeout):  # noqa: ANN001
        assert request.full_url == "http://localhost:11434/api/tags"
        assert timeout == 2
        return _FakeResponse({"models": [{"name": "llama3.2:latest"}]})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    status = local_provider_status(
        {"backend": "ollama", "base_url": "http://localhost:11434"},
        timeout_seconds=2,
    )

    assert status.reachable is True
    assert status.models == ["llama3.2:latest"]
