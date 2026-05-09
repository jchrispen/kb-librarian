from __future__ import annotations

from pathlib import Path

import pytest

from kb_librarian.config import (
    ConfigError,
    default_config,
    load_config,
    render_config,
    resolve_data_dir,
    validate_config,
    write_config_file,
)
from kb_librarian.paths import DEFAULT_DATA_DIR, default_config_path, default_data_dir


def test_default_config_contains_phase_1_sections(tmp_path):
    config = default_config(tmp_path)

    validate_config(config)
    rendered = render_config(config)

    assert "providers:" in rendered
    assert "retry:" in rendered
    assert "policy:" in rendered
    assert "default_provider: anthropic" in rendered
    assert "fallback: {}" in rendered
    assert "backend: direct_http" in rendered
    assert "credential_source: api_key_env" in rendered
    assert "codex:" in rendered
    assert "api_key_env: OPENAI_API_KEY" in rendered
    assert "local:" in rendered
    assert "backend: ollama" in rendered
    assert "base_url: http://127.0.0.1:11434" in rendered
    assert "operations:" in rendered
    assert "retrieval:" in rendered
    assert "embedding_index_path: .kb/embeddings.sqlite" in rendered
    assert "embedding_provider: null" in rendered
    assert "embedding_model: null" in rendered
    assert "embedding_dimensions: null" in rendered
    assert "ingest:" in rendered
    assert "indexes:" in rendered
    assert "review:" in rendered
    assert "git:" in rendered
    assert "auto_commit: false" in rendered
    assert "commit_ingests: true" in rendered
    assert "commit_reviews: true" in rendered
    assert "commit_reindexes: false" in rendered
    assert "commit_topic_reorganizations: true" in rendered
    assert "allow_unrelated_changes: false" in rendered
    assert "privacy:" in rendered
    assert "require_confirmation_for_cloud_llm: false" in rendered
    assert "hooks:" in rendered
    assert "session_start_ingest: false" in rendered


def test_default_data_dir_is_library_next_to_default_config():
    assert DEFAULT_DATA_DIR == Path(".kb") / ".library"
    assert default_config()["data_dir"] == str(Path.home() / ".kb" / ".library")
    assert default_config_path() == Path.home() / ".kb" / "config.yaml"
    assert default_data_dir() == Path.home() / ".kb" / ".library"
    assert resolve_data_dir(env={}) == Path.home() / ".kb" / ".library"


def test_resolve_data_dir_precedence(tmp_path):
    explicit_dir = tmp_path / "explicit"
    env_dir = tmp_path / "env"
    configured_dir = tmp_path / "configured"
    default_dir = tmp_path / "default"
    write_config_file(default_dir / ".kb" / "config.yaml", default_config(configured_dir))

    assert resolve_data_dir(explicit_dir, env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=default_dir) == explicit_dir
    assert resolve_data_dir(env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=default_dir) == env_dir
    assert resolve_data_dir(env={}, default_data_dir=default_dir) == configured_dir
    assert resolve_data_dir(env={}, default_data_dir=tmp_path / "missing-default") == (
        tmp_path / "missing-default" / ".kb" / ".library"
    )


def test_default_control_dir_resolves_to_default_library(tmp_path):
    assert resolve_data_dir(tmp_path / ".kb", env={}, default_data_dir=tmp_path) == (
        tmp_path / ".kb" / ".library"
    )
    assert resolve_data_dir(env={"KB_DATA_DIR": str(tmp_path / ".kb")}, default_data_dir=tmp_path) == (
        tmp_path / ".kb" / ".library"
    )

    write_config_file(tmp_path / ".kb" / "config.yaml", default_config(tmp_path / ".kb"))

    assert resolve_data_dir(env={}, default_data_dir=tmp_path) == tmp_path / ".kb" / ".library"


def test_home_control_dir_env_resolves_to_home_library(tmp_path):
    home_control_dir = tmp_path / ".kb"

    assert resolve_data_dir(env={"KB_DATA_DIR": str(home_control_dir)}) == home_control_dir / ".library"


def test_load_config_env_override_wins_over_config_value(tmp_path):
    env_dir = tmp_path / "env"
    configured_elsewhere = tmp_path / "elsewhere"
    config = default_config(configured_elsewhere)
    write_config_file(env_dir / ".kb" / "config.yaml", config)

    loaded = load_config(env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=tmp_path / "default")

    assert loaded["data_dir"] == str(env_dir)


def test_load_config_uses_default_config_next_to_default_library(tmp_path):
    configured_dir = tmp_path / ".kb" / ".library"
    write_config_file(tmp_path / ".kb" / "config.yaml", default_config(configured_dir))

    loaded = load_config(env={}, default_data_dir=tmp_path)

    assert loaded["data_dir"] == str(configured_dir)


def test_validate_config_reports_missing_required_key(tmp_path):
    config = default_config(tmp_path)
    del config["retrieval"]["body_weight"]

    with pytest.raises(ConfigError, match="retrieval.*body_weight"):
        validate_config(config)


def test_validate_config_rejects_malformed_embedding_seam(tmp_path):
    config = default_config(tmp_path)
    config["retrieval"]["embeddings"] = "yes"
    with pytest.raises(ConfigError, match="retrieval.embeddings"):
        validate_config(config)

    config = default_config(tmp_path)
    config["retrieval"]["lexical_index"] = False
    with pytest.raises(ConfigError, match="retrieval.lexical_index"):
        validate_config(config)

    config = default_config(tmp_path)
    config["retrieval"]["embedding_index_path"] = "../embeddings.sqlite"
    with pytest.raises(ConfigError, match="retrieval.embedding_index_path"):
        validate_config(config)

    config = default_config(tmp_path)
    config["retrieval"]["embedding_provider"] = ""
    with pytest.raises(ConfigError, match="retrieval.embedding_provider"):
        validate_config(config)

    config = default_config(tmp_path)
    config["retrieval"]["embedding_dimensions"] = 0
    with pytest.raises(ConfigError, match="retrieval.embedding_dimensions"):
        validate_config(config)


def test_validate_config_rejects_non_mapping_section(tmp_path):
    config = default_config(tmp_path)
    config["privacy"] = []

    with pytest.raises(ConfigError, match="privacy"):
        validate_config(config)


def test_validate_config_rejects_invalid_provider_retry_settings(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["retry"]["max_attempts"] = 0
    with pytest.raises(ConfigError, match="providers.retry.max_attempts"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["retry"]["base_delay_seconds"] = -0.1
    with pytest.raises(ConfigError, match="providers.retry.base_delay_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["retry"]["base_delay_seconds"] = 1.0
    config["providers"]["retry"]["max_delay_seconds"] = 0.2
    with pytest.raises(ConfigError, match="max_delay_seconds"):
        validate_config(config)


def test_validate_config_allows_provider_policy_default_route(tmp_path):
    config = default_config(tmp_path)
    del config["operations"]["classify"]["provider"]

    validate_config(config)


def test_validate_config_accepts_provider_fallback_routes(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["providers"]["policy"]["fallback"] = {
        "extract": [
            {"provider": "mock", "model": "mock-extract"},
        ]
    }

    validate_config(config)


def test_validate_config_rejects_invalid_provider_policy(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["policy"]["default_provider"] = "missing"
    with pytest.raises(ConfigError, match="default_provider"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"unknown": ["local"]}
    with pytest.raises(ConfigError, match="unknown operation"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"extract": ["missing"]}
    with pytest.raises(ConfigError, match="unknown provider"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"extract": ["anthropic"]}
    with pytest.raises(ConfigError, match="duplicate consecutive"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {
        "extract": [
            {"provider": "local", "model": "llama3.2"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        ]
    }
    with pytest.raises(ConfigError, match="provider cycle"):
        validate_config(config)


def test_validate_config_accepts_local_provider_routes(tmp_path):
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "llama3.2"}

    validate_config(config)


def test_validate_config_accepts_codex_provider_routes(tmp_path):
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "codex", "model": "gpt-5.1-codex"}

    validate_config(config)


def test_validate_config_accepts_vendor_cli_seams_without_breaking_defaults(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["anthropic"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
    }

    validate_config(config)


def test_validate_config_accepts_anthropic_token_env_vendor_cli_seam(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["anthropic"] = {
        "backend": "vendor_cli",
        "credential_source": "token_env",
        "token_env": "CLAUDE_CODE_OAUTH_TOKEN",
    }

    validate_config(config)


def test_validate_config_rejects_malformed_codex_provider(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["codex"]["api_key_env"] = ""
    with pytest.raises(ConfigError, match="providers.codex.api_key_env"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"]["base_url"] = "api.openai.com/v1"
    with pytest.raises(ConfigError, match="providers.codex.base_url"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"]["timeout_seconds"] = 0
    with pytest.raises(ConfigError, match="providers.codex.timeout_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"] = {
        "backend": "vendor_cli",
        "credential_source": "token_env",
        "token_env": "CODEX_TOKEN",
        "base_url": "https://api.openai.com/v1",
        "timeout_seconds": 120,
    }
    with pytest.raises(ConfigError, match="credential_source='token_env'.*backend 'vendor_cli'"):
        validate_config(config)


def test_validate_config_rejects_conflicting_provider_auth_settings(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["anthropic"]["backend"] = "vendor_cli"
    config["providers"]["anthropic"]["credential_source"] = "vendor_cli"
    with pytest.raises(ConfigError, match="providers.anthropic.api_key_env conflicts"):
        validate_config(config)


def test_validate_config_rejects_malformed_local_provider(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "llama-cpp"
    with pytest.raises(ConfigError, match="providers.local.backend"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "vllm"
    validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "lm_studio"
    validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"]["base_url"] = "127.0.0.1:11434"
    with pytest.raises(ConfigError, match="providers.local.base_url"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"]["timeout_seconds"] = 0
    with pytest.raises(ConfigError, match="providers.local.timeout_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"]["credential_source"] = "api_key_env"
    with pytest.raises(ConfigError, match="providers.local.credential_source"):
        validate_config(config)


def test_validate_config_rejects_non_boolean_git_policy(tmp_path):
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = "yes"

    with pytest.raises(ConfigError, match="git.auto_commit"):
        validate_config(config)


def test_validate_config_rejects_invalid_privacy_policy(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["cloud_llm_allowed"] = "yes"
    with pytest.raises(ConfigError, match="privacy.cloud_llm_allowed"):
        validate_config(config)

    config = default_config(tmp_path)
    config["privacy"]["blocked_topics"] = "sensitive"
    with pytest.raises(ConfigError, match="privacy.blocked_topics"):
        validate_config(config)

    config = default_config(tmp_path)
    config["privacy"]["redact_patterns"] = ["("]
    with pytest.raises(ConfigError, match="privacy.redact_patterns"):
        validate_config(config)

    config = default_config(tmp_path)
    config["privacy"]["require_confirmation_for_cloud_llm"] = "yes"
    with pytest.raises(ConfigError, match="privacy.require_confirmation_for_cloud_llm"):
        validate_config(config)
