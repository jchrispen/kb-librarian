from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from kb_librarian.compaction import (
    _can_delete_source_note,
    _cluster_reason,
    _component_evidence,
    _dispositions_by_note_id,
    _eligible_or_supersedable_records,
    _eligible_records,
    _jaccard,
    _list_tokens,
    _load_backlinks,
    _load_merge_pairs,
    _load_retrieval_pairs,
    _log_provider_fallback_event,
    _provider_note_payload,
    _record_evidence,
    _risk,
    _string_set,
    _suggested_title,
    apply_compaction_review_item,
    detect_compaction_clusters,
    draft_compaction_proposal,
    resolve_compaction_target,
    scan_compaction_clusters,
)
from kb_librarian.config import default_config
from kb_librarian.errors import KBLibrarianError
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.providers import ProviderFallbackEvent
from kb_librarian.review import add_review_item, review_state_path
from kb_librarian.storage import NoteRecord, canonical_note_path, load_note_records
from kb_librarian.topic_mutations import (
    TopicMove,
    _apply_topic_moves,
    _can_remove_topic_scope,
    _cleanup_old_topic_dirs,
    _copy_topic_scopes,
    _move_one_note,
    _moves_from_payload,
    _relative_path,
    _remove_empty_parents,
    _validate_move_conflicts,
    apply_topic_review_item,
    promote_topics,
    queue_topic_merge_proposal,
    queue_topic_split_proposal,
    rename_topic,
)
from kb_librarian.usage import log_note_use


def _write_note(
    data_dir: Path,
    *,
    note_id: str,
    title: str,
    topic: str = "agent-systems",
    summary: str = "Summary text.",
    body: str = "## Core idea\n\nPrefer precise task-shaped context for coding agents.\n",
    status: str = "active",
    confidence: str = "high",
    knowledge_type: str = "technique",
    tags: list[str] | None = None,
    phrases: list[str] | None = None,
) -> Path:
    note = Note(
        {
            "id": note_id,
            "title": title,
            "summary": summary,
            "topic": topic,
            "created": "2026-05-05",
            "updated": "2026-05-05",
            "knowledge_type": knowledge_type,
            "status": status,
            "confidence": confidence,
            "retrieval_phrases": phrases or [title.lower()],
            "tags": tags or [topic, "context"],
        },
        body,
    )
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, note)
    return path


def _proposal_payload(*, source_ids: list[str], dispositions: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "kind": "proposal",
        "cluster_id": "cluster-manual",
        "source_note_ids": source_ids,
        "frontmatter": {
            "title": "Canonical agent context",
            "summary": "Canonical context summary.",
            "topic": "agent-systems",
            "knowledge_type": "technique",
            "confidence": "medium",
            "sources": [{"type": "manual", "ref": "seed"}],
            "basis": ["manual review"],
            "retrieval_phrases": ["canonical agent context"],
            "tags": ["agent-systems"],
        },
        "body": "## Core idea\n\nUse the canonical note.\n",
        "dispositions": dispositions or [],
    }


def test_scan_compaction_returns_empty_when_below_configured_threshold(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="Alpha context")

    result = scan_compaction_clusters(
        tmp_path,
        config={"review": {"duplicate_cluster_threshold": "not-an-int"}},
    )

    assert result.clusters == []
    assert result.review_item_ids == []


def test_detect_compaction_clusters_uses_link_usage_and_merge_signals(tmp_path):
    for note_id, title in [
        ("2026-05-05-alpha", "Alpha context"),
        ("2026-05-05-beta", "Beta context"),
        ("2026-05-05-gamma", "Gamma context"),
    ]:
        _write_note(
            tmp_path,
            note_id=note_id,
            title=title,
            body=f"## Core idea\n\nUnique body for {title}.\n",
            tags=["distinct"],
            phrases=[],
        )
    records = load_note_records(tmp_path)

    clusters = detect_compaction_clusters(
        records,
        threshold=2,
        backlinks={"2026-05-05-alpha": ["shared-source"], "2026-05-05-beta": ["shared-source"]},
        usage_pairs={("2026-05-05-alpha", "2026-05-05-beta"): 2},
        merge_pairs={("2026-05-05-alpha", "2026-05-05-beta"): 1},
    )

    assert len(clusters) == 1
    assert clusters[0].note_ids == ["2026-05-05-alpha", "2026-05-05-beta"]
    assert "backlink proximity" in clusters[0].evidence[0]
    assert "repeated retrieval overlap 2" in clusters[0].evidence[0]


def test_compaction_helpers_cover_fallbacks_and_path_edges(tmp_path):
    record_path = _write_note(
        tmp_path,
        note_id="2026-05-05-fact",
        title="Fact note",
        topic="facts",
        status="disputed",
        knowledge_type="fact",
        confidence="low",
    )
    record = load_note_records(tmp_path)[0]
    untitled_record = NoteRecord(
        path=record.path,
        topic_path=record.topic_path,
        note=Note({**record.note.frontmatter, "title": "   "}, record.note.body),
    )
    outside_record = NoteRecord(
        path=Path("/outside/2026-05-05-fact.md"),
        topic_path=record.topic_path,
        note=record.note,
    )

    assert _component_evidence([record], {}) == [f"2026-05-05-fact: Fact note ({record_path.as_posix()})"]
    assert _record_evidence(outside_record, data_dir=tmp_path).endswith("(/outside/2026-05-05-fact.md)")
    assert _provider_note_payload(outside_record, data_dir=tmp_path)["path"] == "/outside/2026-05-05-fact.md"
    assert _cluster_reason([]) == "overlapping note signals"
    assert _cluster_reason(["2026-05-05-a"]) == "overlapping note signals"
    assert _suggested_title([untitled_record]) == "Canonical compacted note"
    assert _risk([record]) == "high"
    active_technique = NoteRecord(
        path=record.path,
        topic_path=record.topic_path,
        note=Note({**record.note.frontmatter, "status": "active", "knowledge_type": "technique", "confidence": "high"}, record.note.body),
    )
    active_heuristic = NoteRecord(
        path=record.path,
        topic_path=record.topic_path,
        note=Note({**record.note.frontmatter, "status": "active", "knowledge_type": "heuristic", "confidence": "high"}, record.note.body),
    )
    assert _risk(
        [active_technique, active_heuristic]
    ) == "medium"
    assert _list_tokens("not-a-list") == set()
    assert _string_set("not-a-list") == set()
    assert _jaccard(set(), {"context"}) == 0.0
    assert _can_delete_source_note(record.note, record.note_id, {record.note_id: 1}) is False
    assert _can_delete_source_note(record.note, "not-mentioned", {}) is True


def test_compaction_filter_and_disposition_helpers_cover_status_and_malformed_items(tmp_path):
    active_path = _write_note(tmp_path, note_id="2026-05-05-active", title="Active")
    archived_path = _write_note(tmp_path, note_id="2026-05-05-archived", title="Archived", status="archived")
    superseded_path = _write_note(tmp_path, note_id="2026-05-05-superseded", title="Superseded", status="superseded")
    records = load_note_records(tmp_path)

    assert [record.path for record in _eligible_records(records)] == [active_path]
    assert [record.path for record in _eligible_or_supersedable_records(records)] == [active_path, superseded_path]
    assert archived_path not in [record.path for record in _eligible_or_supersedable_records(records)]
    assert _dispositions_by_note_id({"dispositions": "not-a-list"}) == {}
    assert _dispositions_by_note_id(
        {
            "dispositions": [
                "bad",
                {"note_id": "  "},
                {"note_id": "2026-05-05-active", "recommendation": " Keep ", "rationale": " rationale "},
            ]
        }
    ) == {"2026-05-05-active": {"recommendation": "keep", "rationale": "rationale"}}


def test_compaction_fallback_event_is_appended_to_errors_log(tmp_path):
    event = ProviderFallbackEvent(
        operation="compact:synthesize",
        provider="local",
        model="llama",
        next_provider="mock",
        next_model="mock",
        classification_kind="transient",
        classification_detail="timeout",
        error="timed out",
    )

    _log_provider_fallback_event(tmp_path, phase="compact", event=event, cluster_id="cluster-a")

    assert "stage=provider-compact-fallback" in (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")


def test_compaction_loaders_tolerate_missing_malformed_and_count_valid_pairs(tmp_path, monkeypatch):
    assert _load_backlinks(tmp_path) == {}
    assert _load_retrieval_pairs(tmp_path) == {}
    (tmp_path / ".kb").mkdir(parents=True)
    (tmp_path / ".kb" / "backlinks.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / ".kb" / "usage.log").write_text(
        "\n".join(
            [
                "{not-json",
                json.dumps({"event": "note-use", "note_id": "2026-05-05-a"}),
                json.dumps({"event": "retrieval", "returned_note_ids": ["2026-05-05-b"]}),
                json.dumps({"event": "retrieval", "returned_note_ids": ["2026-05-05-b", "2026-05-05-a", ""]}),
            ]
        ),
        encoding="utf-8",
    )

    assert _load_backlinks(tmp_path) == {}
    (tmp_path / ".kb" / "backlinks.json").write_text(json.dumps(["not-a-dict"]), encoding="utf-8")
    assert _load_backlinks(tmp_path) == {}
    (tmp_path / ".kb" / "backlinks.json").write_text(
        json.dumps({"2026-05-05-a": ["2026-05-05-b", ""], "ignored": "not-a-list"}),
        encoding="utf-8",
    )
    assert _load_backlinks(tmp_path) == {"2026-05-05-a": ["2026-05-05-b"]}
    assert _load_retrieval_pairs(tmp_path) == {("2026-05-05-a", "2026-05-05-b"): 1}

    add_review_item(
        tmp_path,
        queue="merge",
        title="Merge",
        target_notes=["2026-05-05-b", "2026-05-05-a"],
        proposed_action="merge",
        payload={},
        priority="medium",
        fingerprint="merge-pair",
    )
    assert _load_merge_pairs(tmp_path) == {("2026-05-05-a", "2026-05-05-b"): 1}

    monkeypatch.setattr(
        "kb_librarian.compaction.ensure_review_state",
        lambda *args, **kwargs: {
            "items": [
                "not-an-item",
                {"queue": "classification", "target_notes": ["2026-05-05-a", "2026-05-05-b"]},
                {"queue": "merge", "target_notes": "not-a-list"},
            ]
        },
    )
    assert _load_merge_pairs(tmp_path) == {}


def test_resolve_compaction_review_targets_validate_payloads(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    for note_id in ["2026-05-05-a", "2026-05-05-b"]:
        _write_note(tmp_path, note_id=note_id, title=note_id)
    records = load_note_records(tmp_path)
    monkeypatch.setattr(
        "kb_librarian.compaction.ensure_review_state",
        lambda *args, **kwargs: {
            "items": [
                "not-an-item",
                {"id": "classification-1", "queue": "classification", "payload": {}},
                {"id": "compaction-bad", "queue": "compaction", "payload": "bad"},
                {
                    "id": "compaction-missing",
                    "queue": "compaction",
                    "payload": {"kind": "cluster", "cluster_id": "cluster-missing", "cluster_note_ids": ["missing"]},
                },
                {
                    "id": "compaction-one",
                    "queue": "compaction",
                    "payload": {"kind": "cluster", "cluster_id": "cluster-one", "source_note_ids": ["2026-05-05-a"]},
                },
                {
                    "id": "compaction-ok",
                    "queue": "compaction",
                    "target_notes": ["2026-05-05-a", "2026-05-05-b"],
                    "payload": {"kind": "cluster", "cluster_id": ""},
                },
            ],
        },
    )

    with pytest.raises(KBLibrarianError, match="requires a topic"):
        resolve_compaction_target(tmp_path, records, "   ")
    with pytest.raises(KBLibrarianError, match="missing note IDs"):
        resolve_compaction_target(tmp_path, records, "cluster-missing")
    with pytest.raises(KBLibrarianError, match="insufficient material"):
        resolve_compaction_target(tmp_path, records, "cluster-one")
    selected, cluster_id = resolve_compaction_target(tmp_path, records, "compaction-ok")
    assert [record.note_id for record in selected] == ["2026-05-05-a", "2026-05-05-b"]
    assert cluster_id.startswith("cluster-")
    with pytest.raises(KBLibrarianError, match="No compaction cluster"):
        resolve_compaction_target(tmp_path, records, "does-not-exist")


def test_draft_compaction_reports_unresolvable_duplicate_review_item(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    for note_id in ["2026-05-05-a", "2026-05-05-b"]:
        _write_note(tmp_path, note_id=note_id, title=note_id)
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock"}
    monkeypatch.setattr("kb_librarian.compaction.queue_compaction_proposal_review_item", lambda *args, **kwargs: None)

    with pytest.raises(KBLibrarianError, match="could not be resolved"):
        draft_compaction_proposal(tmp_path, config=config, target="agent-systems")


def test_draft_compaction_rejects_resolved_single_record_target(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A")
    records = load_note_records(tmp_path)
    monkeypatch.setattr("kb_librarian.compaction.resolve_compaction_target", lambda *args, **kwargs: (records, "cluster-one"))

    with pytest.raises(KBLibrarianError, match="at least two source notes"):
        draft_compaction_proposal(tmp_path, config=default_config(tmp_path), target="anything")


def test_apply_compaction_validates_payload_and_source_material(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A")
    _write_note(tmp_path, note_id="2026-05-05-b", title="B")
    monkeypatch.setattr("kb_librarian.compaction.require_clean_worktree", lambda *args, **kwargs: None)

    with pytest.raises(KBLibrarianError, match="Only compaction proposal"):
        apply_compaction_review_item(tmp_path, {"payload": {"kind": "cluster"}})
    with pytest.raises(KBLibrarianError, match="at least two"):
        apply_compaction_review_item(tmp_path, {"payload": _proposal_payload(source_ids=["2026-05-05-a"])})
    with pytest.raises(KBLibrarianError, match="missing notes"):
        apply_compaction_review_item(
            tmp_path,
            {"payload": _proposal_payload(source_ids=["2026-05-05-a", "missing"])},
        )
    with pytest.raises(KBLibrarianError, match="missing proposed frontmatter"):
        apply_compaction_review_item(
            tmp_path,
            {"payload": {"kind": "proposal", "source_note_ids": ["2026-05-05-a", "2026-05-05-b"]}},
        )
    with pytest.raises(KBLibrarianError, match="missing proposed body"):
        payload = _proposal_payload(source_ids=["2026-05-05-a", "2026-05-05-b"])
        payload["frontmatter"] = {"title": "Canonical"}
        payload["body"] = ""
        apply_compaction_review_item(tmp_path, {"payload": payload})


def test_apply_compaction_rejects_existing_canonical_destination(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A")
    _write_note(tmp_path, note_id="2026-05-05-b", title="B")
    monkeypatch.setattr("kb_librarian.compaction.require_clean_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr("kb_librarian.compaction.generate_note_id", lambda *args, **kwargs: "existing-canonical")
    _write_note(tmp_path, note_id="existing-canonical", title="Existing", topic="agent-systems")

    with pytest.raises(KBLibrarianError, match="destination already exists"):
        apply_compaction_review_item(
            tmp_path,
            {"payload": _proposal_payload(source_ids=["2026-05-05-a", "2026-05-05-b"])},
        )


def test_apply_compaction_keeps_deletes_archives_and_supersedes(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    source_ids = [
        "2026-05-05-keep",
        "2026-05-05-delete",
        "2026-05-05-archive",
        "2026-05-05-supersede",
        "2026-05-05-used-delete",
    ]
    for note_id in source_ids:
        _write_note(tmp_path, note_id=note_id, title=note_id)
    log_note_use(tmp_path, note_id="2026-05-05-used-delete")
    monkeypatch.setattr("kb_librarian.compaction.require_clean_worktree", lambda *args, **kwargs: None)
    monkeypatch.setattr("kb_librarian.compaction.reindex_data_dir", lambda data_dir: None)
    monkeypatch.setattr(
        "kb_librarian.compaction._can_delete_source_note",
        lambda canonical, source_note_id, usage_counts: source_note_id == "2026-05-05-delete",
    )

    result = apply_compaction_review_item(
        tmp_path,
        {
            "payload": _proposal_payload(
                source_ids=source_ids,
                dispositions=[
                    {"note_id": "2026-05-05-keep", "recommendation": "review"},
                    {"note_id": "2026-05-05-delete", "recommendation": "delete"},
                    {"note_id": "2026-05-05-archive", "recommendation": "archive"},
                    {"note_id": "2026-05-05-supersede", "recommendation": "supersede"},
                    {"note_id": "2026-05-05-used-delete", "recommendation": "delete"},
                ],
            )
        },
    )

    assert result.kept_note_ids == ["2026-05-05-keep"]
    assert result.deleted_note_ids == ["2026-05-05-delete"]
    assert result.archived_note_ids == ["2026-05-05-archive", "2026-05-05-used-delete"]
    assert result.superseded_note_ids == ["2026-05-05-supersede"]
    assert not canonical_note_path(tmp_path, "agent-systems", "2026-05-05-delete").exists()
    archived = read_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-archive"))
    superseded = read_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-supersede"))
    assert archived.frontmatter["status"] == "archived"
    assert "reviewed_by_user" not in archived.frontmatter
    assert superseded.frontmatter["status"] == "superseded"
    assert superseded.frontmatter["reviewed_by_user"] is True


def test_topic_public_validation_errors(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A", topic="alpha")

    with pytest.raises(KBLibrarianError, match="different old and new"):
        rename_topic(tmp_path, old_topic="Alpha", new_topic="alpha")
    with pytest.raises(KBLibrarianError, match="at least one topic"):
        promote_topics(tmp_path, topics=[""], parent="parent")
    with pytest.raises(KBLibrarianError, match="under itself"):
        promote_topics(tmp_path, topics=["alpha"], parent="alpha/child")
    with pytest.raises(KBLibrarianError, match="already under"):
        promote_topics(tmp_path, topics=["parent/alpha"], parent="parent")
    with pytest.raises(KBLibrarianError, match="has no notes to move"):
        rename_topic(tmp_path, old_topic="missing", new_topic="elsewhere")
    with pytest.raises(KBLibrarianError, match="duplicate topics"):
        promote_topics(tmp_path, topics=["alpha", "Alpha"], parent="parent")
    with pytest.raises(KBLibrarianError, match="overlapping parent and child"):
        promote_topics(tmp_path, topics=["alpha", "alpha/child"], parent="parent")
    with pytest.raises(KBLibrarianError, match="at least two"):
        queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["one"])
    with pytest.raises(KBLibrarianError, match="unique after normalization"):
        queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["one", "One"])
    with pytest.raises(KBLibrarianError, match="must differ"):
        queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["alpha", "beta"])
    with pytest.raises(KBLibrarianError, match="no direct notes"):
        queue_topic_split_proposal(tmp_path, topic="missing", target_topics=["one", "two"])
    with pytest.raises(KBLibrarianError, match="different source topics"):
        queue_topic_merge_proposal(tmp_path, left_topic="alpha", right_topic="alpha", target_topic="combined")
    with pytest.raises(KBLibrarianError, match="have no direct notes"):
        queue_topic_merge_proposal(tmp_path, left_topic="missing-a", right_topic="missing-b", target_topic="combined")
    with pytest.raises(KBLibrarianError, match="Only topic split or merge"):
        apply_topic_review_item(tmp_path, {"payload": {"kind": "other"}})


def test_topic_duplicate_proposals_return_existing_ids_and_unresolved_duplicates_raise(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A", topic="alpha")
    _write_note(tmp_path, note_id="2026-05-05-b", title="B", topic="beta")

    split = queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["one", "two"])
    repeated_split = queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["one", "two"])
    assert repeated_split.created is False
    assert repeated_split.review_item_id == split.review_item_id

    merge = queue_topic_merge_proposal(tmp_path, left_topic="alpha", right_topic="beta", target_topic="combined")
    repeated_merge = queue_topic_merge_proposal(tmp_path, left_topic="alpha", right_topic="beta", target_topic="combined")
    assert repeated_merge.created is False
    assert repeated_merge.review_item_id == merge.review_item_id

    monkeypatch.setattr("kb_librarian.topic_mutations.add_review_item", lambda *args, **kwargs: None)
    monkeypatch.setattr("kb_librarian.topic_mutations._existing_topic_proposal_id", lambda *args, **kwargs: None)
    with pytest.raises(KBLibrarianError, match="split proposal already exists"):
        queue_topic_split_proposal(tmp_path, topic="alpha", target_topics=["three", "four"])
    with pytest.raises(KBLibrarianError, match="merge proposal already exists"):
        queue_topic_merge_proposal(tmp_path, left_topic="alpha", right_topic="beta", target_topic="elsewhere")


def test_topic_move_helpers_cover_same_path_conflict_scope_and_cleanup(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    source_path = _write_note(tmp_path, note_id="2026-05-05-a", title="A", topic="alpha")
    destination_conflict = _write_note(tmp_path, note_id="2026-05-05-conflict", title="Conflict", topic="beta")
    same_path_move = TopicMove(
        note_id="2026-05-05-a",
        old_topic="alpha",
        new_topic="alpha",
        source_path=source_path,
        destination_path=source_path,
    )
    _move_one_note(same_path_move)
    assert read_note(source_path).frontmatter["topic"] == "alpha"

    with pytest.raises(KBLibrarianError, match="destination path conflict"):
        _validate_move_conflicts(
            [
                TopicMove(
                    note_id="2026-05-05-a",
                    old_topic="alpha",
                    new_topic="beta",
                    source_path=source_path,
                    destination_path=destination_conflict,
                )
            ]
        )

    assert _can_remove_topic_scope(source_path.parent) is False
    index_one = source_path.parent / "INDEX.md"
    index_two = source_path.parent / "INDEX-2.md"
    keep_index = source_path.parent / "INDEX-extra.md"
    index_one.write_text("generated\n", encoding="utf-8")
    index_two.write_text("generated\n", encoding="utf-8")
    keep_index.write_text("user\n", encoding="utf-8")
    _cleanup_old_topic_dirs(tmp_path, ["missing", "alpha"])
    assert not index_one.exists()
    assert not index_two.exists()
    assert keep_index.exists()

    old_scope = tmp_path / "topics" / "alpha" / "scope.txt"
    old_scope.write_text("alpha scope\n", encoding="utf-8")
    custom_destination_scope = tmp_path / "topics" / "beta" / "scope.txt"
    custom_destination_scope.write_text("custom scope\n", encoding="utf-8")
    _copy_topic_scopes(
        tmp_path,
        moves=[
            TopicMove(
                note_id="2026-05-05-a",
                old_topic="alpha",
                new_topic="beta",
                source_path=source_path,
                destination_path=destination_conflict,
            )
        ],
    )
    assert custom_destination_scope.read_text(encoding="utf-8") == "custom scope\n"
    custom_destination_scope.write_text("Describe what this topic is for and what it is not for.\nThis file is intentionally user-editable.\n", encoding="utf-8")
    _copy_topic_scopes(
        tmp_path,
        moves=[
            TopicMove(
                note_id="2026-05-05-a",
                old_topic="alpha",
                new_topic="beta",
                source_path=source_path,
                destination_path=destination_conflict,
            )
        ],
    )
    assert custom_destination_scope.read_text(encoding="utf-8") == "alpha scope\n"

    empty_topic = tmp_path / "topics" / "empty-topic"
    empty_topic.mkdir()
    (empty_topic / "scope.txt").write_text("empty\n", encoding="utf-8")
    _cleanup_old_topic_dirs(tmp_path, ["empty-topic"])
    assert not empty_topic.exists()

    monkeypatch.setattr("kb_librarian.topic_mutations.require_clean_worktree", lambda *args, **kwargs: None)
    with pytest.raises(KBLibrarianError, match="No topic moves"):
        _apply_topic_moves(tmp_path, [], action="empty", force=False)


def test_topic_payload_and_path_helpers_validate_missing_and_non_mapping_moves(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(tmp_path, note_id="2026-05-05-a", title="A", topic="alpha")

    with pytest.raises(KBLibrarianError, match="no note moves"):
        _moves_from_payload(tmp_path, {"moves": []})
    with pytest.raises(KBLibrarianError, match="missing note"):
        _moves_from_payload(tmp_path, {"moves": [{"note_id": "missing", "to_topic": "beta"}]})
    moves = _moves_from_payload(
        tmp_path,
        {"moves": ["bad", {"note_id": "2026-05-05-a", "to_topic": "Beta Topic", "reason": "retopic"}]},
    )
    assert len(moves) == 1
    assert moves[0].new_topic == "beta-topic"
    assert moves[0].reason == "retopic"
    assert _relative_path(tmp_path, Path("/outside/path.md")) == "/outside/path.md"


def test_existing_topic_proposal_lookup_skips_non_matches(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    monkeypatch.setattr(
        "kb_librarian.topic_mutations.ensure_review_state",
        lambda *args, **kwargs: {
            "items": [
                {"id": "classification-1", "queue": "classification", "payload": {"fingerprint": "topic:match"}},
                {"id": "topic-1", "queue": "topic", "payload": "bad"},
                {"id": "topic-2", "queue": "topic", "payload": {"fingerprint": "topic:other"}},
            ]
        },
    )
    from kb_librarian.topic_mutations import _existing_topic_proposal_id

    assert _existing_topic_proposal_id(tmp_path, "topic:match") is None


def test_remove_empty_parents_stops_when_directory_is_not_empty(tmp_path):
    stop = tmp_path / "topics"
    child = stop / "alpha" / "beta"
    child.mkdir(parents=True)
    (child / "keep.txt").write_text("keep\n", encoding="utf-8")

    _remove_empty_parents(child, stop=stop)

    assert child.exists()
