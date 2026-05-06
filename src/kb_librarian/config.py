"""Configuration loading, rendering, and validation."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from kb_librarian.errors import ConfigError
from kb_librarian.paths import DEFAULT_DATA_DIR, config_path

REQUIRED_TOP_LEVEL_KEYS = (
    "data_dir",
    "providers",
    "operations",
    "retrieval",
    "ingest",
    "indexes",
    "review",
    "git",
    "privacy",
    "hooks",
)

REQUIRED_SECTION_KEYS = {
    "providers": ("anthropic",),
    "operations": ("extract", "compact", "classify", "integrate", "synthesize"),
    "retrieval": (
        "default_budget_tokens",
        "context_budget_tokens",
        "explore_budget_tokens",
        "index_token_cap",
        "lexical_index",
        "embeddings",
        "title_weight",
        "summary_weight",
        "retrieval_phrase_weight",
        "tag_weight",
        "body_weight",
    ),
    "ingest": (
        "max_notes_per_doc",
        "prefer_skip_over_low_value_note",
        "low_confidence_goes_to_review",
    ),
    "indexes": ("topic_sort", "top_level_min_notes"),
    "review": (
        "stale_after_days",
        "orphan_after_days",
        "duplicate_cluster_threshold",
        "compaction_cooldown_days",
        "max_review_items_per_run",
    ),
    "git": ("auto_commit", "require_clean_worktree_for_rewrites"),
    "privacy": ("cloud_llm_allowed", "blocked_topics", "redact_patterns"),
    "hooks": ("session_start_ingest",),
}


def default_config(data_dir: str | Path = DEFAULT_DATA_DIR, *, hooks: bool = False) -> dict[str, Any]:
    """Return a fresh Phase 1 config dictionary."""

    return {
        "data_dir": str(Path(data_dir).expanduser()),
        "providers": {
            "anthropic": {
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        },
        "operations": {
            "extract": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            "compact": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            "classify": {"provider": "anthropic", "model": "claude-haiku-4-5"},
            "integrate": {"provider": "anthropic", "model": "claude-haiku-4-5"},
            "synthesize": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        },
        "retrieval": {
            "default_budget_tokens": 1500,
            "context_budget_tokens": 1800,
            "explore_budget_tokens": 3000,
            "index_token_cap": 5000,
            "lexical_index": True,
            "embeddings": False,
            "title_weight": 5,
            "summary_weight": 4,
            "retrieval_phrase_weight": 4,
            "tag_weight": 3,
            "body_weight": 1,
        },
        "ingest": {
            "max_notes_per_doc": 7,
            "prefer_skip_over_low_value_note": True,
            "low_confidence_goes_to_review": True,
        },
        "indexes": {
            "topic_sort": "alphabetical",
            "top_level_min_notes": 1,
            "topic_page_size": 50,
            "top_level_page_size": 100,
        },
        "review": {
            "stale_after_days": 180,
            "orphan_after_days": 30,
            "duplicate_cluster_threshold": 4,
            "compaction_cooldown_days": 30,
            "max_review_items_per_run": 10,
        },
        "git": {
            "auto_commit": False,
            "require_clean_worktree_for_rewrites": True,
        },
        "privacy": {
            "cloud_llm_allowed": True,
            "blocked_topics": [],
            "redact_patterns": [],
        },
        "hooks": {
            "session_start_ingest": bool(hooks),
        },
    }


def render_config(config: Mapping[str, Any]) -> str:
    """Render config YAML with stable key ordering."""

    return yaml.safe_dump(
        dict(config),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )


def read_config_file(path: Path) -> dict[str, Any]:
    """Read a YAML config file and return a dictionary."""

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in config file {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    if loaded is None:
        raise ConfigError(f"Config file {path} is empty; expected YAML mapping.")
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping.")
    return loaded


def write_config_file(path: Path, config: Mapping[str, Any]) -> None:
    """Write a config file after validating it."""

    config_copy = deepcopy(dict(config))
    validate_config(config_copy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(config_copy), encoding="utf-8")


def resolve_data_dir(
    explicit_data_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default_data_dir: str | Path = DEFAULT_DATA_DIR,
) -> Path:
    """Resolve the KB data directory using the Phase 1 precedence rules."""

    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser()

    environ = os.environ if env is None else env
    env_data_dir = environ.get("KB_DATA_DIR")
    if env_data_dir:
        return Path(env_data_dir).expanduser()

    default_dir = Path(default_data_dir).expanduser()
    configured_path = config_path(default_dir)
    if configured_path.exists():
        config = read_config_file(configured_path)
        configured_data_dir = config.get("data_dir")
        if configured_data_dir:
            return Path(str(configured_data_dir)).expanduser()

    return default_dir


def load_config(
    explicit_data_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default_data_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Load config for the resolved data directory, falling back to defaults."""

    data_dir = resolve_data_dir(
        explicit_data_dir,
        env=env,
        default_data_dir=default_data_dir,
    )
    path = config_path(data_dir)
    if path.exists():
        config = read_config_file(path)
    else:
        config = default_config(data_dir)

    if explicit_data_dir or ((os.environ if env is None else env).get("KB_DATA_DIR")):
        config["data_dir"] = str(data_dir)

    validate_config(config)
    return config


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate required Phase 1 config keys and basic value types."""

    if not isinstance(config, Mapping):
        raise ConfigError("Config must be a YAML mapping.")

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in config:
            raise ConfigError(f"Config is missing required top-level key: {key}")

    data_dir = config["data_dir"]
    if not isinstance(data_dir, str) or not data_dir.strip():
        raise ConfigError("Config key data_dir must be a non-empty string.")

    for section, keys in REQUIRED_SECTION_KEYS.items():
        value = config.get(section)
        if not isinstance(value, Mapping):
            raise ConfigError(f"Config section {section} must be a mapping.")
        for key in keys:
            if key not in value:
                raise ConfigError(f"Config section {section} is missing required key: {key}")

    anthropic = config["providers"]["anthropic"]
    if not isinstance(anthropic, Mapping):
        raise ConfigError("Config section providers.anthropic must be a mapping.")
    if not anthropic.get("api_key_env"):
        raise ConfigError("Config key providers.anthropic.api_key_env is required.")

    providers = config["providers"]
    for operation in REQUIRED_SECTION_KEYS["operations"]:
        route = config["operations"][operation]
        if not isinstance(route, Mapping):
            raise ConfigError(f"Config operation {operation} must be a mapping.")
        provider = route.get("provider")
        model = route.get("model")
        if not isinstance(provider, str) or not provider.strip():
            raise ConfigError(f"Config operation {operation}.provider must be a non-empty string.")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"Config operation {operation}.model must be a non-empty string.")
        if provider not in providers:
            raise ConfigError(f"Config operation {operation} references unknown provider {provider!r}.")

    indexes = config["indexes"]
    for key in ("topic_page_size", "top_level_page_size"):
        if key not in indexes:
            continue
        value = indexes[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"Config key indexes.{key} must be a positive integer.")
