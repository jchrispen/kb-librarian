"""Shared provider backend and credential-source seam helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


BACKEND_DIRECT_HTTP = "direct_http"
BACKEND_VENDOR_CLI = "vendor_cli"
BACKEND_OLLAMA = "ollama"
BACKEND_VLLM = "vllm"
BACKEND_LM_STUDIO = "lm_studio"
BACKEND_MOCK = "mock"

LOCAL_BACKENDS = {BACKEND_OLLAMA, BACKEND_VLLM, BACKEND_LM_STUDIO}

CREDENTIAL_SOURCE_API_KEY_ENV = "api_key_env"
CREDENTIAL_SOURCE_VENDOR_CLI = "vendor_cli"
CREDENTIAL_SOURCE_TOKEN_ENV = "token_env"
CREDENTIAL_SOURCE_COMMAND = "command"

SUPPORTED_CREDENTIAL_SOURCES = {
    CREDENTIAL_SOURCE_API_KEY_ENV,
    CREDENTIAL_SOURCE_VENDOR_CLI,
    CREDENTIAL_SOURCE_TOKEN_ENV,
    CREDENTIAL_SOURCE_COMMAND,
}


@dataclass(frozen=True)
class ProviderSeam:
    provider_name: str
    backend: str
    credential_source: str | None
    api_key_env: str | None = None
    token_env: str | None = None
    credential_command: str | None = None
    cli_command: str | None = None

    @property
    def diagnostic_credential_source(self) -> str:
        return self.credential_source or "not_applicable"


def validate_provider_seam_config(
    provider_name: str,
    provider_config: Mapping[str, Any],
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
) -> None:
    """Validate backend and credential-source settings for supported providers."""

    if provider_name in {"anthropic", "codex", "local", "mock"}:
        resolve_provider_seam(
            provider_name,
            provider_config,
            error_factory=error_factory,
            error_prefix=error_prefix,
        )


def resolve_provider_seam(
    provider_name: str,
    provider_config: Mapping[str, Any],
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
) -> ProviderSeam:
    """Resolve backend and credential-source defaults for one configured provider."""

    if not isinstance(provider_config, Mapping):
        raise error_factory(f"Config section {error_prefix} must be a mapping.")

    if provider_name == "anthropic":
        return _resolve_cloud_provider_seam(
            provider_name,
            provider_config,
            error_factory=error_factory,
            error_prefix=error_prefix,
            allow_token_env=True,
        )
    if provider_name == "codex":
        return _resolve_cloud_provider_seam(
            provider_name,
            provider_config,
            error_factory=error_factory,
            error_prefix=error_prefix,
            allow_token_env=False,
        )
    if provider_name == "local":
        return _resolve_local_provider_seam(
            provider_name,
            provider_config,
            error_factory=error_factory,
            error_prefix=error_prefix,
        )
    if provider_name == "mock":
        return _resolve_mock_provider_seam(
            provider_name,
            provider_config,
            error_factory=error_factory,
            error_prefix=error_prefix,
        )

    backend = _optional_string(provider_config, "backend", error_factory=error_factory, error_prefix=error_prefix)
    credential_source = _optional_string(
        provider_config,
        "credential_source",
        error_factory=error_factory,
        error_prefix=error_prefix,
    )
    return ProviderSeam(provider_name=provider_name, backend=backend or provider_name, credential_source=credential_source)


def provider_seam_supported_for_runtime(seam: ProviderSeam) -> tuple[bool, str | None]:
    """Return whether the current implementation can execute the resolved seam."""

    if seam.provider_name == "anthropic":
        if seam.backend == BACKEND_DIRECT_HTTP and seam.credential_source == CREDENTIAL_SOURCE_API_KEY_ENV:
            return True, None
        if seam.backend == BACKEND_VENDOR_CLI and seam.credential_source in {
            CREDENTIAL_SOURCE_VENDOR_CLI,
            CREDENTIAL_SOURCE_TOKEN_ENV,
        }:
            return True, None
        return False, _unsupported_runtime_message(seam)
    if seam.provider_name == "codex":
        if seam.backend == BACKEND_DIRECT_HTTP and seam.credential_source == CREDENTIAL_SOURCE_API_KEY_ENV:
            return True, None
        return False, _unsupported_runtime_message(seam)
    if seam.provider_name == "local":
        if seam.backend in LOCAL_BACKENDS and seam.credential_source is None:
            return True, None
        return False, _unsupported_runtime_message(seam)
    if seam.provider_name == "mock":
        if seam.backend == BACKEND_MOCK and seam.credential_source is None:
            return True, None
        return False, _unsupported_runtime_message(seam)
    return False, f"Provider {seam.provider_name!r} is not supported by this CLI."


def _resolve_cloud_provider_seam(
    provider_name: str,
    provider_config: Mapping[str, Any],
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
    allow_token_env: bool,
) -> ProviderSeam:
    backend = _optional_string(provider_config, "backend", error_factory=error_factory, error_prefix=error_prefix)
    if backend is None:
        backend = BACKEND_DIRECT_HTTP
    if backend not in {BACKEND_DIRECT_HTTP, BACKEND_VENDOR_CLI}:
        raise error_factory(
            f"Config key {error_prefix}.backend must be one of: {BACKEND_DIRECT_HTTP}, {BACKEND_VENDOR_CLI}."
        )

    credential_source = _optional_string(
        provider_config,
        "credential_source",
        error_factory=error_factory,
        error_prefix=error_prefix,
    )
    if credential_source is None:
        credential_source = (
            CREDENTIAL_SOURCE_API_KEY_ENV if backend == BACKEND_DIRECT_HTTP else CREDENTIAL_SOURCE_VENDOR_CLI
        )
    if credential_source not in SUPPORTED_CREDENTIAL_SOURCES:
        allowed_sources = [
            CREDENTIAL_SOURCE_API_KEY_ENV,
            CREDENTIAL_SOURCE_VENDOR_CLI,
            CREDENTIAL_SOURCE_TOKEN_ENV,
            CREDENTIAL_SOURCE_COMMAND,
        ]
        raise error_factory(
            f"Config key {error_prefix}.credential_source must be one of: {', '.join(allowed_sources)}."
        )

    api_key_env = _optional_string(provider_config, "api_key_env", error_factory=error_factory, error_prefix=error_prefix)
    token_env = _optional_string(provider_config, "token_env", error_factory=error_factory, error_prefix=error_prefix)
    cli_command = _optional_string(provider_config, "cli_command", error_factory=error_factory, error_prefix=error_prefix)
    credential_command = _optional_string(
        provider_config,
        "credential_command",
        error_factory=error_factory,
        error_prefix=error_prefix,
    )

    allowed_sources = {
        BACKEND_DIRECT_HTTP: {CREDENTIAL_SOURCE_API_KEY_ENV, CREDENTIAL_SOURCE_COMMAND},
        BACKEND_VENDOR_CLI: {CREDENTIAL_SOURCE_VENDOR_CLI},
    }
    if allow_token_env:
        allowed_sources[BACKEND_VENDOR_CLI].add(CREDENTIAL_SOURCE_TOKEN_ENV)

    if credential_source not in allowed_sources[backend]:
        allowed_text = ", ".join(sorted(allowed_sources[backend]))
        raise error_factory(
            f"Config key {error_prefix}.credential_source={credential_source!r} is not supported with "
            f"backend {backend!r}; allowed values: {allowed_text}."
        )

    if credential_source == CREDENTIAL_SOURCE_API_KEY_ENV:
        if api_key_env is None:
            raise error_factory(f"Config key {error_prefix}.api_key_env is required.")
        _reject_conflicting_fields(
            error_factory,
            error_prefix,
            credential_source,
            configured_fields={
                "token_env": token_env,
                "credential_command": credential_command,
                "cli_command": cli_command,
            },
        )
    elif credential_source == CREDENTIAL_SOURCE_VENDOR_CLI:
        _reject_conflicting_fields(
            error_factory,
            error_prefix,
            credential_source,
            configured_fields={
                "api_key_env": api_key_env,
                "token_env": token_env,
                "credential_command": credential_command,
            },
        )
    elif credential_source == CREDENTIAL_SOURCE_TOKEN_ENV:
        if token_env is None:
            raise error_factory(f"Config key {error_prefix}.token_env is required.")
        _reject_conflicting_fields(
            error_factory,
            error_prefix,
            credential_source,
            configured_fields={"api_key_env": api_key_env, "credential_command": credential_command},
        )
    elif credential_source == CREDENTIAL_SOURCE_COMMAND:
        if credential_command is None:
            raise error_factory(f"Config key {error_prefix}.credential_command is required.")
        _reject_conflicting_fields(
            error_factory,
            error_prefix,
            credential_source,
            configured_fields={
                "api_key_env": api_key_env,
                "token_env": token_env,
                "cli_command": cli_command,
            },
        )

    if backend != BACKEND_VENDOR_CLI and cli_command is not None:
        raise error_factory(f"Config key {error_prefix}.cli_command is supported only with backend 'vendor_cli'.")

    return ProviderSeam(
        provider_name=provider_name,
        backend=backend,
        credential_source=credential_source,
        api_key_env=api_key_env,
        token_env=token_env,
        credential_command=credential_command,
        cli_command=cli_command,
    )


def _resolve_local_provider_seam(
    provider_name: str,
    provider_config: Mapping[str, Any],
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
) -> ProviderSeam:
    backend = _optional_string(provider_config, "backend", error_factory=error_factory, error_prefix=error_prefix)
    if backend is None:
        backend = BACKEND_OLLAMA
    if backend not in LOCAL_BACKENDS:
        allowed_backends = ", ".join(sorted(LOCAL_BACKENDS))
        raise error_factory(f"Config key {error_prefix}.backend must be one of: {allowed_backends}.")
    if provider_config.get("credential_source") is not None:
        raise error_factory(
            f"Config key {error_prefix}.credential_source is not supported for local backends."
        )
    _reject_conflicting_fields(
        error_factory,
        error_prefix,
        "not_applicable",
        configured_fields={
            "api_key_env": _optional_string(provider_config, "api_key_env", error_factory=error_factory, error_prefix=error_prefix),
            "token_env": _optional_string(provider_config, "token_env", error_factory=error_factory, error_prefix=error_prefix),
            "cli_command": _optional_string(provider_config, "cli_command", error_factory=error_factory, error_prefix=error_prefix),
            "credential_command": _optional_string(
                provider_config,
                "credential_command",
                error_factory=error_factory,
                error_prefix=error_prefix,
            ),
        },
        unsupported_label="local backends",
    )
    return ProviderSeam(provider_name=provider_name, backend=backend, credential_source=None)


def _resolve_mock_provider_seam(
    provider_name: str,
    provider_config: Mapping[str, Any],
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
) -> ProviderSeam:
    backend = _optional_string(provider_config, "backend", error_factory=error_factory, error_prefix=error_prefix)
    if backend is not None and backend != BACKEND_MOCK:
        raise error_factory(f"Config key {error_prefix}.backend must be '{BACKEND_MOCK}' when present.")
    if provider_config.get("credential_source") is not None:
        raise error_factory(f"Config key {error_prefix}.credential_source is not supported for mock providers.")
    _reject_conflicting_fields(
        error_factory,
        error_prefix,
        "not_applicable",
        configured_fields={
            "api_key_env": _optional_string(provider_config, "api_key_env", error_factory=error_factory, error_prefix=error_prefix),
            "token_env": _optional_string(provider_config, "token_env", error_factory=error_factory, error_prefix=error_prefix),
            "cli_command": _optional_string(provider_config, "cli_command", error_factory=error_factory, error_prefix=error_prefix),
            "credential_command": _optional_string(
                provider_config,
                "credential_command",
                error_factory=error_factory,
                error_prefix=error_prefix,
            ),
        },
        unsupported_label="mock providers",
    )
    return ProviderSeam(provider_name=provider_name, backend=backend or BACKEND_MOCK, credential_source=None)


def _optional_string(
    provider_config: Mapping[str, Any],
    key: str,
    *,
    error_factory: Callable[[str], Exception],
    error_prefix: str,
) -> str | None:
    if key not in provider_config or provider_config[key] is None:
        return None
    value = provider_config[key]
    if not isinstance(value, str) or not value.strip():
        raise error_factory(f"Config key {error_prefix}.{key} must be a non-empty string when present.")
    return value.strip()


def _reject_conflicting_fields(
    error_factory: Callable[[str], Exception],
    error_prefix: str,
    credential_source: str,
    *,
    configured_fields: Mapping[str, str | None],
    unsupported_label: str | None = None,
) -> None:
    for key, value in configured_fields.items():
        if value is None:
            continue
        if unsupported_label is not None:
            raise error_factory(f"Config key {error_prefix}.{key} is not supported for {unsupported_label}.")
        raise error_factory(
            f"Config key {error_prefix}.{key} conflicts with credential_source {credential_source!r}."
        )


def _unsupported_runtime_message(seam: ProviderSeam) -> str:
    if seam.provider_name in {"anthropic", "codex"}:
        if seam.provider_name == "anthropic":
            return (
                f"Provider {seam.provider_name!r} is configured for backend={seam.backend!r} and "
                f"credential_source={seam.diagnostic_credential_source!r}, but that seam is not implemented yet. "
                f"Supported Anthropic seams are backend={BACKEND_DIRECT_HTTP!r} with "
                f"credential_source={CREDENTIAL_SOURCE_API_KEY_ENV!r}, or backend={BACKEND_VENDOR_CLI!r} "
                f"with credential_source={CREDENTIAL_SOURCE_VENDOR_CLI!r} or {CREDENTIAL_SOURCE_TOKEN_ENV!r}."
            )
        return (
            f"Provider {seam.provider_name!r} is configured for backend={seam.backend!r} and "
            f"credential_source={seam.diagnostic_credential_source!r}, but that seam is not implemented yet. "
            f"Use backend={BACKEND_DIRECT_HTTP!r} with credential_source={CREDENTIAL_SOURCE_API_KEY_ENV!r} for now."
        )
    if seam.provider_name == "local":
        return "Supported local backends are: ollama, vllm, lm_studio."
    if seam.provider_name == "mock":
        return "Mock provider does not support custom backend or credential-source settings."
    return f"Provider {seam.provider_name!r} has an unsupported runtime configuration."
