from __future__ import annotations

import pytest

from kb_librarian.config import default_config
from kb_librarian.errors import ProviderError
from kb_librarian.privacy import enforce_provider_privacy, redact_payload, redact_text


def test_redact_text_applies_configured_patterns(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["redact_patterns"] = [r"secret-[0-9]+"]

    assert redact_text(config, "token secret-123 should not leave") == "token [REDACTED] should not leave"


def test_redact_payload_recurses_through_provider_payload(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["redact_patterns"] = [r"api_key=[A-Za-z0-9]+"]

    payload = redact_payload(
        config,
        {
            "summary": "uses api_key=abc123",
            "items": ["clean", "api_key=def456"],
        },
    )

    assert payload == {
        "summary": "uses [REDACTED]",
        "items": ["clean", "[REDACTED]"],
    }


def test_enforce_provider_privacy_blocks_cloud_when_disabled(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["cloud_llm_allowed"] = False

    with pytest.raises(ProviderError, match="blocks cloud provider"):
        enforce_provider_privacy(config, provider_name="anthropic", operation="context:synthesize")


def test_enforce_provider_privacy_blocks_cloud_for_blocked_topic(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["blocked_topics"] = ["sensitive/topic"]

    with pytest.raises(ProviderError, match="sensitive/topic"):
        enforce_provider_privacy(
            config,
            provider_name="codex",
            operation="compact:synthesize",
            topics=["sensitive/topic"],
        )


def test_enforce_provider_privacy_allows_local_provider(tmp_path):
    config = default_config(tmp_path)
    config["privacy"]["cloud_llm_allowed"] = False
    config["privacy"]["blocked_topics"] = ["sensitive"]

    enforce_provider_privacy(
        config,
        provider_name="local",
        operation="compact:synthesize",
        topics=["sensitive"],
    )
