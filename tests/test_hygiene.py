from __future__ import annotations

import json
from datetime import date, timedelta

from kb_librarian.config import default_config
from kb_librarian.hygiene import flag_suspect_note, scan_hygiene_queues
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.review import review_state_path
from kb_librarian.storage import canonical_note_path
from kb_librarian.usage import log_retrieval


def _write_note(
    data_dir,
    *,
    note_id: str,
    title: str,
    summary: str,
    topic: str = "agent-systems",
    knowledge_type: str = "technique",
    staleness_risk: str | None = None,
    updated: str | None = None,
    tags: list[str] | None = None,
) -> None:
    day = updated or date.today().isoformat()
    frontmatter = {
        "id": note_id,
        "title": title,
        "summary": summary,
        "topic": topic,
        "created": "2026-01-01",
        "updated": day,
        "knowledge_type": knowledge_type,
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": [title.lower()],
        "tags": tags or [topic],
    }
    if staleness_risk:
        frontmatter["staleness_risk"] = staleness_risk
    note = Note(frontmatter, "## Core idea\n\nBody.\n")
    write_note(canonical_note_path(data_dir, topic, note_id), note)


def test_scan_hygiene_queues_flags_stale_fact_and_orphan(tmp_path):
    initialize_data_dir(tmp_path)
    old_day = (date.today() - timedelta(days=210)).isoformat()
    _write_note(
        tmp_path,
        note_id="2026-01-01-old-fact",
        title="Old fact",
        summary="Needs reverification.",
        knowledge_type="fact",
        staleness_risk="high",
        updated=old_day,
        tags=["freshness"],
    )
    _write_note(
        tmp_path,
        note_id="2026-01-01-isolated-note",
        title="Isolated note",
        summary="No neighbors.",
        topic="isolated",
        updated=(date.today() - timedelta(days=45)).isoformat(),
        tags=["isolated"],
    )
    reindex_data_dir(tmp_path)

    config = default_config(tmp_path)
    config["review"]["stale_after_days"] = 180
    config["review"]["orphan_after_days"] = 30
    scan = scan_hygiene_queues(tmp_path, config=config)

    assert scan.stale_item_ids == ["stale-" + date.today().isoformat() + "-001"]
    assert len(scan.orphan_item_ids) >= 1
    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    queues = {item["queue"] for item in state["items"]}
    assert "stale" in queues
    assert "orphan" in queues
    stale_md = (tmp_path / "review" / "stale.md").read_text(encoding="utf-8")
    orphan_md = (tmp_path / "review" / "orphans.md").read_text(encoding="utf-8")
    assert "Old fact may be stale" not in stale_md
    assert "- note_id: 2026-01-01-old-fact" in stale_md
    assert "- note_id: 2026-01-01-isolated-note" in orphan_md


def test_scan_hygiene_queues_flags_low_utility_from_retrieval_mismatch(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-01-01-low-utility",
        title="Low utility note",
        summary="Retrieved but not used.",
    )
    for minute in range(3):
        log_retrieval(
            tmp_path,
            command="search",
            query="low utility note",
            returned_note_ids=["2026-01-01-low-utility"],
            result_count=1,
            top_score=4.0,
            timestamp=f"2026-05-06T10:0{minute}:00",
        )
    reindex_data_dir(tmp_path)

    scan = scan_hygiene_queues(tmp_path, config=default_config(tmp_path))
    assert scan.low_utility_item_ids == ["lowutility-" + date.today().isoformat() + "-001"]
    low_utility_md = (tmp_path / "review" / "low-utility.md").read_text(encoding="utf-8")
    assert "- note_id: 2026-01-01-low-utility" in low_utility_md
    assert "- retrievals: 3" in low_utility_md
    assert "- weak_retrievals: 3" in low_utility_md


def test_flag_suspect_routes_and_deduplicates_reasons(tmp_path):
    initialize_data_dir(tmp_path)
    _write_note(
        tmp_path,
        note_id="2026-01-01-suspect-target",
        title="Suspect target",
        summary="Can be flagged.",
    )
    reindex_data_dir(tmp_path)

    first = flag_suspect_note(tmp_path, note_id="2026-01-01-suspect-target", reason="not useful in retrieval")
    second = flag_suspect_note(tmp_path, note_id="2026-01-01-suspect-target", reason="not useful in retrieval")
    dispute = flag_suspect_note(tmp_path, note_id="2026-01-01-suspect-target", reason="contradicts current behavior")

    assert first.queue == "low_utility"
    assert second.queue == "low_utility"
    assert first.item_id == second.item_id
    assert second.suspect_count == 2
    assert dispute.queue == "dispute"

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    low = [item for item in state["items"] if item["queue"] == "low_utility"][0]
    assert "not useful in retrieval" in low["payload"]["reasons"]
    assert "suspect flag: not useful in retrieval" in low["payload"]["reasons"]
    disputes = [item for item in state["items"] if item["queue"] == "dispute"]
    assert len(disputes) == 1
    assert disputes[0]["target_notes"] == ["2026-01-01-suspect-target"]
