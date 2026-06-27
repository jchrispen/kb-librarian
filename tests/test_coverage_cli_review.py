from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from kb_librarian import cli
from kb_librarian.context import ContextResult, ContextSelection, ExploreResult
from kb_librarian.errors import KBLibrarianError
from kb_librarian.git_auto import AutoCommitResult
from kb_librarian.indexing import ReindexResult
from kb_librarian.ingest import IngestReport
from kb_librarian.notes import Note
from kb_librarian.review import (
    ReviewStateError,
    _acknowledge_dispute_on_note,
    _append_source_to_note,
    _compaction_priority,
    _cooldown_elapsed,
    _date_part,
    _find_item_by_id,
    _is_visible_pending_item,
    _relative_path,
    _summary_from_block,
    _target_note_paths,
    accept_review_item,
    add_review_item,
    collect_review_summary,
    defer_review_item,
    explain_review_item,
    queue_compaction_cluster_review_item,
    queue_compaction_proposal_review_item,
    queue_duplicate_review_item,
    queue_parser_failure_review_item,
    queue_unsupported_file_review_item,
    reject_review_item,
    render_review_summary,
    review_state_path,
    upsert_hygiene_review_item,
    upsert_search_miss_review_item,
    validate_review_item,
)


def test_main_without_subcommand_prints_help(capsys):
    assert cli.main([]) == 0

    captured = capsys.readouterr()
    assert "KB Librarian local knowledge CLI." in captured.out
    assert "<command>" in captured.out


def test_main_reports_kb_errors_from_handlers(monkeypatch, capsys):
    parser = cli.build_parser()
    parser.set_defaults(handler=lambda _args: (_ for _ in ()).throw(KBLibrarianError("boom")))
    monkeypatch.setattr(cli, "build_parser", lambda: parser)

    assert cli.main([]) == 1

    captured = capsys.readouterr()
    assert captured.err == "error: boom\n"


def test_review_cli_requires_item_and_defer_days(tmp_path, capsys):
    assert cli.main(["review", "accept", "--data-dir", str(tmp_path)]) == 1
    assert "`kb review accept` requires <item-id>." in capsys.readouterr().err

    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate"},
        priority="medium",
        created="2026-05-04",
        fingerprint="classification-cli",
    )

    assert cli.main(["review", "defer", "classification-2026-05-04-001", "--data-dir", str(tmp_path)]) == 1
    assert "requires --days" in capsys.readouterr().err


def test_review_cli_accept_reject_defer_routes(tmp_path, capsys):
    add_review_item(
        tmp_path,
        queue="searchmiss",
        title="missing query",
        target_notes=[],
        proposed_action="review missed search result",
        payload={"query": "missing query"},
        priority="medium",
        created="2026-05-04",
        fingerprint="searchmiss-cli",
    )
    assert (
        cli.main(
            [
                "review",
                "accept",
                "searchmiss-2026-05-04-001",
                "--resolution-note",
                "Added better tags.",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "Accepted search-miss item" in capsys.readouterr().out

    add_review_item(
        tmp_path,
        queue="classification",
        title="Reject me",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Reject me"},
        priority="medium",
        created="2026-05-05",
        fingerprint="reject-cli",
    )
    assert cli.main(["review", "reject", "classification-2026-05-05-001", "--data-dir", str(tmp_path)]) == 0
    assert "Review item rejected." in capsys.readouterr().out

    add_review_item(
        tmp_path,
        queue="classification",
        title="Defer me",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Defer me"},
        priority="medium",
        created="2026-05-06",
        fingerprint="defer-cli",
    )
    assert cli.main(["review", "defer", "classification-2026-05-06-001", "--days", "1", "--data-dir", str(tmp_path)]) == 0
    assert "Deferred until" in capsys.readouterr().out


def test_context_and_explore_cli_json_and_message_branches(tmp_path, monkeypatch, capsys):
    selection = ContextSelection(
        note_id="2026-05-04-note",
        title="Note",
        summary="Summary.",
        topic="agent-systems",
        knowledge_type="technique",
        status="active",
        confidence="medium",
        updated="2026-05-04",
        path="topics/agent-systems/2026-05-04-note.md",
        excerpt="Excerpt",
        retrieval_phrases=["note"],
        tags=["agent-systems"],
        score=42.0,
        reasons=["title"],
        trust_flags=[],
    )
    monkeypatch.setattr(
        cli,
        "build_context",
        lambda *_args, **kwargs: ContextResult(
            task=kwargs["task"],
            mode=kwargs["mode"],
            budget=kwargs["budget"],
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No context found.",
        ),
    )
    assert cli.main(["context", "missing task", "--data-dir", str(tmp_path)]) == 0
    assert "Try: kb search" in capsys.readouterr().out

    monkeypatch.setattr(
        cli,
        "build_context",
        lambda *_args, **kwargs: ContextResult(
            task=kwargs["task"],
            mode=kwargs["mode"],
            budget=kwargs["budget"],
            synthesis_markdown="Use the note.",
            selected_notes=[selection],
            citations=[{"note_id": selection.note_id, "path": selection.path, "title": selection.title}],
        ),
    )
    assert cli.main(["context", "task", "--json", "--data-dir", str(tmp_path)]) == 0
    assert '"selected_notes"' in capsys.readouterr().out

    monkeypatch.setattr(
        cli,
        "build_explore",
        lambda *_args, **kwargs: ExploreResult(
            problem=kwargs["problem"],
            budget=kwargs["budget"],
            synthesis_markdown="",
            selected_notes=[],
            citations=[],
            message="No exploration found.",
        ),
    )
    assert cli.main(["explore", "problem", "--data-dir", str(tmp_path)]) == 0
    assert "Try: kb search" in capsys.readouterr().out

    monkeypatch.setattr(
        cli,
        "build_explore",
        lambda *_args, **kwargs: ExploreResult(
            problem=kwargs["problem"],
            budget=kwargs["budget"],
            synthesis_markdown="Explore this.",
            selected_notes=[selection],
            citations=[{"note_id": selection.note_id, "path": selection.path, "title": selection.title}],
        ),
    )
    assert cli.main(["explore", "problem", "--json", "--data-dir", str(tmp_path)]) == 0
    assert '"problem": "problem"' in capsys.readouterr().out


def test_ingest_cli_json_quiet_error_and_related_paths(tmp_path, monkeypatch, capsys):
    reports: list[IngestReport] = []

    def fake_ingest(*_args, **_kwargs):
        return reports.pop(0)

    monkeypatch.setattr(cli, "ingest", fake_ingest)
    monkeypatch.setattr(cli, "maybe_auto_commit", lambda *_args, **_kwargs: None)

    ok_report = IngestReport(operation_id="op-ok", created_notes=["n1"], processed_files=1)
    reports.append(ok_report)
    source = tmp_path / "raw.md"
    source.write_text("body", encoding="utf-8")
    assert cli.main(["ingest", str(source), "--json", "--data-dir", str(tmp_path)]) == 0
    assert '"operation_id": "op-ok"' in capsys.readouterr().out

    reports.append(IngestReport(operation_id="op-bad", errors=2))
    assert cli.main(["ingest", "--quiet", "--data-dir", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "ingest completed with 2 errors" in captured.err
    assert captured.out == ""


def test_add_invalid_type_and_raw_queue_branches(tmp_path, capsys):
    source = tmp_path / "seed.txt"
    source.write_text("Unclassified note body.", encoding="utf-8")

    assert cli.main(["add", "--type", "nonsense", "--from-file", str(source), "--data-dir", str(tmp_path)]) == 1
    assert "Unsupported knowledge_type" in capsys.readouterr().err

    assert cli.main(["add", "--from-file", str(source), "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Queued raw input at" in out
    assert (tmp_path / "raw").is_dir()


def test_review_state_validation_and_summary_empty_items(tmp_path):
    base_item = {
        "id": "x",
        "queue": "classification",
        "status": "pending",
        "priority": "medium",
        "title": "T",
        "created": "2026-05-04",
        "updated": "2026-05-04",
        "target_notes": [],
        "proposed_action": "review",
        "payload": {},
        "history": [],
    }
    for item, message in (
        ({**base_item, "queue": "bogus"}, "unsupported queue"),
        (
            {**base_item, "status": "bogus"},
            "unsupported status",
        ),
        (
            {**base_item, "priority": "urgent"},
            "unsupported priority",
        ),
    ):
        with pytest.raises(ReviewStateError, match=message):
            validate_review_item(item)

    text = render_review_summary({"classification": 0}, [], max_items=3)
    assert text.startswith("Review items: 0")
    assert "Showing up to" not in text

    state_path = review_state_path(tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ReviewStateError, match="must be an object"):
        collect_review_summary(tmp_path, max_items=1)


def test_search_miss_upsert_updates_pending_but_not_resolved(tmp_path):
    item_id = upsert_search_miss_review_item(
        tmp_path,
        title="original query",
        payload={"query": "original query", "misses": 1},
        priority="low",
        created="2026-05-04",
        fingerprint="miss-fp",
    )
    assert item_id == "searchmiss-2026-05-04-001"

    updated_id = upsert_search_miss_review_item(
        tmp_path,
        title="updated query",
        payload={"query": "updated query", "misses": 2},
        priority="medium",
        created="2026-05-05",
        fingerprint="miss-fp",
    )
    assert updated_id == item_id
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    item = state["items"][0]
    assert item["title"] == "updated query"
    assert item["payload"]["misses"] == 2
    assert item["history"][-1]["action"] == "updated"

    assert accept_review_item(tmp_path, item_id, resolution_note="Resolved.").changed is True
    assert (
        upsert_search_miss_review_item(
            tmp_path,
            title="new title",
            payload={"query": "new title"},
            priority="high",
            created="2026-05-06",
            fingerprint="miss-fp",
        )
        is None
    )


def test_hygiene_upsert_no_change_reopen_and_missing_history(tmp_path):
    item_id = upsert_hygiene_review_item(
        tmp_path,
        queue="stale",
        title="Old note",
        target_notes=["2026-05-04-old"],
        payload={"note_id": "2026-05-04-old", "age_days": 90},
        priority="medium",
        created="2026-05-04",
        fingerprint="stale-fp",
        source="hygiene",
        return_existing=True,
    )
    assert item_id == "stale-2026-05-04-001"
    assert (
        upsert_hygiene_review_item(
            tmp_path,
            queue="stale",
            title="Old note",
            target_notes=["2026-05-04-old"],
            payload={"note_id": "2026-05-04-old", "age_days": 90},
            priority="medium",
            created="2026-05-04",
            fingerprint="stale-fp",
            source="hygiene",
        )
        is None
    )
    assert reject_review_item(tmp_path, item_id).changed is True

    reopened_id = upsert_hygiene_review_item(
        tmp_path,
        queue="stale",
        title="Old note refreshed",
        target_notes=["2026-05-04-old"],
        payload={"note_id": "2026-05-04-old", "age_days": 120},
        priority="high",
        created="2026-05-05",
        fingerprint="stale-fp",
        source="hygiene",
    )
    assert reopened_id == item_id

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    state["items"][0]["history"] = "not-a-list"
    review_state_path(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ReviewStateError, match="history must be a list"):
        upsert_hygiene_review_item(
            tmp_path,
            queue="stale",
            title="Old note refreshed again",
            target_notes=["2026-05-04-old"],
            payload={"note_id": "2026-05-04-old", "age_days": 130},
            priority="high",
            created="2026-05-06",
            fingerprint="stale-fp",
            source="hygiene",
        )


def test_queue_helpers_render_payload_variants(tmp_path):
    duplicate_id = queue_duplicate_review_item(
        tmp_path,
        source_name="dup.md",
        source_path=Path("raw/dup.md"),
        source_hash="abc",
        archived_path=Path("raw/archive/dup.md"),
    )
    unsupported_id = queue_unsupported_file_review_item(tmp_path, source_path=Path("raw/file.bin"))
    parser_id = queue_parser_failure_review_item(tmp_path, source_path=Path("raw/bad.pdf"), error="parse failed")

    assert duplicate_id == "duplicate-" + date.today().isoformat() + "-001"
    assert unsupported_id == "unsupported-" + date.today().isoformat() + "-001"
    assert parser_id == "parser-" + date.today().isoformat() + "-001"
    parser_text = (tmp_path / "review" / "parser-failures.md").read_text(encoding="utf-8")
    assert "Source: `raw/bad.pdf`" in parser_text
    assert "Reason: parse failed" in parser_text


def test_defer_idempotent_due_and_resolved_state_gates(tmp_path):
    add_review_item(
        tmp_path,
        queue="classification",
        title="Candidate",
        target_notes=[],
        proposed_action="review classification manually",
        payload={"title": "Candidate"},
        priority="medium",
        created="2026-05-04",
        fingerprint="defer-idempotent",
    )
    item_id = "classification-2026-05-04-001"
    due = (date.today() + timedelta(days=2)).isoformat()

    first = defer_review_item(tmp_path, item_id, days=2)
    second = defer_review_item(tmp_path, item_id, days=2)
    assert first.changed is True
    assert second.changed is False
    assert second.message == f"Review item already deferred until {due}."

    assert reject_review_item(tmp_path, item_id).changed is True
    with pytest.raises(ReviewStateError, match="cannot be accepted"):
        accept_review_item(tmp_path, item_id, topic="agent-systems", knowledge_type="technique")
    with pytest.raises(ReviewStateError, match="cannot be deferred"):
        defer_review_item(tmp_path, item_id, days=1)


def test_cli_helper_formatting_and_path_branches(tmp_path, monkeypatch, capsys):
    attempted = AutoCommitResult(attempted=True, committed=False, message="Nothing to commit.")
    committed = AutoCommitResult(
        attempted=True,
        committed=True,
        message="Committed KB changes.",
        commit_sha="abc1234",
        paths=("topics/a.md",),
    )

    cli._print_auto_commit_result(None)
    cli._print_auto_commit_result(AutoCommitResult(attempted=False, committed=False, message="disabled"))
    cli._print_auto_commit_result(attempted)
    cli._print_auto_commit_result(committed)
    out = capsys.readouterr().out
    assert "Nothing to commit." in out
    assert "Committed KB changes. (abc1234)" in out
    assert cli._auto_commit_payload(None) == {"attempted": False, "committed": False}
    assert cli._auto_commit_payload(committed)["paths"] == ["topics/a.md"]

    monkeypatch.chdir(tmp_path)
    assert cli._ingest_related_before_paths(None) == []
    assert cli._ingest_related_before_paths("raw.md") == [tmp_path / "raw.md"]

    existing = tmp_path / "raw.md"
    existing.write_text("one", encoding="utf-8")
    (tmp_path / "raw-2.md").write_text("two", encoding="utf-8")
    assert cli._dedupe_path(existing) == tmp_path / "raw-3.md"

    outside = cli._search_citation(
        {
            "id": "n1",
            "title": "Outside",
            "path": "/elsewhere/n1.md",
            "status": "active",
            "confidence": "high",
        },
        data_dir=tmp_path,
    )
    assert outside["path"] == "/elsewhere/n1.md"


def test_cli_note_seed_extractors_and_input_errors(tmp_path, monkeypatch):
    parsed = Note(
        frontmatter={
            "id": "seed",
            "title": " Seed title ",
            "summary": " Seed summary ",
            "retrieval_phrases": [" one ", "", 7],
            "tags": [],
        },
        body="Seed body without newline",
    )
    note = cli._build_note(
        source_text="# ignored\n\nignored",
        parsed_note=parsed,
        note_id="2026-05-04-seed",
        topic="agent-systems",
        knowledge_type="technique",
        created_date="2026-05-04",
    )

    assert note.frontmatter["title"] == "Seed title"
    assert note.frontmatter["summary"] == "Seed summary"
    assert note.frontmatter["retrieval_phrases"] == ["one"]
    assert note.frontmatter["tags"] == ["agent-systems"]
    assert note.body.endswith("\n")

    assert "## Core idea" in cli._extract_body("", parsed_note=None, knowledge_type="technique")
    assert cli._extract_title("\n#\nPlain title\n") == "#"
    assert cli._extract_summary("# Heading only\n\n") == "No summary provided."
    assert cli._try_parse_seed_note("not: valid: frontmatter") is None

    with pytest.raises(KBLibrarianError, match="Could not read input file"):
        cli._read_add_input(str(tmp_path / "missing.md"))

    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    with pytest.raises(KBLibrarianError, match="No input provided"):
        cli._read_add_input(None)


def test_cli_budget_citation_and_reindex_rendering(tmp_path, capsys):
    results = [
        {"id": "a", "title": "Short", "summary": "one"},
        {"id": "b", "title": "Long", "summary": " ".join(["word"] * 40)},
    ]
    assert cli._apply_budget(results, 0) == results
    assert cli._apply_budget(results, 20) == [results[0]]

    cli._print_citation_block(
        [
            {
                "note_id": "a",
                "path": "topics/a.md",
                "title": "Short",
                "confidence": "high",
                "status": "active",
            }
        ]
    )
    citation_text = capsys.readouterr().out
    assert "## Source notes" in citation_text
    assert "**KB sources:** [a](topics/a.md)" in citation_text

    cli._print_reindex_result(
        ReindexResult(
            artifacts=[tmp_path / "INDEX.md"],
            note_count=2,
            topic_count=1,
            index_backend="fts5",
            maintenance_scan=True,
            compaction_clusters=1,
            compaction_review_items=["compaction-1"],
            stale_review_items=["stale-1"],
            orphan_review_items=["orphan-1"],
            low_utility_review_items=["low-1"],
        )
    )
    out = capsys.readouterr().out
    assert "Compaction scan: 1 cluster(s), 1 new review item(s)." in out
    assert "stale_review_item_ids:" in out
    assert "orphan_review_item_ids:" in out
    assert "low_utility_review_item_ids:" in out


def test_review_schema_and_state_error_edges(tmp_path):
    base_item = {
        "id": "x",
        "queue": "classification",
        "status": "pending",
        "priority": "medium",
        "title": "T",
        "created": "2026-05-04",
        "updated": "2026-05-04",
        "target_notes": [],
        "proposed_action": "review",
        "payload": {},
        "history": [],
    }
    for item, message in (
        ({**base_item, "target_notes": "not-list"}, "target_notes must be a list"),
        ({**base_item, "payload": []}, "payload must be an object"),
        ({**base_item, "history": {}}, "history must be a list"),
        ({**base_item, "title": 12}, "title must be a string"),
        ({**base_item, "defer_until": 12}, "defer_until must be an ISO date string"),
    ):
        with pytest.raises(ReviewStateError, match=message):
            validate_review_item(item)

    state_path = review_state_path(tmp_path)
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ReviewStateError, match="Malformed review state"):
        collect_review_summary(tmp_path, max_items=1)

    state_path.write_text(json.dumps({"version": 999, "items": []}), encoding="utf-8")
    with pytest.raises(ReviewStateError, match="unsupported version"):
        collect_review_summary(tmp_path, max_items=1)

    state_path.write_text(json.dumps({"version": 1, "items": [{}]}), encoding="utf-8")
    with pytest.raises(ReviewStateError, match="missing required fields"):
        collect_review_summary(tmp_path, max_items=1)


def test_review_rendering_and_visibility_variants(tmp_path):
    add_review_item(
        tmp_path,
        queue="classification",
        title="Future",
        target_notes=["missing-note"],
        proposed_action="review classification manually",
        payload={"title": "Future"},
        priority="high",
        created="2026-05-04",
        fingerprint="future",
    )
    defer_review_item(tmp_path, "classification-2026-05-04-001", days=1)
    explained = explain_review_item(tmp_path, "classification-2026-05-04-001")
    assert "defer_until:" in explained
    assert "missing-note -> (missing)" in explained

    summary = render_review_summary(
        {"classification": 1},
        [SimpleNamespace(kind="classification", item_id="classification-2026-05-04-001", summary="Future")],
        max_items=5,
    )
    assert "Showing up to 5 items:" in summary
    assert "Suggested time: choose 1 item(s) now." in summary

    with pytest.raises(ReviewStateError, match="positive integer"):
        defer_review_item(tmp_path, "classification-2026-05-04-001", days=0)


def test_review_duplicate_and_compaction_queue_branches(tmp_path):
    duplicate_id = queue_duplicate_review_item(
        tmp_path,
        source_name="dup.md",
        source_path=Path("raw/dup.md"),
        source_hash="abc",
        archived_path=None,
    )
    assert duplicate_id == "duplicate-" + date.today().isoformat() + "-001"
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert "archived_path" not in state["items"][0]["payload"]

    cluster_id = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["n2", "n1"],
        title="Cluster A",
        reason="overlap",
        evidence=["same title", "same body"],
        suggested_canonical_title="Canonical",
        risk="high",
        cluster_fingerprint="cluster-fp",
        cooldown_days=0,
    )
    assert cluster_id == "compaction-" + date.today().isoformat() + "-001"
    assert (
        queue_compaction_cluster_review_item(
            tmp_path,
            cluster_id="cluster-a",
            source_note_ids=["n2", "n1"],
            title="Cluster A",
            reason="overlap",
            evidence=[],
            suggested_canonical_title="Canonical",
            risk="low",
            cluster_fingerprint="cluster-fp",
            cooldown_days=0,
        )
        is None
    )

    reject_review_item(tmp_path, cluster_id)
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    state["items"][1]["updated"] = "2026-01-01"
    review_state_path(tmp_path).write_text(json.dumps(state), encoding="utf-8")

    reopened = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["n1"],
        title="Cluster A reopened",
        reason="overlap again",
        evidence=["still overlapping"],
        suggested_canonical_title="Canonical",
        risk="low",
        cluster_fingerprint="cluster-fp",
        cooldown_days=0,
    )
    assert reopened == cluster_id
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["items"][1]["status"] == "pending"
    assert state["items"][1]["history"][-1]["action"] == "reopened"


def test_review_compaction_proposal_markdown_variants(tmp_path):
    draft = SimpleNamespace(
        frontmatter={"title": "Canonical"},
        body="## Core idea\n\nMerged body.\n",
        diff_summary="keeps canonical details",
        dispositions=[
            SimpleNamespace(note_id="n1", recommendation="keep", rationale="canonical"),
        ],
    )

    item_id = queue_compaction_proposal_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["n1", "n2"],
        title="Review proposal",
        draft=draft,
        evidence=[f"evidence {index}" for index in range(12)],
        cluster_fingerprint="proposal-fp",
    )

    assert item_id == "compaction-" + date.today().isoformat() + "-001"
    text = (tmp_path / "review" / "pending-compaction.md").read_text(encoding="utf-8")
    assert "- evidence 9" in text
    assert "evidence 10" not in text
    assert "- dispositions:" in text
    assert "n1: keep - canonical" in text
    assert "- proposed_frontmatter:" in text
    assert "- proposed_body:" in text


def test_review_helper_tail_branches_cover_dates_paths_and_visibility(tmp_path):
    assert _date_part("report-20260627.txt") == "2026-06-27"
    assert _cooldown_elapsed("not-a-date", 1) is False
    assert _compaction_priority("high", proposal=False) == "high"
    assert _compaction_priority("low", proposal=False) == "low"
    assert _compaction_priority("ignored", proposal=True) == "medium"
    assert _summary_from_block("```\n```", (r"Title: (.+)",)) == "(no summary)"
    assert _summary_from_block("bad\nTitle: Captured", (r"Title: (.+)",)) == "Captured"
    assert _relative_path(tmp_path, Path("/outside/review.md")) == "/outside/review.md"

    visible_without_due = {"status": "deferred", "defer_until": ""}
    hidden_future = {"status": "deferred", "defer_until": (date.today() + timedelta(days=3)).isoformat()}
    rejected = {"status": "rejected"}
    assert _is_visible_pending_item(visible_without_due) is True
    assert _is_visible_pending_item(hidden_future) is False
    assert _is_visible_pending_item(hidden_future, include_deferred=True) is True
    assert _is_visible_pending_item(rejected) is False

    with pytest.raises(ReviewStateError, match="Unknown review item"):
        _find_item_by_id({"version": 1, "items": []}, "missing")

    item = {"target_notes": "not-a-list"}
    assert _target_note_paths(tmp_path, item) == []


def test_review_note_mutation_helpers_cover_duplicate_and_missing_paths(tmp_path):
    note_path = tmp_path / "topics" / "agent-systems" / "2026-06-01-note.md"
    note_path.parent.mkdir(parents=True)
    note = Note(
        {
            "id": "2026-06-01-note",
            "title": "Note",
            "summary": "Summary",
            "topic": "agent-systems",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "knowledge_type": "technique",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": ["note"],
            "tags": ["agent-systems"],
            "sources": [{"type": "ingest", "ref": "raw/source.md", "hash": "hash-a"}],
            "disputes": [{"review_item_id": "dispute-1", "acknowledged": True}],
            "reviewed_by_user": True,
        },
        "## Core idea\n\nBody.\n",
    )
    note_path.write_text(note.to_markdown(), encoding="utf-8")

    assert _append_source_to_note(note_path, source_path="", source_hash="") is False
    assert _append_source_to_note(note_path, source_path="raw/source.md", source_hash="") is False
    assert _append_source_to_note(note_path, source_path="", source_hash="hash-a") is False
    assert _acknowledge_dispute_on_note(note_path, item_id="dispute-1") is False
    assert _acknowledge_dispute_on_note(note_path, item_id="dispute-2") is True
