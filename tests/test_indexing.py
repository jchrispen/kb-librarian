from __future__ import annotations

import json
import sqlite3

import pytest

from kb_librarian.config import default_config
from kb_librarian.errors import KBLibrarianError, SearchIndexError
from kb_librarian.indexing import index_document, reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.retrieval import (
    CandidateQuery,
    LexicalRanker,
    candidate_source_from_config,
    embedding_seam_status,
    ranker_from_config,
)
from kb_librarian.search_index import (
    build_lexical_index,
    load_backend,
    query_candidates,
    score_document,
    tokenize_query,
)
from kb_librarian.storage import canonical_note_path


def _note_frontmatter(
    *,
    note_id: str,
    title: str,
    topic: str,
    knowledge_type: str = "technique",
    summary: str = "Summary text.",
) -> dict[str, object]:
    return {
        "id": note_id,
        "title": title,
        "summary": summary,
        "topic": topic,
        "created": "2026-05-05",
        "updated": "2026-05-05",
        "knowledge_type": knowledge_type,
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": ["local index"],
        "tags": ["indexing"],
    }


def test_reindex_generates_markdown_indexes_backlinks_and_manifest(tmp_path):
    initialize_data_dir(tmp_path)
    note_one = Note(
        _note_frontmatter(
            note_id="2026-05-05-cli-contract",
            title="CLI contract",
            topic="agent-systems",
        ),
        "See also 2026-05-05-local-index-note.\n",
    )
    note_two = Note(
        _note_frontmatter(
            note_id="2026-05-05-local-index-note",
            title="Local index",
            topic="agent-systems",
            knowledge_type="fact",
        ),
        "Index details.\n",
    )

    write_note(canonical_note_path(tmp_path, "agent-systems", note_one.frontmatter["id"]), note_one)
    write_note(canonical_note_path(tmp_path, "agent-systems", note_two.frontmatter["id"]), note_two)

    first = reindex_data_dir(tmp_path)
    second = reindex_data_dir(tmp_path)

    assert first.note_count == 2
    assert second.topic_count == 1
    assert (tmp_path / "INDEX.md").is_file()
    assert (tmp_path / "topics" / "agent-systems" / "INDEX.md").is_file()
    assert (tmp_path / ".kb" / "fts.sqlite").is_file()

    top_index = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    topic_index = (tmp_path / "topics" / "agent-systems" / "INDEX.md").read_text(encoding="utf-8")
    assert "agent-systems" in top_index
    assert "CLI contract" in topic_index
    assert "| Type | Confidence | Status | Updated |" in topic_index

    backlinks = json.loads((tmp_path / ".kb" / "backlinks.json").read_text(encoding="utf-8"))
    assert backlinks["2026-05-05-local-index-note"] == ["2026-05-05-cli-contract"]

    manifest = json.loads((tmp_path / ".kb" / "index-manifest.json").read_text(encoding="utf-8"))
    assert manifest["note_count"] == 2
    assert manifest["topic_count"] == 1
    assert manifest["build_time"] == "2026-05-05T00:00:00Z"
    assert manifest["indexed_files"] == sorted(manifest["indexed_files"])

    # Deterministic for unchanged notes.
    assert (tmp_path / ".kb" / "index-manifest.json").read_text(encoding="utf-8") == json.dumps(
        manifest,
        indent=2,
        sort_keys=True,
    ) + "\n"


def test_reindex_supports_nested_topics_and_creates_parent_child_artifacts(tmp_path):
    initialize_data_dir(tmp_path)
    nested = Note(
        _note_frontmatter(
            note_id="2026-05-05-retrieval-note",
            title="Retrieval note",
            topic="agent-systems/retrieval",
        ),
        "Nested topic body.\n",
    )
    write_note(
        canonical_note_path(tmp_path, "agent-systems/retrieval", "2026-05-05-retrieval-note"),
        nested,
    )

    result = reindex_data_dir(tmp_path)

    assert result.note_count == 1
    assert result.topic_count == 2
    assert (tmp_path / "topics" / "agent-systems" / "scope.txt").is_file()
    assert (tmp_path / "topics" / "agent-systems" / "retrieval" / "scope.txt").is_file()
    assert (tmp_path / "topics" / "agent-systems" / "INDEX.md").is_file()
    assert (tmp_path / "topics" / "agent-systems" / "retrieval" / "INDEX.md").is_file()
    assert "2026-05-05-retrieval-note" in (
        tmp_path / "topics" / "agent-systems" / "retrieval" / "INDEX.md"
    ).read_text(encoding="utf-8")
    assert "- [agent-systems](topics/agent-systems/)" in (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert "- [agent-systems/retrieval](topics/agent-systems/retrieval/)" in (
        tmp_path / "INDEX.md"
    ).read_text(encoding="utf-8")


def test_index_document_includes_required_search_fields(tmp_path):
    initialize_data_dir(tmp_path)
    note = Note(
        _note_frontmatter(
            note_id="2026-05-05-agent-heuristic",
            title="Agent heuristic",
            topic="agent-systems",
            knowledge_type="heuristic",
        )
        | {
            "agent_use": ["architecture review"],
            "applies_when": ["designing CLI tools"],
            "does_not_apply_when": ["casual browsing"],
        },
        "Body content here.\n",
    )
    path = canonical_note_path(tmp_path, "agent-systems", "2026-05-05-agent-heuristic")
    write_note(path, note)

    result = reindex_data_dir(tmp_path)
    assert result.note_count == 1

    from kb_librarian.storage import load_note_records

    record = load_note_records(tmp_path)[0]
    document = index_document(record)
    assert document["id"] == "2026-05-05-agent-heuristic"
    assert document["title"] == "Agent heuristic"
    assert document["summary"] == "Summary text."
    assert document["topic"] == "agent-systems"
    assert document["knowledge_type"] == "heuristic"
    assert document["tags"] == "indexing"
    assert document["retrieval_phrases"] == "local index"
    assert document["agent_use"] == "architecture review"
    assert document["applies_when"] == "designing CLI tools"
    assert document["does_not_apply_when"] == "casual browsing"
    assert document["body"] == "Body content here.\n"
    assert document["status"] == "active"
    assert document["confidence"] == "high"
    assert document["updated"] == "2026-05-05"


def test_scoring_favors_title_matches_with_config_weights():
    query = "cli contract"
    tokens = tokenize_query(query)
    weights = {
        "title_weight": 10,
        "summary_weight": 1,
        "retrieval_phrase_weight": 1,
        "tag_weight": 1,
        "body_weight": 1,
    }
    title_match = {
        "id": "2026-05-05-a",
        "title": "CLI contract pattern",
        "summary": "unrelated",
        "tags": "indexing",
        "retrieval_phrases": "index",
        "body": "miscellaneous text",
    }
    body_match = {
        "id": "2026-05-05-b",
        "title": "General note",
        "summary": "unrelated",
        "tags": "indexing",
        "retrieval_phrases": "index",
        "body": "This body mentions cli contract once.",
    }

    assert score_document(title_match, query=query, tokens=tokens, weights=weights) > score_document(
        body_match,
        query=query,
        tokens=tokens,
        weights=weights,
    )


def test_reindex_paginates_large_topic_indexes_deterministically(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["indexes"]["topic_page_size"] = 2
    note_ids = [
        "2026-05-05-index-page-c",
        "2026-05-05-index-page-a",
        "2026-05-05-index-page-e",
        "2026-05-05-index-page-b",
        "2026-05-05-index-page-d",
    ]
    for note_id in note_ids:
        note = Note(
            _note_frontmatter(
                note_id=note_id,
                title=note_id.rsplit("-", maxsplit=1)[-1].upper(),
                topic="agent-systems",
                summary=f"Summary for {note_id}.",
            ),
            f"Body for {note_id} with pagination retrieval.\n",
        )
        write_note(canonical_note_path(tmp_path, "agent-systems", note_id), note)

    first = reindex_data_dir(tmp_path, config=config)
    second = reindex_data_dir(tmp_path, config=config)

    assert first.note_count == 5
    assert second.note_count == 5
    page_one = (tmp_path / "topics" / "agent-systems" / "INDEX.md").read_text(encoding="utf-8")
    page_two = (tmp_path / "topics" / "agent-systems" / "INDEX-2.md").read_text(encoding="utf-8")
    page_three = (tmp_path / "topics" / "agent-systems" / "INDEX-3.md").read_text(encoding="utf-8")

    assert "Page 1 of 3." in page_one
    assert "Navigation: [Next](INDEX-2.md)" in page_one
    assert "2026-05-05-index-page-a" in page_one
    assert "2026-05-05-index-page-b" in page_one
    assert "2026-05-05-index-page-c" not in page_one
    assert "Navigation: [Previous](INDEX.md) | [Next](INDEX-3.md)" in page_two
    assert "2026-05-05-index-page-c" in page_two
    assert "2026-05-05-index-page-d" in page_two
    assert "Navigation: [Previous](INDEX-2.md)" in page_three
    assert "2026-05-05-index-page-e" in page_three
    assert page_three == (tmp_path / "topics" / "agent-systems" / "INDEX-3.md").read_text(encoding="utf-8")


def test_reindex_paginates_top_level_index_and_cleans_stale_pages(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["indexes"]["top_level_page_size"] = 2
    for topic in ("alpha", "beta", "gamma"):
        note_id = f"2026-05-05-{topic}-note"
        note = Note(
            _note_frontmatter(
                note_id=note_id,
                title=f"{topic.title()} note",
                topic=topic,
                summary=f"Summary for {topic}.",
            ),
            f"Body for {topic}.\n",
        )
        write_note(canonical_note_path(tmp_path, topic, note_id), note)

    reindex_data_dir(tmp_path, config=config)
    top_page_one = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    top_page_two = tmp_path / "INDEX-2.md"

    assert "Page 1 of 2." in top_page_one
    assert "- [alpha](topics/alpha/)" in top_page_one
    assert "- [beta](topics/beta/)" in top_page_one
    assert "- [gamma](topics/gamma/)" not in top_page_one
    assert top_page_two.is_file()
    assert "- [gamma](topics/gamma/)" in top_page_two.read_text(encoding="utf-8")

    config["indexes"]["top_level_page_size"] = 10
    reindex_data_dir(tmp_path, config=config)

    assert not top_page_two.exists()
    assert "Page 1 of 2." not in (tmp_path / "INDEX.md").read_text(encoding="utf-8")


def test_retrieval_uses_lexical_index_when_markdown_indexes_are_paginated(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["indexes"]["topic_page_size"] = 1
    target_id = "2026-05-05-late-page-note"
    notes = [
        ("2026-05-05-early-page-note", "Early page", "ordinary body"),
        (target_id, "Late page", "needle phrase for lexical retrieval"),
    ]
    for note_id, title, body in notes:
        note = Note(
            _note_frontmatter(
                note_id=note_id,
                title=title,
                topic="agent-systems",
                summary=body,
            ),
            f"{body}\n",
        )
        write_note(canonical_note_path(tmp_path, "agent-systems", note_id), note)

    reindex_data_dir(tmp_path, config=config)
    from kb_librarian.search_index import query_candidates

    matches = query_candidates(tmp_path / ".kb" / "fts.sqlite", query="needle phrase")

    assert (tmp_path / "topics" / "agent-systems" / "INDEX-2.md").is_file()
    assert target_id in [match["id"] for match in matches]


def test_retrieval_seam_uses_lexical_source_and_ranker_by_default(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    target_id = "2026-05-05-seam-note"
    note = Note(
        _note_frontmatter(
            note_id=target_id,
            title="Embedding seam retrieval",
            topic="agent-systems",
            summary="Lexical retrieval remains the active source.",
        ),
        "Future embedding support should not change default lexical retrieval.\n",
    )
    write_note(canonical_note_path(tmp_path, "agent-systems", target_id), note)
    reindex_data_dir(tmp_path, config=config)

    source = candidate_source_from_config(tmp_path, config)
    candidates = source.candidates(CandidateQuery("embedding seam retrieval"))
    ranked = ranker_from_config(config).rank(candidates, query="embedding seam retrieval")

    assert source.name == "lexical"
    assert [item["id"] for item in ranked] == [target_id]


# --- retrieval.py gap coverage ---

def test_lexical_ranker_filters_zero_score_candidates():
    ranker = LexicalRanker(weights={"title_weight": 5})
    # Candidates with no matching tokens score 0 and must be dropped.
    candidates = [
        {
            "id": "2026-01-01-a",
            "title": "no match here",
            "summary": "",
            "tags": "",
            "retrieval_phrases": "",
            "body": "",
            "updated": "2026-01-01",
        }
    ]
    result = ranker.rank(candidates, query="zzzunlikelytokenzzzz")
    assert result == []


def test_candidate_source_from_config_raises_without_retrieval_mapping():
    with pytest.raises(KBLibrarianError, match="retrieval must be a mapping"):
        candidate_source_from_config(None, {})


def test_candidate_source_from_config_raises_when_lexical_index_disabled():
    config = {"retrieval": {"lexical_index": False}}
    with pytest.raises(KBLibrarianError, match="No supported retrieval source"):
        candidate_source_from_config(None, config)


def test_ranker_from_config_raises_without_retrieval_mapping():
    with pytest.raises(KBLibrarianError, match="retrieval must be a mapping"):
        ranker_from_config({})


def test_embedding_seam_status_returns_false_without_retrieval_mapping():
    status = embedding_seam_status({})
    assert status == {"configured": False, "enabled": False, "supported": False}


def test_embedding_seam_status_with_retrieval_mapping():
    config = {"retrieval": {"lexical_index": True, "embedding_provider": "openai"}}
    status = embedding_seam_status(config)
    assert status["configured"] is True
    assert status["supported"] is False


# --- search_index.py gap coverage ---

def _make_plain_db(db_path):
    """Create a minimal SQLite search DB without FTS5 (plain backend)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE notes (
            id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL,
            summary TEXT NOT NULL, topic TEXT NOT NULL, knowledge_type TEXT NOT NULL,
            tags TEXT NOT NULL, retrieval_phrases TEXT NOT NULL, agent_use TEXT NOT NULL,
            applies_when TEXT NOT NULL, does_not_apply_when TEXT NOT NULL,
            body TEXT NOT NULL, status TEXT NOT NULL, confidence TEXT NOT NULL,
            updated TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO metadata VALUES ('backend', 'plain')")
    conn.commit()
    conn.close()


def test_load_backend_raises_when_db_missing(tmp_path):
    with pytest.raises(SearchIndexError, match="not found"):
        load_backend(tmp_path / "nonexistent.sqlite")


def test_load_backend_returns_plain_when_no_metadata_row(tmp_path):
    db_path = tmp_path / "fts.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()
    assert load_backend(db_path) == "plain"


def test_query_candidates_empty_query_returns_nothing(tmp_path):
    db_path = tmp_path / "fts.sqlite"
    _make_plain_db(db_path)
    result = query_candidates(db_path, query="")
    assert result == []


def test_query_candidates_with_plain_backend(tmp_path):
    db_path = tmp_path / "fts.sqlite"
    _make_plain_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-01-01-note", "/path/to/note.md", "Plain note",
            "summary", "topic", "technique", "tag", "phrase",
            "", "", "", "body text", "active", "high", "2026-01-01",
        ),
    )
    conn.commit()
    conn.close()
    result = query_candidates(db_path, query="plain note")
    ids = [r["id"] for r in result]
    assert "2026-01-01-note" in ids


def test_tokenize_query_deduplicates_repeated_tokens():
    tokens = tokenize_query("cli cli contract cli")
    assert tokens == ["cli", "contract"]


def test_load_backend_raises_on_corrupted_db(tmp_path):
    db_path = tmp_path / "bad.sqlite"
    db_path.write_bytes(b"not a sqlite database")
    with pytest.raises(SearchIndexError, match="Invalid search index"):
        load_backend(db_path)


def test_render_page_navigation_single_page_no_nav_links():
    from kb_librarian.indexing import _render_page_navigation
    # With only 1 page, navigation should have no Previous/Next links.
    lines = _render_page_navigation(1, 1)
    assert "Navigation:" not in "\n".join(lines)
    assert "Pages:" in "\n".join(lines)


def test_reindex_raises_when_scan_clusters_called_without_config(tmp_path):
    initialize_data_dir(tmp_path)
    with pytest.raises(ValueError, match="config is required"):
        from kb_librarian.indexing import reindex_data_dir as _reindex
        _reindex(tmp_path, scan_clusters=True, config=None)


def test_group_records_returns_empty_when_topics_dir_absent(tmp_path):
    from kb_librarian.indexing import group_records_for_indexing
    # data_dir without a topics/ dir → grouped should just be {} for no records
    result = group_records_for_indexing(tmp_path, [])
    assert result == {}


def test_topic_scope_summary_returns_default_when_all_lines_blank(tmp_path):
    from kb_librarian.indexing import _topic_scope_summary
    topic_dir = tmp_path / "topics" / "test"
    topic_dir.mkdir(parents=True)
    (topic_dir / "scope.txt").write_text("\n\n   \n", encoding="utf-8")
    assert _topic_scope_summary(topic_dir) == "topic notes"


def test_configured_page_size_returns_default_for_missing_indexes(tmp_path):
    from kb_librarian.indexing import _configured_page_size
    assert _configured_page_size({"no_indexes": True}, key="topic_page_size", default=25) == 25
    assert _configured_page_size({"indexes": {"topic_page_size": True}}, key="topic_page_size", default=25) == 25
    assert _configured_page_size({"indexes": {"topic_page_size": 0}}, key="topic_page_size", default=25) == 25


def test_stale_index_pages_skips_topics_root_when_absent(tmp_path):
    from kb_librarian.indexing import _stale_generated_index_pages
    # If topics/ doesn't exist, should not fail and return candidates from data_dir only.
    result = _stale_generated_index_pages(tmp_path, expected_paths=set())
    assert result == []


def test_score_document_exact_id_match_boosts_score():
    note_id = "2026-01-01-exact"
    doc = {
        "id": note_id,
        "title": "Some title",
        "summary": "summary",
        "tags": "",
        "retrieval_phrases": "",
        "body": "",
    }
    tokens = tokenize_query(note_id)
    score = score_document(doc, query=note_id, tokens=tokens, weights={})
    # Exact id match adds 10_000 to score
    assert score >= 10_000.0


def test_reindex_with_scan_clusters_calls_compaction_and_hygiene_queues(tmp_path, monkeypatch):
    # Lines 106-115: scan_clusters=True triggers scan_compaction_clusters and
    # scan_hygiene_queues imports and calls inside reindex_data_dir.
    from types import SimpleNamespace

    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock"}

    compaction_called = []
    hygiene_called = []

    monkeypatch.setattr(
        "kb_librarian.compaction.scan_compaction_clusters",
        lambda data_dir, *, config: (
            compaction_called.append(data_dir)
            or SimpleNamespace(clusters=[], review_item_ids=[])
        ),
    )
    monkeypatch.setattr(
        "kb_librarian.hygiene.scan_hygiene_queues",
        lambda data_dir, *, config: (
            hygiene_called.append(data_dir)
            or SimpleNamespace(stale_item_ids=[], orphan_item_ids=[], low_utility_item_ids=[])
        ),
    )

    result = reindex_data_dir(tmp_path, config=config, scan_clusters=True)

    assert result.maintenance_scan is True
    assert result.compaction_clusters == 0
    assert compaction_called == [tmp_path]
    assert hygiene_called == [tmp_path]
