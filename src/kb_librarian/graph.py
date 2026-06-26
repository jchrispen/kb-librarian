"""Concept graph derived from note records (read-only, no schema change).

Edges are *real* note-to-note references only:

- ``disputes`` frontmatter entries that resolve to a known note id.
- ``[[id]]`` wikilinks parsed from note bodies that resolve to a known note id.

Tags and topics are carried on nodes for grouping/coloring, not turned into
edges: they express similarity/category, not a reference, and would produce a
dense O(n^2) hairball. Backlinks are the inverse of the directed edges above.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kb_librarian.storage import load_note_records

# Matches [[note-id]] or [[note-id|alias]] wikilinks in note bodies.
_WIKILINK_PATTERN = re.compile(r"\[\[\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]")

EDGE_DISPUTES = "disputes"
EDGE_LINK = "link"


@dataclass(frozen=True)
class GraphNode:
    id: str
    title: str
    topic: str
    knowledge_type: str
    status: str
    tags: list[str]


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    type: str


@dataclass
class Graph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def backlinks(self) -> dict[str, list[str]]:
        """Inverse of the directed edges: target id -> sorted source ids."""
        incoming: dict[str, set[str]] = {node.id: set() for node in self.nodes}
        for edge in self.edges:
            incoming.setdefault(edge.target, set()).add(edge.source)
        return {target: sorted(sources) for target, sources in incoming.items()}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def build_graph(data_dir: Path) -> Graph:
    """Derive the concept graph from the canonical notes under ``data_dir``."""
    records = load_note_records(data_dir, validate=False)

    nodes: list[GraphNode] = []
    known_ids: set[str] = set()
    for record in records:
        fm = record.note.frontmatter
        node_id = str(fm["id"])
        known_ids.add(node_id)
        nodes.append(
            GraphNode(
                id=node_id,
                title=str(fm.get("title", node_id)),
                topic=str(fm.get("topic", "")),
                knowledge_type=str(fm.get("knowledge_type", "")),
                status=str(fm.get("status", "")),
                tags=_string_list(fm.get("tags")),
            )
        )

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def _add_edge(source: str, target: str, edge_type: str) -> None:
        if target not in known_ids or target == source:
            return
        key = (source, target, edge_type)
        if key in seen:
            return
        seen.add(key)
        edges.append(GraphEdge(source=source, target=target, type=edge_type))

    for record in records:
        fm = record.note.frontmatter
        source = str(fm["id"])
        for disputed in _string_list(fm.get("disputes")):
            _add_edge(source, disputed.strip(), EDGE_DISPUTES)
        for match in _WIKILINK_PATTERN.findall(record.note.body):
            _add_edge(source, match.strip(), EDGE_LINK)

    return Graph(nodes=nodes, edges=edges)


def graph_to_dict(graph: Graph) -> dict[str, Any]:
    """Serialize the graph to a JSON-ready payload."""
    return {
        "nodes": [
            {
                "id": node.id,
                "title": node.title,
                "topic": node.topic,
                "knowledge_type": node.knowledge_type,
                "status": node.status,
                "tags": node.tags,
            }
            for node in graph.nodes
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "type": edge.type}
            for edge in graph.edges
        ],
        "backlinks": graph.backlinks(),
    }


def render_graph_summary(graph: Graph) -> str:
    """Human-readable summary of the graph."""
    node_count = len(graph.nodes)
    if node_count == 0:
        return "No notes found; the concept graph is empty.\n"

    by_type: dict[str, int] = {}
    for edge in graph.edges:
        by_type[edge.type] = by_type.get(edge.type, 0) + 1

    backlinks = graph.backlinks()
    most_referenced = sorted(
        graph.nodes,
        key=lambda node: (-len(backlinks.get(node.id, [])), node.id),
    )

    lines = [
        f"{node_count} note(s), {len(graph.edges)} edge(s).",
    ]
    if by_type:
        breakdown = ", ".join(f"{name}: {count}" for name, count in sorted(by_type.items()))
        lines.append(f"Edges by type: {breakdown}")
    else:
        lines.append("No reference edges (disputes or [[id]] wikilinks) found.")

    top = [node for node in most_referenced if backlinks.get(node.id)]
    if top:
        lines.append("Most referenced:")
        for node in top[:5]:
            lines.append(f"  {node.id} <- {len(backlinks[node.id])} ({node.title})")

    return "\n".join(lines) + "\n"
