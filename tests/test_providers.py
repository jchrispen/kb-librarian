from __future__ import annotations

import json
import io
import subprocess
from pathlib import Path
import urllib.error

import pytest

from kb_librarian.provider_retry import classify_provider_failure
from kb_librarian.config import default_config
from kb_librarian.errors import ProviderError
from kb_librarian.providers import (
    ClaudeCliProvider,
    CodexProvider,
    LocalOpenAICompatibleProvider,
    LocalOllamaProvider,
    MockProvider,
    claude_cli_status,
    call_with_provider_policy,
    local_provider_status,
    operation_route,
    operation_routes,
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


def test_claude_cli_status_reports_missing_command(monkeypatch):
    monkeypatch.setattr("kb_librarian.providers.shutil.which", lambda command: None)

    status = claude_cli_status("claude", env={})

    assert status.available is False
    assert status.code == "missing_command"
