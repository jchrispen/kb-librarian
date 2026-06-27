from __future__ import annotations

import json

from kb_librarian.graph import (
    EDGE_DISPUTES,
    EDGE_LINK,
    build_graph,
    graph_to_dict,
    render_graph_summary,
    render_html,
)
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


def test_render_html_is_self_contained_and_embeds_data(tmp_path):
    _write(tmp_path, "note-a", "Topic", body="[[note-b]]")
    _write(tmp_path, "note-b", "Topic")

    html = render_html(build_graph(tmp_path))
    assert html.startswith("<!DOCTYPE html>")
    assert "cytoscape" in html
    # Embedded payload is valid and present; no leftover format placeholders.
    assert "note-a" in html and "note-b" in html
    assert "__DATA__" not in html and "{{" not in html


def test_render_html_escapes_closing_tags(tmp_path):
    # A title containing </script> must not break out of the inline data block.
    _write(tmp_path, "x", "Topic", title="evil </script> title")
    html = render_html(build_graph(tmp_path))
    assert "</script> title" not in html.split("</body>")[0].replace("<\\/script>", "")


def test_build_graph_disputes_as_string(tmp_path):
    # disputes as a bare string (not a list) should still resolve an edge.
    _write(tmp_path, "note-a", "Topic", disputes="note-b")
    _write(tmp_path, "note-b", "Topic")

    graph = build_graph(tmp_path)
    edges = {(e.source, e.target, e.type) for e in graph.edges}
    assert ("note-a", "note-b", EDGE_DISPUTES) in edges


def test_build_graph_deduplicates_wikilink_edges(tmp_path):
    # Same [[id]] wikilink appearing twice in body must produce only one edge.
    _write(tmp_path, "note-a", "Topic", body="[[note-b]] and also [[note-b]] again.")
    _write(tmp_path, "note-b", "Topic")

    graph = build_graph(tmp_path)
    link_edges = [e for e in graph.edges if e.type == EDGE_LINK]
    assert len(link_edges) == 1


def test_render_graph_summary_empty_graph(tmp_path):
    summary = render_graph_summary(build_graph(tmp_path))
    assert "No notes found" in summary


def test_render_graph_summary_with_nodes_no_edges(tmp_path):
    _write(tmp_path, "note-a", "Topic")
    _write(tmp_path, "note-b", "Topic")

    summary = render_graph_summary(build_graph(tmp_path))
    assert "2 note(s)" in summary
    assert "No reference edges" in summary


def test_render_graph_summary_with_edges_and_backlinks(tmp_path):
    _write(tmp_path, "note-a", "Topic", body="[[note-c]]")
    _write(tmp_path, "note-b", "Topic", body="[[note-c]]")
    _write(tmp_path, "note-c", "Topic")

    summary = render_graph_summary(build_graph(tmp_path))
    assert "3 note(s)" in summary
    assert "Edges by type" in summary
    assert "Most referenced" in summary
    assert "note-c" in summary
