from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kb_librarian import cli
from kb_librarian.errors import AmbiguousNoteIdError, KBLibrarianError, NoteNotFoundError
from kb_librarian.git_auto import AutoCommitResult
from kb_librarian.ingest import IngestReport
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.review import (
    ReviewStateError,
    _accept_classification_item,
    _accept_dispute_item,
    _accept_merge_item,
    _accept_searchmiss_item,
    _acknowledge_dispute_on_note,
    _append_merge_body_to_note,
    _append_source_by_note_id,
    _append_source_to_note,
    _code_block_after,
    _create_note_from_classification,
    _definition,
    _duplicate_items_from_ingest_log,
    _load_state,
    _render_item_markdown,
    _state_items,
    _target_notes_from_fields,
    _unsupported_items_from_error_log,
    add_review_item,
    collect_review_summary,
    queue_compaction_cluster_review_item,
    reject_review_item,
    review_state_path,
    upsert_hygiene_review_item,
    upsert_search_miss_review_item,
)
from kb_librarian.storage import NoteRecord, canonical_note_path, ensure_topic_layout


def _note(
    *,
    note_id: str = "2026-06-01-note",
    title: str = "Note",
    topic: str = "agent-systems",
    sources: object | None = None,
    disputes: object | None = None,
) -> Note:
    frontmatter = {
        "id": note_id,
        "title": title,
        "summary": f"{title} summary.",
        "topic": topic,
        "created": "2026-06-01",
        "updated": "2026-06-01",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "medium",
        "retrieval_phrases": [title.lower()],
        "tags": [topic],
    }
    if sources is not None:
        frontmatter["sources"] = sources
    if disputes is not None:
        frontmatter["disputes"] = disputes
    return Note(frontmatter=frontmatter, body="## Core idea\n\nBody.\n")


def _write_note(data_dir: Path, *, note_id: str = "2026-06-01-note", topic: str = "agent-systems") -> Path:
    ensure_topic_layout(data_dir, topic)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, _note(note_id=note_id, topic=topic))
    return path


def test_cli_context_mode_and_plain_synthesis_branches(tmp_path, monkeypatch, capsys):
    args = SimpleNamespace(data_dir=str(tmp_path), mode="bad", budget=10, task="task", json=False, report_miss=False)
    with pytest.raises(KBLibrarianError, match="Unsupported mode"):
        cli._handle_context(args)

    monkeypatch.setattr(cli, "build_context", lambda *_args, **kwargs: SimpleNamespace(
        task=kwargs["task"],
        mode=kwargs["mode"],
        budget=kwargs["budget"],
        synthesis_markdown="Synthesized answer.",
        selected_notes=[],
        citations=[],
        message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_args, **_kwargs: None)

    args.mode = "coding"
    assert cli._handle_context(args) == 0
    out = capsys.readouterr().out
    assert "# KB Context" in out
    assert "Synthesized answer." in out


def test_cli_explore_plain_synthesis_and_budget_error(tmp_path, monkeypatch, capsys):
    args = SimpleNamespace(data_dir=str(tmp_path), budget=0, problem="problem", json=False, report_miss=False)
    with pytest.raises(KBLibrarianError, match="positive integer"):
        cli._handle_explore(args)

    monkeypatch.setattr(cli, "build_explore", lambda *_args, **kwargs: SimpleNamespace(
        problem=kwargs["problem"],
        budget=kwargs["budget"],
        synthesis_markdown="Explore answer.",
        selected_notes=[],
        citations=[],
        message="",
    ))
    monkeypatch.setattr(cli, "log_retrieval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "maybe_log_search_miss", lambda *_args, **_kwargs: None)

    args.budget = 25
    assert cli._handle_explore(args) == 0
    out = capsys.readouterr().out
    assert "# Exploration" in out
    assert "Explore answer." in out


def test_cli_compact_graph_topic_and_flag_branches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "draft_compaction_proposal", lambda *_args, **_kwargs: SimpleNamespace(
        review_item_id="compaction-2026-06-01-001",
        cluster_id="cluster-a",
        source_note_ids=["n1", "n2"],
        created=False,
        draft=SimpleNamespace(diff_summary="No changes."),
    ))
    assert cli.main(["compact", "cluster-a", "--json", "--data-dir", str(tmp_path)]) == 0
    assert '"review_item_id": "compaction-2026-06-01-001"' in capsys.readouterr().out

    monkeypatch.setattr(cli, "build_graph", lambda _data_dir: {"graph": True})
    monkeypatch.setattr(cli, "graph_to_dict", lambda graph: {"nodes": [graph]})
    monkeypatch.setattr(cli, "render_graph_summary", lambda graph: f"summary={graph['graph']}\n")
    monkeypatch.setattr(cli, "render_html", lambda graph: "<html>graph</html>")
    html_path = tmp_path / "graph.html"
    assert cli.main(["graph", "--html", str(html_path), "--data-dir", str(tmp_path)]) == 0
    assert html_path.read_text(encoding="utf-8") == "<html>graph</html>"
    assert cli.main(["graph", "--json", "--data-dir", str(tmp_path)]) == 0
    assert '"nodes"' in capsys.readouterr().out
    assert cli.main(["graph", "--data-dir", str(tmp_path)]) == 0
    assert "summary=True" in capsys.readouterr().out

    monkeypatch.setattr(cli, "rename_topic", lambda *_args, **_kwargs: SimpleNamespace(moved_notes=["a"]))
    monkeypatch.setattr(cli, "promote_topics", lambda *_args, **_kwargs: SimpleNamespace(moved_notes=["a", "b"]))
    monkeypatch.setattr(cli, "queue_topic_split_proposal", lambda *_args, **_kwargs: SimpleNamespace(
        created=False,
        review_item_id="topic-2026-06-01-001",
        moves=[{"note_id": "n1"}],
    ))
    monkeypatch.setattr(cli, "queue_topic_merge_proposal", lambda *_args, **_kwargs: SimpleNamespace(
        created=True,
        review_item_id="topic-2026-06-01-002",
        moves=[],
    ))
    assert cli.main(["topic", "rename", "old", "new", "--data-dir", str(tmp_path)]) == 0
    assert "moved 1 note(s)" in capsys.readouterr().out
    assert cli.main(["topic", "promote", "a", "b", "--under", "parent", "--data-dir", str(tmp_path)]) == 0
    assert "moved 2 note(s)" in capsys.readouterr().out
    assert cli.main(["topic", "split", "parent", "--into", "a", "b", "--data-dir", str(tmp_path)]) == 0
    assert "Existing topic split proposal" in capsys.readouterr().out
    assert cli.main(["topic", "merge", "a", "b", "--as", "c", "--data-dir", str(tmp_path)]) == 0
    assert "Created topic merge proposal" in capsys.readouterr().out

    monkeypatch.setattr(cli, "_require_note_id", lambda *_args, **_kwargs: None)
    with pytest.raises(KBLibrarianError, match="non-empty reason"):
        cli._handle_flag_suspect(SimpleNamespace(data_dir=str(tmp_path), id="n1", reason=" "))
    monkeypatch.setattr(cli, "flag_suspect_note", lambda *_args, **_kwargs: SimpleNamespace(
        created_or_updated=False,
        queue="low_utility",
        item_id="low-utility-1",
    ))
    assert cli._handle_flag_suspect(SimpleNamespace(data_dir=str(tmp_path), id="n1", reason="stale")) == 0
    assert "existing review item remains low-utility-1" in capsys.readouterr().out


def test_cli_topic_counts_rendering_and_note_id_errors(tmp_path):
    first = _write_note(tmp_path, note_id="2026-06-01-one", topic="agent-systems")
    second = _write_note(tmp_path, note_id="2026-06-01-two", topic="agent-systems/retrieval")
    records = [
        NoteRecord(path=first, topic_path="agent-systems", note=read_note(first)),
        NoteRecord(path=second, topic_path="agent-systems/retrieval", note=read_note(second)),
    ]
    (tmp_path / "review").mkdir(exist_ok=True)
    (tmp_path / "review" / "review-items.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "id": "stale-2026-06-01-001",
                        "queue": "stale",
                        "status": "pending",
                        "priority": "medium",
                        "title": "Stale",
                        "created": "2026-06-01",
                        "updated": "2026-06-01",
                        "target_notes": ["2026-06-01-one"],
                        "proposed_action": "reverify",
                        "payload": {},
                        "history": [],
                    },
                    {
                        "id": "orphan-2026-06-01-001",
                        "queue": "orphan",
                        "status": "deferred",
                        "priority": "medium",
                        "title": "Orphan",
                        "created": "2026-06-01",
                        "updated": "2026-06-01",
                        "target_notes": [],
                        "proposed_action": "review",
                        "payload": {"note_id": "2026-06-01-two"},
                        "history": [],
                    },
                    {"not": "an item"},
                ],
            }
        ),
        encoding="utf-8",
    )
    counts, available = cli._load_topic_review_counts(tmp_path, records)
    assert available is True
    assert counts["agent-systems"]["stale"] == 1
    assert counts["agent-systems/retrieval"]["orphan"] == 1

    rendered_list = cli._render_topic_list(["agent-systems"], {"agent-systems": [records[0]]}, counts, True)
    assert "stale=1" in rendered_list
    rendered_tree = cli._render_topic_tree(
        ["agent-systems", "agent-systems/retrieval"],
        {"agent-systems": [records[0]], "agent-systems/retrieval": [records[1]]},
        counts,
        True,
    )
    assert "notes=1 direct/2 total" in rendered_tree
    assert "orphan=1" in rendered_tree
    assert "No topics indexed yet." in cli._render_topic_list([], {}, {}, False)
    assert "No topics indexed yet." in cli._render_topic_tree([], {}, {}, False)

    (tmp_path / "review" / "review-items.json").write_text("{bad", encoding="utf-8")
    assert cli._load_topic_review_counts(tmp_path, records) == ({}, False)
    (tmp_path / "review" / "review-items.json").write_text("[]", encoding="utf-8")
    assert cli._load_topic_review_counts(tmp_path, records) == ({}, False)
    (tmp_path / "review" / "review-items.json").write_text('{"items": {}}', encoding="utf-8")
    assert cli._load_topic_review_counts(tmp_path, records) == ({}, False)

    with pytest.raises(NoteNotFoundError):
        cli._require_note_id(tmp_path, "missing")
    duplicate = _write_note(tmp_path, note_id="2026-06-01-one", topic="other-topic")
    assert duplicate.exists()
    with pytest.raises(AmbiguousNoteIdError):
        cli._require_note_id(tmp_path, "2026-06-01-one")


def test_review_upserts_refresh_reopen_and_ignore_resolved_search_misses(tmp_path):
    first = upsert_search_miss_review_item(
        tmp_path,
        title="old query",
        payload={"query": "old query"},
        priority="low",
        created="2026-06-01",
        fingerprint="miss-a",
    )
    assert first == "searchmiss-2026-06-01-001"
    updated = upsert_search_miss_review_item(
        tmp_path,
        title="new query",
        payload={"query": "new query", "misses": 2},
        priority="high",
        created="2026-06-01",
        fingerprint="miss-a",
    )
    assert updated == first
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["items"][0]["title"] == "new query"
    assert state["items"][0]["history"][-1]["action"] == "updated"
    reject_review_item(tmp_path, first)
    assert upsert_search_miss_review_item(
        tmp_path,
        title="ignored",
        payload={"query": "ignored"},
        priority="high",
        created="2026-06-01",
        fingerprint="miss-a",
    ) is None

    stale = upsert_hygiene_review_item(
        tmp_path,
        queue="stale",
        title="Stale",
        target_notes=["n1"],
        payload={"note_id": "n1", "age_days": 30},
        priority="medium",
        created="2026-06-01",
        fingerprint="stale-a",
        source="scan",
    )
    assert stale == "stale-2026-06-01-001"
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    state["items"][1]["status"] = "rejected"
    review_state_path(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    reopened = upsert_hygiene_review_item(
        tmp_path,
        queue="stale",
        title="Stale refreshed",
        target_notes=["n1", "n2"],
        payload={"note_id": "n1", "age_days": 31},
        priority="high",
        created="2026-06-01",
        fingerprint="stale-a",
        source="scan",
    )
    assert reopened == stale
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["items"][1]["status"] == "pending"
    assert state["items"][1]["history"][-1]["action"] == "reopened"


def test_review_compaction_reopens_after_cooldown_and_state_errors(tmp_path):
    existing = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["n2", "n1"],
        title="Cluster A",
        reason="overlap",
        evidence=["same title"],
        suggested_canonical_title="Canonical",
        risk="low",
        cluster_fingerprint="cluster-a",
        cooldown_days=0,
    )
    assert existing == "compaction-2026-06-27-001" or existing.startswith("compaction-")
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    state["items"][0]["status"] = "rejected"
    state["items"][0]["updated"] = (date.today() - timedelta(days=3)).isoformat()
    review_state_path(tmp_path).write_text(json.dumps(state), encoding="utf-8")

    reopened = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["n3"],
        title="Cluster A refreshed",
        reason="overlap",
        evidence=["same title"],
        suggested_canonical_title="Canonical",
        risk="high",
        cluster_fingerprint="cluster-a",
        cooldown_days=1,
    )
    assert reopened == existing
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["items"][0]["status"] == "pending"
    assert state["items"][0]["priority"] == "high"
    assert state["items"][0]["history"][-1]["action"] == "reopened"

    bad_path = tmp_path / "bad-review.json"
    bad_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ReviewStateError, match="must be an object"):
        _load_state(bad_path)
    bad_path.write_text(json.dumps({"version": 999, "items": []}), encoding="utf-8")
    with pytest.raises(ReviewStateError, match="unsupported version"):
        _load_state(bad_path)
    with pytest.raises(ReviewStateError, match="items must be a list"):
        _state_items({"items": {}})
    with pytest.raises(ReviewStateError, match="only objects"):
        _state_items({"items": ["bad"]})
    with pytest.raises(ReviewStateError, match="Unsupported review queue"):
        _definition("unknown")


def test_review_importers_markdown_and_accept_searchmiss_edges(tmp_path):
    assert _duplicate_items_from_ingest_log(tmp_path) == []
    (tmp_path / ".kb").mkdir()
    (tmp_path / ".kb" / "ingested.json").write_text("{bad", encoding="utf-8")
    assert _duplicate_items_from_ingest_log(tmp_path) == []
    (tmp_path / ".kb" / "ingested.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert _duplicate_items_from_ingest_log(tmp_path) == []
    (tmp_path / ".kb" / "ingested.json").write_text(
        json.dumps([{"status": "created"}, {"status": "duplicate", "source_path": "/tmp/dup.md"}]),
        encoding="utf-8",
    )
    imported = _duplicate_items_from_ingest_log(tmp_path)
    assert imported[0]["queue"] == "duplicate"
    assert imported[0]["title"] == "/tmp/dup.md"

    assert _unsupported_items_from_error_log(tmp_path) == []
    (tmp_path / ".kb" / "errors.log").write_text(
        "no marker here\n20260601T120000 Unsupported ingest file extension for /tmp/raw.bin\n",
        encoding="utf-8",
    )
    unsupported = _unsupported_items_from_error_log(tmp_path)
    assert unsupported[0]["queue"] == "unsupported_file"
    assert unsupported[0]["title"] == "raw.bin"

    assert _code_block_after("before marker after", "marker") is None
    assert _code_block_after("before marker\n```markdown\nbody\n```", "marker") == "body"
    assert _target_notes_from_fields({"target_note_ids": ""}) == []
    assert _target_notes_from_fields({"target_note_ids": " n1, ,n2 "}) == ["n1", "n2"]

    item = {"payload": "bad"}
    with pytest.raises(ReviewStateError, match="resolution-note"):
        _accept_searchmiss_item(item, resolution_note="")
    assert _accept_searchmiss_item(item, resolution_note="fixed") == "Accepted search-miss item with a resolution note."
    assert item["payload"]["resolution_note"] == "fixed"


def test_review_render_item_markdown_for_topic_hygiene_and_mutation_edges(tmp_path):
    topic_lines = _render_item_markdown(
        {
            "id": "topic-1",
            "queue": "topic",
            "target_notes": [],
            "proposed_action": "review",
            "payload": {
                "kind": "split",
                "source_topic": "parent",
                "source_topics": ["a", "b"],
                "target_topics": ["c", "d"],
                "target_topic": "merged",
                "moves": ["bad", {"note_id": "n1", "from_topic": "a", "to_topic": "b", "reason": "fit"}],
            },
        }
    )
    assert "- source_topics: a, b" in topic_lines
    assert "  - n1: a -> b (fit)" in topic_lines

    stale_lines = _render_item_markdown(
        {
            "id": "stale-1",
            "queue": "stale",
            "target_notes": [],
            "proposed_action": "refresh",
            "payload": {"note_id": "n1", "evidence": ["old"], "source_quality": "weak"},
        }
    )
    assert "- evidence:" in stale_lines
    orphan_lines = _render_item_markdown(
        {
            "id": "orphan-1",
            "queue": "orphan",
            "target_notes": [],
            "proposed_action": "review",
            "payload": {"note_id": "n1", "suggested_actions": ["link it"]},
        }
    )
    assert "  - link it" in orphan_lines
    low_lines = _render_item_markdown(
        {
            "id": "low-1",
            "queue": "low_utility",
            "target_notes": [],
            "proposed_action": "review",
            "payload": {"note_id": "n1", "reasons": ["unused"], "evidence": ["no hits"]},
        }
    )
    assert low_lines.count("- evidence:") == 1

    path = canonical_note_path(tmp_path, "agent-systems", "2026-06-01-note")
    path.parent.mkdir(parents=True)
    write_note(path, _note(sources="not a list"))
    assert _append_source_to_note(path, source_path="raw/a.md", source_hash="") is True
    assert read_note(path).frontmatter["sources"][-1]["ref"] == "raw/a.md"
    assert _append_source_to_note(path, source_path="raw/a.md", source_hash="") is False

    write_note(path, _note(sources=[None]))
    assert _append_source_to_note(path, source_path="", source_hash="hash-a") is True
    assert read_note(path).frontmatter["sources"][-1]["hash"] == "hash-a"


def test_review_accept_state_guards_and_classification_note_id_branch(tmp_path):
    note_path = _write_note(tmp_path, note_id="2026-06-01-target")
    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"source": "raw/a.md", "source_hash": "hash-a"},
        priority="medium",
        created="2026-06-01",
        fingerprint="classification-a",
    )
    accepted = cli.main(
        [
            "review",
            "accept",
            "classification-2026-06-01-001",
            "--note-id",
            "2026-06-01-target",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert accepted == 0
    assert read_note(note_path).frontmatter["sources"][-1]["hash"] == "hash-a"

    add_review_item(
        tmp_path,
        queue="searchmiss",
        title="Search miss",
        target_notes=[],
        proposed_action="review missed search result",
        payload={"query": "missing"},
        priority="medium",
        created="2026-06-02",
        fingerprint="searchmiss-a",
    )
    with pytest.raises(ReviewStateError, match="resolution-note"):
        cli.accept_review_item(tmp_path, "searchmiss-2026-06-02-001")

    add_review_item(
        tmp_path,
        queue="classification",
        title="Rejected",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Rejected"},
        priority="medium",
        created="2026-06-03",
        fingerprint="classification-b",
    )
    reject_review_item(tmp_path, "classification-2026-06-03-001")
    with pytest.raises(ReviewStateError, match="cannot be accepted"):
        cli.accept_review_item(tmp_path, "classification-2026-06-03-001", topic="a", knowledge_type="technique")
    with pytest.raises(ReviewStateError, match="cannot be rejected"):
        reject_review_item(tmp_path, "classification-2026-06-01-001")


def test_cli_ingest_quiet_error_and_review_accept_auto_commit_branches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ingest", lambda *_args, **_kwargs: IngestReport(operation_id="op-bad", errors=2))
    assert cli._handle_ingest(
        SimpleNamespace(data_dir=str(tmp_path), file=None, force=False, quiet=True, resume=False, json=False)
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ingest completed with 2 errors" in captured.err

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "accept_review_item", lambda *_args, **_kwargs: SimpleNamespace(
        changed=True,
        item_id="classification-2026-06-01-001",
        message="Accepted item.",
    ))

    def fake_auto_commit(*_args, **kwargs):
        calls.append(kwargs)
        return AutoCommitResult(attempted=True, committed=False, message="Nothing to commit.")

    monkeypatch.setattr(cli, "maybe_auto_commit", fake_auto_commit)
    assert cli._handle_review(
        SimpleNamespace(
            data_dir=str(tmp_path),
            action="accept",
            item_id="classification-2026-06-01-001",
            topic=None,
            knowledge_type=None,
            note_id=None,
            append_body=False,
            resolution_note=None,
            force=False,
        )
    ) == 0
    out = capsys.readouterr().out
    assert "Accepted item." in out
    assert "Nothing to commit." in out
    assert calls[0]["operation"] == "review"

    with pytest.raises(KBLibrarianError, match="Unsupported review action"):
        cli._handle_review(
            SimpleNamespace(
                data_dir=str(tmp_path),
                action="archive",
                item_id="classification-2026-06-01-001",
            )
        )


def test_cli_add_note_extraction_fallbacks_and_review_target_review_counts(tmp_path):
    parsed = Note(
        frontmatter={
            "id": "seed",
            "title": " Parsed Title ",
            "summary": " Parsed summary. ",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "medium",
            "retrieval_phrases": [" phrase ", "", 2],
            "tags": ["tag-a", " ", 3],
        },
        body="Body without newline",
    )
    assert cli._extract_body("", parsed_note=parsed, knowledge_type="technique") == "Body without newline\n"
    assert cli._extract_title("", parsed_note=parsed) == "Parsed Title"
    assert cli._extract_summary("", parsed_note=parsed) == "Parsed summary."
    assert cli._extract_string_list(parsed, "retrieval_phrases") == ["phrase"]
    assert cli._extract_string_list(parsed, "tags") == ["tag-a"]

    assert cli._extract_title("\n# Heading\nbody") == "Heading"
    assert cli._extract_title("\n\n") == "Untitled note"
    assert cli._extract_summary("# Heading\nSecond sentence. More.") == "Second sentence"
    assert cli._extract_summary("# Heading\n") == "No summary provided."
    assert cli._extract_string_list(None, "tags") == []
    assert cli._extract_string_list(Note(frontmatter={"tags": "bad"}, body=""), "tags") == []

    built = cli._build_note(
        source_text="Source text.",
        parsed_note=None,
        note_id="2026-06-01-built",
        topic="Agent Systems/Review",
        knowledge_type="technique",
        created_date="2026-06-01",
    )
    assert built.frontmatter["tags"] == ["agent-systems/review"]

    path = _write_note(tmp_path, note_id="2026-06-01-one", topic="agent-systems")
    records = [NoteRecord(path=path, topic_path="agent-systems", note=read_note(path))]
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "review-items.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "review-1",
                        "queue": "classification",
                        "status": "pending",
                        "target_notes": ["missing", "2026-06-01-one"],
                        "payload": {"note_id": " "},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    counts, available = cli._load_topic_review_counts(tmp_path, records)
    assert available is True
    assert counts["agent-systems"]["review"] == 1


def test_review_accept_classification_merge_and_dispute_edges(tmp_path):
    note_path = _write_note(tmp_path, note_id="2026-06-01-target")
    classification = {
        "id": "classification-1",
        "queue": "classification",
        "payload": {"source": "raw/a.md", "source_hash": "hash-a"},
    }
    assert _accept_classification_item(
        tmp_path,
        classification,
        topic=None,
        knowledge_type=None,
        note_id="2026-06-01-target",
    ) == "Accepted by appending source to note 2026-06-01-target."
    assert _accept_classification_item(
        tmp_path,
        classification,
        topic=None,
        knowledge_type=None,
        note_id="2026-06-01-target",
    ) == "Accepted; source already present on note 2026-06-01-target."
    with pytest.raises(ReviewStateError, match="not found"):
        _append_source_by_note_id(tmp_path, note_id="missing", source_path="raw/a.md", source_hash="")
    with pytest.raises(ReviewStateError, match="requires --topic"):
        _accept_classification_item(tmp_path, {"payload": {}}, topic=None, knowledge_type="technique", note_id=None)
    with pytest.raises(ReviewStateError, match="requires --type"):
        _accept_classification_item(tmp_path, {"payload": {}}, topic="agent-systems", knowledge_type="bad", note_id=None)

    created_id = _create_note_from_classification(
        tmp_path,
        payload={"title": "Created Note", "summary": "", "source": "raw/b.md", "source_hash": "hash-b"},
        topic="agent-systems",
        knowledge_type="technique",
    )
    created_path = canonical_note_path(tmp_path, "agent-systems", created_id)
    assert read_note(created_path).frontmatter["sources"] == [
        {"type": "ingest", "ref": "raw/b.md", "hash": "hash-b"}
    ]

    with pytest.raises(ReviewStateError, match="requires --append-body"):
        _accept_merge_item(tmp_path, {"id": "merge-1", "target_notes": ["2026-06-01-target"]}, append_body=False)
    with pytest.raises(ReviewStateError, match="candidate body is missing"):
        _accept_merge_item(tmp_path, {"id": "merge-1", "target_notes": ["2026-06-01-target"], "payload": "bad"}, append_body=True)
    with pytest.raises(ReviewStateError, match="at least one target note"):
        _accept_merge_item(
            tmp_path,
            {"id": "merge-1", "target_notes": [], "payload": {"candidate_body": "Candidate."}},
            append_body=True,
        )
    with pytest.raises(ReviewStateError, match="was not found"):
        _accept_merge_item(
            tmp_path,
            {"id": "merge-1", "target_notes": ["missing"], "payload": {"candidate_body": "Candidate."}},
            append_body=True,
        )

    merge_item = {
        "id": "merge-1",
        "target_notes": ["2026-06-01-target"],
        "payload": {"candidate_body": "Candidate body.", "source": "raw/c.md", "source_hash": "hash-c"},
    }
    assert _accept_merge_item(tmp_path, merge_item, append_body=True) == (
        "Accepted merge into 1 target note(s); body appended on 1 note(s)."
    )
    assert _append_merge_body_to_note(note_path, item_id="merge-1", candidate_body="Candidate body.") is False

    with pytest.raises(ReviewStateError, match="at least one target note"):
        _accept_dispute_item(tmp_path, {"id": "dispute-1", "target_notes": []})
    with pytest.raises(ReviewStateError, match="was not found"):
        _accept_dispute_item(tmp_path, {"id": "dispute-1", "target_notes": ["missing"]})
    assert _accept_dispute_item(tmp_path, {"id": "dispute-1", "target_notes": ["2026-06-01-target"]}) == (
        "Accepted dispute acknowledgement on 1 note(s)."
    )
    assert _acknowledge_dispute_on_note(note_path, item_id="dispute-1") is False
