from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from kb_librarian.config import ConfigError, default_config, load_config, read_config_file, validate_config
from kb_librarian.context import (
    ContextSelection,
    _apply_context_budget,
    _apply_explore_budget,
    _confidence_score,
    _coerce_string_list,
    _load_backlinks,
    _recency_score,
    _related_explore_selection,
    _score_explore_record,
    _score_record,
    _status_score,
    _trim_to_tokens,
    _trust_flags,
)
from kb_librarian.errors import KBLibrarianError
from kb_librarian.notes import Note
from kb_librarian.provider_seams import (
    BACKEND_DIRECT_HTTP,
    BACKEND_VENDOR_CLI,
    CREDENTIAL_SOURCE_API_KEY_ENV,
    CREDENTIAL_SOURCE_COMMAND,
    ProviderSeam,
    provider_seam_supported_for_runtime,
)
from kb_librarian.storage import NoteRecord
from kb_librarian.usage import (
    _clean_mapping,
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


def test_context_helpers_cover_budget_trimming_recency_and_coercion(tmp_path):
    oversized = _minimal_selection("2026-06-01-large", excerpt_words=400)
    small = _minimal_selection("2026-06-01-small", excerpt_words=2)

    assert _apply_context_budget([oversized, small], 60) == [small]
    assert _apply_explore_budget([oversized, small], 0) == [oversized]
    assert _apply_context_budget([oversized], 240) == []
    assert _trim_to_tokens("one two three", 2) == "one ..."
    assert _coerce_string_list("not-list") == []
    assert _coerce_string_list([" keep ", "", 7, "also"]) == ["keep", "also"]
    assert _recency_score("bad-date") == 0.0
    assert _recency_score("2999-01-01") == 0.0
    assert _recency_score("2000-01-01") == -4.0
    assert _trust_flags(status="active", confidence="high", staleness_risk="low") == []
    assert _status_score("superseded") == -4.0
    assert _status_score("needs-review") == -6.0
    assert _status_score("disputed") == -12.0
    assert _status_score("unknown") == 0.0
    assert _confidence_score("medium") == 2.0
    assert _confidence_score("low") == -7.0
    assert _confidence_score("unknown") == 0.0
    assert _recency_score(date.today().isoformat()) == 8.0

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
