from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kb_librarian.config import default_config
from kb_librarian.errors import ProviderError
from kb_librarian.provider_retry import classify_provider_failure, retry_policy_from_config
from kb_librarian.providers import (
    AnthropicProvider,
    ClassificationResult,
    ClaudeCliProvider,
    CodexCliProvider,
    CodexProvider,
    LocalOllamaProvider,
    LocalOpenAICompatibleProvider,
    MockProvider,
    _claude_cli_provider_error_message,
    _classify_claude_cli_auth_failure,
    _classify_codex_cli_auth_failure,
    _codex_cli_provider_error_message,
    _codex_cli_status_message,
    _claude_cli_status_message,
    _codex_envelope_text,
    _codex_http_error,
    _envelope_text,
    _first_heading_or_line,
    _first_paragraph,
    _guess_knowledge_type,
    _guess_topic,
    _has_no_durable_note_signal,
    _ollama_model_names,
    _openai_compatible_url,
    _openai_model_names,
    _provider_error_text,
    _provider_seam,
    _retrieval_phrases,
    call_with_provider_policy,
    candidate_to_payload,
    claude_cli_status,
    codex_cli_status,
    local_provider_status,
    operation_route,
    operation_routes,
    parse_json_response,
    provider_from_config,
    validate_candidate_payload,
    validate_classification_payload,
    validate_compaction_payload,
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


def test_operation_routes_use_policy_default_and_fallback(tmp_path):
    config = default_config(tmp_path)
    del config["operations"]["extract"]["provider"]
    config["providers"]["mock"] = {}
    config["providers"]["policy"]["default_provider"] = "local"
    config["providers"]["policy"]["fallback"] = {
        "extract": [
            {"provider": "mock", "model": "mock-extract"},
        ]
    }

    routes = operation_routes(config, "extract")

    assert operation_route(config, "extract").provider == "local"
    assert [(route.provider, route.model) for route in routes] == [
        ("local", "claude-sonnet-4-6"),
        ("mock", "mock-extract"),
    ]


def test_call_with_provider_policy_falls_back_after_transient_failure(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["primary"] = {}
    config["providers"]["fallback"] = {}
    config["operations"]["synthesize"] = {"provider": "primary", "model": "primary-model"}
    config["providers"]["policy"]["fallback"] = {
        "synthesize": [
            {"provider": "fallback", "model": "fallback-model"},
        ]
    }
    policy = config["providers"]["retry"] = {
        "max_attempts": 1,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "jitter_seconds": 0.0,
    }
    attempts = []
    fallbacks = []

    class Provider:
        pass

    def provider_factory(_config, provider_name, **kwargs):  # noqa: ANN001
        attempts.append(provider_name)
        return Provider()

    def call(provider, route):  # noqa: ANN001
        if route.provider == "primary":
            raise ProviderError("service unavailable")
        return f"{route.provider}:{route.model}"

    from kb_librarian.provider_retry import retry_policy_from_config

    result = call_with_provider_policy(
        config,
        "synthesize",
        operation_name="context:synthesize",
        retry_policy=retry_policy_from_config({"providers": {"retry": policy}}),
        call=call,
        provider_factory=provider_factory,
        on_fallback=fallbacks.append,
    )

    assert result == "fallback:fallback-model"
    assert attempts == ["primary", "fallback"]
    assert fallbacks[0].provider == "primary"
    assert fallbacks[0].next_provider == "fallback"


def test_call_with_provider_policy_does_not_fallback_after_non_transient_failure(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["primary"] = {}
    config["providers"]["fallback"] = {}
    config["operations"]["synthesize"] = {"provider": "primary", "model": "primary-model"}
    config["providers"]["policy"]["fallback"] = {"synthesize": ["fallback"]}
    attempts = []

    class Provider:
        pass

    def provider_factory(_config, provider_name, **kwargs):  # noqa: ANN001
        attempts.append(provider_name)
        return Provider()

    def call(provider, route):  # noqa: ANN001
        raise ProviderError("Provider response was not valid JSON")

    from kb_librarian.provider_retry import retry_policy_from_config

    with pytest.raises(ProviderError, match="attempts \\[primary\\(primary-model\\)\\]"):
        call_with_provider_policy(
            config,
            "synthesize",
            operation_name="context:synthesize",
            retry_policy=retry_policy_from_config({"providers": {"retry": {"max_attempts": 1}}}),
            call=call,
            provider_factory=provider_factory,
        )

    assert attempts == ["primary"]


def test_call_with_provider_policy_enforces_cloud_privacy_before_provider_creation(tmp_path):
    config = default_config(tmp_path)
    config["operations"]["synthesize"] = {"provider": "anthropic", "model": "claude-haiku-4-5"}
    config["privacy"]["cloud_llm_allowed"] = False
    attempts = []

    def provider_factory(_config, provider_name, **kwargs):  # noqa: ANN001
        attempts.append(provider_name)
        return MockProvider()

    from kb_librarian.provider_retry import retry_policy_from_config

    with pytest.raises(ProviderError, match="Privacy policy"):
        call_with_provider_policy(
            config,
            "synthesize",
            operation_name="context:synthesize",
            retry_policy=retry_policy_from_config({"providers": {"retry": {"max_attempts": 1}}}),
            call=lambda provider, route: "ok",
            provider_factory=provider_factory,
        )

    assert attempts == []


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


class _FakeRawResponse:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.raw


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


def test_provider_from_config_builds_local_vllm_provider():
    provider = provider_from_config(
        {
            "providers": {
                "local": {
                    "backend": "vllm",
                    "base_url": "http://127.0.0.1:8000",
                    "timeout_seconds": 3,
                }
            }
        },
        "local",
    )

    assert isinstance(provider, LocalOpenAICompatibleProvider)
    assert provider.backend == "vllm"


def test_provider_from_config_builds_local_lm_studio_provider():
    provider = provider_from_config(
        {
            "providers": {
                "local": {
                    "backend": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "timeout_seconds": 3,
                }
            }
        },
        "local",
    )

    assert isinstance(provider, LocalOpenAICompatibleProvider)
    assert provider.backend == "lm_studio"


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


def test_provider_from_config_builds_codex_vendor_cli_provider(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    provider = provider_from_config(
        {
            "providers": {
                "codex": {
                    "backend": "vendor_cli",
                    "credential_source": "vendor_cli",
                    "cli_command": "codex",
                    "base_url": "https://api.openai.com/v1",
                    "timeout_seconds": 3,
                }
            }
        },
        "codex",
        env={},
    )

    assert isinstance(provider, CodexCliProvider)
    assert provider.command_path == "/usr/bin/codex"


def test_provider_from_config_preserves_legacy_api_key_config():
    provider = provider_from_config(
        {
            "providers": {
                "anthropic": {
                    "api_key_env": "ANTHROPIC_API_KEY",
                }
            }
        },
        "anthropic",
        env={"ANTHROPIC_API_KEY": "test-key"},
    )

    assert provider.__class__.__name__ == "AnthropicProvider"


def test_provider_from_config_requires_codex_api_key():
    with pytest.raises(ProviderError, match="Missing Codex credentials for credential_source 'api_key_env'"):
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


def test_provider_from_config_requires_codex_cli_binary(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)

    with pytest.raises(ProviderError, match="Codex CLI command 'codex' was not found"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                        "base_url": "https://api.openai.com/v1",
                    }
                }
            },
            "codex",
            env={},
        )


def test_provider_from_config_does_not_fallback_from_codex_vendor_cli_to_api_key(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)

    with pytest.raises(ProviderError, match="Codex CLI command 'codex' was not found"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                        "base_url": "https://api.openai.com/v1",
                    }
                }
            },
            "codex",
            env={"OPENAI_API_KEY": "test-key"},
        )


def test_provider_from_config_builds_anthropic_vendor_cli_provider(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    provider = provider_from_config(
        {
            "providers": {
                "anthropic": {
                    "backend": "vendor_cli",
                    "credential_source": "vendor_cli",
                    "cli_command": "claude",
                }
            }
        },
        "anthropic",
        env={},
    )

    assert isinstance(provider, ClaudeCliProvider)
    assert provider.command_path == "/usr/bin/claude"


def test_provider_from_config_requires_anthropic_token_env_for_vendor_cli(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    with pytest.raises(ProviderError, match="Missing Claude Code OAuth token"):
        provider_from_config(
            {
                "providers": {
                    "anthropic": {
                        "backend": "vendor_cli",
                        "credential_source": "token_env",
                        "token_env": "CLAUDE_CODE_OAUTH_TOKEN",
                    }
                }
            },
            "anthropic",
            env={},
        )


def test_provider_from_config_does_not_fallback_from_anthropic_token_env_to_api_key(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    with pytest.raises(ProviderError, match="Missing Claude Code OAuth token"):
        provider_from_config(
            {
                "providers": {
                    "anthropic": {
                        "backend": "vendor_cli",
                        "credential_source": "token_env",
                        "token_env": "CLAUDE_CODE_OAUTH_TOKEN",
                    }
                }
            },
            "anthropic",
            env={"ANTHROPIC_API_KEY": "test-key"},
        )


def test_provider_from_config_requires_claude_cli_binary(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)

    with pytest.raises(ProviderError, match="Claude Code CLI command 'claude' was not found"):
        provider_from_config(
            {
                "providers": {
                    "anthropic": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                    }
                }
            },
            "anthropic",
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


def test_codex_cli_provider_maps_structured_response(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        commands.append((command, kwargs))
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "candidates": [
                        {
                            "title": "Codex CLI note",
                            "summary": "Codex CLI delegation returns structured notes.",
                            "knowledge_type": "technique",
                            "body": "## Core idea\n\nUse Codex CLI delegation.\n",
                            "retrieval_phrases": ["codex cli delegation"],
                            "tags": ["codex"],
                            "confidence": "high",
                            "utility_score": "high",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = CodexCliProvider(
        command_path="/usr/bin/codex",
        timeout_seconds=12,
        env={"HOME": "/tmp/test-home"},
    )
    result = provider.extract_candidates(
        text="# Codex CLI\n\nUse account-backed Codex routing.",
        source_path=Path("raw/codex-cli.md"),
        max_notes=2,
        model="gpt-5-codex",
    )

    command, kwargs = commands[0]
    assert result.candidates[0].title == "Codex CLI note"
    assert command[:4] == ["/usr/bin/codex", "exec", "--sandbox", "read-only"]
    assert "--ask-for-approval" in command
    assert "--ephemeral" in command
    assert "--ignore-rules" in command
    assert "--ignore-user-config" in command
    assert "--output-schema" in command
    assert kwargs["cwd"] == "/tmp/opencode"
    assert kwargs["timeout"] == 12
    assert kwargs["env"] == {"HOME": "/tmp/test-home"}
    assert kwargs["input"].startswith("Extract durable KB Librarian candidate notes")


def test_codex_cli_provider_surfaces_login_required_error(monkeypatch):
    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="Please run codex login to continue.",
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = CodexCliProvider(command_path="/usr/bin/codex")

    with pytest.raises(ProviderError, match="requires an active ChatGPT login"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5-codex")


def test_claude_cli_provider_maps_structured_response(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "is_error": False,
                    "result": "Done.",
                    "structured_output": {
                        "candidates": [
                            {
                                "title": "Claude CLI note",
                                "summary": "Claude CLI delegation returns structured notes.",
                                "knowledge_type": "technique",
                                "body": "## Core idea\n\nUse Claude CLI delegation.\n",
                                "retrieval_phrases": ["claude cli delegation"],
                                "tags": ["anthropic"],
                                "confidence": "high",
                                "utility_score": "high",
                            }
                        ]
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(
        command_path="/usr/bin/claude",
        credential_source="vendor_cli",
        token_env=None,
        timeout_seconds=12,
        env={"HOME": "/tmp/test-home"},
    )
    result = provider.extract_candidates(
        text="# Claude CLI\n\nUse account-backed Anthropic routing.",
        source_path=Path("raw/claude.md"),
        max_notes=2,
        model="claude-haiku-4-5",
    )

    command, kwargs = commands[0]
    assert result.candidates[0].title == "Claude CLI note"
    assert command[:4] == ["/usr/bin/claude", "-p", "--model", "claude-haiku-4-5"]
    assert "--tools" in command
    assert "--no-session-persistence" in command
    assert "--disable-slash-commands" in command
    assert "--json-schema" in command
    assert kwargs["cwd"] == "/tmp/opencode"
    assert kwargs["timeout"] == 12
    assert kwargs["env"] == {"HOME": "/tmp/test-home"}


def test_claude_cli_provider_surfaces_login_required_error(monkeypatch):
    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="Please run claude auth login to continue.",
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(
        command_path="/usr/bin/claude",
        credential_source="vendor_cli",
        token_env=None,
    )

    with pytest.raises(ProviderError, match="requires an active login"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


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


def test_local_vllm_provider_maps_structured_response(monkeypatch):
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
                                                "title": "vLLM note",
                                                "summary": "vLLM returns structured output.",
                                                "knowledge_type": "technique",
                                                "body": "## Core idea\n\nUse vLLM local routes.\n",
                                                "retrieval_phrases": ["vllm local"],
                                                "tags": ["vllm"],
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

    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000", timeout_seconds=7)
    result = provider.extract_candidates(
        text="# vLLM\n\nUse local structured routes.",
        source_path=Path("raw/vllm.md"),
        max_notes=2,
        model="NousResearch/Meta-Llama-3-8B-Instruct",
    )

    request, body, timeout = requests[0]
    assert result.candidates[0].title == "vLLM note"
    assert request.full_url == "http://localhost:8000/v1/responses"
    assert body["text"]["format"]["type"] == "json_object"
    assert timeout == 7


def test_local_lm_studio_provider_uses_existing_v1_base_url(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):  # noqa: ANN001
        requests.append((request.full_url, json.loads(request.data.decode("utf-8")), timeout))
        return _FakeResponse({"output_text": "## Directly relevant techniques\n\n- local route\n"})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOpenAICompatibleProvider(
        backend="lm_studio",
        base_url="http://localhost:1234/v1",
        timeout_seconds=5,
    )
    result = provider.synthesize_context(task="x", selected_notes=[], model="qwen2.5-instruct")

    assert "Directly relevant techniques" in result
    assert requests[0][0] == "http://localhost:1234/v1/responses"
    assert "text" not in requests[0][1]
    assert requests[0][2] == 5


def test_local_openai_compatible_provider_maps_transport_error(monkeypatch):
    def fake_urlopen(request, timeout):  # noqa: ANN001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="lm_studio", base_url="http://localhost:1234")

    with pytest.raises(ProviderError, match="LM Studio"):
        provider.synthesize_context(task="x", selected_notes=[], model="qwen2.5-instruct")


def test_local_provider_status_reports_openai_compatible_models(monkeypatch):
    def fake_urlopen(request, timeout):  # noqa: ANN001
        assert request.full_url == "http://localhost:8000/v1/models"
        assert timeout == 2
        return _FakeResponse({"data": [{"id": "NousResearch/Meta-Llama-3-8B-Instruct"}]})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    status = local_provider_status(
        {"backend": "vllm", "base_url": "http://localhost:8000"},
        timeout_seconds=2,
    )

    assert status.reachable is True
    assert status.models == ["NousResearch/Meta-Llama-3-8B-Instruct"]


def test_claude_cli_status_reports_authenticated(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: "/usr/bin/claude")

    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"loggedIn": True, "authMethod": "claude.ai"}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    status = claude_cli_status("claude", env={})

    assert status.available is True
    assert status.authenticated is True
    assert status.code == "authenticated"


def test_claude_cli_status_reports_login_required(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: "/usr/bin/claude")

    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"loggedIn": False}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    status = claude_cli_status("claude", env={})

    assert status.available is True
    assert status.authenticated is False
    assert status.code == "login_required"


def test_claude_cli_status_scrubs_secret_output(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: "/usr/bin/claude")

    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            1,
            stdout='authorization: Bearer secret-token-value',
            stderr='CLAUDE_CODE_OAUTH_TOKEN=super-secret',
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    status = claude_cli_status("claude", env={})

    assert status.code == "auth_check_failed"
    assert "secret-token-value" not in status.message
    assert "super-secret" not in status.message
    assert "[REDACTED]" in status.message


def test_claude_cli_provider_error_scrubs_secret_output(monkeypatch):
    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command,
            1,
            stdout='authorization: Bearer secret-token-value',
            stderr='refresh_token=super-secret',
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(
        command_path="/usr/bin/claude",
        credential_source="vendor_cli",
        token_env=None,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")

    message = str(exc_info.value)
    assert "secret-token-value" not in message
    assert "super-secret" not in message
    assert "[REDACTED]" in message


def test_claude_cli_status_reports_missing_command(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)

    status = claude_cli_status("claude", env={})

    assert status.available is False
    assert status.code == "missing_command"


# --- operation_routes config error guards ---

def test_operation_routes_raises_when_operations_not_mapping():
    with pytest.raises(ProviderError, match="operations must be a mapping"):
        operation_routes({"operations": "not-a-mapping"}, "extract")


def test_operation_routes_raises_when_operation_entry_not_mapping():
    with pytest.raises(ProviderError, match="extract.*must be a mapping"):
        operation_routes({"operations": {"extract": "not-a-mapping"}}, "extract")


def test_operation_routes_raises_when_no_provider_found():
    # No provider key and no policy.default_provider → must raise
    with pytest.raises(ProviderError, match="requires a provider"):
        operation_routes({"operations": {"extract": {"model": "m"}}, "providers": "bad"}, "extract")


def test_operation_routes_raises_when_model_missing():
    with pytest.raises(ProviderError, match="requires a model"):
        operation_routes({"operations": {"extract": {"provider": "mock"}}, "providers": {}}, "extract")


# --- provider_from_config error guards ---

def test_provider_from_config_raises_when_provider_not_configured():
    with pytest.raises(ProviderError, match="'unknown' is not configured"):
        provider_from_config({"providers": {}}, "unknown")


def test_provider_from_config_raises_when_provider_config_not_mapping():
    with pytest.raises(ProviderError, match="must be a mapping"):
        provider_from_config({"providers": {"mock": "not-a-dict"}}, "mock")


def test_provider_from_config_raises_for_anthropic_missing_api_key():
    with pytest.raises(ProviderError, match="Missing Anthropic credentials"):
        provider_from_config(
            {"providers": {"anthropic": {"backend": "direct_http", "api_key_env": "MISSING_KEY"}}},
            "anthropic",
            env={},
        )


def test_provider_from_config_builds_anthropic_token_env_provider_when_token_set(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    provider = provider_from_config(
        {
            "providers": {
                "anthropic": {
                    "backend": "vendor_cli",
                    "credential_source": "token_env",
                    "token_env": "MY_TOKEN",
                }
            }
        },
        "anthropic",
        env={"MY_TOKEN": "secret-token"},
    )
    assert isinstance(provider, ClaudeCliProvider)


def test_provider_from_config_raises_for_unsupported_provider():
    with pytest.raises(ProviderError, match="Unsupported provider 'foobar'"):
        provider_from_config({"providers": {"foobar": {}}}, "foobar")


# --- codex config validation ---

def test_provider_from_config_codex_requires_base_url():
    with pytest.raises(ProviderError, match="base_url is required"):
        provider_from_config({"providers": {"codex": {"api_key_env": "CODEX_KEY"}}}, "codex")


def test_provider_from_config_codex_organization_must_be_string():
    with pytest.raises(ProviderError, match="organization must be a string"):
        provider_from_config(
            {"providers": {"codex": {"api_key_env": "CODEX_KEY", "base_url": "https://api.openai.com/v1", "organization": 123}}},
            "codex",
        )


def test_provider_from_config_codex_empty_organization_rejected():
    with pytest.raises(ProviderError, match="organization must be a string"):
        provider_from_config(
            {"providers": {"codex": {"api_key_env": "CODEX_KEY", "base_url": "https://api.openai.com/v1", "organization": "  "}}},
            "codex",
        )


def test_provider_from_config_codex_project_must_be_string():
    with pytest.raises(ProviderError, match="project must be a string"):
        provider_from_config(
            {"providers": {"codex": {"api_key_env": "CODEX_KEY", "base_url": "https://api.openai.com/v1", "project": 99}}},
            "codex",
        )


def test_provider_from_config_codex_empty_project_rejected():
    with pytest.raises(ProviderError, match="project must be a string"):
        provider_from_config(
            {"providers": {"codex": {"api_key_env": "CODEX_KEY", "base_url": "https://api.openai.com/v1", "project": " "}}},
            "codex",
        )


def test_provider_from_config_codex_vendor_cli_wrong_base_url(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")
    with pytest.raises(ProviderError, match="base_url must remain"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                        "base_url": "https://other.example/v1",
                    }
                }
            },
            "codex",
            env={},
        )


def test_provider_from_config_codex_vendor_cli_organization_rejected(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")
    with pytest.raises(ProviderError, match="organization is supported only with backend 'direct_http'"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                        "base_url": "https://api.openai.com/v1",
                        "organization": "my-org",
                    }
                }
            },
            "codex",
            env={},
        )


def test_provider_from_config_codex_vendor_cli_project_rejected(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")
    with pytest.raises(ProviderError, match="project is supported only with backend 'direct_http'"):
        provider_from_config(
            {
                "providers": {
                    "codex": {
                        "backend": "vendor_cli",
                        "credential_source": "vendor_cli",
                        "base_url": "https://api.openai.com/v1",
                        "project": "my-project",
                    }
                }
            },
            "codex",
            env={},
        )


def test_provider_from_config_local_requires_base_url():
    with pytest.raises(ProviderError, match="base_url is required"):
        provider_from_config({"providers": {"local": {}}}, "local")


# --- _fallback_routes edge cases ---

def test_fallback_routes_entry_none_returns_empty():
    config = default_config(Path("/tmp"))
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "m"}
    config["providers"]["policy"]["fallback"] = {"extract": None}
    routes = operation_routes(config, "extract")
    assert len(routes) == 1  # only primary, None fallback treated as empty


def test_fallback_routes_entry_not_list_raises():
    config = default_config(Path("/tmp"))
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "m"}
    config["providers"]["policy"]["fallback"] = {"extract": "bad-string"}
    with pytest.raises(ProviderError, match="must be a list"):
        operation_routes(config, "extract")


def test_fallback_routes_entry_invalid_type_raises():
    config = default_config(Path("/tmp"))
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "m"}
    config["providers"]["policy"]["fallback"] = {"extract": [42]}  # int entry → provider=""
    with pytest.raises(ProviderError, match="must be a provider string"):
        operation_routes(config, "extract")


# --- call_with_provider_policy branches ---

def test_call_with_provider_policy_fallback_without_callback_works(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["primary"] = {}
    config["providers"]["secondary"] = {}
    config["operations"]["synth"] = {"provider": "primary", "model": "pm"}
    config["providers"]["policy"]["fallback"] = {
        "synth": [{"provider": "secondary", "model": "sm"}]
    }
    policy = {"max_attempts": 1, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}

    class FakeProvider:
        pass

    def factory(_c, name, **kw):
        return FakeProvider()

    def call(provider, route):
        if route.provider == "primary":
            # Use a transient error so the policy falls back to secondary
            raise ProviderError("HTTP 429 Too Many Requests rate limit")
        return f"ok:{route.provider}"

    result = call_with_provider_policy(
        config, "synth",
        operation_name="synth",
        retry_policy=retry_policy_from_config({"providers": {"retry": policy}}),
        call=call,
        provider_factory=factory,
        on_fallback=None,
    )
    assert result == "ok:secondary"


# --- local_provider_status edge cases ---

def test_local_provider_status_bad_config_returns_error():
    status = local_provider_status({"backend": "ollama"})  # missing base_url
    assert status.reachable is False
    assert "base_url" in status.message


def test_local_provider_status_unsupported_backend(monkeypatch):
    import kb_librarian.providers as providers_module
    from kb_librarian.provider_seams import ProviderSeam
    fake_seam = ProviderSeam(provider_name="local", backend="custom_backend", credential_source=None)
    monkeypatch.setattr(providers_module, "resolve_provider_seam", lambda *a, **kw: fake_seam)
    status = local_provider_status({"backend": "custom_backend", "base_url": "http://localhost:9999"})
    assert status.reachable is False
    assert "Unsupported local backend" in status.message


def test_local_provider_status_ollama_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO(b""))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "ollama", "base_url": "http://localhost:11434"})
    assert status.reachable is False
    assert "503" in status.message


def test_local_provider_status_ollama_timeout(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "ollama", "base_url": "http://localhost:11434"})
    assert status.reachable is False
    assert "timed out" in status.message


def test_local_provider_status_ollama_bad_json(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "ollama", "base_url": "http://localhost:11434"})
    assert status.reachable is False
    assert "not valid JSON" in status.message


def test_local_provider_status_openai_compatible_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "vllm", "base_url": "http://localhost:8000"})
    assert status.reachable is False
    assert "404" in status.message


def test_local_provider_status_openai_compatible_timeout(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "lm_studio", "base_url": "http://localhost:1234"})
    assert status.reachable is False
    assert "timed out" in status.message


def test_local_provider_status_openai_compatible_bad_json(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "vllm", "base_url": "http://localhost:8000"})
    assert status.reachable is False
    assert "not valid JSON" in status.message


# --- claude_cli_status error paths ---

def test_claude_cli_status_reports_timeout(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10))

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = claude_cli_status("claude", env={})
    assert status.code == "status_timeout"
    assert status.available is True


def test_claude_cli_status_reports_oserror(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        raise OSError("cannot exec")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = claude_cli_status("claude", env={})
    assert status.code == "command_error"
    assert status.available is False


def test_claude_cli_status_reports_bad_json(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not json", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = claude_cli_status("claude", env={})
    assert status.code == "unsupported_cli"


def test_claude_cli_status_reports_missing_loggedin_field(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout='{"status": "ok"}', stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = claude_cli_status("claude", env={})
    assert status.code == "unsupported_cli"


# --- codex_cli_status ---

def test_codex_cli_status_reports_missing_command(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)
    status = codex_cli_status("codex", env={})
    assert status.available is False
    assert status.code == "missing_command"


def test_codex_cli_status_reports_timeout(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 10))

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "status_timeout"
    assert status.available is True


def test_codex_cli_status_reports_oserror(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        raise OSError("cannot exec")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "command_error"
    assert status.available is False


def test_codex_cli_status_reports_authenticated(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="logged in as user@example.com", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "authenticated"
    assert status.available is True


def test_codex_cli_status_reports_login_required(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="please run codex login to continue", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "login_required"


def test_codex_cli_status_reports_auth_check_failed(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="some unrecognized error", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "auth_check_failed"


def test_codex_cli_status_reports_unsupported_output(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: f"/usr/bin/{command}")

    def fake_run(command, **kwargs):
        # Return code 0 but not "logged in"
        return subprocess.CompletedProcess(command, 0, stdout="auth status: unknown", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    status = codex_cli_status("codex", env={})
    assert status.code == "unsupported_cli"


# --- _provider_seam wraps non-ProviderError ---

def test_provider_seam_wraps_non_provider_error(monkeypatch):
    import kb_librarian.providers as providers_module

    def fail(*args, **kwargs):
        raise ValueError("internal seam error")

    monkeypatch.setattr(providers_module, "resolve_provider_seam", fail)
    with pytest.raises(ProviderError, match="internal seam error"):
        _provider_seam("anthropic", {"backend": "direct_http"})


# --- MockProvider edge cases ---

def test_mock_provider_extract_returns_empty_for_short_text():
    provider = MockProvider()
    result = provider.extract_candidates(
        text="Short text",
        source_path=Path("raw/x.md"),
        max_notes=5,
        model="mock",
    )
    assert result.candidates == []


def test_mock_provider_extract_returns_empty_for_no_ingest_signal():
    provider = MockProvider()
    result = provider.extract_candidates(
        text="# Title\n\ndo not ingest this document please.",
        source_path=Path("raw/x.md"),
        max_notes=5,
        model="mock",
    )
    assert result.candidates == []


def test_mock_provider_synthesize_context_with_empty_notes():
    provider = MockProvider()
    result = provider.synthesize_context(
        task="review",
        selected_notes=[],
    )
    assert "No selected notes" in result
    assert "No applicable heuristics" in result


def test_mock_provider_synthesize_context_with_non_mapping_note():
    provider = MockProvider()
    result = provider.synthesize_context(
        task="review",
        selected_notes=["not-a-mapping"],
    )
    # Non-mapping entries are skipped but produce no crash
    assert "## Directly relevant techniques" in result


def test_mock_provider_synthesize_context_with_note_missing_summary():
    provider = MockProvider()
    result = provider.synthesize_context(
        task="review",
        selected_notes=[{"note_id": "2026-01-01-test", "title": "Test", "summary": ""}],
    )
    assert "## Directly relevant techniques" in result


def test_mock_provider_synthesize_context_with_trust_flags():
    provider = MockProvider()
    result = provider.synthesize_context(
        task="review",
        selected_notes=[{
            "note_id": "2026-01-01-test",
            "title": "Test",
            "summary": "A test note.",
            "trust_flags": ["confidence:low"],
        }],
    )
    assert "trust_flags" in result or "caution" in result


def test_mock_provider_synthesize_context_with_non_list_selected_notes():
    provider = MockProvider()
    result = provider.synthesize_context(task="review", selected_notes="not-a-list")
    assert "No selected notes" in result


def test_mock_provider_synthesize_exploration_with_empty_notes():
    provider = MockProvider()
    result = provider.synthesize_exploration(problem="what's the best approach?", selected_notes=[])
    assert "No directly relevant concepts" in result
    assert "## Open questions" in result


def test_mock_provider_synthesize_exploration_with_note_no_summary():
    # Covers branch where note_id exists but summary is empty.
    provider = MockProvider()
    result = provider.synthesize_exploration(
        problem="x",
        selected_notes=[{"note_id": "nid", "title": "Title", "summary": ""}],
    )
    assert "## Directly relevant concepts" in result


def test_mock_provider_synthesize_exploration_with_non_list_notes():
    provider = MockProvider()
    result = provider.synthesize_exploration(problem="x", selected_notes=42)
    assert "No directly relevant concepts" in result


def test_mock_provider_synthesize_compaction_with_no_source_notes():
    provider = MockProvider()
    result = provider.synthesize_compaction(source_notes=[], model="mock")
    assert "unknown-note" in result.get("source_note_ids", [])
    assert "No source notes were provided" in result.get("body", "")


def test_mock_provider_synthesize_compaction_with_non_list_source_notes():
    provider = MockProvider()
    result = provider.synthesize_compaction(source_notes="bad", model="mock")
    assert "unknown-note" in result.get("source_note_ids", [])


def test_mock_provider_synthesize_compaction_note_without_id():
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=[{"note_id": "", "frontmatter": {"title": "X"}}],
        model="mock",
    )
    assert "unknown-note" in result.get("source_note_ids", [])


def test_mock_provider_synthesize_compaction_note_with_title_no_summary():
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=[{
            "note_id": "2026-01-01-x",
            "frontmatter": {"title": "Test Title"},
        }],
        model="mock",
    )
    # Has note_id, no summary → uses title in body
    assert "2026-01-01-x" in result.get("source_note_ids", [])


def test_mock_provider_synthesize_compaction_first_note_not_mapping():
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=["not-a-mapping"],
        model="mock",
    )
    # First is not a mapping → first_fm = {}
    assert "unknown-note" in result.get("source_note_ids", [])


def test_mock_provider_synthesize_compaction_fm_not_mapping():
    # frontmatter inside note is not a mapping
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=[{"note_id": "note-1", "frontmatter": "not-mapping"}],
        model="mock",
    )
    assert "note-1" in result.get("source_note_ids", [])


# --- LocalOllamaProvider uncovered methods ---

def _ollama_classification_response() -> dict:
    return {"response": json.dumps({
        "topic": "agent-systems",
        "knowledge_type": "technique",
        "confidence": "high",
        "reason": "clear fit",
    })}


def _ollama_integration_response() -> dict:
    return {"response": json.dumps({
        "verdict": "unrelated",
        "target_note_ids": [],
        "rationale": "different topic",
    })}


def test_local_ollama_provider_classify_candidate(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_ollama_classification_response())

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context for agents.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nUse task-shaped context for agents.",
        source_path=Path("raw/x.md"),
        model="llama3.2",
    )
    assert result.topic == "agent-systems"


def test_local_ollama_provider_integration_verdict(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_ollama_integration_response())

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context for agents.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    classification = ClassificationResult(topic="agent-systems", knowledge_type="technique", confidence="high", reason="ok")
    result = provider.integration_verdict(
        candidate=candidate,
        classification=classification,
        matches=[],
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        model="llama3.2",
    )
    assert result.verdict == "unrelated"


def test_local_ollama_provider_synthesize_exploration(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "## Exploration result\n\nFound relevant patterns."})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    result = provider.synthesize_exploration(problem="how to reduce token usage?", selected_notes=[], model="llama3.2")
    assert "Exploration result" in result


def test_local_ollama_provider_synthesize_compaction(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "Compact Note", "summary": "Compacted.", "topic": "general",
                        "knowledge_type": "technique", "confidence": "medium",
                        "retrieval_phrases": ["compact"], "tags": ["general"]},
        "body": "Compact body.\n",
        "source_note_ids": ["note-1"],
        "dispositions": [{"note_id": "note-1", "recommendation": "supersede", "rationale": "merged"}],
        "diff_summary": "Merged two notes.",
    }

    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": json.dumps(compaction_payload)})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    result = provider.synthesize_compaction(source_notes=[], cluster_id="cluster-1", model="llama3.2")
    assert isinstance(result, dict)


def test_local_ollama_provider_synthesize_context_non_list_notes(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "## Context\n\nGenerated context output."})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    result = provider.synthesize_context(task="review", selected_notes="not-a-list", model="llama3.2")
    assert result


def test_local_ollama_provider_http_error_on_generate(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO(b"oops"))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_timeout_on_generate(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_bad_json_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "not-json-after-parsing-as-text"})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="valid JSON"):
        provider.classify_candidate(
            candidate=MockProvider().extract_candidates(
                text="# Title\n\nAgent context.",
                source_path=Path("raw/x.md"),
                max_notes=1,
                model="mock",
            ).candidates[0],
            text="# Title\n\nAgent context.",
            source_path=Path("raw/x.md"),
            model="llama3.2",
        )


def test_local_ollama_provider_non_mapping_json_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": '"just-a-string"'})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError):
        provider.classify_candidate(
            candidate=MockProvider().extract_candidates(
                text="# Title\n\nAgent context.",
                source_path=Path("raw/x.md"),
                max_notes=1,
                model="mock",
            ).candidates[0],
            text="# Title\n\nAgent context.",
            source_path=Path("raw/x.md"),
            model="llama3.2",
        )


def test_local_ollama_provider_error_key_in_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "text", "error": "model not found"})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="model not found"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_empty_response_content(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "   "})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="did not contain text content"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_empty_model_raises(monkeypatch):
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="requires a model"):
        provider.synthesize_context(task="x", selected_notes=[], model="")


# --- AnthropicProvider methods ---

def _anthropic_text_response(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_anthropic_provider_classify_candidate(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response(json.dumps({
            "topic": "agent-systems",
            "knowledge_type": "technique",
            "confidence": "high",
            "reason": "clear",
        })))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = AnthropicProvider(api_key="test-key")
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        model="claude-haiku-4-5",
    )
    assert result.topic == "agent-systems"


def test_anthropic_provider_integration_verdict(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response(json.dumps({
            "verdict": "unrelated",
            "target_note_ids": [],
            "rationale": "different",
        })))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = AnthropicProvider(api_key="test-key")
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    classification = ClassificationResult(topic="agent-systems", knowledge_type="technique", confidence="high", reason="ok")
    result = provider.integration_verdict(
        candidate=candidate,
        classification=classification,
        matches=[],
        text="# Agent context\n\nContext.",
        source_path=Path("raw/x.md"),
        model="claude-haiku-4-5",
    )
    assert result.verdict == "unrelated"


def test_anthropic_provider_synthesize_exploration(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response("## Exploration\n\nResults."))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_exploration(problem="efficiency", selected_notes=[], model="claude-haiku-4-5")
    assert "Exploration" in result


def test_anthropic_provider_synthesize_compaction(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "Compact", "summary": "Sum.", "topic": "general",
                        "knowledge_type": "technique", "confidence": "medium",
                        "retrieval_phrases": ["compact"], "tags": ["general"]},
        "body": "Body.\n",
        "source_note_ids": ["note-1"],
        "dispositions": [{"note_id": "note-1", "recommendation": "supersede", "rationale": "merged"}],
        "diff_summary": "Merged.",
    }

    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response(json.dumps(compaction_payload)))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_compaction(source_notes=[], cluster_id="c1", model="claude-haiku-4-5")
    assert isinstance(result, dict)


def test_anthropic_provider_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 429, "Too Many Requests", {}, io.BytesIO(b'{"error": "rate limit"}')
        )

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_url_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="connection"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_timeout(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_bad_json_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse("not-valid-json-string")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_synthesize_non_list_notes(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response("Context output."))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_context(task="x", selected_notes="not-a-list", model="claude-haiku-4-5")
    assert result


def test_anthropic_provider_synthesize_exploration_non_list_notes(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response("Exploration output."))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_exploration(problem="x", selected_notes=None, model="claude-haiku-4-5")
    assert result


def test_anthropic_provider_synthesize_compaction_non_list_notes(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "C", "summary": "S.", "topic": "g",
                        "knowledge_type": "technique", "confidence": "medium",
                        "retrieval_phrases": ["c"], "tags": ["g"]},
        "body": "B.\n",
        "source_note_ids": ["n1"],
        "dispositions": [{"note_id": "n1", "recommendation": "supersede", "rationale": "ok"}],
        "diff_summary": "D.",
    }

    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response(json.dumps(compaction_payload)))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_compaction(source_notes=None, cluster_id="c", model="claude-haiku-4-5")
    assert isinstance(result, dict)


# --- ClaudeCliProvider uncovered methods ---

def _claude_cli_json_response(payload: dict) -> dict:
    return {
        "type": "result",
        "is_error": False,
        "result": "",
        "structured_output": payload,
    }


def _claude_cli_text_response(text: str) -> dict:
    return {
        "type": "result",
        "is_error": False,
        "result": text,
    }


def test_claude_cli_provider_classify_candidate(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_json_response({
                "topic": "agent-systems",
                "knowledge_type": "technique",
                "confidence": "high",
                "reason": "clear",
            })),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        model="claude-haiku-4-5",
    )
    assert result.topic == "agent-systems"


def test_claude_cli_provider_integration_verdict(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_json_response({
                "verdict": "unrelated",
                "target_note_ids": [],
                "rationale": "different",
            })),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    classification = ClassificationResult(topic="agent-systems", knowledge_type="technique", confidence="high", reason="ok")
    result = provider.integration_verdict(
        candidate=candidate,
        classification=classification,
        matches=[],
        text="# Agent context\n\nContext.",
        source_path=Path("raw/x.md"),
        model="claude-haiku-4-5",
    )
    assert result.verdict == "unrelated"


def test_claude_cli_provider_synthesize_exploration(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_text_response("## Exploration\n\nResults.")),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.synthesize_exploration(problem="efficiency", selected_notes=[], model="claude-haiku-4-5")
    assert "Exploration" in result


def test_claude_cli_provider_synthesize_compaction(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "Compact", "summary": "Sum.", "topic": "general",
                        "knowledge_type": "technique", "confidence": "medium",
                        "retrieval_phrases": ["compact"], "tags": ["general"]},
        "body": "Body.\n",
        "source_note_ids": ["note-1"],
        "dispositions": [{"note_id": "note-1", "recommendation": "supersede", "rationale": "merged"}],
        "diff_summary": "Merged.",
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_json_response(compaction_payload)),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.synthesize_compaction(source_notes=[], cluster_id="c1", model="claude-haiku-4-5")
    assert isinstance(result, dict)


def test_claude_cli_provider_synthesize_non_list_notes(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_text_response("Context output.")),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.synthesize_context(task="x", selected_notes="not-a-list", model="claude-haiku-4-5")
    assert result


def test_claude_cli_provider_json_fallback_to_result_field(monkeypatch):
    """When structured_output is None, falls back to parse_json_response(result)."""
    payload = {"topic": "general", "knowledge_type": "technique", "confidence": "high", "reason": "ok"}

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nContext.",
        source_path=Path("raw/x.md"),
        model="claude-haiku-4-5",
    )
    assert result.knowledge_type == "technique"


def test_claude_cli_provider_missing_structured_output_raises(monkeypatch):
    """When neither structured_output nor valid result → raises."""
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "result", "is_error": False, "result": ""}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="did not include structured JSON output"):
        provider.classify_candidate(
            candidate=candidate,
            text="# Agent context\n\nContext.",
            source_path=Path("raw/x.md"),
            model="claude-haiku-4-5",
        )


def test_claude_cli_provider_empty_text_result_raises(monkeypatch):
    """When synthesize gets empty result field → raises."""
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "result", "is_error": False, "result": ""}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="did not contain text content"):
        provider.synthesize_exploration(problem="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_timeout_raises(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 120))

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_oserror_raises(monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="failed to start"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_json_decode_error_raises(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not json at all", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="not valid JSON"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_non_mapping_response_raises(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout='"just-a-string"', stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="not a JSON object"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_is_error_true_raises(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "result", "is_error": True, "result": "please run claude auth login"}),
            stderr="",
        )

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="requires an active login"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_claude_cli_provider_no_model_raises():
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="requires a model"):
        provider.synthesize_context(task="x", selected_notes=[], model="")


# --- validate_*_payload error paths ---

def test_validate_classification_payload_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="Classification response must be a JSON object"):
        validate_classification_payload([1, 2, 3])


def test_validate_integration_payload_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="Integration response must be a JSON object"):
        validate_integration_payload("not-a-mapping")


def test_validate_integration_payload_raises_for_unrelated_with_targets():
    with pytest.raises(ProviderError, match="must not include target_note_ids"):
        validate_integration_payload({
            "verdict": "unrelated",
            "target_note_ids": ["note-1"],
            "rationale": "bad",
        })


def test_validate_compaction_payload_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="Compaction response must be a JSON object"):
        validate_compaction_payload(42)


def test_validate_compaction_payload_raises_when_frontmatter_not_mapping():
    with pytest.raises(ProviderError, match="frontmatter must be an object"):
        validate_compaction_payload({"frontmatter": "not-a-mapping", "body": "x", "source_note_ids": [], "dispositions": [], "diff_summary": "d"})


def test_validate_compaction_payload_raises_for_empty_dispositions():
    with pytest.raises(ProviderError, match="dispositions must be a non-empty list"):
        validate_compaction_payload({
            "frontmatter": {"title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
                            "confidence": "high", "retrieval_phrases": [], "tags": []},
            "body": "B",
            "source_note_ids": [],
            "dispositions": [],
            "diff_summary": "D",
        })


def test_validate_compaction_payload_raises_for_non_mapping_disposition():
    with pytest.raises(ProviderError, match="disposition 0 must be an object"):
        validate_compaction_payload({
            "frontmatter": {"title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
                            "confidence": "high", "retrieval_phrases": [], "tags": []},
            "body": "B",
            "source_note_ids": ["n1"],
            "dispositions": ["not-a-mapping"],
            "diff_summary": "D",
        })


def test_validate_compaction_payload_raises_when_disposition_missing_source_id():
    with pytest.raises(ProviderError, match="missing"):
        validate_compaction_payload({
            "frontmatter": {"title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
                            "confidence": "high", "retrieval_phrases": [], "tags": []},
            "body": "B",
            "source_note_ids": ["n1", "n2"],
            "dispositions": [{"note_id": "n1", "recommendation": "supersede", "rationale": "ok"}],
            "diff_summary": "D",
        })


def test_validate_compaction_payload_includes_optional_frontmatter_fields():
    result = validate_compaction_payload({
        "frontmatter": {
            "title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
            "confidence": "high", "retrieval_phrases": [], "tags": [],
            "basis": ["source-note"],
            "agent_use": ["coding agent"],
        },
        "body": "B",
        "source_note_ids": ["n1"],
        "dispositions": [{"note_id": "n1", "recommendation": "supersede", "rationale": "ok"}],
        "diff_summary": "D",
    })
    assert "basis" in result.frontmatter
    assert "agent_use" in result.frontmatter


def test_validate_candidate_payload_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="Candidate must be a JSON object"):
        validate_candidate_payload(42)


# --- Helper function error paths ---

def test_required_string_raises_for_missing_field():
    with pytest.raises(ProviderError, match="topic.*must be a non-empty string"):
        validate_classification_payload({"topic": "", "knowledge_type": "technique", "confidence": "high"})


def test_optional_string_raises_for_non_string_value():
    with pytest.raises(ProviderError, match="must be a string"):
        validate_classification_payload({"topic": "x", "knowledge_type": "technique", "confidence": "high", "reason": 42})


def test_required_enum_raises_for_invalid_value():
    with pytest.raises(ProviderError, match="must be one of"):
        validate_classification_payload({"topic": "x", "knowledge_type": "INVALID-TYPE", "confidence": "high"})


def test_required_string_list_raises_for_non_list():
    with pytest.raises(ProviderError, match="must be a list of strings"):
        validate_extraction_payload({"candidates": [
            {"title": "T", "summary": "S", "body": "B",
             "retrieval_phrases": "not-a-list", "tags": [], "confidence": "high", "utility_score": "high"}
        ]})


def test_optional_string_list_raises_for_non_list():
    with pytest.raises(ProviderError, match="must be a list of strings"):
        validate_candidate_payload({
            "title": "T", "summary": "S", "body": "B",
            "retrieval_phrases": [], "tags": [], "confidence": "high", "utility_score": "high",
            "agent_use": "not-a-list",
        })


def test_clean_string_list_raises_for_non_string_item():
    with pytest.raises(ProviderError, match="must be a string"):
        validate_extraction_payload({"candidates": [
            {"title": "T", "summary": "S", "body": "B",
             "retrieval_phrases": [42], "tags": [], "confidence": "high", "utility_score": "high"}
        ]})


# --- Auth failure classification messages ---

def test_classify_claude_cli_expired():
    assert _classify_claude_cli_auth_failure("your token has expired please reauth") == "expired"


def test_classify_claude_cli_login_required():
    assert _classify_claude_cli_auth_failure("not logged in please run claude auth login") == "login_required"


def test_classify_claude_cli_unsupported():
    assert _classify_claude_cli_auth_failure("unknown option --output-format") == "unsupported_cli"


def test_classify_claude_cli_default():
    assert _classify_claude_cli_auth_failure("some unrecognized error") == "auth_check_failed"


def test_claude_cli_status_message_expired():
    msg = _claude_cli_status_message("expired", "token expired error")
    assert "expired" in msg.lower()


def test_claude_cli_status_message_login_required():
    msg = _claude_cli_status_message("login_required", "")
    assert "login" in msg.lower()


def test_claude_cli_status_message_unsupported():
    msg = _claude_cli_status_message("unsupported_cli", "")
    assert "Update Claude Code" in msg


def test_claude_cli_status_message_default():
    msg = _claude_cli_status_message("auth_check_failed", "")
    assert "auth status" in msg


def test_claude_cli_provider_error_message_expired_token_env():
    msg = _claude_cli_provider_error_message("token has expired", "token_env", "MY_TOKEN")
    assert "MY_TOKEN" in msg or "expired" in msg.lower()


def test_claude_cli_provider_error_message_expired_vendor_cli():
    msg = _claude_cli_provider_error_message("your session expired reauth", "vendor_cli", None)
    assert "expired" in msg.lower()


def test_claude_cli_provider_error_message_login_required_token_env():
    msg = _claude_cli_provider_error_message("not logged in", "token_env", "MY_TOKEN")
    assert "OAuth token" in msg or "MY_TOKEN" in msg


def test_claude_cli_provider_error_message_unsupported():
    msg = _claude_cli_provider_error_message("unknown option --output-format", "vendor_cli", None)
    assert "Update Claude Code" in msg


def test_claude_cli_provider_error_message_with_output():
    msg = _claude_cli_provider_error_message("some error", "vendor_cli", None)
    assert "some error" in msg


def test_classify_codex_cli_expired():
    assert _classify_codex_cli_auth_failure("your session token has expired") == "expired"


def test_classify_codex_cli_login_required():
    assert _classify_codex_cli_auth_failure("please run codex login to continue") == "login_required"


def test_classify_codex_cli_unsupported():
    assert _classify_codex_cli_auth_failure("unknown command --output-last-message") == "unsupported_cli"


def test_classify_codex_cli_default():
    assert _classify_codex_cli_auth_failure("unknown error") == "auth_check_failed"


def test_codex_cli_status_message_expired():
    msg = _codex_cli_status_message("expired", "token expired")
    assert "expired" in msg.lower()


def test_codex_cli_status_message_login_required():
    msg = _codex_cli_status_message("login_required", "")
    assert "login" in msg.lower()


def test_codex_cli_status_message_unsupported():
    msg = _codex_cli_status_message("unsupported_cli", "")
    assert "Update Codex" in msg


def test_codex_cli_status_message_default():
    msg = _codex_cli_status_message("auth_check_failed", "")
    assert "login status" in msg


def test_codex_cli_provider_error_message_expired():
    msg = _codex_cli_provider_error_message("token expired please login again")
    assert "expired" in msg.lower()


def test_codex_cli_provider_error_message_login_required():
    msg = _codex_cli_provider_error_message("please run codex login to continue")
    assert "ChatGPT login" in msg or "login" in msg.lower()


def test_codex_cli_provider_error_message_unsupported():
    msg = _codex_cli_provider_error_message("unknown command --output-last-message")
    assert "Update Codex" in msg


def test_codex_cli_provider_error_message_default():
    msg = _codex_cli_provider_error_message("some unknown error")
    assert "delegation failed" in msg


def test_codex_cli_provider_error_message_with_output():
    msg = _codex_cli_provider_error_message("some output here")
    assert "some output here" in msg


# --- _envelope_text and _codex_envelope_text ---

def test_envelope_text_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="not a JSON object"):
        _envelope_text("not-a-mapping")


def test_envelope_text_raises_when_no_text_content():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _envelope_text({"content": []})


def test_codex_envelope_text_raises_for_non_mapping():
    with pytest.raises(ProviderError, match="not a JSON object"):
        _codex_envelope_text("not-a-mapping")


def test_codex_envelope_text_uses_output_text_shortcut():
    result = _codex_envelope_text({"output_text": "direct result"})
    assert result == "direct result"


def test_codex_envelope_text_handles_refusal():
    with pytest.raises(ProviderError, match="refused request"):
        _codex_envelope_text({
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "I cannot do that."}]}]
        })


def test_codex_envelope_text_handles_incomplete_details():
    with pytest.raises(ProviderError, match="incomplete"):
        _codex_envelope_text({
            "output": [],
            "incomplete_details": {"reason": "max_tokens"},
        })


def test_codex_envelope_text_raises_when_no_content():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": []})


def test_codex_envelope_text_skips_non_mapping_output_items():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": ["not-a-mapping"]})


def test_codex_envelope_text_skips_non_list_content():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": [{"type": "message", "content": "not-a-list"}]})


def test_codex_envelope_text_skips_non_mapping_content_items():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": [{"type": "message", "content": ["not-a-mapping"]}]})


def test_codex_envelope_text_skips_empty_output_text():
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": [{"type": "message", "content": [{"type": "output_text", "text": ""}]}]})


# --- _provider_error_text ---

def test_provider_error_text_with_string_json():
    result = _provider_error_text('{"message": "rate limit", "code": "rate_limited"}')
    assert "rate_limited" in result and "rate limit" in result


def test_provider_error_text_with_bad_json_string():
    result = _provider_error_text("plain error message")
    assert result == "plain error message"


def test_provider_error_text_with_nested_error_mapping():
    result = _provider_error_text({"error": {"message": "nested error", "code": "err_code"}})
    assert "nested error" in result


def test_provider_error_text_with_mapping_no_message():
    result = _provider_error_text({"code": "some_code"})
    assert "some_code" in result


def test_provider_error_text_with_non_string():
    result = _provider_error_text(42)
    assert result == "42"


# --- CodexCliProvider uncovered methods ---

def test_codex_cli_provider_classify_candidate(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps({
            "topic": "agent-systems",
            "knowledge_type": "technique",
            "confidence": "high",
            "reason": "clear",
        }), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = CodexCliProvider(command_path="/usr/bin/codex", timeout_seconds=12)
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        model="gpt-5.1",
    )
    assert result.topic == "agent-systems"


def test_codex_cli_provider_synthesize_exploration(monkeypatch):
    def fake_run(command, **kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("## Exploration\n\nResults.", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)

    provider = CodexCliProvider(command_path="/usr/bin/codex")
    result = provider.synthesize_exploration(problem="efficiency", selected_notes=[], model="gpt-5.1")
    assert "Exploration" in result


def test_codex_cli_provider_timeout_raises(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = CodexCliProvider(command_path="/usr/bin/codex")
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_cli_provider_oserror_raises(monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = CodexCliProvider(command_path="/usr/bin/codex")
    with pytest.raises(ProviderError, match="failed to start"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_cli_provider_empty_output_raises(monkeypatch):
    def fake_run(command, **kwargs):
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("   ", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = CodexCliProvider(command_path="/usr/bin/codex")
    with pytest.raises(ProviderError, match="did not contain a final message"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_cli_provider_missing_output_file_raises(monkeypatch):
    def fake_run(command, **kwargs):
        # Don't write the output file → OSError on read
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = CodexCliProvider(command_path="/usr/bin/codex")
    with pytest.raises(ProviderError, match="could not be read"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_cli_provider_no_model_raises():
    provider = CodexCliProvider(command_path="/usr/bin/codex")
    with pytest.raises(ProviderError, match="requires a model"):
        provider.synthesize_context(task="x", selected_notes=[], model="")


# --- CodexProvider uncovered methods ---

def test_codex_provider_classify_candidate(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "topic": "agent-systems",
                "knowledge_type": "technique",
                "confidence": "high",
                "reason": "clear",
            })}]}]
        })

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    candidate = MockProvider().extract_candidates(
        text="# Agent context\n\nUse task-shaped context.",
        source_path=Path("raw/x.md"),
        max_notes=1,
        model="mock",
    ).candidates[0]
    result = provider.classify_candidate(
        candidate=candidate,
        text="# Agent context\n\nContext.",
        source_path=Path("raw/x.md"),
        model="gpt-5.1",
    )
    assert result.topic == "agent-systems"


def test_codex_provider_synthesize_exploration(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "## Exploration\n\nResults."}]}]
        })

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    result = provider.synthesize_exploration(problem="efficiency", selected_notes=[], model="gpt-5.1")
    assert "Exploration" in result


def test_codex_provider_url_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="connection"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_timeout(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_bad_json_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse("not-valid-json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_non_mapping_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse([1, 2, 3])

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="not a JSON object"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_error_key_in_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"error": {"code": "rate_limited", "message": "too fast"}})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="returned an error"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_no_model_raises():
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="requires a model"):
        provider.synthesize_context(task="x", selected_notes=[], model="")


def test_codex_provider_with_org_and_project(monkeypatch):
    headers_seen = {}

    def fake_urlopen(request, timeout):
        headers_seen.update(dict(request.headers))
        return _FakeResponse({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "result"}]}]
        })

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)

    provider = CodexProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        organization="my-org",
        project="my-project",
    )
    result = provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")
    assert result
    assert "Openai-organization" in headers_seen or "openai-organization" in {k.lower() for k in headers_seen}


# --- LocalOpenAICompatibleProvider uncovered methods ---

def test_local_openai_compatible_http_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "Error", {}, io.BytesIO(b"internal error"))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_openai_compatible_timeout(monkeypatch):
    def fake_urlopen(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="lm_studio", base_url="http://localhost:1234")
    with pytest.raises(ProviderError, match="timed out"):
        provider.synthesize_context(task="x", selected_notes=[], model="qwen2.5")


def test_local_openai_compatible_bad_json(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError, match="not valid"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_openai_compatible_non_mapping_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse([1, 2, 3])

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError, match="not a JSON object"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_openai_compatible_error_key_in_response(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"error": "model not found"})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="lm_studio", base_url="http://localhost:1234")
    with pytest.raises(ProviderError, match="returned an error"):
        provider.synthesize_context(task="x", selected_notes=[], model="qwen2.5")


def test_local_openai_compatible_no_model_raises():
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError, match="requires a model"):
        provider.synthesize_context(task="x", selected_notes=[], model="")


# --- Additional coverage for remaining lines ---

# operation_routes / _operation_provider edge cases

def test_operation_routes_raises_for_missing_operation():
    config = default_config(Path("/tmp"))
    with pytest.raises(ProviderError, match="must be a mapping"):
        operation_routes(config, "nonexistent_operation_xyz")


def test_operation_provider_uses_default_provider_from_policy():
    config = default_config(Path("/tmp"))
    config["providers"]["policy"]["default_provider"] = "mock"
    config["providers"]["mock"] = {}
    config["operations"]["synth"] = {"model": "m"}  # no provider key
    routes = operation_routes(config, "synth")
    assert routes[0].provider == "mock"


def test_operation_provider_no_policy_mapping_returns_none():
    # providers.policy is not a Mapping → _operation_provider returns None → ProviderError
    with pytest.raises(ProviderError, match="requires a provider"):
        operation_routes({"operations": {"extract": {"model": "m"}}, "providers": {}}, "extract")


def test_call_with_provider_policy_no_routes_raises(tmp_path):
    import kb_librarian.providers as providers_module
    config = default_config(tmp_path)
    # monkeypatch operation_routes to return empty list to exercise the "no_attempts" error path
    policy = {"max_attempts": 1, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    monkeypatched = False

    def fake_routes(cfg, op):
        return []

    original = providers_module.operation_routes
    providers_module.operation_routes = fake_routes
    try:
        with pytest.raises(ProviderError, match="no provider attempts"):
            call_with_provider_policy(
                config, "extract",
                operation_name="extract",
                retry_policy=retry_policy_from_config({"providers": {"retry": policy}}),
                call=lambda p, r: None,
            )
    finally:
        providers_module.operation_routes = original


# _provider_seam re-raise of ProviderError

def test_provider_seam_reraises_provider_error():
    # Invalid backend for codex → ProviderError from resolve_provider_seam → re-raised
    with pytest.raises(ProviderError, match="backend must be one of"):
        _provider_seam("codex", {"backend": "invalid_backend", "api_key_env": "KEY"})


# _ensure_runtime_support unsupported case

def test_provider_from_config_unsupported_seam_raises():
    # anthropic direct_http + vendor_cli is not a supported combination
    with pytest.raises(ProviderError, match="is not supported"):
        provider_from_config(
            {"providers": {"anthropic": {"backend": "direct_http", "credential_source": "vendor_cli"}}},
            "anthropic",
        )


# local_provider_status seam ProviderError catch

def test_local_provider_status_seam_config_error():
    # "invalid_backend" → resolve_provider_seam raises ProviderError → caught, returned as config error
    status = local_provider_status({"backend": "invalid_backend", "base_url": "http://localhost:9999"})
    assert status.reachable is False
    assert "configuration error" in status.message.lower()


# URLError paths

def test_local_provider_status_ollama_url_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "ollama", "base_url": "http://localhost:11434"})
    assert status.reachable is False
    assert "unreachable" in status.message


def test_local_provider_status_openai_compatible_url_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    status = local_provider_status({"backend": "vllm", "base_url": "http://localhost:8000"})
    assert status.reachable is False
    assert "unreachable" in status.message


# LocalOllamaProvider - raw JSON parse error and non-mapping

def test_local_ollama_provider_raw_json_decode_error(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="not valid JSON"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_non_mapping_json(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b'"just-a-string"')

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="not a JSON object"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_ollama_provider_non_list_notes_exploration(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": "## Exploration output."})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    result = provider.synthesize_exploration(problem="x", selected_notes=None, model="llama3.2")
    assert result


def test_local_ollama_provider_non_list_source_notes_compaction(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
                        "confidence": "medium", "retrieval_phrases": [], "tags": []},
        "body": "B", "source_note_ids": ["n1"],
        "dispositions": [{"note_id": "n1", "recommendation": "supersede", "rationale": "ok"}],
        "diff_summary": "D",
    }
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": json.dumps(compaction_payload)})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    result = provider.synthesize_compaction(source_notes=None, cluster_id="c", model="llama3.2")
    assert isinstance(result, dict)


def test_local_ollama_provider_compaction_non_mapping_raises(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse({"response": '"just-a-string"'})

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="Compaction response must be a JSON object"):
        provider.synthesize_compaction(source_notes=[], cluster_id="c", model="llama3.2")


def test_local_ollama_provider_http_error_no_body(monkeypatch):
    exc = urllib.error.HTTPError("http://localhost:11434/api/generate", 503, "Unavailable", {}, None)
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="HTTP 503"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


# AnthropicProvider - additional error paths

def test_anthropic_provider_messages_raw_json_decode_error(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="not valid Messages API JSON"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_http_error_no_body(monkeypatch):
    exc = urllib.error.HTTPError("https://api.anthropic.com/v1/messages", 429, "Too Many", {}, None)
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


def test_anthropic_provider_compaction_non_mapping_raises(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response('"just-a-string"'))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="Compaction response must be a JSON object"):
        provider.synthesize_compaction(source_notes=[], cluster_id="c", model="claude-haiku-4-5")


def test_anthropic_provider_non_list_exploration_notes(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_anthropic_text_response("Exploration output."))

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    result = provider.synthesize_exploration(problem="x", selected_notes=None, model="claude-haiku-4-5")
    assert result


# ClaudeCliProvider - additional edge cases

def test_claude_cli_provider_non_list_exploration_notes(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_text_response("Exploration output.")),
            stderr="",
        )
    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.synthesize_exploration(problem="x", selected_notes=None, model="claude-haiku-4-5")
    assert result


def test_claude_cli_provider_non_list_compaction_notes(monkeypatch):
    compaction_payload = {
        "frontmatter": {"title": "T", "summary": "S", "topic": "g", "knowledge_type": "technique",
                        "confidence": "medium", "retrieval_phrases": [], "tags": []},
        "body": "B", "source_note_ids": ["n1"],
        "dispositions": [{"note_id": "n1", "recommendation": "supersede", "rationale": "ok"}],
        "diff_summary": "D",
    }
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps(_claude_cli_json_response(compaction_payload)),
            stderr="",
        )
    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    result = provider.synthesize_compaction(source_notes=None, cluster_id="c", model="claude-haiku-4-5")
    assert isinstance(result, dict)


def test_claude_cli_provider_compaction_non_mapping_raises(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"type": "result", "is_error": False, "result": json.dumps("just-a-string"), "structured_output": "just-a-string"}),
            stderr="",
        )
    monkeypatch.setattr("kb_librarian.providers.subprocess.run", fake_run)
    provider = ClaudeCliProvider(command_path="/usr/bin/claude", credential_source="vendor_cli", token_env=None)
    with pytest.raises(ProviderError, match="Compaction response must be a JSON object"):
        provider.synthesize_compaction(source_notes=[], cluster_id="c", model="claude-haiku-4-5")


# _codex_http_error branches

def test_codex_http_error_401_auth_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 401, "Unauthorized", {}, io.BytesIO(b'{"error": "unauthorized"}'))
    err = _codex_http_error(exc)
    assert "401" in str(err)
    assert "api_key_env" in str(err)


def test_codex_http_error_403_auth_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 403, "Forbidden", {}, io.BytesIO(b""))
    err = _codex_http_error(exc)
    assert "403" in str(err)
    assert "api_key_env" in str(err)


def test_codex_http_error_404_model_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 404, "Not Found", {}, io.BytesIO(b""))
    err = _codex_http_error(exc)
    assert "404" in str(err)
    assert "base_url" in str(err)


def test_codex_http_error_429_rate_limit_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 429, "Too Many Requests", {}, io.BytesIO(b""))
    err = _codex_http_error(exc)
    assert "429" in str(err)
    assert "rate limited" in str(err)


def test_codex_http_error_500_server_error_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 500, "Internal Server Error", {}, io.BytesIO(b""))
    err = _codex_http_error(exc)
    assert "500" in str(err)
    assert "server error" in str(err)


def test_codex_http_error_unreadable_body():
    class _UnreadableBody:
        def read(self):
            raise OSError("cannot read")

        def close(self):
            pass

    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 500, "Error", {}, _UnreadableBody())
    err = _codex_http_error(exc)
    assert "500" in str(err)  # no body but still has code info


def test_codex_provider_json_decode_error(monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeRawResponse(b"not {{ valid json")
    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="not valid Responses API JSON"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


def test_codex_provider_http_error_401(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))
    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = CodexProvider(api_key="test-key", base_url="https://api.openai.com/v1")
    with pytest.raises(ProviderError, match="401"):
        provider.synthesize_context(task="x", selected_notes=[], model="gpt-5.1")


# validate_extraction_payload additional paths

def test_validate_extraction_payload_non_mapping_raises():
    with pytest.raises(ProviderError, match="Extraction response must be a JSON object"):
        validate_extraction_payload("not-a-mapping")


def test_validate_extraction_payload_non_list_candidates_raises():
    with pytest.raises(ProviderError, match="must include a candidates list"):
        validate_extraction_payload({"candidates": "not-a-list"})


# _optional_string_list with None value

def test_optional_string_list_none_returns_empty():
    from kb_librarian.providers import _optional_string_list
    result = _optional_string_list({"agent_use": None}, "agent_use")
    assert result == []


# _provider_timeout_seconds bool branch

def test_provider_timeout_seconds_bool_returns_default():
    from kb_librarian.providers import _provider_timeout_seconds
    result = _provider_timeout_seconds(True, default=30.0)
    assert result == 30.0

    result2 = _provider_timeout_seconds(False, default=30.0)
    assert result2 == 30.0


# _claude_cli_provider_error_message with excerpt

def test_claude_cli_provider_error_message_with_excerpt():
    msg = _claude_cli_provider_error_message("some auth error here to include", "vendor_cli", None)
    assert "some auth error here to include" in msg or "Output:" in msg


def test_codex_cli_provider_error_message_with_excerpt():
    msg = _codex_cli_provider_error_message("some codex error output here")
    assert "some codex error output here" in msg or "Output:" in msg


# _openai_compatible_url with non-v1 base_path

def test_openai_compatible_url_with_non_v1_base_path():
    url = _openai_compatible_url("http://localhost:8000/api", "/models")
    assert "/v1/models" in url


def test_openai_compatible_url_with_empty_base_path():
    url = _openai_compatible_url("http://localhost:8000", "/models")
    assert "/v1/models" in url


def test_openai_compatible_url_with_v1_base_path():
    url = _openai_compatible_url("http://localhost:8000/v1", "/models")
    assert url.endswith("/models")
    assert "/v1/models" in url


# _ollama_model_names edge cases

def test_ollama_model_names_non_mapping_returns_empty():
    result = _ollama_model_names("not-a-mapping")
    assert result == []


def test_ollama_model_names_non_list_models_returns_empty():
    result = _ollama_model_names({"models": "not-a-list"})
    assert result == []


def test_ollama_model_names_non_mapping_item_skipped():
    result = _ollama_model_names({"models": ["not-a-mapping"]})
    assert result == []


def test_ollama_model_names_with_both_name_and_model():
    result = _ollama_model_names({"models": [{"name": "llama3.2", "model": "llama3.2:latest"}]})
    assert "llama3.2" in result


# _openai_model_names edge cases

def test_openai_model_names_non_mapping_returns_empty():
    result = _openai_model_names("not-a-mapping")
    assert result == []


def test_openai_model_names_non_list_data_returns_empty():
    result = _openai_model_names({"data": "not-a-list"})
    assert result == []


def test_openai_model_names_non_mapping_item_skipped():
    result = _openai_model_names({"data": ["not-a-mapping"]})
    assert result == []


def test_openai_model_names_empty_id_skipped():
    result = _openai_model_names({"data": [{"id": "  "}, {"id": "gpt-5.1"}]})
    assert result == ["gpt-5.1"]


# Local HTTP error body reading error

def test_local_openai_compatible_http_error_unreadable_body(monkeypatch):
    class _UnreadableBody:
        def read(self):
            raise OSError("cannot read")

        def close(self):
            pass

    exc = urllib.error.HTTPError("http://localhost:8000/v1/responses", 500, "Error", {}, _UnreadableBody())
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


def test_local_openai_compatible_http_error_lm_studio_hint(monkeypatch):
    exc = urllib.error.HTTPError("http://localhost:1234/v1/responses", 503, "Unavailable", {}, io.BytesIO(b""))
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="lm_studio", base_url="http://localhost:1234")
    with pytest.raises(ProviderError, match="LM Studio"):
        provider.synthesize_context(task="x", selected_notes=[], model="qwen2.5")


def test_local_openai_compatible_http_error_ollama_generic_hint(monkeypatch):
    exc = urllib.error.HTTPError("http://localhost:8888/v1/responses", 404, "Not Found", {}, io.BytesIO(b""))
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    # Use a custom backend that doesn't match vllm or lm_studio to hit generic hint
    from kb_librarian.provider_seams import ProviderSeam
    import kb_librarian.providers as providers_module
    fake_seam = ProviderSeam(provider_name="local", backend="other_local", credential_source=None)

    class _CustomProvider(LocalOpenAICompatibleProvider):
        pass
    provider = _CustomProvider(backend="other_local", base_url="http://localhost:8888")
    with pytest.raises(ProviderError, match="HTTP 404"):
        provider.synthesize_context(task="x", selected_notes=[], model="some-model")


def test_local_openai_compatible_url_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOpenAICompatibleProvider(backend="vllm", base_url="http://localhost:8000")
    with pytest.raises(ProviderError):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


# MockProvider helper function branches

def test_guess_knowledge_type_anti_pattern():
    assert _guess_knowledge_type("anti-pattern to avoid") == "anti-pattern"


def test_guess_knowledge_type_decision():
    assert _guess_knowledge_type("this was a decision we decided on") == "decision"


def test_guess_knowledge_type_open_question():
    assert _guess_knowledge_type("open question that is unresolved") == "open-question"


def test_guess_knowledge_type_pattern():
    assert _guess_knowledge_type("the pattern to use here") == "pattern"


def test_guess_knowledge_type_heuristic():
    assert _guess_knowledge_type("prefer this approach as a heuristic") == "heuristic"


def test_guess_knowledge_type_fact():
    assert _guess_knowledge_type("sqlite supports full text search") == "fact"


def test_guess_knowledge_type_default():
    assert _guess_knowledge_type("some random text") == "technique"


def test_guess_topic_python():
    assert _guess_topic("use pytest fixtures in python") == "python"


def test_guess_topic_agent():
    assert _guess_topic("agent context cli kb") == "agent-systems"


def test_guess_topic_general():
    assert _guess_topic("some random unrelated content") == "general"


def test_has_no_durable_note_signal_do_not_ingest():
    assert _has_no_durable_note_signal("Please do not ingest this document") is True


def test_has_no_durable_note_signal_no_durable():
    assert _has_no_durable_note_signal("No durable notes worth creating here.") is True


def test_has_no_durable_note_signal_returns_false():
    assert _has_no_durable_note_signal("Use context for agent tasks.") is False


def test_first_heading_or_line_with_heading():
    assert _first_heading_or_line("# My Heading\n\ncontent") == "My Heading"


def test_first_heading_or_line_with_plain_line():
    assert _first_heading_or_line("Just a plain line\n\ncontent") == "Just a plain line"


def test_first_heading_or_line_with_empty_heading():
    # heading marker "#" with no text → heading empty → falls through to `return stripped` → returns "#"
    assert _first_heading_or_line("#\n\nplain") == "#"


def test_first_heading_or_line_with_empty_string():
    assert _first_heading_or_line("") == ""


def test_first_heading_or_line_skips_blank_first_line():
    # blank first line → continue → then heading → line 2749 is the continue
    assert _first_heading_or_line("  \n# Heading") == "Heading"


def test_first_paragraph_excludes_heading():
    result = _first_paragraph("# Heading\n\nFirst paragraph content.", exclude_heading=True)
    assert result == "First paragraph content."


def test_first_paragraph_with_empty_first():
    result = _first_paragraph("\n\nActual content.", exclude_heading=True)
    assert result == "Actual content."


def test_first_paragraph_returns_empty_on_no_content():
    result = _first_paragraph("   \n\n   ", exclude_heading=True)
    assert result == ""


def test_retrieval_phrases_with_short_title():
    phrases = _retrieval_phrases("short", "summary")
    assert len(phrases) >= 1


def test_retrieval_phrases_fallback_on_empty():
    phrases = _retrieval_phrases("", "")
    assert phrases == ["durable knowledge"]


def test_mock_provider_extract_with_anti_pattern_text():
    provider = MockProvider()
    result = provider.extract_candidates(
        text="# Anti-Pattern: Tight Coupling\n\nThis anti-pattern causes failures when you avoid proper abstractions.",
        source_path=Path("raw/x.md"),
        max_notes=5,
        model="mock",
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].knowledge_type == "anti-pattern"


def test_mock_provider_extract_with_python_topic():
    provider = MockProvider()
    result = provider.extract_candidates(
        text="# Pytest Fixtures\n\nUsing pytest fixtures in python projects improves test isolation.",
        source_path=Path("raw/x.md"),
        max_notes=5,
        model="mock",
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].topic == "python"


def test_mock_provider_extract_with_no_heading():
    provider = MockProvider()
    result = provider.extract_candidates(
        text="Just a plain paragraph without any heading but with enough content to be worth keeping.",
        source_path=Path("raw/x.md"),
        max_notes=5,
        model="mock",
    )
    assert len(result.candidates) == 1
    # Title comes from first line (no heading)
    assert "Just" in result.candidates[0].title


def test_mock_provider_synthesize_context_note_with_id_no_summary(monkeypatch):
    # Heuristics section: note has note_id + title but "summary" for techniques section is empty
    # The note should appear in techniques only if note_id + summary (not title)
    # And heuristics uses note_id + title
    provider = MockProvider()
    result = provider.synthesize_context(
        task="review",
        selected_notes=[{
            "note_id": "2026-01-01-test",
            "title": "Test Title",
            "summary": "",  # empty summary → skipped from "directly relevant" section
        }],
    )
    # The heuristics section should use title + note_id, so should appear there
    assert "Test Title" in result or "2026-01-01-test" in result


def test_mock_provider_synthesize_exploration_with_note_and_adjacent_patterns():
    provider = MockProvider()
    # Add 5 notes so adjacent patterns loop fills up
    notes = [
        {"note_id": f"2026-01-0{i}", "title": f"Note {i}", "summary": f"Summary {i}."}
        for i in range(1, 6)
    ]
    result = provider.synthesize_exploration(problem="efficiency", selected_notes=notes)
    assert "## Adjacent patterns" in result


# _codex_envelope_text with incomplete_details empty reason

def test_codex_envelope_text_incomplete_details_empty_reason():
    # incomplete_details present but reason is empty string → falls through to no-content error
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": [], "incomplete_details": {"reason": "  "}})


# validate_integration_payload with verdict=unrelated and no targets passes

def test_validate_integration_payload_adds_nuance_with_targets():
    result = validate_integration_payload({
        "verdict": "adds_nuance",
        "target_note_ids": ["note-1"],
        "rationale": "extends the existing concept",
    })
    assert result.verdict == "adds_nuance"


# _clean_string_list with empty item

def test_clean_string_list_skips_whitespace_only_items():
    from kb_librarian.providers import _clean_string_list
    result = _clean_string_list(["  ", "valid", "  "], "field")
    assert result == ["valid"]


# --- Final coverage gap tests ---

# _operation_provider edge cases (lines 508, 514, 517, 520)

def test_operation_provider_returns_none_when_providers_not_mapping():
    # providers is not a mapping → _operation_provider returns None → ProviderError
    with pytest.raises(ProviderError, match="requires a provider"):
        operation_routes({"operations": {"extract": {"model": "m"}}}, "extract")


def test_operation_provider_default_provider_not_string():
    # providers.policy.default_provider is not a string → returns None
    with pytest.raises(ProviderError, match="requires a provider"):
        operation_routes(
            {"operations": {"extract": {"model": "m"}}, "providers": {"policy": {"default_provider": 42}}},
            "extract"
        )


# _fallback_routes lines 514, 517, 520 — route has direct provider so _operation_provider
# returns early; then _fallback_routes is called with various non-Mapping structures.

def test_fallback_routes_no_providers_key():
    # no "providers" key → _fallback_routes returns [] at line 514
    routes = operation_routes({"operations": {"extract": {"provider": "mock", "model": "m"}}}, "extract")
    assert routes[0].provider == "mock"
    assert len(routes) == 1


def test_fallback_routes_no_policy_key():
    # providers exists but no "policy" key → _fallback_routes returns [] at line 517
    routes = operation_routes(
        {"operations": {"extract": {"provider": "mock", "model": "m"}}, "providers": {}},
        "extract"
    )
    assert routes[0].provider == "mock"
    assert len(routes) == 1


def test_fallback_routes_no_fallback_key():
    # providers.policy exists but no "fallback" key → _fallback_routes returns [] at line 520
    routes = operation_routes(
        {"operations": {"extract": {"provider": "mock", "model": "m"}}, "providers": {"policy": {}}},
        "extract"
    )
    assert routes[0].provider == "mock"
    assert len(routes) == 1


# _ensure_runtime_support unsupported seam (line 837)

def test_ensure_runtime_support_raises_for_unsupported_seam():
    # anthropic direct_http + command resolves OK but is not supported at runtime
    with pytest.raises(ProviderError):
        provider_from_config(
            {"providers": {"anthropic": {"backend": "direct_http", "credential_source": "command", "credential_command": "my-cred-cmd"}}},
            "anthropic",
        )


# MockProvider synthesize_context heuristics loop false branch (978->973)
# - note in loop where note_id exists but title is empty → branch taken without appending

def test_mock_provider_synthesize_context_note_without_title_in_heuristics():
    provider = MockProvider()
    # note_id present but no title → heuristics if condition (note_id and title) is False → loop continues
    result = provider.synthesize_context(
        task="review",
        selected_notes=[{
            "note_id": "2026-01-01-test",
            "title": "",  # empty title
            "summary": "Has a summary but no title.",
        }],
    )
    assert "## Applicable heuristics" in result
    # should NOT have the note referenced in heuristics since title is empty
    assert "## Directly relevant techniques" in result


# MockProvider synthesize_exploration adjacent patterns loop false branch (1032->1029)

def test_mock_provider_synthesize_exploration_note_without_title_in_adjacent():
    provider = MockProvider()
    # note_id present but no title → adjacent patterns if condition is False
    result = provider.synthesize_exploration(
        problem="efficiency",
        selected_notes=[{
            "note_id": "2026-01-01-test",
            "title": "",  # empty title  
            "summary": "A summary without title.",
        }],
    )
    assert "## Adjacent patterns" in result


# MockProvider synthesize_compaction elif note_title branch (1098->1100)

def test_mock_provider_synthesize_compaction_note_with_title_not_summary():
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=[{
            "note_id": "2026-01-01-x",
            "frontmatter": {"title": "My Title", "tags": [], "retrieval_phrases": []},
            # no "summary" key in frontmatter → note_summary="" and note_title="My Title" → elif note_title: branch
        }],
        model="mock",
    )
    assert "2026-01-01-x" in result.get("source_note_ids", [])


def test_mock_provider_synthesize_compaction_note_with_empty_title_and_no_summary():
    provider = MockProvider()
    result = provider.synthesize_compaction(
        source_notes=[{
            "note_id": "2026-01-01-y",
            "frontmatter": {"title": "", "summary": "", "tags": [], "retrieval_phrases": []},
            # both empty → elif note_title: is False → branch 1098->1100 (skip to tags.extend)
        }],
        model="mock",
    )
    assert "2026-01-01-y" in result.get("source_note_ids", [])


# LocalOllamaProvider HTTP error with unreadable body (1310-1311)

def test_local_ollama_provider_http_error_unreadable_body(monkeypatch):
    class _UnreadableBody:
        def read(self):
            raise OSError("cannot read body")
        def close(self): pass
    exc = urllib.error.HTTPError(
        "http://localhost:11434/api/generate", 503, "Unavailable", {},
        _UnreadableBody()
    )
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = LocalOllamaProvider(base_url="http://localhost:11434")
    with pytest.raises(ProviderError, match="HTTP 503"):
        provider.synthesize_context(task="x", selected_notes=[], model="llama3.2")


# AnthropicProvider HTTP error with unreadable body (1536-1537)

def test_anthropic_provider_http_error_unreadable_body(monkeypatch):
    class _UnreadableBody:
        def read(self):
            raise OSError("cannot read body")

        def close(self):
            pass

    exc = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages", 503, "Service Unavailable", {},
        _UnreadableBody()
    )
    def fake_urlopen(request, timeout):
        raise exc

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="test-key")
    with pytest.raises(ProviderError, match="HTTP 503"):
        provider.synthesize_context(task="x", selected_notes=[], model="claude-haiku-4-5")


# _codex_envelope_text inner loop branches (2045->2065, 2060->2052, 2062->2052)

def test_codex_envelope_text_output_not_a_list():
    # output is not a list → 2045->2065 branch (skip the loop entirely)
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({"output": "not-a-list"})


def test_codex_envelope_text_content_item_without_text_field():
    # content item has type output_text but text is not a string → 2056 False → 2060 branch
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": None}]}]
        })


def test_codex_envelope_text_refusal_empty_string_skipped():
    # content item type is "refusal" but refusal text is empty → 2062->2052 (if refusal: False)
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "   "}]}]
        })


def test_codex_envelope_text_unknown_content_type_skipped():
    # content item type is neither "output_text" nor "refusal" → 2060->2052 (elif False)
    with pytest.raises(ProviderError, match="did not contain text content"):
        _codex_envelope_text({
            "output": [{"type": "message", "content": [{"type": "unknown_type"}]}]
        })


# _codex_http_error - HTTP 400 (not 401/403/404/429/5xx) → no suffix (2093->2095)

def test_codex_http_error_400_no_specific_hint():
    exc = urllib.error.HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {}, io.BytesIO(b"bad request"))
    err = _codex_http_error(exc)
    assert "400" in str(err)
    assert "api_key_env" not in str(err)
    assert "base_url" not in str(err)


# _provider_error_text message without code (line 2114)

def test_provider_error_text_with_message_no_code():
    result = _provider_error_text({"message": "some error message"})
    assert result == "some error message"


# _claude_cli_provider_error_message: 2442->2444 = if excerpt: is False → need empty output

def test_claude_cli_provider_error_message_empty_output_no_excerpt():
    # empty output → excerpt is empty → 2442->2444 branch (skip the Output: append)
    msg = _claude_cli_provider_error_message("", "vendor_cli", None)
    assert "Output:" not in msg


# _codex_cli_provider_error_message: 2503->2505 = if excerpt: is False → need empty output

def test_codex_cli_provider_error_message_empty_output_no_excerpt():
    # empty output → excerpt is empty → 2503->2505 branch (skip the Output: append)
    msg = _codex_cli_provider_error_message("")
    assert "Output:" not in msg


# _local_openai_compatible_transport_message generic fallback (2729)

def test_local_openai_compatible_transport_message_generic(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("kb_librarian.providers.urllib.request.urlopen", fake_urlopen)
    # Use a custom backend string that's not vllm or lm_studio
    provider = LocalOpenAICompatibleProvider(backend="custom_local", base_url="http://localhost:9999")
    with pytest.raises(ProviderError):
        provider.synthesize_context(task="x", selected_notes=[], model="some-model")


# call_with_provider_policy - routes is empty - monkeypatched (305->346, 349)

def test_call_with_provider_policy_transient_error_no_fallback(tmp_path):
    import kb_librarian.providers as providers_module
    # monkeypatch operation_routes to return only 1 route
    # so transient error has no fallback → break → raise with last_error
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "m"}
    policy = {"max_attempts": 1, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}

    class FakeProvider:
        pass

    def factory(_c, name, **kw):
        return FakeProvider()

    def call(provider, route):
        raise ProviderError("HTTP 429 rate limit")

    with pytest.raises(ProviderError, match="stopped after provider attempts"):
        call_with_provider_policy(
            config, "extract",
            operation_name="extract",
            retry_policy=retry_policy_from_config({"providers": {"retry": policy}}),
            call=call,
            provider_factory=factory,
        )


# --- provider_seams.py coverage gap tests ---

from kb_librarian.provider_seams import (
    ProviderSeam, _reject_conflicting_fields, _resolve_mock_provider_seam,
    _unsupported_runtime_message, provider_seam_supported_for_runtime,
    resolve_provider_seam, validate_provider_seam_config,
)

def _err(msg: str) -> Exception:
    return ValueError(msg)


# validate_provider_seam_config: unknown provider name → 57->exit (no-op)

def test_validate_provider_seam_config_unknown_provider_noop():
    # provider_name not in the known set → function exits without calling resolve
    validate_provider_seam_config(
        "openai",
        {"backend": "direct_http"},
        error_factory=_err,
        error_prefix="providers.openai",
    )  # should not raise


# resolve_provider_seam: config is not a Mapping (line 76)

def test_resolve_provider_seam_non_mapping_raises():
    with pytest.raises(ValueError, match="must be a mapping"):
        resolve_provider_seam(
            "anthropic",
            "not-a-mapping",  # type: ignore[arg-type]
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# resolve_provider_seam: unknown provider returns generic seam (lines 109-116)

def test_resolve_provider_seam_unknown_provider():
    seam = resolve_provider_seam(
        "openai",
        {"backend": "direct_http"},
        error_factory=_err,
        error_prefix="providers.openai",
    )
    assert seam.provider_name == "openai"
    assert seam.backend == "direct_http"


# provider_seam_supported_for_runtime: codex with unsupported seam (line 136)

def test_provider_seam_supported_codex_unsupported():
    seam = ProviderSeam(provider_name="codex", backend="direct_http", credential_source="vendor_cli")
    supported, msg = provider_seam_supported_for_runtime(seam)
    assert not supported
    assert "Codex" in msg


# provider_seam_supported_for_runtime: local with unsupported seam (line 140)

def test_provider_seam_supported_local_unsupported():
    seam = ProviderSeam(provider_name="local", backend="direct_http", credential_source="api_key_env")
    supported, msg = provider_seam_supported_for_runtime(seam)
    assert not supported
    assert "local backends" in msg


# provider_seam_supported_for_runtime: mock with unsupported seam (lines 144-145)

def test_provider_seam_supported_mock_unsupported():
    seam = ProviderSeam(provider_name="mock", backend="direct_http", credential_source="api_key_env")
    supported, msg = provider_seam_supported_for_runtime(seam)
    assert not supported
    assert "Mock" in msg


# _resolve_cloud_provider_seam: unsupported credential_source string (lines 175-181)

def test_resolve_cloud_provider_seam_unsupported_credential_source():
    with pytest.raises(ValueError, match="must be one of"):
        resolve_provider_seam(
            "anthropic",
            {"credential_source": "invalid_source"},
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# _resolve_cloud_provider_seam: api_key_env not set with api_key_env credential (line 211)

def test_resolve_cloud_provider_seam_missing_api_key_env():
    with pytest.raises(ValueError, match="api_key_env is required"):
        resolve_provider_seam(
            "anthropic",
            {"backend": "direct_http", "credential_source": "api_key_env"},
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# _resolve_cloud_provider_seam: token_env not set with token_env credential (line 235)

def test_resolve_cloud_provider_seam_missing_token_env():
    with pytest.raises(ValueError, match="token_env is required"):
        resolve_provider_seam(
            "anthropic",
            {"backend": "vendor_cli", "credential_source": "token_env"},
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# 242->256: credential_source is not "command" → branch to line 256 (already covered by other tests)
# Line 244: credential_command is required when credential_source is "command"

def test_resolve_cloud_provider_seam_missing_credential_command():
    with pytest.raises(ValueError, match="credential_command is required"):
        resolve_provider_seam(
            "anthropic",
            {"backend": "direct_http", "credential_source": "command"},
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# cli_command conflicts with non-CLI credential sources.

def test_resolve_cloud_provider_seam_cli_command_conflicts_with_api_key_env():
    with pytest.raises(ValueError, match="cli_command conflicts with credential_source 'api_key_env'"):
        resolve_provider_seam(
            "anthropic",
            {
                "backend": "direct_http",
                "credential_source": "api_key_env",
                "api_key_env": "ANTHROPIC_KEY",
                "cli_command": "/usr/bin/claude",
            },
            error_factory=_err,
            error_prefix="providers.anthropic",
        )


# _resolve_mock_provider_seam: wrong backend (line 316)

def test_resolve_mock_provider_seam_wrong_backend():
    with pytest.raises(ValueError, match="backend must be 'mock'"):
        resolve_provider_seam(
            "mock",
            {"backend": "direct_http"},
            error_factory=_err,
            error_prefix="providers.mock",
        )


# _resolve_mock_provider_seam: credential_source set (line 318)

def test_resolve_mock_provider_seam_credential_source_rejected():
    with pytest.raises(ValueError, match="credential_source is not supported"):
        resolve_provider_seam(
            "mock",
            {"credential_source": "api_key_env"},
            error_factory=_err,
            error_prefix="providers.mock",
        )


# _reject_conflicting_fields: unsupported_label path (line 366)

def test_reject_conflicting_fields_with_unsupported_label():
    with pytest.raises(ValueError, match="not supported for test providers"):
        _reject_conflicting_fields(
            _err, "providers.mock", "not_applicable",
            configured_fields={"api_key_env": "MY_KEY"},
            unsupported_label="test providers",
        )


# _unsupported_runtime_message: codex branch (line 382)

def test_unsupported_runtime_message_codex():
    seam = ProviderSeam(provider_name="codex", backend="vendor_cli", credential_source="api_key_env")
    msg = _unsupported_runtime_message(seam)
    assert "Codex" in msg
    assert "not implemented yet" in msg


# _unsupported_runtime_message: local branch (line 389-390)

def test_unsupported_runtime_message_local():
    seam = ProviderSeam(provider_name="local", backend="custom", credential_source=None)
    msg = _unsupported_runtime_message(seam)
    assert "ollama" in msg


# _unsupported_runtime_message: mock branch (lines 391-392)

def test_unsupported_runtime_message_mock():
    seam = ProviderSeam(provider_name="mock", backend="other", credential_source=None)
    msg = _unsupported_runtime_message(seam)
    assert "Mock" in msg


# _unsupported_runtime_message: unknown provider fallback (line 393)

def test_unsupported_runtime_message_unknown():
    seam = ProviderSeam(provider_name="unknown_provider", backend="x", credential_source=None)
    msg = _unsupported_runtime_message(seam)
    assert "unsupported runtime configuration" in msg
