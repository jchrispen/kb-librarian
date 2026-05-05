from __future__ import annotations

import json

from kb_librarian.indexing import index_document, reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.search_index import score_document, tokenize_query
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
