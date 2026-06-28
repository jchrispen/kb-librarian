from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import kb_librarian.review as review_mod
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.review import (
    _acknowledge_dispute_on_note,
    _accept_dispute_item,
    _accept_merge_item,
    _append_item,
    _cooldown_elapsed,
    _create_note_from_classification,
    _item_summary,
    _legacy_created,
    _legacy_title,
    _render_item_markdown,
    _render_rejected_items,
    _summary_from_block,
    _transition_item,
    add_review_item,
    collect_review_summary,
    defer_review_item,
    ensure_review_state,
    queue_compaction_cluster_review_item,
    review_state_path,
    upsert_hygiene_review_item,
    upsert_search_miss_review_item,
)
from kb_librarian.storage import canonical_note_path, ensure_topic_layout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note(
    *,
    note_id: str = "2026-06-01-note",
    title: str = "Note",
    topic: str = "agent-systems",
    sources: object | None = None,
    disputes: object | None = None,
) -> Note:
    frontmatter = {
        "id": note_id,
        "title": title,
        "summary": f"{title} summary.",
        "topic": topic,
        "created": "2026-06-01",
        "updated": "2026-06-01",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "medium",
        "retrieval_phrases": [title.lower()],
        "tags": [topic],
    }
    if sources is not None:
        frontmatter["sources"] = sources
    if disputes is not None:
        frontmatter["disputes"] = disputes
    return Note(frontmatter=frontmatter, body="## Core idea\n\nBody.\n")


def _write_note(data_dir: Path, *, note_id: str = "2026-06-01-note", topic: str = "agent-systems") -> Path:
    ensure_topic_layout(data_dir, topic)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, _note(note_id=note_id, topic=topic))
    return path


def _bypass_state_items(state):
    """Return items from state without running validate_review_item (for injecting bad state)."""
    items = state.get("items", [])
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    return []


# ---------------------------------------------------------------------------
# Tests: upsert functions with non-list history (defensive branches)
# ---------------------------------------------------------------------------


def test_search_miss_upsert_history_not_list(tmp_path, monkeypatch):
    """Lines 297->305: False branch of 'if isinstance(history, list)' in upsert_search_miss."""
    # Create state file with non-list history directly
    state_path = review_state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "id": "searchmiss-2026-06-01-001",
        "queue": "searchmiss",
        "status": "pending",
        "priority": "low",
        "title": "Old query",
        "created": "2026-06-01",
        "updated": "2026-06-01",
        "target_notes": [],
        "proposed_action": "review missed search result",
        "payload": {"fingerprint": "miss-nonlist"},
        "history": None,  # intentionally non-list
    }
    state_path.write_text(json.dumps({"version": 1, "items": [item]}), encoding="utf-8")

    # Patch _state_items to skip validation so the non-list history is tolerated on load
    monkeypatch.setattr(review_mod, "_state_items", _bypass_state_items)

    # Call upsert with the same fingerprint → enters the update path
    # 'if isinstance(history, list)' is False → branch 297->305 (no append, goes to _write_state)
    result = upsert_search_miss_review_item(
        tmp_path,
        title="new query",
        payload={"query": "new query"},
        priority="high",
        created="2026-06-01",
        fingerprint="miss-nonlist",
    )
    assert result == "searchmiss-2026-06-01-001"


def test_hygiene_upsert_history_not_list(tmp_path, monkeypatch):
    """Lines 382-383: Create new list when history is not a list in upsert_hygiene."""
    state_path = review_state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "id": "stale-2026-06-01-001",
        "queue": "stale",
        "status": "pending",
        "priority": "medium",
        "title": "Old stale",
        "created": "2026-06-01",
        "updated": "2026-06-01",
        "target_notes": [],
        "proposed_action": "review stale note",
        "payload": {"fingerprint": "stale-nonlist"},
        "history": None,  # intentionally non-list
    }
    state_path.write_text(json.dumps({"version": 1, "items": [item]}), encoding="utf-8")

    # Patch _state_items to skip validation
    monkeypatch.setattr(review_mod, "_state_items", _bypass_state_items)

    # Call upsert with changed data → update path → lines 382-383 execute (history = [] + assign)
    result = upsert_hygiene_review_item(
        tmp_path,
        queue="stale",
        title="Stale refreshed",
        target_notes=["n1"],
        payload={"note_id": "n1", "age_days": 30},
        priority="high",
        created="2026-06-01",
        fingerprint="stale-nonlist",
        source="scan",
    )
    assert result == "stale-2026-06-01-001"


def test_compaction_cluster_reopen_history_not_list(tmp_path, monkeypatch):
    """Lines 536-537: Create new list when history is not a list on reopen."""
    state_path = review_state_path(tmp_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    item = {
        "id": "compaction-2026-06-01-001",
        "queue": "compaction",
        "status": "rejected",
        "priority": "medium",
        "title": "Cluster A",
        "created": "2026-06-01",
        "updated": (date.today() - timedelta(days=10)).isoformat(),
        "target_notes": ["n1"],
        "proposed_action": "review compaction cluster",
        "payload": {"fingerprint": "compaction-cluster:cluster-fp-nonlist"},
        "history": None,  # intentionally non-list
    }
    state_path.write_text(json.dumps({"version": 1, "items": [item]}), encoding="utf-8")

    # Patch _state_items to skip validation
    monkeypatch.setattr(review_mod, "_state_items", _bypass_state_items)

    # Reopen → cooldown=0 passes, status="rejected" → reopen path → lines 536-537 execute
    result = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-x",
        source_note_ids=["n2"],
        title="Cluster A refreshed",
        reason="overlap",
        evidence=["same title"],
        suggested_canonical_title="Canonical",
        risk="medium",
        cluster_fingerprint="cluster-fp-nonlist",
        cooldown_days=0,
    )
    assert result == "compaction-2026-06-01-001"


# ---------------------------------------------------------------------------
# Tests: defer_review_item accepted branch (line 731)
# ---------------------------------------------------------------------------


def test_defer_accepted_item_raises(tmp_path):
    """Line 731: Deferred on an already-accepted item raises."""
    add_review_item(
        tmp_path,
        queue="stale",
        title="Stale Note",
        target_notes=["n1"],
        proposed_action="review stale note",
        payload={"note_id": "n1"},
        priority="medium",
        created="2026-06-01",
        fingerprint="defer-accepted-fp",
    )
    # Accept the item by manipulating state
    state_path = review_state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["items"][0]["status"] = "accepted"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    from kb_librarian.review import ReviewStateError
    with pytest.raises(ReviewStateError, match="accepted and cannot be deferred"):
        defer_review_item(tmp_path, state["items"][0]["id"], days=7)


# ---------------------------------------------------------------------------
# Tests: collect_review_summary max_items=0 (branch 832->834)
# ---------------------------------------------------------------------------


def test_collect_review_summary_max_items_zero(tmp_path):
    """Lines 832->834: False branch when max_items <= 0 (no truncation)."""
    # No items in state; max_items=0 → if max_items > 0 is False → branch 832->834
    counts, items = collect_review_summary(tmp_path, max_items=0)
    assert isinstance(counts, dict)
    assert isinstance(items, list)


# ---------------------------------------------------------------------------
# Tests: _append_item fingerprint collision (lines 924-925)
# ---------------------------------------------------------------------------


def test_append_item_fingerprint_collision():
    """Lines 924-925: _append_item returns existing id when fingerprint already exists."""
    state: dict = {"version": 1, "items": []}
    item_id1 = _append_item(
        state,
        queue="stale",
        title="First",
        target_notes=[],
        proposed_action="review stale note",
        payload={},
        priority="medium",
        created="2026-06-01",
        history=[],
        fingerprint="fp-collision",
    )
    # Second call with same fingerprint → returns existing id (lines 924-925)
    item_id2 = _append_item(
        state,
        queue="stale",
        title="Second",
        target_notes=[],
        proposed_action="review stale note",
        payload={},
        priority="medium",
        created="2026-06-01",
        history=[],
        fingerprint="fp-collision",
    )
    assert item_id1 == item_id2
    assert len(state["items"]) == 1  # no duplicate added


# ---------------------------------------------------------------------------
# Tests: _legacy_title fallback (line 1114)
# ---------------------------------------------------------------------------


def test_legacy_title_all_empty():
    """Line 1114: Falls back to _summary_from_block when all key fields are empty."""
    result = _legacy_title("stale", {}, "")
    # _summary_from_block with empty block returns "(no summary)" which is truthy
    assert isinstance(result, str)
    # Verify it didn't return early (all keys were empty → reached line 1114)
    assert result != ""


def test_legacy_title_all_empty_returns_queue_or_summary():
    """Line 1114 alternative: when _summary_from_block returns empty, falls back to queue name."""
    # Empty block → _summary_from_block returns "(no summary)" which is truthy
    result = _legacy_title("orphan", {"candidate_title": "", "title": ""}, "")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: _legacy_created OSError (lines 1130-1131)
# ---------------------------------------------------------------------------


def test_legacy_created_oserror():
    """Lines 1130-1131: When path.stat() raises OSError, returns today's date."""
    # Pass an ID with no date pattern and a non-existent path
    result = _legacy_created("no-date-in-this-id", Path("/nonexistent/path/that/does/not/exist"))
    assert result == date.today().isoformat()


# ---------------------------------------------------------------------------
# Tests: _render_item_markdown missing branches
# ---------------------------------------------------------------------------


def test_render_item_markdown_compaction_no_evidence():
    """Lines 1278->1281: False branch when compaction has no evidence list."""
    lines = _render_item_markdown({
        "id": "comp-noevidence",
        "queue": "compaction",
        "payload": {"kind": "cluster"},  # no evidence key
        "proposed_action": "review compaction cluster",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    assert not any("- evidence:" in line for line in lines)


def test_render_item_markdown_compaction_non_mapping_disposition():
    """Line 1289: continue when a disposition is not a Mapping."""
    lines = _render_item_markdown({
        "id": "comp-baddispo",
        "queue": "compaction",
        "payload": {
            "dispositions": [
                "not a mapping",  # triggers line 1289 (continue)
                {"note_id": "n1", "recommendation": "merge", "rationale": "overlap"},
            ]
        },
        "proposed_action": "review compaction cluster",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    # The valid mapping disposition should appear
    assert any("n1" in line for line in lines)


def test_render_item_markdown_topic_no_moves():
    """Lines 1313->1374: False branch when topic has no moves list."""
    lines = _render_item_markdown({
        "id": "topic-nomoves",
        "queue": "topic",
        "payload": {"kind": "split", "source_topic": "parent"},  # no moves
        "proposed_action": "review topic split",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    assert not any("- moves:" in line for line in lines)


def test_render_item_markdown_orphan_no_suggested_actions():
    """Lines 1353->1374: False branch when orphan has no suggested_actions."""
    lines = _render_item_markdown({
        "id": "orphan-noactions",
        "queue": "orphan",
        "payload": {"note_id": "n1"},  # no suggested_actions
        "proposed_action": "review orphan note",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    assert not any("- suggested_actions:" in line for line in lines)


def test_render_item_markdown_low_utility_no_reasons():
    """Lines 1366->1369: False branch when low_utility has no reasons list."""
    lines = _render_item_markdown({
        "id": "low-noreasons",
        "queue": "low_utility",
        "payload": {"note_id": "n1", "evidence": ["no hits"]},  # no reasons
        "proposed_action": "review low utility note",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    assert not any("- reasons:" in line for line in lines)


def test_render_item_markdown_low_utility_no_evidence():
    """Lines 1370->1374: False branch when low_utility has no evidence list."""
    lines = _render_item_markdown({
        "id": "low-noevidence",
        "queue": "low_utility",
        "payload": {"note_id": "n1", "reasons": ["unused"]},  # no evidence
        "proposed_action": "review low utility note",
        "target_notes": [],
    })
    assert isinstance(lines, list)
    assert not any("- evidence:" in line for line in lines)


# ---------------------------------------------------------------------------
# Tests: _item_summary fallback to title (line 1407)
# ---------------------------------------------------------------------------


def test_item_summary_no_payload_keys():
    """Line 1407: Falls back to item title when payload has no summary keys."""
    item = {
        "id": "item-1",
        "queue": "stale",
        "title": "My item title",
        "payload": {},  # no summary-relevant keys
    }
    result = _item_summary(item)
    assert result == "My item title"


def test_item_summary_non_dict_payload():
    """Line 1407: Falls back to item title when payload is not a dict."""
    item = {
        "id": "item-2",
        "queue": "stale",
        "title": "Fallback title",
        "payload": "bad payload",  # not a dict → falls through to title
    }
    result = _item_summary(item)
    assert result == "Fallback title"


# ---------------------------------------------------------------------------
# Tests: _cooldown_elapsed invalid date (lines 1433-1434)
# ---------------------------------------------------------------------------


def test_cooldown_elapsed_invalid_date():
    """Lines 1433-1434: ValueError caught when date string is invalid → returns False."""
    # _date_part("2026-13-99") extracts "2026-13-99" which is invalid calendar date
    result = _cooldown_elapsed("2026-13-99", 0)
    assert result is False


# ---------------------------------------------------------------------------
# Tests: _summary_from_block missing branches (1456->1452, 1460)
# ---------------------------------------------------------------------------


def test_summary_from_block_all_code_lines_returns_no_summary():
    """Line 1460 not taken, 1461 taken: All lines start with ``` → returns '(no summary)'."""
    # All non-empty lines start with ``` → the second loop never returns (skips 1460)
    block = "```python\n```"
    result = _summary_from_block(block, (r"^title:\s+(.+)$",))
    assert result == "(no summary)"


def test_summary_from_block_no_pattern_match_plain_line():
    """Line 1460: No pattern matches, but non-code line exists → returns that line."""
    block = "some plain text without a match"
    result = _summary_from_block(block, (r"^title:\s+(.+)$",))
    assert result == "some plain text without a match"


def test_summary_from_block_match_empty_capture():
    """Lines 1456->1452: Regex matches but stripped capture is empty → continues loop."""
    # Pattern uses (.*) which can match empty string; first line matches with empty group
    # triggering the False branch of 'if value:' at line 1456 → back to for loop at 1452
    block = "- title:\n- title: real title"
    result = _summary_from_block(block, (r"^- title:\s*(.*)$",))
    assert result == "real title"


# ---------------------------------------------------------------------------
# Tests: _transition_item with non-list history (lines 1509-1510)
# ---------------------------------------------------------------------------


def test_transition_item_history_not_list():
    """Lines 1509-1510: Creates new history list when existing history is not a list."""
    item: dict = {
        "id": "test-item",
        "status": "pending",
        "updated": "2026-06-01",
        "history": "not a list",  # intentionally non-list
    }
    _transition_item(item, to_status="rejected", action="rejected", note="testing")
    # Lines 1509-1510 should have replaced "history" with []
    assert isinstance(item["history"], list)
    assert len(item["history"]) == 1
    assert item["history"][0]["action"] == "rejected"
    assert item["status"] == "rejected"


# ---------------------------------------------------------------------------
# Tests: _render_rejected_items empty id (line 1544)
# ---------------------------------------------------------------------------


def test_render_rejected_items_empty_id(tmp_path):
    """Line 1544: Skip rejected item when item_id is empty."""
    items = [
        {
            "id": "",  # empty id → line 1544 (continue)
            "queue": "stale",
            "status": "rejected",
            "priority": "medium",
            "title": "Bad item",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "target_notes": [],
            "proposed_action": "review",
            "payload": {},
        },
        {
            "id": "   ",  # whitespace → stripped to empty → line 1544 (continue)
            "queue": "stale",
            "status": "rejected",
            "priority": "medium",
            "title": "Another bad item",
            "created": "2026-06-01",
            "updated": "2026-06-01",
            "target_notes": [],
            "proposed_action": "review",
            "payload": {},
        },
    ]
    _render_rejected_items(tmp_path, items)
    rejected_dir = tmp_path / "review" / "rejected"
    # No files should be written because both ids were empty
    assert not any(rejected_dir.glob("*.md")) if rejected_dir.exists() else True


# ---------------------------------------------------------------------------
# Tests: _accept_merge_item when marker already present (lines 1654->1656)
# ---------------------------------------------------------------------------


def test_accept_merge_item_marker_already_present(tmp_path):
    """Lines 1654->1656: False branch when _append_merge_body_to_note returns False."""
    note_path = _write_note(tmp_path, note_id="2026-06-01-merge-target")
    merge_item = {
        "id": "merge-already",
        "target_notes": ["2026-06-01-merge-target"],
        "payload": {"candidate_body": "Merged body."},
    }
    # First call: marker not present → body is appended → returns True → changed=1
    result1 = _accept_merge_item(tmp_path, merge_item, append_body=True)
    assert "body appended on 1 note(s)" in result1

    # Second call: marker already present → _append_merge_body_to_note returns False
    # → 1654->1656 (False branch, changed stays 0)
    result2 = _accept_merge_item(tmp_path, merge_item, append_body=True)
    assert "body appended on 0 note(s)" in result2


# ---------------------------------------------------------------------------
# Tests: _accept_dispute_item loop back (lines 1674->1669)
# ---------------------------------------------------------------------------


def test_accept_dispute_item_multiple_targets(tmp_path):
    """Lines 1674->1669: Loop back when there are multiple target notes."""
    path_a = _write_note(tmp_path, note_id="2026-06-01-dispute-a")
    path_b = _write_note(tmp_path, note_id="2026-06-01-dispute-b")
    # Both notes exist; loop iterates twice → 1674->1669 (loop back to for header)
    result = _accept_dispute_item(tmp_path, {
        "id": "dispute-multi",
        "target_notes": ["2026-06-01-dispute-a", "2026-06-01-dispute-b"],
    })
    assert "2 note(s)" in result


# ---------------------------------------------------------------------------
# Tests: _create_note_from_classification source path/hash branches
# ---------------------------------------------------------------------------


def test_create_note_no_source(tmp_path):
    """Lines 1752->1760: False branch when neither source_path nor source_hash is set."""
    note_id = _create_note_from_classification(
        tmp_path,
        payload={"title": "No Source Note", "summary": "A summary."},
        topic="agent-systems",
        knowledge_type="technique",
    )
    assert note_id
    path = canonical_note_path(tmp_path, "agent-systems", note_id)
    note = read_note(path)
    assert note.frontmatter["sources"] == []


def test_create_note_only_source_hash(tmp_path):
    """Lines 1754->1756: False branch of 'if source_path' when only hash is set."""
    note_id = _create_note_from_classification(
        tmp_path,
        payload={"title": "Hash Only Note", "source_hash": "hash-xyz"},
        topic="agent-systems",
        knowledge_type="technique",
    )
    assert note_id
    path = canonical_note_path(tmp_path, "agent-systems", note_id)
    note = read_note(path)
    sources = note.frontmatter["sources"]
    assert len(sources) == 1
    assert sources[0].get("hash") == "hash-xyz"
    assert "ref" not in sources[0]


def test_create_note_only_source_path(tmp_path):
    """Lines 1756->1758: False branch of 'if source_hash' when only path is set."""
    note_id = _create_note_from_classification(
        tmp_path,
        payload={"title": "Path Only Note", "source": "raw/only.md"},
        topic="agent-systems",
        knowledge_type="technique",
    )
    assert note_id
    path = canonical_note_path(tmp_path, "agent-systems", note_id)
    note = read_note(path)
    sources = note.frontmatter["sources"]
    assert len(sources) == 1
    assert sources[0].get("ref") == "raw/only.md"
    assert "hash" not in sources[0]


# ---------------------------------------------------------------------------
# Tests: _acknowledge_dispute_on_note (lines 1842, 1845-1848)
# ---------------------------------------------------------------------------


def test_acknowledge_dispute_non_dict_entry(tmp_path):
    """Line 1842: continue when disputes contains a non-dict entry."""
    note_path = _write_note(tmp_path, note_id="2026-06-01-dispute-nondic")
    note = read_note(note_path)
    frontmatter = dict(note.frontmatter)
    # Put a non-dict entry in disputes (triggers line 1842 continue)
    frontmatter["disputes"] = [
        "not a dict",  # non-dict → continue
        {"review_item_id": "dispute-a"},  # dict but no acknowledged field
    ]
    write_note(note_path, Note(frontmatter=frontmatter, body=note.body))
    # _acknowledge_dispute_on_note: skips "not a dict", then finds "dispute-a"
    # and it's not acknowledged → runs the else branch (adds new entry)
    # Wait — "dispute-a" is found but has no "acknowledged" field, so it DOES match
    # Let's check: entry.get("review_item_id") == "dispute-a" → True
    # entry.get("acknowledged") → None → falsy → lines 1845-1848 execute!
    result = _acknowledge_dispute_on_note(note_path, item_id="dispute-a")
    assert result is True
    updated = read_note(note_path)
    disputes = updated.frontmatter.get("disputes", [])
    # Find the dict entry that was updated
    matched = [e for e in disputes if isinstance(e, dict) and e.get("review_item_id") == "dispute-a"]
    assert matched
    assert matched[0].get("acknowledged") is True


def test_acknowledge_dispute_already_acknowledged(tmp_path):
    """Lines 1845-1848: Entry found but already acknowledged → changed=False."""
    note_path = _write_note(tmp_path, note_id="2026-06-01-dispute-acked")
    note = read_note(note_path)
    frontmatter = dict(note.frontmatter)
    frontmatter["reviewed_by_user"] = True
    frontmatter["disputes"] = [
        {
            "review_item_id": "dispute-b",
            "acknowledged": True,
            "acknowledged_at": "2026-06-01T00:00:00",
        }
    ]
    write_note(note_path, Note(frontmatter=frontmatter, body=note.body))
    # Already acknowledged → changed=False
    result = _acknowledge_dispute_on_note(note_path, item_id="dispute-b")
    assert result is False
