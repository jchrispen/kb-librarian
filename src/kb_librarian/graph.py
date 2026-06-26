"""Concept graph derived from note records (read-only, no schema change).

Edges are *real* note-to-note references only:

- ``disputes`` frontmatter entries that resolve to a known note id.
- ``[[id]]`` wikilinks parsed from note bodies that resolve to a known note id.

Tags and topics are carried on nodes for grouping/coloring, not turned into
edges: they express similarity/category, not a reference, and would produce a
dense O(n^2) hairball. Backlinks are the inverse of the directed edges above.
"""

from __future__ import annotations

import json
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


_CYTOSCAPE_CDN = "https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KB Librarian concept graph</title>
<script src="__CDN__"></script>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; }
  #cy { position: absolute; inset: 0 320px 0 0; background: #fafafa; }
  #panel { position: absolute; top: 0; right: 0; bottom: 0; width: 320px;
            box-sizing: border-box; padding: 16px; overflow: auto;
            border-left: 1px solid #ddd; background: #fff; }
  #panel h1 { font-size: 15px; margin: 0 0 8px; }
  #panel .meta { color: #666; font-size: 12px; margin-bottom: 12px; }
  #detail { font-size: 13px; }
  #detail .label { color: #888; }
  .legend span { display: inline-block; margin-right: 12px; font-size: 12px; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
          margin-right: 4px; vertical-align: middle; }
</style>
</head>
<body>
<div id="cy"></div>
<div id="panel">
  <h1>KB concept graph</h1>
  <div class="meta">__SUMMARY__</div>
  <div class="legend">
    <span><span class="dot" style="background:#1f77b4"></span>link</span>
    <span><span class="dot" style="background:#d62728"></span>disputes</span>
  </div>
  <hr>
  <div id="detail">Click a node to see its details and backlinks.</div>
</div>
<script>
const DATA = __DATA__;
const backlinks = DATA.backlinks || {};
const titles = {};
DATA.nodes.forEach(n => titles[n.id] = n.title);
const elements = [
  ...DATA.nodes.map(n => ({ data: { id: n.id, label: n.title, topic: n.topic,
      knowledge_type: n.knowledge_type, status: n.status } })),
  ...DATA.edges.map((e, i) => ({ data: { id: 'e' + i, source: e.source,
      target: e.target, type: e.type } })),
];
const cy = cytoscape({
  container: document.getElementById('cy'),
  elements,
  style: [
    { selector: 'node', style: { 'label': 'data(label)', 'font-size': 9,
        'background-color': '#888', 'text-wrap': 'wrap', 'text-max-width': 90 } },
    { selector: 'edge', style: { 'width': 1.5, 'line-color': '#1f77b4',
        'target-arrow-color': '#1f77b4', 'target-arrow-shape': 'triangle',
        'curve-style': 'bezier' } },
    { selector: 'edge[type = "disputes"]', style: { 'line-color': '#d62728',
        'target-arrow-color': '#d62728', 'line-style': 'dashed' } },
  ],
  layout: { name: 'cose', animate: false },
});
cy.on('tap', 'node', evt => {
  const d = evt.target.data();
  const incoming = (backlinks[d.id] || []);
  const links = incoming.length
    ? incoming.map(id => '<li>' + (titles[id] || id) + '</li>').join('')
    : '<li><em>none</em></li>';
  document.getElementById('detail').innerHTML =
    '<h1>' + d.label + '</h1>' +
    '<p><span class="label">id:</span> ' + d.id + '<br>' +
    '<span class="label">topic:</span> ' + (d.topic || '—') + '<br>' +
    '<span class="label">type:</span> ' + (d.knowledge_type || '—') + '<br>' +
    '<span class="label">status:</span> ' + (d.status || '—') + '</p>' +
    '<p class="label">referenced by:</p><ul>' + links + '</ul>';
});
</script>
</body>
</html>
"""


def render_html(graph: Graph) -> str:
    """Render the graph as a single self-contained HTML page (Cytoscape via CDN)."""
    payload = json.dumps(graph_to_dict(graph)).replace("</", "<\\/")
    summary = f"{len(graph.nodes)} note(s), {len(graph.edges)} edge(s)"
    return (
        _HTML_TEMPLATE.replace("__CDN__", _CYTOSCAPE_CDN)
        .replace("__SUMMARY__", summary)
        .replace("__DATA__", payload)
    )


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
