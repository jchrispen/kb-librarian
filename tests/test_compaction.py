from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from kb_librarian.compaction import (
    detect_compaction_clusters,
    draft_compaction_proposal,
    resolve_compaction_target,
    scan_compaction_clusters,
)
from kb_librarian.config import default_config, write_config_file
from kb_librarian.errors import KBLibrarianError, ProviderError
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.providers import validate_compaction_payload
from kb_librarian.review import accept_review_item, queue_compaction_cluster_review_item, reject_review_item, review_state_path
from kb_librarian.storage import canonical_note_path, load_note_records


def _write_note(
    data_dir,
    *,
    note_id: str,
    title: str,
    summary: str,
    topic: str = "agent-systems",
    body: str | None = None,
    tags: list[str] | None = None,
    phrases: list[str] | None = None,
) -> None:
    note = Note(
        {
            "id": note_id,
            "title": title,
            "summary": summary,
            "topic": topic,
            "created": "2026-05-05",
            "updated": "2026-05-05",
            "knowledge_type": "heuristic",
            "status": "active",
            "confidence": "high",
            "retrieval_phrases": phrases or ["agent context retrieval", "task shaped context"],
            "tags": tags or ["agent-systems", "context"],
        },
        body
        or (
            "## Judgment\n\n"
            "Prefer compact task-shaped context for coding agents before reading full notes.\n"
        ),
    )
    write_note(canonical_note_path(data_dir, topic, note_id), note)


def test_detect_compaction_clusters_is_deterministic(tmp_path):
    records = []
    for index in range(4):
        _write_note(
            tmp_path,
            note_id=f"2026-05-05-agent-context-{index}",
            title=f"Agent context retrieval {index}",
            summary="Prefer compact task-shaped context for coding agents.",
        )
    records = load_note_records(tmp_path)

    first = detect_compaction_clusters(records, threshold=4)
    second = detect_compaction_clusters(list(reversed(records)), threshold=4)

    assert [cluster.note_ids for cluster in first] == [cluster.note_ids for cluster in second]
    assert len(first) == 1
    assert first[0].note_ids == sorted(record.note_id for record in records)
    assert first[0].cluster_id.startswith("cluster-")
    assert first[0].risk == "low"


def test_scan_compaction_clusters_queues_idempotent_review_item(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["review"]["duplicate_cluster_threshold"] = 3
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    for index in range(3):
        _write_note(
            tmp_path,
            note_id=f"2026-05-05-context-module-{index}",
            title=f"Context module for agents {index}",
            summary="Agents should use compact context modules.",
        )

    first = scan_compaction_clusters(tmp_path, config=config)
    second = scan_compaction_clusters(tmp_path, config=config)

    assert len(first.clusters) == 1
    assert len(first.review_item_ids) == 1
    assert second.review_item_ids == []

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    item = state["items"][0]
    assert item["queue"] == "compaction"
    assert item["payload"]["kind"] == "cluster"
    assert item["payload"]["cluster_id"] == first.clusters[0].cluster_id
    rendered = (tmp_path / "review" / "pending-compaction.md").read_text(encoding="utf-8")
    assert "## item: compaction-" in rendered
    assert "- kind: cluster" in rendered
    assert "- evidence:" in rendered


def test_compaction_cluster_cooldown_suppresses_recent_rejected_item(tmp_path):
    initialize_data_dir(tmp_path)
    item_id = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["2026-05-05-a", "2026-05-05-b"],
        title="Compaction candidate",
        reason="overlap",
        evidence=["a + b: same normalized title"],
        suggested_canonical_title="Agent context",
        risk="medium",
        cluster_fingerprint="cluster-fp",
        cooldown_days=30,
    )
    assert item_id is not None
    reject_review_item(tmp_path, item_id)

    repeated = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["2026-05-05-a", "2026-05-05-b"],
        title="Compaction candidate",
        reason="overlap",
        evidence=["a + b: same normalized title"],
        suggested_canonical_title="Agent context",
        risk="medium",
        cluster_fingerprint="cluster-fp",
        cooldown_days=30,
    )

    assert repeated is None

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    state["items"][0]["updated"] = (date.today() - timedelta(days=31)).isoformat()
    review_state_path(tmp_path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reopened = queue_compaction_cluster_review_item(
        tmp_path,
        cluster_id="cluster-a",
        source_note_ids=["2026-05-05-a", "2026-05-05-b"],
        title="Compaction candidate",
        reason="overlap",
        evidence=["a + b: same normalized title"],
        suggested_canonical_title="Agent context",
        risk="medium",
        cluster_fingerprint="cluster-fp",
        cooldown_days=30,
    )

    assert reopened == item_id
    reopened_state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    assert reopened_state["items"][0]["status"] == "pending"


def test_resolve_compaction_target_accepts_topic_and_rejects_single_note_topic(tmp_path):
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-a",
        title="Agent context A",
        summary="A.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-b",
        title="Agent context B",
        summary="B.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-python-only",
        title="Python note",
        summary="Only one note.",
        topic="python",
    )
    records = load_note_records(tmp_path)

    selected, cluster_id = resolve_compaction_target(tmp_path, records, "agent-systems")
    assert [record.note_id for record in selected] == [
        "2026-05-05-agent-context-a",
        "2026-05-05-agent-context-b",
    ]
    assert cluster_id.startswith("cluster-")

    with pytest.raises(KBLibrarianError, match="insufficient material"):
        resolve_compaction_target(tmp_path, records, "python")


def test_draft_compaction_proposal_with_mock_provider_queues_review_item(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock-compact"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-a",
        title="Agent context A",
        summary="Use context before coding.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-b",
        title="Agent context B",
        summary="Cite context sources.",
    )

    result = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")
    repeated = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")

    assert result.created is True
    assert repeated.created is False
    assert repeated.review_item_id == result.review_item_id
    assert result.draft.frontmatter["title"] == "Agent context A"
    assert "recommends supersession" in result.draft.diff_summary

    state = json.loads(review_state_path(tmp_path).read_text(encoding="utf-8"))
    proposals = [item for item in state["items"] if item["payload"].get("kind") == "proposal"]
    assert len(proposals) == 1
    assert proposals[0]["payload"]["dispositions"][0]["recommendation"] == "supersede"
    rendered = (tmp_path / "review" / "pending-compaction.md").read_text(encoding="utf-8")
    assert "- kind: proposal" in rendered
    assert "- proposed_body:" in rendered


def test_draft_compaction_proposal_routes_to_local_provider(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["operations"]["compact"] = {"provider": "local", "model": "llama3.2"}
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-local-a",
        title="Agent context A",
        summary="Use context before coding.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-local-b",
        title="Agent context B",
        summary="Cite context sources.",
    )

    from kb_librarian.providers import MockProvider

    provider_names = []
    provider = MockProvider()

    def _provider_from_config(config, provider_name, **kwargs):  # noqa: ANN001
        provider_names.append(provider_name)
        return provider

    monkeypatch.setattr("kb_librarian.compaction.provider_from_config", _provider_from_config)

    result = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")

    assert result.created is True
    assert provider_names == ["local"]


def test_draft_compaction_proposal_routes_to_codex_provider(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["operations"]["compact"] = {"provider": "codex", "model": "gpt-5.1-codex"}
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-codex-a",
        title="Agent context A",
        summary="Use context before coding.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-codex-b",
        title="Agent context B",
        summary="Cite context sources.",
    )

    from kb_librarian.providers import MockProvider

    provider_names = []
    provider = MockProvider()

    def _provider_from_config(config, provider_name, **kwargs):  # noqa: ANN001
        provider_names.append(provider_name)
        return provider

    monkeypatch.setattr("kb_librarian.compaction.provider_from_config", _provider_from_config)

    result = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")

    assert result.created is True
    assert provider_names == ["codex"]


def test_draft_compaction_proposal_retries_transient_provider_failure(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock-compact"}
    config["providers"]["retry"] = {
        "max_attempts": 3,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "jitter_seconds": 0.0,
    }
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-retry-a",
        title="Agent context A",
        summary="Use context before coding.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-retry-b",
        title="Agent context B",
        summary="Cite context sources.",
    )

    from kb_librarian.providers import MockProvider

    class FlakyCompactionProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = 0

        def synthesize_compaction(self, **kwargs):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("service unavailable")
            return super().synthesize_compaction(**kwargs)

    provider = FlakyCompactionProvider()
    monkeypatch.setattr("kb_librarian.compaction.provider_from_config", lambda *args, **kwargs: provider)

    result = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")

    assert result.created is True
    assert provider.calls == 2
    errors_log = (tmp_path / ".kb" / "errors.log").read_text(encoding="utf-8")
    assert "stage=provider-compact" in errors_log


def test_accept_compaction_proposal_creates_canonical_note_and_supersedes_sources(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock-compact"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-a",
        title="Agent context A",
        summary="Use context before coding.",
    )
    _write_note(
        tmp_path,
        note_id="2026-05-05-agent-context-b",
        title="Agent context B",
        summary="Cite context sources.",
    )

    proposal = draft_compaction_proposal(tmp_path, config=config, target="agent-systems")
    accepted = accept_review_item(tmp_path, proposal.review_item_id)

    assert accepted.changed is True
    assert "Accepted compaction into note" in accepted.message
    records = load_note_records(tmp_path)
    by_id = {record.note_id: record for record in records}
    canonical_ids = sorted(set(by_id) - set(proposal.source_note_ids))
    assert len(canonical_ids) == 1
    canonical_id = canonical_ids[0]
    assert by_id[canonical_id].note.frontmatter["sources"][0]["source_note_ids"] == proposal.source_note_ids
    for source_id in proposal.source_note_ids:
        frontmatter = by_id[source_id].note.frontmatter
        assert frontmatter["status"] == "superseded"
        assert frontmatter["superseded_by"] == canonical_id
    assert (tmp_path / ".kb" / "fts.sqlite").is_file()


def test_validate_compaction_payload_requires_dispositions_for_all_sources():
    with pytest.raises(ProviderError, match="missing"):
        validate_compaction_payload(
            {
                "frontmatter": {
                    "title": "Canonical",
                    "summary": "Summary.",
                    "topic": "agent-systems",
                    "knowledge_type": "heuristic",
                    "confidence": "medium",
                    "retrieval_phrases": ["canonical"],
                    "tags": ["agent-systems"],
                },
                "body": "## Judgment\n\nUse the canonical note.\n",
                "source_note_ids": ["2026-05-05-a", "2026-05-05-b"],
                "dispositions": [
                    {
                        "note_id": "2026-05-05-a",
                        "recommendation": "supersede",
                        "rationale": "folded into canonical note",
                    }
                ],
                "diff_summary": "One canonical note.",
            }
        )
