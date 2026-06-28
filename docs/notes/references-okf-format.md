# OKF (Open Knowledge Format) — external reference

_Captured: 2026-06-26_

Source: [GoogleCloudPlatform/knowledge-catalog `/okf`](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)

OKF is a vendor-neutral spec for representing knowledge as markdown + YAML
frontmatter "bundles," plus a reference producer (BigQuery + Gemini crawl) and a
self-contained HTML visualizer. Its storage philosophy (markdown-first,
git-friendly, portable to Obsidian/Notion/MkDocs) converges with kb-librarian's
existing note model — so it validates our design rather than extending it.

**Not adopted, and why:** the reference agent is cloud-native and puts an LLM
crawl in the critical path, which contradicts the local-first / no-network
constraint in [the agent-first ADR](../../adrs/2026-05-09-agent-first-architecture.md).
It also has no retrieval engine, which is kb-librarian's actual value.

## Ideas worth keeping

1. **`SPEC.md` as an interchange/export target.** If note portability to other
   tools ever becomes a goal, OKF's bundle schema is a ready-made vendor-neutral
   format to export to instead of inventing one. No export format is in scope or
   the deferred backlog today.

## Adopted since capture

- **Concept graph + backlinks + static visualizer.** KB Librarian now derives
  reference/backlink graph edges and can write an optional interactive HTML
  visualization via `kb graph --html PATH`.
