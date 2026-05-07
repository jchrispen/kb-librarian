from __future__ import annotations

import io
import urllib.error

import pytest

from kb_librarian.errors import ProviderError
from kb_librarian.provider_retry import (
    FailureClassification,
    RetryEvent,
    RetryPolicy,
    call_with_retry,
    classify_provider_failure,
    retry_policy_from_config,
)


def test_retry_policy_from_config_uses_defaults_when_missing(tmp_path):
    policy = retry_policy_from_config({"data_dir": str(tmp_path), "providers": {}})
    assert policy.max_attempts == 3
    assert policy.base_delay_seconds == 0.25
    assert policy.max_delay_seconds == 2.0
    assert policy.jitter_seconds == 0.1


def test_retry_policy_from_config_applies_bounds():
    policy = retry_policy_from_config(
        {
            "providers": {
                "retry": {
                    "max_attempts": 5,
                    "base_delay_seconds": 1.2,
                    "max_delay_seconds": 0.4,
                    "jitter_seconds": 0.0,
                }
            }
        }
    )
    assert policy.max_attempts == 5
    assert policy.base_delay_seconds == 1.2
    assert policy.max_delay_seconds == 1.2
    assert policy.jitter_seconds == 0.0


def test_classify_provider_failure_detects_transient_http_429():
    cause = urllib.error.HTTPError(
        url="https://api.example.test",
        code=429,
        msg="rate limited",
        hdrs=None,
        fp=io.BytesIO(b"retry later"),
    )
    error = ProviderError("provider failed")
    error.__cause__ = cause

    classification = classify_provider_failure(error)

    assert classification.transient is True
    assert classification.kind == "http_error"
    assert classification.detail == "http_status=429"


def test_classify_provider_failure_marks_validation_error_permanent():
    classification = classify_provider_failure(ProviderError("Provider response was not valid JSON"))
    assert classification.transient is False
    assert classification.kind == "provider_error"


def test_call_with_retry_retries_transient_failures_then_succeeds():
    attempts = {"count": 0}
    slept: list[float] = []
    retries: list[RetryEvent] = []

    def call() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ProviderError("timeout while contacting provider")
        return "ok"

    result = call_with_retry(
        "ingest:extract",
        call,
        policy=RetryPolicy(max_attempts=4, base_delay_seconds=0.2, max_delay_seconds=0.5, jitter_seconds=0.0),
        on_retry=retries.append,
        sleep=lambda seconds: slept.append(seconds),
    )

    assert result == "ok"
    assert attempts["count"] == 3
    assert [round(value, 3) for value in slept] == [0.2, 0.4]
    assert [(event.attempt, event.max_attempts) for event in retries] == [(1, 4), (2, 4)]


def test_call_with_retry_stops_on_permanent_failure_without_retry():
    retries: list[RetryEvent] = []
    finals: list[RetryEvent] = []

    def call() -> str:
        raise ProviderError("Provider response field candidates must be a list")

    with pytest.raises(ProviderError, match="stopped without retry"):
        call_with_retry(
            "ingest:extract",
            call,
            policy=RetryPolicy(max_attempts=5, base_delay_seconds=0.1, max_delay_seconds=1.0, jitter_seconds=0.0),
            on_retry=retries.append,
            on_final_failure=finals.append,
            sleep=lambda _: None,
        )

    assert retries == []
    assert len(finals) == 1
    assert finals[0].classification == FailureClassification(
        kind="provider_error",
        transient=False,
        detail="non_transient",
    )


def test_call_with_retry_raises_after_max_attempts_for_transient_failure():
    retries: list[RetryEvent] = []
    finals: list[RetryEvent] = []

    def call() -> str:
        raise ProviderError("service unavailable")

    with pytest.raises(ProviderError, match="exhausted retries after 3 attempts"):
        call_with_retry(
            "context:synthesize",
            call,
            policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.2, max_delay_seconds=0.3, jitter_seconds=0.0),
            on_retry=retries.append,
            on_final_failure=finals.append,
            sleep=lambda _: None,
        )

    assert len(retries) == 2
    assert len(finals) == 1
    assert finals[0].attempt == 3
