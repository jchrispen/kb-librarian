from __future__ import annotations

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


def test_default_config_contains_phase_1_sections(tmp_path):
    config = default_config(tmp_path)

    validate_config(config)
    rendered = render_config(config)

    assert "providers:" in rendered
    assert "retry:" in rendered
    assert "operations:" in rendered
    assert "retrieval:" in rendered
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
    assert "hooks:" in rendered
    assert "session_start_ingest: false" in rendered


def test_resolve_data_dir_precedence(tmp_path):
    explicit_dir = tmp_path / "explicit"
    env_dir = tmp_path / "env"
    configured_dir = tmp_path / "configured"
    default_dir = tmp_path / "default"
    write_config_file(default_dir / ".kb" / "config.yaml", default_config(configured_dir))

    assert resolve_data_dir(explicit_dir, env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=default_dir) == explicit_dir
    assert resolve_data_dir(env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=default_dir) == env_dir
    assert resolve_data_dir(env={}, default_data_dir=default_dir) == configured_dir
    assert resolve_data_dir(env={}, default_data_dir=tmp_path / "missing-default") == tmp_path / "missing-default"


def test_load_config_env_override_wins_over_config_value(tmp_path):
    env_dir = tmp_path / "env"
    configured_elsewhere = tmp_path / "elsewhere"
    config = default_config(configured_elsewhere)
    write_config_file(env_dir / ".kb" / "config.yaml", config)

    loaded = load_config(env={"KB_DATA_DIR": str(env_dir)}, default_data_dir=tmp_path / "default")

    assert loaded["data_dir"] == str(env_dir)


def test_validate_config_reports_missing_required_key(tmp_path):
    config = default_config(tmp_path)
    del config["retrieval"]["body_weight"]

    with pytest.raises(ConfigError, match="retrieval.*body_weight"):
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


def test_validate_config_rejects_non_boolean_git_policy(tmp_path):
    config = default_config(tmp_path)
    config["git"]["auto_commit"] = "yes"

    with pytest.raises(ConfigError, match="git.auto_commit"):
        validate_config(config)
