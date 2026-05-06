from __future__ import annotations

import json
from datetime import datetime

from kb_librarian.init import initialize_data_dir
from kb_librarian.review import review_state_path
from kb_librarian.usage import (
    log_suspect_flag,
    log_note_use,
    log_retrieval,
    log_search_miss,
    maybe_log_search_miss,
    read_search_miss_events,
    read_usage_events,
    render_usage_summary,
    summarize_usage,
)


def test_usage_logs_are_structured_body_free_jsonl(tmp_path):
    log_retrieval(
        tmp_path,
        command="context",
        query="agent context task",
        task="agent context task",
        mode="coding",
        budget=900,
        returned_note_ids=["2026-05-06-agent-context"],
        result_count=1,
        top_score=42.123,
        synthesis_succeeded=True,
        timestamp="2026-05-06T10:00:00",
    )
    log_note_use(
        tmp_path,
        note_id="2026-05-06-agent-context",
        task="used in architecture review",
        timestamp="2026-05-06T10:02:00",
    )

    lines = (tmp_path / ".kb" / "usage.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["event"] == "retrieval"
    assert records[0]["returned_note_ids"] == ["2026-05-06-agent-context"]
    assert records[0]["top_score"] == 42.12
    assert records[1]["event"] == "note-use"
    assert "body" not in records[0]

    summary = summarize_usage(tmp_path)
    assert summary.retrieval_count == 1
    assert summary.logged_use_count == 1
    assert summary.retrieved_notes == {"2026-05-06-agent-context": 1}
    assert summary.used_notes == {"2026-05-06-agent-context": 1}
    rendered = render_usage_summary(summary)
    assert "Usage summary" in rendered
    assert "retrievals: 1" in rendered
    assert "logged uses: 1" in rendered


def test_usage_since_and_note_filters(tmp_path):
    log_retrieval(
        tmp_path,
        command="search",
        query="old query",
        returned_note_ids=["2026-05-06-old"],
        result_count=1,
        timestamp="2026-04-01T10:00:00",
    )
    log_retrieval(
        tmp_path,
        command="search",
        query="fresh query",
        returned_note_ids=["2026-05-06-fresh"],
        result_count=1,
        timestamp="2026-05-05T10:00:00",
    )
    log_note_use(
        tmp_path,
        note_id="2026-05-06-fresh",
        timestamp="2026-05-05T10:01:00",
    )

    summary = summarize_usage(
        tmp_path,
        since="7d",
        note_id="2026-05-06-fresh",
        now=datetime(2026, 5, 6, 12, 0, 0),
    )

    assert summary.window == "last 7d"
    assert summary.note_id == "2026-05-06-fresh"
    assert summary.retrieval_count == 1
    assert summary.logged_use_count == 1
    assert summary.search_miss_count is None
    assert summary.retrieved_notes == {"2026-05-06-fresh": 1}


def test_repeated_search_misses_promote_to_review_item(tmp_path):
    initialize_data_dir(tmp_path)

    first = log_search_miss(
        tmp_path,
        command="search",
        query="quantum gardening",
        result_count=0,
        top_score=None,
        filters={},
        reason="zero-results",
        timestamp="2026-05-06T10:00:00",
    )
    second = log_search_miss(
        tmp_path,
        command="search",
        query="quantum gardening",
        result_count=0,
        top_score=None,
        filters={},
        reason="zero-results",
        timestamp="2026-05-06T10:01:00",
    )
    third = log_search_miss(
        tmp_path,
        command="search",
        query="quantum gardening",
        result_count=0,
        top_score=None,
        filters={},
        reason="zero-results",
        timestamp="2026-05-06T10:02:00",
    )

    assert first is None
    assert second is None
    assert third == "searchmiss-2026-05-06-001"
    assert len(read_search_miss_events(tmp_path)) == 3

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    item = state["items"][0]
    assert item["queue"] == "searchmiss"
    assert item["payload"]["query"] == "quantum gardening"
    assert item["payload"]["misses"] == 3
    assert item["payload"]["reasons"] == {"zero-results": 3}

    rendered = (tmp_path / "review" / "search-misses.md").read_text(encoding="utf-8")
    assert "## item: searchmiss-2026-05-06-001" in rendered
    assert "- query: quantum gardening" in rendered
    assert "- misses: 3" in rendered
    assert "- reasons: zero-results=3" in rendered


def test_reported_poor_result_promotes_immediately(tmp_path):
    initialize_data_dir(tmp_path)

    item_id = maybe_log_search_miss(
        tmp_path,
        command="search",
        query="agent context",
        result_count=2,
        top_score=99.0,
        filters={},
        report_miss=True,
        timestamp="2026-05-06T11:00:00",
    )

    assert item_id == "searchmiss-2026-05-06-001"
    assert read_usage_events(tmp_path) == []
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    item = state["items"][0]
    assert item["priority"] == "high"
    assert item["payload"]["high_value"] is True
    assert item["payload"]["reasons"] == {"reported-poor-result": 1}


def test_usage_summary_and_stats_include_suspect_flags(tmp_path):
    initialize_data_dir(tmp_path)
    log_retrieval(
        tmp_path,
        command="search",
        query="agent context",
        returned_note_ids=["2026-05-06-agent-context"],
        result_count=1,
        timestamp="2026-05-06T09:00:00",
    )
    log_suspect_flag(
        tmp_path,
        note_id="2026-05-06-agent-context",
        reason="outdated recommendation",
        timestamp="2026-05-06T09:01:00",
    )

    summary = summarize_usage(tmp_path)
    assert summary.suspect_flags
    assert "outdated recommendation" in summary.suspect_flags[0]

    stats = json.loads((tmp_path / ".kb" / "stats.json").read_text(encoding="utf-8"))
    assert stats["usage"]["suspect_flags"] == 1
