from __future__ import annotations

from kb_librarian.graph import EDGE_DISPUTES, EDGE_LINK, build_graph, graph_to_dict
from kb_librarian.notes import Note, write_note
from kb_librarian.storage import canonical_note_path, ensure_topic_layout


def _frontmatter(note_id: str, topic: str, **extra) -> dict[str, object]:
    fm = {
        "id": note_id,
        "title": f"Title {note_id}",
        "summary": "Summary",
        "topic": topic,
        "created": "2026-05-05",
        "updated": "2026-05-05",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": [],
        "tags": [],
    }
    fm.update(extra)
    return fm


def _write(data_dir, note_id, topic, body="", **extra):
    ensure_topic_layout(data_dir, topic)
    note = Note(frontmatter=_frontmatter(note_id, topic, **extra), body=body)
    write_note(canonical_note_path(data_dir, topic, note_id), note)


def test_disputes_and_wikilink_edges(tmp_path):
    _write(tmp_path, "note-a", "Topic", body="See [[note-b]] for details.", disputes=["note-c"])
    _write(tmp_path, "note-b", "Topic")
    _write(tmp_path, "note-c", "Topic")

    graph = build_graph(tmp_path)
    assert {node.id for node in graph.nodes} == {"note-a", "note-b", "note-c"}
    edges = {(e.source, e.target, e.type) for e in graph.edges}
    assert ("note-a", "note-b", EDGE_LINK) in edges
    assert ("note-a", "note-c", EDGE_DISPUTES) in edges


def test_unresolved_references_are_dropped(tmp_path):
    _write(tmp_path, "note-a", "Topic", body="Dangling [[ghost]].", disputes=["missing"])
    graph = build_graph(tmp_path)
    assert graph.edges == []


def test_backlinks_are_inverse_of_edges(tmp_path):
    _write(tmp_path, "note-a", "Topic", body="[[note-c]]")
    _write(tmp_path, "note-b", "Topic", body="[[note-c]]")
    _write(tmp_path, "note-c", "Topic")

    backlinks = build_graph(tmp_path).backlinks()
    assert backlinks["note-c"] == ["note-a", "note-b"]
    assert backlinks["note-a"] == []


def test_empty_kb_yields_empty_graph(tmp_path):
    payload = graph_to_dict(build_graph(tmp_path))
    assert payload == {"nodes": [], "edges": [], "backlinks": {}}
