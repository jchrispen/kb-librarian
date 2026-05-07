"""Configuration loading, rendering, and validation."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml

from kb_librarian.errors import ConfigError
from kb_librarian.paths import (
    DEFAULT_ROOT,
    config_path,
    default_config_path,
    default_data_dir as default_library_dir,
    is_control_dir,
    is_library_dir_next_to_control_dir,
)

PROVIDER_CONTROL_KEYS = {"retry", "policy"}
MAX_PROVIDER_FALLBACKS_PER_OPERATION = 4

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


def default_config(data_dir: str | Path | None = None, *, hooks: bool = False) -> dict[str, Any]:
    """Return a fresh Phase 1 config dictionary."""

    if data_dir is None:
        data_dir = default_library_dir()

    return {
        "data_dir": str(Path(data_dir).expanduser()),
        "providers": {
            "anthropic": {
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "codex": {
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.openai.com/v1",
                "timeout_seconds": 120,
            },
            "local": {
                "backend": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "timeout_seconds": 120,
            },
            "retry": {
                "max_attempts": 3,
                "base_delay_seconds": 0.25,
                "max_delay_seconds": 2.0,
                "jitter_seconds": 0.1,
            },
            "policy": {
                "default_provider": "anthropic",
                "fallback": {},
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
            "commit_ingests": True,
            "commit_reviews": True,
            "commit_reindexes": False,
            "commit_topic_reorganizations": True,
            "allow_unrelated_changes": False,
            "require_clean_worktree_for_rewrites": True,
        },
        "privacy": {
            "cloud_llm_allowed": True,
            "blocked_topics": [],
            "redact_patterns": [],
            "require_confirmation_for_cloud_llm": False,
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
    default_data_dir: str | Path = DEFAULT_ROOT,
) -> Path:
    """Resolve the KB data directory using the Phase 1 precedence rules."""

    if explicit_data_dir:
        return _normalize_resolved_data_dir(explicit_data_dir, default_data_dir)

    environ = os.environ if env is None else env
    env_data_dir = environ.get("KB_DATA_DIR")
    if env_data_dir:
        return _normalize_resolved_data_dir(env_data_dir, default_data_dir)

    default_root = Path(default_data_dir).expanduser()
    configured_path = default_config_path(default_root)
    if configured_path.exists():
        config = read_config_file(configured_path)
        configured_data_dir = config.get("data_dir")
        if configured_data_dir:
            return _normalize_resolved_data_dir(str(configured_data_dir), default_data_dir)

    return default_library_dir(default_root)


def _normalize_resolved_data_dir(data_dir: str | Path, default_root: str | Path = DEFAULT_ROOT) -> Path:
    path = Path(data_dir).expanduser()
    if is_control_dir(path):
        return path / ".library"
    return path


def load_config(
    explicit_data_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default_data_dir: str | Path = DEFAULT_ROOT,
) -> dict[str, Any]:
    """Load config for the resolved data directory, falling back to defaults."""

    data_dir = resolve_data_dir(
        explicit_data_dir,
        env=env,
        default_data_dir=default_data_dir,
    )
    path = data_dir.parent / "config.yaml" if is_library_dir_next_to_control_dir(data_dir) else config_path(data_dir)
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

    codex = config["providers"].get("codex")
    if codex is not None:
        if not isinstance(codex, Mapping):
            raise ConfigError("Config section providers.codex must be a mapping when present.")
        api_key_env = codex.get("api_key_env")
        if not isinstance(api_key_env, str) or not api_key_env.strip():
            raise ConfigError("Config key providers.codex.api_key_env must be a non-empty string.")
        base_url = codex.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigError("Config key providers.codex.base_url must be a non-empty URL.")
        parsed_base_url = urlparse(base_url.strip())
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise ConfigError("Config key providers.codex.base_url must be an http(s) URL.")
        _validate_positive_number(codex, "providers.codex.timeout_seconds")

    local = config["providers"].get("local")
    if local is not None:
        if not isinstance(local, Mapping):
            raise ConfigError("Config section providers.local must be a mapping when present.")
        backend = local.get("backend", "ollama")
        if backend != "ollama":
            raise ConfigError("Config key providers.local.backend must be 'ollama'.")
        base_url = local.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigError("Config key providers.local.base_url must be a non-empty URL.")
        parsed_base_url = urlparse(base_url.strip())
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise ConfigError("Config key providers.local.base_url must be an http(s) URL.")
        _validate_positive_number(local, "providers.local.timeout_seconds")

    retry = config["providers"].get("retry")
    if retry is not None:
        if not isinstance(retry, Mapping):
            raise ConfigError("Config section providers.retry must be a mapping when present.")
        _validate_positive_int(retry, "providers.retry.max_attempts", minimum=1)
        _validate_non_negative_number(retry, "providers.retry.base_delay_seconds")
        _validate_non_negative_number(retry, "providers.retry.max_delay_seconds")
        _validate_non_negative_number(retry, "providers.retry.jitter_seconds")
        base_delay = float(retry.get("base_delay_seconds", 0.0) or 0.0)
        max_delay = float(retry.get("max_delay_seconds", 0.0) or 0.0)
        if max_delay < base_delay:
            raise ConfigError("Config key providers.retry.max_delay_seconds must be >= base_delay_seconds.")

    _validate_provider_policy(config)

    providers = config["providers"]
    for operation in REQUIRED_SECTION_KEYS["operations"]:
        route = config["operations"][operation]
        if not isinstance(route, Mapping):
            raise ConfigError(f"Config operation {operation} must be a mapping.")
        provider = route.get("provider")
        model = route.get("model")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise ConfigError(f"Config operation {operation}.provider must be a non-empty string when present.")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"Config operation {operation}.model must be a non-empty string.")
        if provider is not None and provider.strip() not in _adapter_provider_names(providers):
            raise ConfigError(f"Config operation {operation} references unknown provider {provider!r}.")
        if provider is None and _policy_default_provider(providers) is None:
            raise ConfigError(
                f"Config operation {operation}.provider is required when providers.policy.default_provider is unset."
            )

    indexes = config["indexes"]
    for key in ("topic_page_size", "top_level_page_size"):
        if key not in indexes:
            continue
        value = indexes[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"Config key indexes.{key} must be a positive integer.")

    git = config["git"]
    for key in (
        "auto_commit",
        "commit_ingests",
        "commit_reviews",
        "commit_reindexes",
        "commit_topic_reorganizations",
        "allow_unrelated_changes",
        "require_clean_worktree_for_rewrites",
    ):
        if key in git and not isinstance(git[key], bool):
            raise ConfigError(f"Config key git.{key} must be a boolean.")

    privacy = config["privacy"]
    if not isinstance(privacy["cloud_llm_allowed"], bool):
        raise ConfigError("Config key privacy.cloud_llm_allowed must be a boolean.")
    if "require_confirmation_for_cloud_llm" in privacy and not isinstance(
        privacy["require_confirmation_for_cloud_llm"], bool
    ):
        raise ConfigError("Config key privacy.require_confirmation_for_cloud_llm must be a boolean.")
    _validate_string_list(privacy, "privacy.blocked_topics")
    _validate_string_list(privacy, "privacy.redact_patterns")
    for index, pattern in enumerate(privacy.get("redact_patterns", [])):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(f"Config key privacy.redact_patterns[{index}] is not a valid regex: {exc}") from exc


def _validate_positive_int(section: Mapping[str, Any], key: str, *, minimum: int) -> None:
    leaf = key.rsplit(".", maxsplit=1)[-1]
    if leaf not in section:
        return
    value = section[leaf]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"Config key {key} must be an integer >= {minimum}.")


def _validate_non_negative_number(section: Mapping[str, Any], key: str) -> None:
    leaf = key.rsplit(".", maxsplit=1)[-1]
    if leaf not in section:
        return
    value = section[leaf]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
        raise ConfigError(f"Config key {key} must be a number >= 0.")


def _validate_positive_number(section: Mapping[str, Any], key: str) -> None:
    leaf = key.rsplit(".", maxsplit=1)[-1]
    if leaf not in section:
        return
    value = section[leaf]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise ConfigError(f"Config key {key} must be a number > 0.")


def _validate_string_list(section: Mapping[str, Any], key: str) -> None:
    leaf = key.rsplit(".", maxsplit=1)[-1]
    value = section.get(leaf)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"Config key {key} must be a list of strings.")


def _validate_provider_policy(config: Mapping[str, Any]) -> None:
    providers = config["providers"]
    operations = config["operations"]
    if not isinstance(providers, Mapping) or not isinstance(operations, Mapping):
        return

    policy = providers.get("policy")
    if policy is None:
        return
    if not isinstance(policy, Mapping):
        raise ConfigError("Config section providers.policy must be a mapping when present.")

    adapter_names = _adapter_provider_names(providers)
    default_provider = policy.get("default_provider")
    if default_provider is not None:
        if not isinstance(default_provider, str) or not default_provider.strip():
            raise ConfigError("Config key providers.policy.default_provider must be a non-empty string when present.")
        if default_provider.strip() not in adapter_names:
            raise ConfigError(
                f"Config key providers.policy.default_provider references unknown provider {default_provider!r}."
            )

    fallback = policy.get("fallback", {})
    if fallback is None:
        fallback = {}
    if not isinstance(fallback, Mapping):
        raise ConfigError("Config section providers.policy.fallback must be a mapping when present.")

    for operation, entries in fallback.items():
        if operation not in REQUIRED_SECTION_KEYS["operations"]:
            raise ConfigError(f"Config providers.policy.fallback has unknown operation {operation!r}.")
        if not isinstance(entries, list):
            raise ConfigError(f"Config providers.policy.fallback.{operation} must be a list.")
        if len(entries) > MAX_PROVIDER_FALLBACKS_PER_OPERATION:
            raise ConfigError(
                f"Config providers.policy.fallback.{operation} must contain at most "
                f"{MAX_PROVIDER_FALLBACKS_PER_OPERATION} providers."
            )

        primary = _operation_provider(operations[operation], providers)
        sequence: list[str] = [primary] if primary is not None else []
        for index, entry in enumerate(entries):
            provider = _fallback_entry_provider(operation, index, entry)
            if provider not in adapter_names:
                raise ConfigError(
                    f"Config providers.policy.fallback.{operation}[{index}] references unknown provider {provider!r}."
                )
            sequence.append(provider)
            if isinstance(entry, Mapping):
                model = entry.get("model")
                if model is not None and (not isinstance(model, str) or not model.strip()):
                    raise ConfigError(
                        f"Config providers.policy.fallback.{operation}[{index}].model "
                        "must be a non-empty string when present."
                    )

        for left, right in zip(sequence, sequence[1:]):
            if left == right:
                raise ConfigError(
                    f"Config providers.policy.fallback.{operation} contains duplicate consecutive provider {left!r}."
                )
        if len(set(sequence)) != len(sequence):
            raise ConfigError(f"Config providers.policy.fallback.{operation} contains a provider cycle.")


def _adapter_provider_names(providers: Mapping[str, Any]) -> set[str]:
    return {
        str(provider_name)
        for provider_name, provider_config in providers.items()
        if provider_name not in PROVIDER_CONTROL_KEYS and isinstance(provider_config, Mapping)
    }


def _policy_default_provider(providers: Mapping[str, Any]) -> str | None:
    policy = providers.get("policy")
    if not isinstance(policy, Mapping):
        return None
    provider = policy.get("default_provider")
    if not isinstance(provider, str) or not provider.strip():
        return None
    return provider.strip()


def _operation_provider(route: Any, providers: Mapping[str, Any]) -> str | None:
    if not isinstance(route, Mapping):
        return None
    provider = route.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    return _policy_default_provider(providers)


def _fallback_entry_provider(operation: str, index: int, entry: Any) -> str:
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    if isinstance(entry, Mapping):
        provider = entry.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip()
    raise ConfigError(
        f"Config providers.policy.fallback.{operation}[{index}] must be a provider string "
        "or a mapping with provider."
    )
