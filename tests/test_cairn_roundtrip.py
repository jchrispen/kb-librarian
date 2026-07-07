"""Round-trip guarantee for Cairn's custom frontmatter keys.

Cairn (a downstream consumer) attaches custom frontmatter keys to kb notes.
These are unknown to kb's schema, so this test pins the guarantee that they
survive the write + reindex pipeline unchanged: kb's validator is permissive
(only required fields are checked), `order_frontmatter` appends unknown keys
after the known order, and `reindex` never rewrites source note files.
"""

from __future__ import annotations

from kb_librarian.indexing import reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.storage import canonical_note_path

# The custom keys Cairn carries. `type`/`topics` are preserved additively and
# stay distinct from kb's own `knowledge_type` (enum) and `topic` (single
# hierarchy path) -- no collision, no duplication of meaning. `certainty` is a
# new key by design and must not disturb kb's required `confidence` enum.
CAIRN_KEYS = {
    "type": "observation",
    "people": ["alice", "bob"],
    "place": "Portland, OR",
    "topics": ["memory", "travel"],
    "media": ["photo-001.jpg", "audio-002.m4a"],
    "certainty": "probable",
    "when_captured": "2026-07-06",
    "when_happened": "1998-04-02",
    # ISO datetime as a string on purpose: a bare YAML timestamp would parse to
    # a datetime, which kb's write path truncates to a date. Cairn stores the
    # sort key as text so the time survives.
    "when_sort": "1998-04-02T14:30:00",
}


def _cairn_note() -> Note:
    frontmatter = {
        "id": "2026-07-06-cairn-roundtrip",
        "title": "Cairn round-trip sample",
        "summary": "A note carrying Cairn's custom frontmatter keys.",
        "topic": "cairn/samples",
        "created": "2026-07-06",
        "updated": "2026-07-06",
        "knowledge_type": "fact",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": ["cairn round trip"],
        "tags": ["cairn"],
        **CAIRN_KEYS,
    }
    return Note(frontmatter, "\n## Claim\n\nSample body.\n")


def test_cairn_keys_survive_write_and_reindex(tmp_path):
    initialize_data_dir(tmp_path)
    note = _cairn_note()
    path = canonical_note_path(tmp_path, note.frontmatter["topic"], note.frontmatter["id"])
    write_note(path, note)

    result = reindex_data_dir(tmp_path)
    assert result.note_count == 1

    reloaded = read_note(path)
    for key, value in CAIRN_KEYS.items():
        assert reloaded.frontmatter[key] == value, f"Cairn key {key!r} did not round-trip"

    # kb's own required enum stays untouched and distinct from `certainty`.
    assert reloaded.frontmatter["confidence"] == "high"
    assert reloaded.frontmatter["certainty"] == "probable"
