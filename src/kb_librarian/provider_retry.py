"""Provider retry/backoff helpers."""

from __future__ import annotations

import random
import socket
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar

from kb_librarian.errors import ProviderError


T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.25
DEFAULT_MAX_DELAY_SECONDS = 2.0
DEFAULT_JITTER_SECONDS = 0.1

_TRANSIENT_MESSAGE_TOKENS = (
    "429",
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "econnreset",
    "econnrefused",
    "overloaded",
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter_seconds: float


@dataclass(frozen=True)
class FailureClassification:
    kind: str
    transient: bool
    detail: str


@dataclass(frozen=True)
class RetryEvent:
    operation: str
    attempt: int
    max_attempts: int
    delay_seconds: float
    classification: FailureClassification
    error: ProviderError


def retry_policy_from_config(config: Mapping[str, Any]) -> RetryPolicy:
    providers = config.get("providers")
    retry: Mapping[str, Any] = {}
    if isinstance(providers, Mapping):
        configured = providers.get("retry")
        if isinstance(configured, Mapping):
            retry = configured

    max_attempts = _coerce_int(retry.get("max_attempts"), default=DEFAULT_MAX_ATTEMPTS, minimum=1)
    base_delay_seconds = _coerce_float(retry.get("base_delay_seconds"), default=DEFAULT_BASE_DELAY_SECONDS, minimum=0.0)
    max_delay_seconds = _coerce_float(retry.get("max_delay_seconds"), default=DEFAULT_MAX_DELAY_SECONDS, minimum=0.0)
    jitter_seconds = _coerce_float(retry.get("jitter_seconds"), default=DEFAULT_JITTER_SECONDS, minimum=0.0)
    if max_delay_seconds < base_delay_seconds:
        max_delay_seconds = base_delay_seconds

    return RetryPolicy(
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        jitter_seconds=jitter_seconds,
    )


def classify_provider_failure(error: ProviderError) -> FailureClassification:
    for cause in _exception_chain(error):
        if isinstance(cause, urllib.error.HTTPError):
            code = int(cause.code)
            transient = code == 429 or 500 <= code <= 599
            return FailureClassification(
                kind="http_error",
                transient=transient,
                detail=f"http_status={code}",
            )
        if isinstance(cause, (TimeoutError, socket.timeout)):
            return FailureClassification(kind="timeout", transient=True, detail=type(cause).__name__)
        if isinstance(cause, urllib.error.URLError):
            return FailureClassification(kind="transport_error", transient=True, detail=str(cause.reason))

    lowered = str(error).lower()
    for token in _TRANSIENT_MESSAGE_TOKENS:
        if token in lowered:
            return FailureClassification(kind="transient_message", transient=True, detail=token)
    return FailureClassification(kind="provider_error", transient=False, detail="non_transient")


def call_with_retry(
    operation: str,
    call: Callable[[], T],
    *,
    policy: RetryPolicy,
    on_retry: Callable[[RetryEvent], None] | None = None,
    on_final_failure: Callable[[RetryEvent], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    random_float: Callable[[], float] = random.random,
) -> T:
    attempts = max(1, int(policy.max_attempts))
    attempt = 1
    while True:
        try:
            return call()
        except ProviderError as error:
            classification = classify_provider_failure(error)
            delay_seconds = _retry_delay(policy, attempt, random_float=random_float)
            event = RetryEvent(
                operation=operation,
                attempt=attempt,
                max_attempts=attempts,
                delay_seconds=delay_seconds,
                classification=classification,
                error=error,
            )
            retryable = classification.transient and attempt < attempts
            if retryable:
                if on_retry is not None:
                    on_retry(event)
                if delay_seconds > 0:
                    sleep(delay_seconds)
                attempt += 1
                continue

            if on_final_failure is not None:
                on_final_failure(event)
            if classification.transient and attempt >= attempts:
                raise ProviderError(
                    "Provider operation "
                    f"{operation!r} exhausted retries after {attempt} attempts "
                    f"({classification.kind}: {classification.detail}). Last error: {error}"
                ) from error
            raise ProviderError(
                "Provider operation "
                f"{operation!r} stopped without retry "
                f"({classification.kind}: {classification.detail}). Error: {error}"
            ) from error


def _retry_delay(policy: RetryPolicy, attempt: int, *, random_float: Callable[[], float]) -> float:
    exponent = max(0, attempt - 1)
    base = policy.base_delay_seconds * (2**exponent)
    bounded = min(policy.max_delay_seconds, base)
    jitter = policy.jitter_seconds * max(0.0, random_float()) if policy.jitter_seconds > 0 else 0.0
    return max(0.0, bounded + jitter)


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = [error]
    current = error
    while True:
        cause = current.__cause__
        if cause is None or cause in chain:
            break
        chain.append(cause)
        current = cause
    return chain


def _coerce_int(value: Any, *, default: int, minimum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, value)


def _coerce_float(value: Any, *, default: float, minimum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(minimum, float(value))
    return default
