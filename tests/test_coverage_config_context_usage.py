from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from kb_librarian.config import (
    ConfigError,
    _validate_positive_int,
    _operation_provider,
    _policy_default_provider,
    _validate_provider_policy,
    default_config,
    load_config,
    read_config_file,
    render_config,
    resolve_data_dir,
    validate_config,
)
from kb_librarian.context import (
    ContextSelection,
    _apply_context_budget,
    _apply_explore_budget,
    _confidence_score,
    _coerce_string_list,
    _load_backlinks,
    _log_provider_fallback_event,
    _recency_score,
    _related_explore_selection,
    _related_note_ids,
    _score_explore_record,
    _score_record,
    _status_score,
    _trim_to_tokens,
    _trust_flags,
    build_context,
    build_explore,
)
from kb_librarian.errors import KBLibrarianError
from kb_librarian.notes import Note
from kb_librarian.provider_seams import (
    BACKEND_DIRECT_HTTP,
    BACKEND_VENDOR_CLI,
    CREDENTIAL_SOURCE_API_KEY_ENV,
    CREDENTIAL_SOURCE_COMMAND,
    CREDENTIAL_SOURCE_TOKEN_ENV,
    CREDENTIAL_SOURCE_VENDOR_CLI,
    ProviderSeam,
    provider_seam_supported_for_runtime,
    resolve_provider_seam,
)
from kb_librarian.providers import ProviderFallbackEvent
from kb_librarian.storage import NoteRecord
from kb_librarian.usage import (
    _clean_mapping,
    _normalized_query,
    _now_iso,
    _parse_timestamp,
    _returned_note_ids,
    _miss_reason,
    _read_jsonl,
    log_search_miss,
    log_note_use,
    log_retrieval,
    note_usage_counts,
    parse_since,
    promote_search_misses,
    refresh_usage_stats,
    render_usage_summary,
    summarize_usage,
)


def _minimal_selection(note_id: str, *, excerpt_words: int = 8, score: float = 10.0) -> ContextSelection:
    return ContextSelection(
        note_id=note_id,
        title=f"Title {note_id}",
        summary="Summary",
        topic="agent-systems",
        knowledge_type="technique",
        status="active",
        confidence="high",
        updated=date.today().isoformat(),
        path=f"topics/agent-systems/{note_id}.md",
        excerpt=" ".join(f"word{index}" for index in range(excerpt_words)),
        retrieval_phrases=["task context"],
        tags=["agent"],
        score=score,
        reasons=["title"],
        trust_flags=[],
    )


def _record(tmp_path, *, frontmatter: dict[str, object], body: str = "body text") -> NoteRecord:
    path = tmp_path / "topics" / str(frontmatter.get("topic", "agent-systems")) / f"{frontmatter['id']}.md"
    return NoteRecord(path=path, topic_path=str(frontmatter.get("topic", "agent-systems")), note=Note(frontmatter, body))


def test_config_file_reader_reports_malformed_empty_and_non_mapping_files(tmp_path):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("data_dir: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed YAML"):
        read_config_file(malformed)

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        read_config_file(empty)

    sequence = tmp_path / "sequence.yaml"
    sequence.write_text("- not\n- mapping\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML mapping"):
        read_config_file(sequence)


def test_config_file_reader_wraps_os_errors(tmp_path, monkeypatch):
    unreadable = tmp_path / "config.yaml"

    def _raise_os_error(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(type(unreadable), "read_text", _raise_os_error)

    with pytest.raises(ConfigError, match="Could not read config file"):
        read_config_file(unreadable)


def test_load_config_uses_defaults_when_no_config_file_exists(tmp_path):
    data_dir = tmp_path / "library"

    loaded = load_config(data_dir, env={})

    assert loaded["data_dir"] == str(data_dir)


def test_resolve_data_dir_reads_control_dir_and_configured_library(tmp_path):
    control_dir = tmp_path / ".kb"
    assert resolve_data_dir(control_dir, env={}, default_data_dir=tmp_path) == control_dir / ".library"

    configured = default_config(tmp_path / "configured-library")
    config_path = control_dir / "config.yaml"
    # Keep the fixture grounded in the default shape while still exercising
    # the configured data-dir branch.
    config_path.parent.mkdir()
    config_path.write_text(render_config(configured), encoding="utf-8")

    assert resolve_data_dir(env={}, default_data_dir=tmp_path) == tmp_path / "configured-library"

    config_path.write_text("data_dir: ''\n", encoding="utf-8")
    assert resolve_data_dir(env={}, default_data_dir=tmp_path) == tmp_path / ".kb" / ".library"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda config: config.clear(), "missing required top-level key"),
        (lambda config: config.__setitem__("data_dir", " "), "data_dir"),
        (lambda config: config.__setitem__("operations", {**config["operations"], "extract": []}), "operation extract"),
        (
            lambda config: config["operations"].__setitem__("extract", {"provider": " ", "model": "model"}),
            "extract.provider",
        ),
        (
            lambda config: config["operations"].__setitem__("extract", {"provider": "anthropic", "model": ""}),
            "extract.model",
        ),
        (
            lambda config: config["operations"].__setitem__("extract", {"provider": "missing", "model": "model"}),
            "unknown provider",
        ),
        (lambda config: config["indexes"].__setitem__("topic_page_size", True), "indexes.topic_page_size"),
    ],
)
def test_validate_config_rejects_edge_shape_errors(tmp_path, mutate, message):
    config = default_config(tmp_path)
    mutate(config)

    with pytest.raises(ConfigError, match=message):
        validate_config(config)


def test_validate_config_covers_optional_numeric_absent_and_invalid_edges(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["anthropic"].pop("timeout_seconds", None)
    config["providers"]["retry"].pop("base_delay_seconds", None)
    config["providers"]["retry"].pop("max_delay_seconds", None)
    config["providers"]["retry"].pop("jitter_seconds", None)
    validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["anthropic"]["timeout_seconds"] = True
    with pytest.raises(ConfigError, match="timeout_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["retry"]["base_delay_seconds"] = -0.1
    with pytest.raises(ConfigError, match="base_delay_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["retrieval"]["default_budget_tokens"] = True
    with pytest.raises(ConfigError, match="default_budget_tokens"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"] = None
    validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"extract": []}
    validate_config(config)
    _validate_positive_int({}, "section.value", minimum=1)


def test_validate_config_rejects_non_mapping_root():
    with pytest.raises(ConfigError, match="YAML mapping"):
        validate_config([])


@pytest.mark.parametrize(
    ("provider_update", "message"),
    [
        ({"anthropic": []}, "providers.anthropic"),
        ({"codex": []}, "providers.codex"),
        ({"codex": {"base_url": "", "backend": "direct_http", "credential_source": "api_key_env", "api_key_env": "OPENAI_API_KEY"}}, "codex.base_url"),
        ({"codex": {"base_url": "ftp://example.test", "backend": "direct_http", "credential_source": "api_key_env", "api_key_env": "OPENAI_API_KEY"}}, "http\\(s\\) URL"),
        ({"local": []}, "providers.local"),
        ({"local": {"backend": "ollama", "base_url": ""}}, "local.base_url"),
        ({"local": {"backend": "ollama", "base_url": "unix:///tmp/socket"}}, "http\\(s\\) URL"),
        ({"retry": []}, "providers.retry"),
        ({"custom": []}, "providers.custom"),
    ],
)
def test_validate_config_rejects_provider_section_edges(tmp_path, provider_update, message):
    config = default_config(tmp_path)
    for key, value in provider_update.items():
        config["providers"][key] = value

    with pytest.raises(ConfigError, match=message):
        validate_config(config)


def test_validate_config_covers_codex_local_and_privacy_tail_edges(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["codex"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
        "base_url": "https://proxy.example.test/v1",
    }
    with pytest.raises(ConfigError, match="must remain"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
        "base_url": "https://api.openai.com/v1",
        "organization": "org-test",
    }
    with pytest.raises(ConfigError, match="providers.codex.organization is supported only"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["local"] = {
        "backend": "ollama",
        "base_url": "http://localhost:11434",
        "timeout_seconds": True,
    }
    with pytest.raises(ConfigError, match="local.timeout_seconds"):
        validate_config(config)

    config = default_config(tmp_path)
    config["privacy"]["require_confirmation_for_cloud_llm"] = "yes"
    with pytest.raises(ConfigError, match="require_confirmation"):
        validate_config(config)


def test_validate_config_rejects_policy_without_default_provider_for_implicit_operation(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["policy"]["default_provider"] = None
    del config["operations"]["extract"]["provider"]

    with pytest.raises(ConfigError, match="extract.provider is required"):
        validate_config(config)


def test_validate_config_accepts_absent_optional_index_and_retry_sections(tmp_path):
    config = default_config(tmp_path)
    del config["indexes"]["topic_page_size"]
    del config["indexes"]["top_level_page_size"]
    del config["providers"]["retry"]

    validate_config(config)


def test_validate_config_accepts_absent_optional_codex_and_local_sections(tmp_path):
    config = default_config(tmp_path)
    del config["providers"]["codex"]
    del config["providers"]["local"]

    validate_config(config)


def test_validate_config_rejects_empty_policy_default_and_project_edges(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["policy"]["default_provider"] = ""
    with pytest.raises(ConfigError, match="default_provider"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"]["project"] = ""
    with pytest.raises(ConfigError, match="providers.codex.project"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["codex"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
        "base_url": "https://api.openai.com/v1",
        "project": "proj-test",
        "timeout_seconds": 120,
    }
    with pytest.raises(ConfigError, match="providers.codex.project is supported only"):
        validate_config(config)


def test_config_policy_helpers_cover_non_mapping_and_default_provider_edges(tmp_path):
    _validate_provider_policy({"providers": [], "operations": {}})
    _validate_provider_policy({"providers": {}, "operations": []})
    assert _policy_default_provider({"policy": []}) is None
    assert _operation_provider([], {"policy": {"default_provider": "codex"}}) is None
    assert _operation_provider({}, {"policy": {"default_provider": "codex"}}) == "codex"

    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"model": "mock-model"}
    config["providers"]["policy"]["default_provider"] = "mock"
    validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["providers"]["policy"]["fallback"] = {"extract": [{"provider": "   "}]}
    with pytest.raises(ConfigError, match="must be a provider string"):
        validate_config(config)


@pytest.mark.parametrize(
    ("fallback", "message"),
    [
        (None, None),
        ([], "providers.policy.fallback"),
        ({"extract": "local"}, "fallback.extract must be a list"),
        ({"extract": ["local", "codex", "mock", "custom", "another"]}, "at most"),
        ({"extract": [{"provider": "local", "model": ""}]}, "model"),
        ({"extract": [42]}, "must be a provider string"),
    ],
)
def test_validate_config_provider_policy_fallback_edges(tmp_path, fallback, message):
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["providers"]["custom"] = {"backend": "custom"}
    config["providers"]["another"] = {"backend": "another"}
    config["providers"]["policy"]["fallback"] = fallback

    if message is None:
        validate_config(config)
    else:
        with pytest.raises(ConfigError, match=message):
            validate_config(config)


def test_validate_config_rejects_retrieval_and_policy_tail_edges(tmp_path):
    config = default_config(tmp_path)
    config["retrieval"]["embedding_index_path"] = " "
    with pytest.raises(ConfigError, match="embedding_index_path"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"] = []
    with pytest.raises(ConfigError, match="providers.policy"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"extract": ["codex", "local", "codex"]}
    with pytest.raises(ConfigError, match="provider cycle"):
        validate_config(config)

    config = default_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {"extract": ["anthropic"]}
    with pytest.raises(ConfigError, match="duplicate consecutive"):
        validate_config(config)


def test_provider_seam_runtime_reports_unsupported_custom_provider_and_command_source():
    custom_supported, custom_message = provider_seam_supported_for_runtime(
        ProviderSeam("custom", "custom", None)
    )
    command_supported, command_message = provider_seam_supported_for_runtime(
        ProviderSeam("anthropic", BACKEND_DIRECT_HTTP, CREDENTIAL_SOURCE_COMMAND, credential_command="secret-tool")
    )

    assert custom_supported is False
    assert "not supported by this CLI" in str(custom_message)
    assert command_supported is False
    assert "not implemented yet" in str(command_message)


def test_validate_config_accepts_direct_http_command_credentials_but_runtime_flags_unimplemented(tmp_path):
    config = default_config(tmp_path)
    config["providers"]["anthropic"] = {
        "backend": BACKEND_DIRECT_HTTP,
        "credential_source": CREDENTIAL_SOURCE_COMMAND,
        "credential_command": "secret-tool lookup anthropic",
    }

    validate_config(config)
    supported, message = provider_seam_supported_for_runtime(
        ProviderSeam("anthropic", BACKEND_DIRECT_HTTP, CREDENTIAL_SOURCE_COMMAND, credential_command="secret-tool")
    )

    assert supported is False
    assert "Supported Anthropic seams" in str(message)


def test_provider_seam_runtime_supports_codex_vendor_cli_and_rejects_token_env():
    supported, message = provider_seam_supported_for_runtime(
        ProviderSeam("codex", BACKEND_VENDOR_CLI, "vendor_cli")
    )
    unsupported, unsupported_message = provider_seam_supported_for_runtime(
        ProviderSeam("codex", BACKEND_VENDOR_CLI, "token_env", token_env="CODEX_TOKEN")
    )

    assert (supported, message) == (True, None)
    assert unsupported is False
    assert "Supported Codex seams" in str(unsupported_message)


def test_provider_seam_resolver_rejects_vendor_cli_field_conflicts_and_cli_command_backend():
    with pytest.raises(ConfigError, match="api_key_env"):
        resolve_provider_seam(
            "codex",
            {
                "backend": BACKEND_VENDOR_CLI,
                "credential_source": CREDENTIAL_SOURCE_VENDOR_CLI,
                "api_key_env": "OPENAI_API_KEY",
            },
            error_factory=ConfigError,
            error_prefix="providers.codex",
        )

    with pytest.raises(ConfigError, match="cli_command"):
        resolve_provider_seam(
            "anthropic",
            {
                "backend": BACKEND_DIRECT_HTTP,
                "credential_source": CREDENTIAL_SOURCE_API_KEY_ENV,
                "api_key_env": "ANTHROPIC_API_KEY",
                "cli_command": "claude",
            },
            error_factory=ConfigError,
            error_prefix="providers.anthropic",
        )


def test_provider_seam_resolver_rejects_missing_command_credentials():
    with pytest.raises(ConfigError, match="credential_command is required"):
        resolve_provider_seam(
            "anthropic",
            {
                "backend": BACKEND_DIRECT_HTTP,
                "credential_source": CREDENTIAL_SOURCE_COMMAND,
            },
            error_factory=ConfigError,
            error_prefix="providers.anthropic",
        )

    with pytest.raises(ConfigError, match="token_env is required"):
        resolve_provider_seam(
            "anthropic",
            {
                "backend": BACKEND_VENDOR_CLI,
                "credential_source": CREDENTIAL_SOURCE_TOKEN_ENV,
            },
            error_factory=ConfigError,
            error_prefix="providers.anthropic",
        )


def test_context_scoring_filters_weak_trust_flags_and_scores_risk_branches(tmp_path):
    base = {
        "id": "2026-06-01-risk-note",
        "title": "Unrelated title",
        "summary": "minimal weak match",
        "topic": "agent-systems",
        "created": "2026-06-01",
        "updated": "not-a-date",
        "knowledge_type": "fact",
        "status": "needs-review",
        "confidence": "low",
        "staleness_risk": "high",
        "retrieval_phrases": ["weak"],
        "tags": ["misc"],
    }

    weak = _score_record(
        task="weak",
        tokens=["weak"],
        mode="research",
        record=_record(tmp_path, frontmatter=base),
        data_dir=tmp_path,
        usage_count=0,
    )
    strong_frontmatter = {**base, "title": "Strong task context", "summary": "strong task context", "status": "disputed"}
    strong = _score_record(
        task="strong task context",
        tokens=["strong", "task", "context"],
        mode="research",
        record=_record(tmp_path, frontmatter=strong_frontmatter),
        data_dir=tmp_path,
        usage_count=12,
    )

    assert weak is None
    assert strong is not None
    assert "staleness_high" in strong.reasons
    assert "usage" in strong.reasons
    assert "status:disputed" in strong.trust_flags
    assert "confidence:low" in strong.trust_flags


def test_context_build_early_returns_and_related_note_extraction(tmp_path, monkeypatch):
    config = default_config(tmp_path)
    monkeypatch.setattr("kb_librarian.context.retry_policy_from_config", lambda config: object())
    monkeypatch.setattr("kb_librarian.context.reindex_data_dir", lambda data_dir: None)

    monkeypatch.setattr("kb_librarian.context.load_note_records", lambda *args, **kwargs: [])
    empty = build_context(tmp_path, config=config, task="task", mode="coding", budget=100)
    empty_explore = build_explore(tmp_path, config=config, problem="problem", budget=100)
    assert empty.selected_notes == []
    assert empty.message
    assert empty_explore.selected_notes == []
    assert empty_explore.message

    active = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-active",
            "title": "Active",
            "summary": "Summary",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "other",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": [],
            "tags": [],
            "nested": {"ref": "2026-06-01-nested"},
        },
        body="See 2026-06-01-body and 2026-06-01-active.",
    )
    archived = _record(
        tmp_path,
        frontmatter={
            **active.note.frontmatter,
            "id": "2026-06-01-archived",
            "title": "Archived",
            "status": "archived",
        },
    )
    monkeypatch.setattr("kb_librarian.context.load_note_records", lambda *args, **kwargs: [active, archived])
    monkeypatch.setattr(
        "kb_librarian.context.candidate_source_from_config",
        lambda *args, **kwargs: SimpleNamespace(candidates=lambda query: []),
    )
    no_candidates = build_context(tmp_path, config=config, task="task", mode="coding", budget=100)
    assert no_candidates.message

    monkeypatch.setattr(
        "kb_librarian.context.candidate_source_from_config",
        lambda *args, **kwargs: SimpleNamespace(
            candidates=lambda query: [
                {"id": ""},
                {"id": "missing"},
                {"id": "2026-06-01-archived"},
                {"id": "2026-06-01-active"},
            ]
        ),
    )
    monkeypatch.setattr("kb_librarian.context.tokenize_query", lambda query: ["nomatch"])
    no_scores = build_context(tmp_path, config=config, task="nomatch", mode="coding", budget=100)
    assert no_scores.message

    assert _related_note_ids(active, backlinks={"2026-06-01-active": ["2026-06-01-backlink"]}) == {
        "2026-06-01-body",
        "2026-06-01-nested",
        "2026-06-01-backlink",
    }


def test_context_build_returns_empty_when_budget_filters_all_scored_notes(tmp_path, monkeypatch):
    config = default_config(tmp_path)
    record = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-scored",
            "title": "Scored",
            "summary": "Summary",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": ["task"],
            "tags": [],
        },
    )
    oversized = _minimal_selection("2026-06-01-scored", excerpt_words=400)
    monkeypatch.setattr("kb_librarian.context.retry_policy_from_config", lambda config: object())
    monkeypatch.setattr("kb_librarian.context.load_note_records", lambda *args, **kwargs: [record])
    monkeypatch.setattr(
        "kb_librarian.context.candidate_source_from_config",
        lambda *args, **kwargs: SimpleNamespace(candidates=lambda query: [{"id": "2026-06-01-scored"}]),
    )
    monkeypatch.setattr("kb_librarian.context.tokenize_query", lambda query: ["task"])
    monkeypatch.setattr("kb_librarian.context._score_record", lambda **kwargs: oversized)

    result = build_context(tmp_path, config=config, task="task", mode="coding", budget=240)

    assert result.selected_notes == []
    assert result.message


def test_build_explore_reindexes_and_adds_related_notes(tmp_path, monkeypatch):
    config = default_config(tmp_path)
    primary = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-primary",
            "title": "Primary",
            "summary": "Problem adjacent summary",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "pattern",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": ["problem"],
            "tags": [],
        },
        body="Links to 2026-06-01-related, 2026-06-01-archived, and 2026-06-01-missing.",
    )
    related = _record(
        tmp_path,
        frontmatter={
            **primary.note.frontmatter,
            "id": "2026-06-01-related",
            "title": "Related",
            "retrieval_phrases": [],
        },
    )
    archived = _record(
        tmp_path,
        frontmatter={
            **primary.note.frontmatter,
            "id": "2026-06-01-archived",
            "title": "Archived",
            "status": "archived",
            "retrieval_phrases": [],
        },
    )
    reindexed: list[Path] = []
    monkeypatch.setattr("kb_librarian.context.retry_policy_from_config", lambda config: object())
    monkeypatch.setattr("kb_librarian.context.reindex_data_dir", lambda data_dir: reindexed.append(data_dir))
    monkeypatch.setattr("kb_librarian.context.load_note_records", lambda *args, **kwargs: [primary, related, archived])
    monkeypatch.setattr(
        "kb_librarian.context.candidate_source_from_config",
        lambda *args, **kwargs: SimpleNamespace(candidates=lambda query: [{"id": "2026-06-01-primary"}]),
    )
    monkeypatch.setattr(
        "kb_librarian.context.call_with_provider_policy",
        lambda *args, **kwargs: "synthesis",
    )

    result = build_explore(tmp_path, config=config, problem="problem", budget=300)

    assert reindexed == [tmp_path]
    assert {item.note_id for item in result.selected_notes} == {"2026-06-01-primary", "2026-06-01-related"}
    assert result.synthesis_markdown == "synthesis"


def test_context_helpers_cover_budget_trimming_recency_and_coercion(tmp_path):
    oversized = _minimal_selection("2026-06-01-large", excerpt_words=400)
    small = _minimal_selection("2026-06-01-small", excerpt_words=2)

    assert _apply_context_budget([oversized, small], 60) == [small]
    assert _apply_explore_budget([oversized, small], 0) == [oversized]
    assert _apply_context_budget([oversized], 0) == [oversized]
    assert _apply_context_budget([oversized], 240) == []
    assert _apply_explore_budget([oversized, small], 260) == [small]
    second = _minimal_selection("2026-06-01-second", excerpt_words=1)
    assert _apply_explore_budget([small, second], 100) == [small]
    assert _trim_to_tokens("one two three", 2) == "one ..."
    assert _coerce_string_list("not-list") == []
    assert _coerce_string_list([" keep ", "", 7, "also"]) == ["keep", "also"]
    assert _recency_score("bad-date") == 0.0
    assert _recency_score("2999-01-01") == 0.0
    assert _recency_score("2000-01-01") == -4.0
    assert _recency_score((date.today() - timedelta(days=240)).isoformat()) == 0.0
    assert _trust_flags(status="active", confidence="high", staleness_risk="low") == []
    assert _status_score("superseded") == -4.0
    assert _status_score("needs-review") == -6.0
    assert _status_score("disputed") == -12.0
    assert _status_score("unknown") == 0.0
    assert _confidence_score("medium") == 2.0
    assert _confidence_score("low") == -7.0
    assert _confidence_score("unknown") == 0.0
    assert _recency_score(date.today().isoformat()) == 8.0
    assert _recency_score((date.today() - timedelta(days=60)).isoformat()) == 4.0

    archived = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-archived",
            "title": "Archived",
            "summary": "Archived note",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "pattern",
            "status": "archived",
            "confidence": "high",
            "retrieval_phrases": [],
            "tags": [],
        },
    )
    assert _related_explore_selection(record=archived, data_dir=tmp_path) is None


def test_context_budget_and_logging_cover_remaining_edges(tmp_path):
    first = _minimal_selection("2026-06-01-first", excerpt_words=1)
    second = _minimal_selection("2026-06-01-second", excerpt_words=1)
    selected = _apply_context_budget([first, second], 240)
    assert [item.note_id for item in selected] == ["2026-06-01-first", "2026-06-01-second"]
    explore_selected = _apply_explore_budget([first, second], 300)
    assert [item.note_id for item in explore_selected] == ["2026-06-01-first", "2026-06-01-second"]

    event = ProviderFallbackEvent(
        operation="context:synthesize",
        provider="primary",
        model="model-a",
        next_provider="fallback",
        next_model="model-b",
        classification_kind="provider_error",
        classification_detail="timeout",
        error="boom",
    )
    _log_provider_fallback_event(tmp_path, phase="context", event=event, query="task")
    assert "stage=provider-context-fallback" in (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")


def test_context_explore_scoring_covers_exact_and_medium_staleness_paths(tmp_path):
    record = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-explore",
            "title": "Explore",
            "summary": "Explore adjacent concepts.",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": date.today().isoformat(),
            "knowledge_type": "pattern",
            "status": "active",
            "confidence": "medium",
            "staleness_risk": "medium",
            "retrieval_phrases": ["adjacent concept"],
            "tags": ["agent"],
        },
        body="Explore adjacent body context.",
    )

    selection = _score_explore_record(
        problem="Explore",
        tokens=["explore", "adjacent", "agent"],
        record=record,
        data_dir=tmp_path,
        indexed_match=True,
    )

    assert selection is not None
    assert "exact_id_or_title" in selection.reasons
    assert "lexical_match" in selection.reasons
    assert "staleness_medium" in selection.reasons
    assert "exploration_type_fit" in selection.reasons


def test_context_scoring_covers_medium_staleness_and_high_explore_risk(tmp_path):
    context_record = _record(
        tmp_path,
        frontmatter={
            "id": "2026-06-01-context",
            "title": "Task context",
            "summary": "Task context summary",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": date.today().isoformat(),
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "medium",
            "staleness_risk": "medium",
            "retrieval_phrases": ["task context"],
            "tags": ["agent"],
        },
        body="Task context body.",
    )
    context_selection = _score_record(
        task="task context",
        tokens=["task", "context"],
        mode="coding",
        record=context_record,
        data_dir=tmp_path,
        usage_count=0,
    )
    explore_record = _record(
        tmp_path,
        frontmatter={
            **context_record.note.frontmatter,
            "id": "2026-06-01-explore-high",
            "title": "Explore high",
            "summary": "Explore high risk summary",
            "staleness_risk": "high",
            "retrieval_phrases": ["explore high"],
        },
        body="Explore high risk body.",
    )
    explore_selection = _score_explore_record(
        problem="explore high",
        tokens=["explore", "high"],
        record=explore_record,
        data_dir=tmp_path,
        indexed_match=False,
    )
    zero_recency_record = _record(
        tmp_path,
        frontmatter={
            **context_record.note.frontmatter,
            "id": "2026-06-01-explore-zero-recency",
            "title": "Explore zero",
            "summary": "Explore zero recency summary",
            "updated": (date.today() - timedelta(days=240)).isoformat(),
            "retrieval_phrases": ["explore zero"],
        },
        body="Explore zero recency body.",
    )
    zero_recency_selection = _score_explore_record(
        problem="explore zero",
        tokens=["explore", "zero"],
        record=zero_recency_record,
        data_dir=tmp_path,
        indexed_match=False,
    )

    assert context_selection is not None
    assert "staleness_medium" in context_selection.reasons
    assert explore_selection is not None
    assert "staleness_high" in explore_selection.reasons
    assert zero_recency_selection is not None
    assert "recency" not in zero_recency_selection.reasons


def test_load_backlinks_tolerates_missing_malformed_and_filters_entries(tmp_path):
    assert _load_backlinks(tmp_path) == {}

    backlinks_path = tmp_path / ".kb" / "backlinks.json"
    backlinks_path.parent.mkdir()
    backlinks_path.write_text("[1, 2]", encoding="utf-8")
    assert _load_backlinks(tmp_path) == {}

    backlinks_path.write_text("{not-json", encoding="utf-8")
    assert _load_backlinks(tmp_path) == {}

    backlinks_path.write_text(
        json.dumps({"source": ["target", "", 123], "bad": "target", 7: ["ignored"]}),
        encoding="utf-8",
    )
    assert _load_backlinks(tmp_path) == {"source": ["target"], "7": ["ignored"]}


def test_usage_parses_since_variants_and_rejects_invalid_value():
    now = datetime(2026, 6, 1, 12, 0, 0)

    assert parse_since(None, now=now) is None
    assert parse_since("  ", now=now) is None
    assert parse_since("30m", now=now) == datetime(2026, 6, 1, 11, 30, 0)
    assert parse_since("2h", now=now) == datetime(2026, 6, 1, 10, 0, 0)
    assert parse_since("3d", now=now) == datetime(2026, 5, 29, 12, 0, 0)
    assert parse_since("2w", now=now) == datetime(2026, 5, 18, 12, 0, 0)
    assert parse_since("2026-05-31", now=now) == datetime(2026, 5, 31, 0, 0, 0)
    assert parse_since("2026-05-31T09:30:00", now=now) == datetime(2026, 5, 31, 9, 30, 0)
    with pytest.raises(KBLibrarianError, match="--since"):
        parse_since("soon", now=now)


def test_usage_readers_ignore_corrupt_lines_and_summarize_note_filtered_events(tmp_path):
    usage_path = tmp_path / ".kb" / "usage.log"
    usage_path.parent.mkdir()
    usage_path.write_text(
        "\n"
        '{"event": "retrieval", "command": "", "timestamp": "2026-06-01T10:00:00", '
        '"returned_note_ids": ["note-a", "", 5]}\n'
        "not-json\n"
        "[1, 2]\n"
        '{"event": "note-use", "timestamp": "bad-time", "note_id": "note-a"}\n'
        '{"event": "suspect-flag", "timestamp": "2026-06-01T10:01:00", "note_id": "note-a", "reason": ""}\n',
        encoding="utf-8",
    )

    events = _read_jsonl(usage_path)
    summary = summarize_usage(tmp_path, since="2026-06-01", note_id="note-a")
    rendered = render_usage_summary(summary)

    assert len(events) == 3
    assert summary.retrieval_count == 1
    assert summary.logged_use_count == 0
    assert summary.search_miss_count is None
    assert summary.retrievals_by_command == {"unknown": 1}
    assert summary.retrieved_notes == {"note-a": 1}
    assert summary.suspect_flags == ["note-a: no reason (2026-06-01T10:01:00)"]
    assert "note: note-a" in rendered
    assert "suspect flags:" in rendered


def test_usage_counts_stats_and_clean_mapping_handle_edge_values(tmp_path):
    log_retrieval(
        tmp_path,
        command="context",
        query="query",
        returned_note_ids=["note-a", "", "note-b"],
        result_count=2,
        timestamp="2026-06-01T10:00:00+00:00",
    )
    log_note_use(tmp_path, note_id="note-a", timestamp="2026-06-01T10:01:00")
    stats_path = tmp_path / ".kb" / "stats.json"
    stats_path.write_text("[bad]", encoding="utf-8")

    refresh_usage_stats(tmp_path)
    stats = json.loads(stats_path.read_text(encoding="utf-8"))

    assert note_usage_counts(tmp_path) == {"note-a": 2, "note-b": 1}
    assert stats["usage"]["retrievals"] == 1
    assert stats["usage"]["logged_uses"] == 1
    assert stats["usage"]["last_event_at"] == "2026-06-01T10:01:00"
    assert _clean_mapping(
        {
            "none": None,
            "string": "value",
            "number": 3,
            "list": ["a", "", 5],
            "object": {"nested": "value"},
        }
    ) == {
        "string": "value",
        "number": 3,
        "list": ["a", "5"],
        "object": "{'nested': 'value'}",
    }
    stats_path.write_text("{bad", encoding="utf-8")
    refresh_usage_stats(tmp_path)
    assert json.loads(stats_path.read_text(encoding="utf-8"))["usage"]["retrievals"] == 1

    assert _parse_timestamp("") is None
    assert "T" in _now_iso()


def test_usage_stats_refresh_replaces_non_mapping_stats_payload(tmp_path):
    stats_path = tmp_path / ".kb" / "stats.json"
    stats_path.parent.mkdir()
    stats_path.write_text("[]", encoding="utf-8")

    refresh_usage_stats(tmp_path)

    assert set(json.loads(stats_path.read_text(encoding="utf-8"))) == {"usage"}


def test_usage_jsonl_reader_returns_empty_when_read_fails(tmp_path, monkeypatch):
    log_path = tmp_path / ".kb" / "usage.log"
    log_path.parent.mkdir()
    log_path.write_text("{}", encoding="utf-8")

    def raise_os_error(*args, **kwargs):
        raise OSError("cannot read")

    monkeypatch.setattr(type(log_path), "read_text", raise_os_error)

    assert _read_jsonl(log_path) == []


def test_usage_window_compares_timezone_aware_timestamps(tmp_path):
    log_retrieval(
        tmp_path,
        command="context",
        query="query",
        returned_note_ids=["note-a"],
        result_count=1,
        timestamp="2026-06-01T12:00:00+00:00",
    )

    summary = summarize_usage(
        tmp_path,
        since="2026-06-01T07:00:00-05:00",
        now=datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
    )

    assert summary.retrieval_count == 1


def test_usage_summary_without_note_filter_includes_misses_uses_and_empty_flags(tmp_path):
    log_retrieval(
        tmp_path,
        command="search",
        query="query",
        returned_note_ids=["note-a"],
        result_count=1,
        timestamp="2026-06-01T10:00:00",
    )
    log_note_use(tmp_path, note_id="note-a", timestamp="2026-06-01T10:01:00")
    log_search_miss(
        tmp_path,
        command="search",
        query="missing context",
        result_count=0,
        top_score=None,
        filters={"topic": "agent-systems"},
        reason="zero-results",
        timestamp="2026-06-01T10:02:00",
    )

    summary = summarize_usage(tmp_path, since=None)
    rendered = render_usage_summary(summary)

    assert summary.logged_use_count == 1
    assert summary.search_miss_count == 1
    assert summary.used_notes == {"note-a": 1}
    assert summary.suspect_flags == []
    assert "search misses: 1" in rendered
    assert "suspect flags: none" in rendered


def test_usage_promotes_repeated_and_high_value_search_miss_groups(tmp_path):
    for index in range(5):
        log_search_miss(
            tmp_path,
            command="context",
            query="Sparse Result",
            result_count=0,
            top_score=None,
            filters={"tag": ["agent", None], "ignore": None},
            reason="zero-results",
            timestamp=f"2026-06-01T10:0{index}:00",
        )
    log_search_miss(
        tmp_path,
        command="explore",
        query="urgent gap",
        result_count=2,
        top_score=0.1,
        filters={},
        reason="low-score",
        high_value=True,
        timestamp="2026-06-01T11:00:00",
    )

    promoted = promote_search_misses(tmp_path)

    assert len(promoted) == 2
    assert _miss_reason(result_count=0, top_score=None, report_miss=False) == "zero-results"
    assert _miss_reason(result_count=2, top_score=0.1, report_miss=False) == "low-score"
    assert _miss_reason(result_count=2, top_score=5.0, report_miss=False) is None
    assert _miss_reason(result_count=2, top_score=5.0, report_miss=True) == "reported-poor-result"
    assert _normalized_query("!!!") == "!!!"
    assert _returned_note_ids({"returned_note_ids": "bad"}) == []


# --- New coverage tests for missing branches ---

def test_resolve_provider_seam_credential_source_command_happy_path():
    # Branch 242->256: CREDENTIAL_SOURCE_COMMAND path with valid credential_command,
    # no cli_command, backend=BACKEND_DIRECT_HTTP — should succeed without raising.
    from kb_librarian.config import ConfigError

    seam = resolve_provider_seam(
        "anthropic",
        {
            "backend": BACKEND_DIRECT_HTTP,
            "credential_source": CREDENTIAL_SOURCE_COMMAND,
            "credential_command": "get-token",
        },
        error_factory=ConfigError,
        error_prefix="providers.anthropic",
    )

    assert seam.backend == BACKEND_DIRECT_HTTP
    assert seam.credential_source == CREDENTIAL_SOURCE_COMMAND
    # Line 257 (backend != BACKEND_VENDOR_CLI and cli_command is not None) is
    # GENUINELY UNREACHABLE: any BACKEND_DIRECT_HTTP path with cli_command set
    # would already have raised via _reject_conflicting_fields before reaching 257.


def test_summarize_usage_note_id_filter_rejects_non_matching_events(tmp_path):
    # Branches 233->235, 237->239, 240->231, 241->231:
    # retrieval/note-use/suspect-flag events that don't match the note_id filter,
    # and an unknown event type that falls through all conditions.
    import json as _json
    usage_path = tmp_path / ".kb" / "usage.log"
    usage_path.parent.mkdir()
    usage_path.write_text(
        "\n".join(
            [
                # retrieval not containing "target-note" (233->235 False branch)
                _json.dumps({"event": "retrieval", "returned_note_ids": ["other-note"], "command": "search"}),
                # note-use with wrong note_id (237->239 False branch)
                _json.dumps({"event": "note-use", "note_id": "other-note"}),
                # suspect-flag with wrong note_id (241->231 False branch)
                _json.dumps({"event": "suspect-flag", "note_id": "other-note", "reason": "wrong"}),
                # unknown event type — reaches 240, condition False, goes to 231
                _json.dumps({"event": "custom-unknown-event"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_usage(tmp_path, since=None, note_id="target-note")

    assert summary.retrieval_count == 0
    assert summary.logged_use_count == 0
    assert summary.suspect_flags == []


def test_summarize_usage_note_use_with_empty_note_id_not_counted(tmp_path):
    # Branch 255->253: note-use event with empty note_id — logged to note_use_events
    # but skipped when building used_counter because used_id is falsy.
    import json as _json
    usage_path = tmp_path / ".kb" / "usage.log"
    usage_path.parent.mkdir()
    usage_path.write_text(
        _json.dumps({"event": "note-use", "note_id": ""}) + "\n",
        encoding="utf-8",
    )

    summary = summarize_usage(tmp_path, since=None, note_id=None)

    # Event is in note_use_events (counted), but not added to used_counter (empty ID).
    assert summary.logged_use_count == 1
    assert summary.used_notes == {}


def test_note_usage_counts_skips_unknown_event_and_empty_note_id(tmp_path):
    # Branch 364->360: unknown event type is skipped.
    # Branch 366->360: note-use event with empty note_id is not counted.
    import json as _json
    usage_path = tmp_path / ".kb" / "usage.log"
    usage_path.parent.mkdir()
    usage_path.write_text(
        "\n".join(
            [
                # Unknown event type — not "retrieval" or "note-use" (364->360)
                _json.dumps({"event": "suspect-flag", "note_id": "some-note"}),
                # note-use with empty note_id — empty string skipped (366->360)
                _json.dumps({"event": "note-use", "note_id": "   "}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    counts = note_usage_counts(tmp_path)

    assert counts == {}


def test_promote_search_misses_when_upsert_returns_none(tmp_path, monkeypatch):
    # Branch 209->204: _upsert_search_miss_item returns None, so item_id is not
    # appended and the loop continues to the next group.
    import json as _json
    misses_path = tmp_path / ".kb" / "search-misses.log"
    misses_path.parent.mkdir()
    # Write 3 identical misses to meet the repeat threshold.
    events = [
        {"event": "search-miss", "query": "test missing query", "timestamp": f"2026-06-01T10:0{i}:00",
         "command": "context", "reason": "zero-results", "result_count": 0,
         "top_score": None, "high_value": False, "filters": {}}
        for i in range(3)
    ]
    misses_path.write_text("\n".join(_json.dumps(e) for e in events) + "\n", encoding="utf-8")

    monkeypatch.setattr("kb_librarian.usage._upsert_search_miss_item", lambda *args, **kwargs: None)

    promoted = promote_search_misses(tmp_path)

    assert promoted == []

    # usage:334->337 is GENUINELY UNREACHABLE: the if-chain for unit in [m/h/d/w]
    # is exhaustive — if the regex matched, unit is always one of those 4 values,
    # so the False branch of 'if unit == "w":' can never be reached.
    # usage:345 is UNREACHABLE in Python 3.11+: datetime.fromisoformat() now
    # accepts date-only strings like "2026-06-01", so the try block at line 338
    # always succeeds and line 345 (datetime.combine fallback) is never executed.
