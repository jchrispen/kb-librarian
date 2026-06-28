"""Coverage tests for missing lines/branches in src/kb_librarian/cli.py."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kb_librarian import cli
from kb_librarian.errors import AmbiguousNoteIdError, KBLibrarianError
from kb_librarian.notes import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_note(note_id: str = "2026-01-01-test", topic: str = "test-topic") -> Note:
    frontmatter = {
        "id": note_id,
        "title": "Test Note",
        "summary": "A test summary.",
        "topic": topic,
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "medium",
        "retrieval_phrases": ["test note"],
        "tags": [topic],
    }
    return Note(frontmatter=frontmatter, body="## Core\n\nContent.\n")


# ---------------------------------------------------------------------------
# _handle_init (lines 398, 400-401)
# ---------------------------------------------------------------------------

def test_handle_init_already_initialized(tmp_path, monkeypatch, capsys):
    """Line 398: 'Already initialized; no files changed.' when nothing created."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    args = SimpleNamespace(data_dir=str(tmp_path), hooks=False)
    assert cli._handle_init(args) == 0
    assert "Already initialized" in capsys.readouterr().out


def test_handle_init_hooks_guidance(tmp_path, monkeypatch, capsys):
    """Lines 400-401: hooks guidance printed when args.hooks=True."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "render_preamble_guidance", lambda *_: "## Preamble\n")
    monkeypatch.setattr(cli, "render_hooks_guidance", lambda *_: "## Hooks\n")
    args = SimpleNamespace(data_dir=str(tmp_path), hooks=True)
    assert cli._handle_init(args) == 0
    out = capsys.readouterr().out
    assert "## Preamble" in out
    assert "## Hooks" in out


# ---------------------------------------------------------------------------
# _handle_ingest (lines 434-435, 436->443)
# ---------------------------------------------------------------------------

def test_handle_ingest_non_quiet_non_json(tmp_path, monkeypatch, capsys):
    """Lines 434-435: render_report + _print_auto_commit_result in non-quiet non-json path."""
    report = SimpleNamespace(errors=0, created_notes=[], operation_id="op-1")
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "ingest", lambda *_a, **_kw: report)
    monkeypatch.setattr(cli, "render_report", lambda _: "Report output.\n")
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), file=None, force=False, quiet=False, resume=False, json=False
    )
    assert cli._handle_ingest(args) == 0
    assert "Report output." in capsys.readouterr().out


def test_handle_ingest_quiet_with_errors(tmp_path, monkeypatch, capsys):
    """Lines 436->443: quiet=True with errors prints to stderr."""
    report = SimpleNamespace(errors=1, created_notes=[], operation_id="op-err")
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "ingest", lambda *_a, **_kw: report)
    args = SimpleNamespace(
        data_dir=str(tmp_path), file=None, force=False, quiet=True, resume=False, json=False
    )
    assert cli._handle_ingest(args) == 1
    assert "ingest completed with 1 errors" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _handle_review (lines 453-456, 463-464, 479->488)
# ---------------------------------------------------------------------------

def test_handle_review_list(tmp_path, monkeypatch, capsys):
    """Lines 453-456: review 'list' action."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"review": {"max_review_items_per_run": 10}})
    monkeypatch.setattr(cli, "collect_review_summary", lambda *_a, **_kw: ({}, []))
    monkeypatch.setattr(cli, "render_review_summary", lambda *_a, **_kw: "## Review summary\n")
    assert cli.main(["review", "--data-dir", str(tmp_path)]) == 0
    assert "## Review summary" in capsys.readouterr().out


def test_handle_review_explain(tmp_path, monkeypatch, capsys):
    """Lines 463-464: review 'explain' action."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {})
    monkeypatch.setattr(cli, "explain_review_item", lambda *_a, **_kw: "Explanation text.\n")
    assert cli.main(["review", "explain", "item-001", "--data-dir", str(tmp_path)]) == 0
    assert "Explanation text." in capsys.readouterr().out


def test_handle_review_accept_with_changed(tmp_path, monkeypatch, capsys):
    """Lines 479->488: review 'accept' when result.changed=True triggers auto-commit."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "accept_review_item", lambda *_a, **_kw: SimpleNamespace(
        message="Accepted.", changed=True, item_id="item-001"
    ))
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)
    assert cli.main(["review", "accept", "item-001", "--data-dir", str(tmp_path)]) == 0
    assert "Accepted." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_context (line 517, lines 587->590)
# ---------------------------------------------------------------------------

def test_handle_context_budget_zero(tmp_path, monkeypatch):
    """Line 517: budget<=0 raises KBLibrarianError."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"retrieval": {"context_budget_tokens": "100"}})
    args = SimpleNamespace(
        data_dir=str(tmp_path), mode="coding", budget=0, task="task", json=False, report_miss=False
    )
    with pytest.raises(KBLibrarianError, match="positive integer"):
        cli._handle_context(args)


def test_handle_context_synthesis_markdown(tmp_path, monkeypatch, capsys):
    """Lines 587->590: synthesis_markdown is printed in plain output."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"retrieval": {"context_budget_tokens": "100"}})
    monkeypatch.setattr(cli, "build_context", lambda *_a, **kw: SimpleNamespace(
        task=kw["task"], mode=kw["mode"], budget=kw["budget"],
        synthesis_markdown="Synthesis here.", selected_notes=[], citations=[], message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), mode="coding", budget=100, task="task", json=False, report_miss=False
    )
    assert cli._handle_context(args) == 0
    assert "Synthesis here." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_explore (lines 668->671)
# ---------------------------------------------------------------------------

def test_handle_explore_synthesis_markdown(tmp_path, monkeypatch, capsys):
    """Lines 668->671: explore synthesis_markdown is printed in plain output."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"retrieval": {"explore_budget_tokens": "100"}})
    monkeypatch.setattr(cli, "build_explore", lambda *_a, **kw: SimpleNamespace(
        problem=kw["problem"], budget=kw["budget"],
        synthesis_markdown="Explore synthesis.", selected_notes=[], citations=[], message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), budget=100, problem="problem", json=False, report_miss=False
    )
    assert cli._handle_explore(args) == 0
    assert "Explore synthesis." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_add – create note path (lines 705-737)
# ---------------------------------------------------------------------------

def test_handle_add_creates_note(tmp_path, monkeypatch, capsys):
    """Lines 705-737: _handle_add with topic + knowledge_type creates a note."""
    note_id = "2026-01-01-test"
    note = _make_note(note_id=note_id)
    note_path = tmp_path / "notes" / "test-topic" / f"{note_id}.md"

    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "_read_add_input", lambda *_: ("## Test\n\nContent.", "test.md"))
    monkeypatch.setattr(cli, "_try_parse_seed_note", lambda *_: None)
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "ensure_unique_note_ids", lambda *_: None)
    monkeypatch.setattr(cli, "generate_note_id", lambda *_a, **_kw: note_id)
    monkeypatch.setattr(cli, "_build_note", lambda *_a, **_kw: note)
    monkeypatch.setattr(cli, "ensure_topic_layout", lambda *_: None)
    monkeypatch.setattr(cli, "canonical_note_path", lambda *_a: note_path)
    monkeypatch.setattr(cli, "write_note", lambda *_: None)
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=1, topic_count=1, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)

    args = SimpleNamespace(
        data_dir=str(tmp_path), topic="test-topic", knowledge_type="technique", from_file=None
    )
    assert cli._handle_add(args) == 0
    assert f"Created note {note_id}" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_reindex (lines 741-763)
# ---------------------------------------------------------------------------

def test_handle_reindex(tmp_path, monkeypatch, capsys):
    """Lines 741-763: _handle_reindex calls reindex_data_dir and reports."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=5, topic_count=2, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)
    assert cli.main(["reindex", "--data-dir", str(tmp_path)]) == 0
    assert "Reindexed 5 notes" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_doctor without --self-test (lines 772-775)
# ---------------------------------------------------------------------------

def test_handle_doctor_without_self_test(tmp_path, monkeypatch, capsys):
    """Lines 772-775: _handle_doctor runs run_doctor + render_doctor_report."""
    monkeypatch.setattr(cli, "run_doctor", lambda *_: SimpleNamespace(error_count=0))
    monkeypatch.setattr(cli, "render_doctor_report", lambda *_: "Doctor report.\n")
    assert cli.main(["doctor", "--data-dir", str(tmp_path)]) == 0
    assert "Doctor report." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _capture_auto_commit_before (line 781)
# ---------------------------------------------------------------------------

def test_capture_auto_commit_before_enabled(tmp_path, monkeypatch):
    """Line 781: returns snapshot when git.auto_commit=True."""
    snapshot = object()
    monkeypatch.setattr(cli, "capture_git_snapshot", lambda *_: snapshot)
    result = cli._capture_auto_commit_before(tmp_path, {"git": {"auto_commit": True}})
    assert result is snapshot


# ---------------------------------------------------------------------------
# _handle_compact non-JSON (lines 840-847)
# ---------------------------------------------------------------------------

def test_handle_compact_non_json(tmp_path, monkeypatch, capsys):
    """Lines 840-847: compact non-JSON output path."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "draft_compaction_proposal", lambda *_a, **_kw: SimpleNamespace(
        review_item_id="compaction-2026-01-01-001",
        cluster_id="cluster-a",
        source_note_ids=["n1", "n2"],
        created=True,
        draft=SimpleNamespace(diff_summary="Merged 2 notes."),
    ))
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)
    assert cli.main(["compact", "cluster-a", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Created compaction proposal" in out
    assert "cluster_id: cluster-a" in out
    assert "Merged 2 notes." in out


# ---------------------------------------------------------------------------
# _handle_search (lines 851-920)
# ---------------------------------------------------------------------------

def _mock_result(tmp_path: Path) -> dict:
    return {
        "id": "2026-01-01-note",
        "title": "Test Note",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "medium",
        "topic": "test-topic",
        "summary": "A test summary.",
        "path": str(tmp_path / "notes" / "test-topic" / "2026-01-01-note.md"),
        "score": 0.9,
    }


def test_handle_search_with_results(tmp_path, monkeypatch, capsys):
    """Lines 851-920: _handle_search with results prints each item."""
    results = [_mock_result(tmp_path)]
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {
        "retrieval": {"default_budget_tokens": "2000"},
    })
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=1, topic_count=1, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "candidate_source_from_config", lambda *_: SimpleNamespace(
        candidates=lambda *_a, **_kw: []
    ))
    monkeypatch.setattr(cli, "ranker_from_config", lambda *_: SimpleNamespace(
        rank=lambda *_a, **_kw: results
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    assert cli.main(["search", "test query", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2026-01-01-note" in out
    assert "Test Note" in out


def test_handle_search_no_results(tmp_path, monkeypatch, capsys):
    """Lines 901-903: search with no results prints 'No results.'"""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {
        "retrieval": {"default_budget_tokens": "2000"},
    })
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=0, topic_count=0, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "candidate_source_from_config", lambda *_: SimpleNamespace(
        candidates=lambda *_a, **_kw: []
    ))
    monkeypatch.setattr(cli, "ranker_from_config", lambda *_: SimpleNamespace(
        rank=lambda *_a, **_kw: []
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    assert cli.main(["search", "nothing here", "--data-dir", str(tmp_path)]) == 0
    assert "No results." in capsys.readouterr().out


def test_handle_search_with_citations(tmp_path, monkeypatch, capsys):
    """Line 918: with_citations=True triggers citation block."""
    results = [_mock_result(tmp_path)]
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {
        "retrieval": {"default_budget_tokens": "2000"},
    })
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=1, topic_count=1, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "candidate_source_from_config", lambda *_: SimpleNamespace(
        candidates=lambda *_a, **_kw: []
    ))
    monkeypatch.setattr(cli, "ranker_from_config", lambda *_: SimpleNamespace(
        rank=lambda *_a, **_kw: results
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    assert cli.main(["search", "test query", "--with-citations", "--data-dir", str(tmp_path)]) == 0
    assert "## Source notes" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_get (lines 924-950)
# ---------------------------------------------------------------------------

def test_handle_get_full_text(tmp_path, monkeypatch, capsys):
    """Lines 924-950: _handle_get prints raw file content when summary=False."""
    note_path = tmp_path / "notes" / "test-topic" / "2026-01-01-test.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("---\nid: 2026-01-01-test\n---\nBody content.\n", encoding="utf-8")
    record = SimpleNamespace(
        note_id="2026-01-01-test",
        path=note_path,
        note=SimpleNamespace(frontmatter={"title": "Test", "summary": "S.", "knowledge_type": "technique",
                                           "status": "active", "confidence": "medium", "topic": "t",
                                           "retrieval_phrases": []}),
    )
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [record])
    args = SimpleNamespace(data_dir=str(tmp_path), id="2026-01-01-test", summary=False)
    assert cli._handle_get(args) == 0
    assert "Body content." in capsys.readouterr().out


def test_handle_get_summary(tmp_path, monkeypatch, capsys):
    """Lines 935-947: _handle_get with summary=True prints frontmatter fields."""
    note_path = tmp_path / "notes" / "test-topic" / "2026-01-01-test.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("---\nid: 2026-01-01-test\n---\nBody.\n", encoding="utf-8")
    record = SimpleNamespace(
        note_id="2026-01-01-test",
        path=note_path,
        note=SimpleNamespace(frontmatter={"title": "Test Note", "summary": "A summary.",
                                           "knowledge_type": "technique", "status": "active",
                                           "confidence": "medium", "topic": "test-topic",
                                           "retrieval_phrases": ["phrase one", "phrase two"]}),
    )
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [record])
    args = SimpleNamespace(data_dir=str(tmp_path), id="2026-01-01-test", summary=True)
    assert cli._handle_get(args) == 0
    out = capsys.readouterr().out
    assert "title: Test Note" in out
    assert "phrase one" in out


# ---------------------------------------------------------------------------
# _handle_topics (lines 954-965)
# ---------------------------------------------------------------------------

def test_handle_topics_list(tmp_path, monkeypatch, capsys):
    """Lines 954-965: _handle_topics renders topic list."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "group_records_for_indexing", lambda *_: {})
    monkeypatch.setattr(cli, "_load_topic_review_counts", lambda *_: ({}, False))
    assert cli.main(["topics", "--data-dir", str(tmp_path)]) == 0
    assert "# Topics" in capsys.readouterr().out


def test_handle_topics_tree(tmp_path, monkeypatch, capsys):
    """Lines 961-962: _handle_topics with --tree renders topic tree."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "group_records_for_indexing", lambda *_: {})
    monkeypatch.setattr(cli, "_load_topic_review_counts", lambda *_: ({}, False))
    assert cli.main(["topics", "--tree", "--data-dir", str(tmp_path)]) == 0
    assert "# Topics" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _load_topic_review_counts (lines 1084, 1099)
# ---------------------------------------------------------------------------

def test_load_topic_review_counts_no_file(tmp_path):
    """Line 1084: returns ({}, False) when state_path doesn't exist."""
    counts, available = cli._load_topic_review_counts(tmp_path, [])
    assert counts == {}
    assert available is False


def test_load_topic_review_counts_items_not_list(tmp_path):
    """Line 1099: returns ({}, False) when items is not a list."""
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "review-items.json").write_text('{"version": 1, "items": {}}', encoding="utf-8")
    counts, available = cli._load_topic_review_counts(tmp_path, [])
    assert counts == {}
    assert available is False


# ---------------------------------------------------------------------------
# _review_target_note_ids (lines 1122->1127, 1125->1123, 1128->1132)
# ---------------------------------------------------------------------------

def test_review_target_note_ids_list_with_empty_string():
    """Lines 1122->1127, 1125->1123: non-empty target_notes, empty string filtered out."""
    item = {"target_notes": ["note-1", "", "note-2"], "payload": None}
    result = cli._review_target_note_ids(item)
    assert result == {"note-1", "note-2"}


def test_review_target_note_ids_payload_note_id():
    """Lines 1128->1132: payload dict with note_id adds to targets."""
    item = {"target_notes": [], "payload": {"note_id": "note-from-payload"}}
    result = cli._review_target_note_ids(item)
    assert result == {"note-from-payload"}


# ---------------------------------------------------------------------------
# _render_topic_list (lines 1148->1153)
# ---------------------------------------------------------------------------

def test_render_topic_list_review_available():
    """Lines 1148->1153: review metrics included when review_available=True."""
    topics = ["test-topic"]
    grouped = {"test-topic": [object()]}
    review_counts = {"test-topic": {"stale": 2, "orphan": 1, "review": 3}}
    result = cli._render_topic_list(topics, grouped, review_counts, True)
    assert "stale=2" in result
    assert "orphan=1" in result
    assert "review=3" in result


# ---------------------------------------------------------------------------
# _render_topic_tree (lines 1205->1209)
# ---------------------------------------------------------------------------

def test_render_topic_tree_with_subtopics():
    """Lines 1205->1209: 'notes=X direct/Y total' shown when subtopics exist."""
    topics = ["parent", "parent/child"]
    grouped = {"parent": [object()], "parent/child": [object(), object()]}
    result = cli._render_topic_tree(topics, grouped, {}, False)
    assert "notes=1 direct/3 total" in result


# ---------------------------------------------------------------------------
# _handle_log_use (lines 1220-1225)
# ---------------------------------------------------------------------------

def test_handle_log_use(tmp_path, monkeypatch, capsys):
    """Lines 1220-1225: _handle_log_use calls log_note_use and prints confirmation."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_require_note_id", lambda *_: None)
    monkeypatch.setattr(cli, "log_note_use", lambda *_a, **_kw: None)
    args = SimpleNamespace(data_dir=str(tmp_path), id="2026-01-01-note", task="testing")
    assert cli._handle_log_use(args) == 0
    assert "Logged use of note 2026-01-01-note" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_usage (lines 1229-1236)
# ---------------------------------------------------------------------------

def test_handle_usage_no_note(tmp_path, monkeypatch, capsys):
    """Lines 1229-1236: _handle_usage without note filter."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "summarize_usage", lambda *_a, **_kw: SimpleNamespace())
    monkeypatch.setattr(cli, "render_usage_summary", lambda *_: "Usage report.\n")
    assert cli.main(["usage", "--data-dir", str(tmp_path)]) == 0
    assert "Usage report." in capsys.readouterr().out


def test_handle_usage_with_note(tmp_path, monkeypatch, capsys):
    """Lines 1232-1233: _handle_usage with note filter calls _require_note_id."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_require_note_id", lambda *_: None)
    monkeypatch.setattr(cli, "summarize_usage", lambda *_a, **_kw: SimpleNamespace())
    monkeypatch.setattr(cli, "render_usage_summary", lambda *_: "Usage report.\n")
    assert cli.main(["usage", "--note", "2026-01-01-note", "--data-dir", str(tmp_path)]) == 0
    assert "Usage report." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _handle_flag_suspect (line 1250)
# ---------------------------------------------------------------------------

def test_handle_flag_suspect_not_created(tmp_path, monkeypatch, capsys):
    """Line 1250: created_or_updated=False prints 'existing review item remains' message."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "_require_note_id", lambda *_: None)
    monkeypatch.setattr(cli, "flag_suspect_note", lambda *_a, **_kw: SimpleNamespace(
        created_or_updated=False,
        queue="low_utility",
        item_id="low-utility-2026-01-01-001",
    ))
    args = SimpleNamespace(data_dir=str(tmp_path), id="2026-01-01-note", reason="It is suspect.")
    assert cli._handle_flag_suspect(args) == 0
    assert "existing review item remains low-utility-2026-01-01-001" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _read_add_input (line 1266)
# ---------------------------------------------------------------------------

def test_read_add_input_stdin_isatty(monkeypatch):
    """Line 1266: stdin.isatty()=True raises KBLibrarianError."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with pytest.raises(KBLibrarianError, match="No input provided"):
        cli._read_add_input(None)


# ---------------------------------------------------------------------------
# _build_note (lines 1283->1286)
# ---------------------------------------------------------------------------

def test_build_note_no_tags_uses_topic(tmp_path):
    """Lines 1283->1286: when no tags extracted, topic is used as tag."""
    note = cli._build_note(
        source_text="## My note\n\nContent here.",
        parsed_note=None,
        note_id="2026-01-01-my-note",
        topic="test-topic",
        knowledge_type="technique",
        created_date="2026-01-01",
    )
    assert "test-topic" in note.frontmatter["tags"]


# ---------------------------------------------------------------------------
# _extract_title (lines 1327->1330)
# ---------------------------------------------------------------------------

def test_extract_title_from_markdown_heading():
    """Lines 1327->1330: first non-blank line starting with # yields heading text."""
    result = cli._extract_title("# My Heading\n\nContent.", parsed_note=None)
    assert result == "My Heading"


# ---------------------------------------------------------------------------
# _extract_summary (lines 1345->1348, 1355->1348)
# ---------------------------------------------------------------------------

def test_extract_summary_empty_parsed_note_summary():
    """Lines 1345->1348: parsed_note with empty/whitespace summary falls through to source_text."""
    note = Note(
        frontmatter={
            "id": "n", "title": "T", "summary": "   ",
            "topic": "t", "created": "2026-01-01", "updated": "2026-01-01",
            "knowledge_type": "technique", "status": "active", "confidence": "medium",
            "retrieval_phrases": [], "tags": ["t"],
        },
        body="",
    )
    result = cli._extract_summary("First sentence. More text.", parsed_note=note)
    assert result == "First sentence"


def test_extract_summary_skips_heading_line():
    """Lines 1355->1348: heading lines starting with # are skipped."""
    result = cli._extract_summary("# Heading\n\nActual summary line.", parsed_note=None)
    assert result == "Actual summary line"


# ---------------------------------------------------------------------------
# _queue_raw_input (lines 1380-1381, 1387->1389)
# ---------------------------------------------------------------------------

def test_queue_raw_input_stdin_source(tmp_path):
    """Lines 1380-1381: stdin source_name generates timestamped filename."""
    path = cli._queue_raw_input(tmp_path, "some content\n", source_name="stdin")
    assert path.name.startswith("stdin-")
    assert path.suffix == ".md"


def test_queue_raw_input_adds_trailing_newline(tmp_path):
    """Lines 1387->1389: content without trailing newline gets one appended."""
    path = cli._queue_raw_input(tmp_path, "content without newline", source_name="input.md")
    assert path.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# _apply_budget (lines 1412->1418)
# ---------------------------------------------------------------------------

def test_apply_budget_stops_when_exceeded():
    """Lines 1412->1418: second result is dropped when cumulative tokens exceed budget."""
    # Each result is ~116 tokens; budget=50 fits the first (empty selected), not the second.
    big_result = {"title": "word " * 50, "summary": "word " * 50}
    results = [big_result, big_result]
    limited = cli._apply_budget(results, 50)
    # First item always added (selected is empty so guard is False), then second would exceed.
    assert len(limited) == 1


# ---------------------------------------------------------------------------
# _require_note_id (line 1472->exit)
# ---------------------------------------------------------------------------

def test_require_note_id_ambiguous(tmp_path, monkeypatch):
    """Line 1472->exit: multiple matching records raises AmbiguousNoteIdError."""
    r1 = SimpleNamespace(note_id="2026-01-01-note", path=tmp_path / "a" / "note.md")
    r2 = SimpleNamespace(note_id="2026-01-01-note", path=tmp_path / "b" / "note.md")
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [r1, r2])
    with pytest.raises(AmbiguousNoteIdError):
        cli._require_note_id(tmp_path, "2026-01-01-note")


# ---------------------------------------------------------------------------
# _print_reindex_result (lines 1481->1510)
# ---------------------------------------------------------------------------

def test_print_reindex_result_maintenance_scan_all_lists(capsys):
    """Lines 1481->1510: maintenance_scan=True with all review item lists populated."""
    result = SimpleNamespace(
        note_count=10,
        topic_count=3,
        index_backend="fts",
        maintenance_scan=True,
        compaction_clusters=2,
        compaction_review_items=["comp-001", "comp-002"],
        stale_review_items=["stale-001"],
        orphan_review_items=["orphan-001"],
        low_utility_review_items=["low-001"],
        artifacts=[],
    )
    cli._print_reindex_result(result)
    out = capsys.readouterr().out
    assert "Compaction scan:" in out
    assert "comp-001" in out
    assert "comp-002" in out
    assert "stale-001" in out
    assert "orphan-001" in out
    assert "low-001" in out
    assert "Hygiene scan:" in out


def test_print_reindex_result_maintenance_scan_empty_lists(capsys):
    """Lines 1487->1491, 1498->1502, 1502->1506, 1506->1510: empty sub-lists skipped."""
    result = SimpleNamespace(
        note_count=3,
        topic_count=1,
        index_backend="fts",
        maintenance_scan=True,
        compaction_clusters=0,
        compaction_review_items=[],
        stale_review_items=[],
        orphan_review_items=[],
        low_utility_review_items=[],
        artifacts=[],
    )
    cli._print_reindex_result(result)
    out = capsys.readouterr().out
    assert "Compaction scan:" in out
    assert "Hygiene scan: stale=0, orphan=0, low_utility=0" in out
    # No sub-lists printed when empty
    assert "stale_review_item_ids:" not in out


# ---------------------------------------------------------------------------
# Additional branch coverage for remaining missing lines
# ---------------------------------------------------------------------------

def test_handle_ingest_quiet_no_errors(tmp_path, monkeypatch, capsys):
    """Line 436->443: quiet=True with errors=0 (nothing printed, returns 0)."""
    report = SimpleNamespace(errors=0, created_notes=[], operation_id="op-ok")
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "ingest", lambda *_a, **_kw: report)
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), file=None, force=False, quiet=True, resume=False, json=False
    )
    assert cli._handle_ingest(args) == 0
    out, err = capsys.readouterr().out, capsys.readouterr().err
    assert out == "" and err == ""


def test_handle_review_accept_not_changed(tmp_path, monkeypatch, capsys):
    """Line 479->488: review accept with result.changed=False skips auto-commit."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"git": {}})
    monkeypatch.setattr(cli, "accept_review_item", lambda *_a, **_kw: SimpleNamespace(
        message="No change.", changed=False, item_id="item-002"
    ))
    assert cli.main(["review", "accept", "item-002", "--data-dir", str(tmp_path)]) == 0
    assert "No change." in capsys.readouterr().out


def test_handle_context_no_synthesis(tmp_path, monkeypatch, capsys):
    """Lines 587->590: synthesis_markdown is falsy; citation block printed directly."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"retrieval": {"context_budget_tokens": "100"}})
    monkeypatch.setattr(cli, "build_context", lambda *_a, **kw: SimpleNamespace(
        task=kw["task"], mode=kw["mode"], budget=kw["budget"],
        synthesis_markdown="", selected_notes=[], citations=[], message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), mode="coding", budget=100, task="task", json=False, report_miss=False
    )
    assert cli._handle_context(args) == 0
    out = capsys.readouterr().out
    assert "# KB Context" in out
    assert "## Source notes" in out


def test_handle_explore_no_synthesis(tmp_path, monkeypatch, capsys):
    """Lines 668->671: explore synthesis_markdown is falsy; citation block printed."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {"retrieval": {"explore_budget_tokens": "100"}})
    monkeypatch.setattr(cli, "build_explore", lambda *_a, **kw: SimpleNamespace(
        problem=kw["problem"], budget=kw["budget"],
        synthesis_markdown="", selected_notes=[], citations=[], message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_a, **_kw: None)
    args = SimpleNamespace(
        data_dir=str(tmp_path), budget=100, problem="problem", json=False, report_miss=False
    )
    assert cli._handle_explore(args) == 0
    out = capsys.readouterr().out
    assert "# Exploration" in out
    assert "## Source notes" in out


def test_handle_search_budget_zero(tmp_path, monkeypatch):
    """Line 864: budget=0 raises KBLibrarianError in _handle_search."""
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_config", lambda *_: {
        "retrieval": {"default_budget_tokens": "1000"},
    })
    monkeypatch.setattr(cli, "reindex_data_dir", lambda *_a, **_kw: SimpleNamespace(
        note_count=0, topic_count=0, index_backend="fts", maintenance_scan=False, artifacts=[]
    ))
    monkeypatch.setattr(cli, "candidate_source_from_config", lambda *_: SimpleNamespace(
        candidates=lambda *_a, **_kw: []
    ))
    monkeypatch.setattr(cli, "ranker_from_config", lambda *_: SimpleNamespace(
        rank=lambda *_a, **_kw: []
    ))
    # Pass --budget 0 via args directly to avoid CLI budget parsing confusion
    args = SimpleNamespace(
        data_dir=str(tmp_path), query="test", topic=None, knowledge_type=None,
        budget=0, json=False, report_miss=False, with_citations=False,
    )
    with pytest.raises(KBLibrarianError, match="positive integer"):
        cli._handle_search(args)


def test_handle_get_ambiguous(tmp_path, monkeypatch):
    """Lines 931-932: _handle_get with ambiguous note IDs."""
    r1 = SimpleNamespace(note_id="2026-01-01-note", path=tmp_path / "a" / "note.md")
    r2 = SimpleNamespace(note_id="2026-01-01-note", path=tmp_path / "b" / "note.md")
    monkeypatch.setattr(cli, "initialize_data_dir", lambda *_a, **_kw: [])
    monkeypatch.setattr(cli, "load_note_records", lambda *_a, **_kw: [r1, r2])
    args = SimpleNamespace(data_dir=str(tmp_path), id="2026-01-01-note", summary=False)
    from kb_librarian.errors import AmbiguousNoteIdError
    with pytest.raises(AmbiguousNoteIdError):
        cli._handle_get(args)


def test_load_topic_review_counts_non_dict_item(tmp_path):
    """Line 1099: items list contains a non-dict element (continue branch)."""
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "review-items.json").write_text(
        '{"version": 1, "items": ["not a dict", null]}', encoding="utf-8"
    )
    counts, available = cli._load_topic_review_counts(tmp_path, [])
    assert counts == {}
    assert available is True  # state_path existed and was valid JSON with a list


def test_review_target_note_ids_target_notes_not_list():
    """Line 1122->1127: target_notes is not a list (False branch of isinstance check)."""
    item = {"target_notes": None, "payload": None}
    result = cli._review_target_note_ids(item)
    assert result == set()

    item2 = {"target_notes": "not-a-list", "payload": None}
    result2 = cli._review_target_note_ids(item2)
    assert result2 == set()


def test_render_topic_list_review_not_available():
    """Lines 1148->1153: review_available=False with topics → no stale/orphan metrics."""
    topics = ["test-topic"]
    grouped = {"test-topic": [object()]}
    result = cli._render_topic_list(topics, grouped, {}, False)
    assert "test-topic" in result
    assert "stale=" not in result


def test_build_note_with_parsed_tags():
    """Lines 1283->1286: parsed_note has tags → tags not replaced (False branch)."""
    from kb_librarian.notes import parse_note_text
    # Build a note text with tags in frontmatter
    text = (
        "---\n"
        "id: 2026-01-01-test\n"
        "title: Test\n"
        "summary: Summary.\n"
        "topic: my-topic\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "knowledge_type: technique\n"
        "status: active\n"
        "confidence: medium\n"
        "retrieval_phrases: []\n"
        "tags: [custom-tag]\n"
        "---\n"
        "Body.\n"
    )
    parsed = parse_note_text(text, validate=False)
    note = cli._build_note(
        source_text=text,
        parsed_note=parsed,
        note_id="2026-01-01-test",
        topic="my-topic",
        knowledge_type="technique",
        created_date="2026-01-01",
    )
    assert "custom-tag" in note.frontmatter["tags"]


def test_extract_title_empty_line_before_heading():
    """Lines 1327->1330: empty line before heading causes continue, then heading parsed."""
    result = cli._extract_title("\n# Real Heading\nOther.", parsed_note=None)
    assert result == "Real Heading"


def test_extract_summary_empty_sentence_continues():
    """Lines 1355->1348: line produces empty sentence; loop continues to next line."""
    # A line that is just "." gives sentence="" → False branch → loop continues
    result = cli._extract_summary(".\nActual content here.", parsed_note=None)
    assert result == "Actual content here"
