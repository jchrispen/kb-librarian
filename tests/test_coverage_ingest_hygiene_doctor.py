from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from kb_librarian.config import default_config, write_config_file
from kb_librarian.doctor import render_doctor_report, run_doctor
from kb_librarian.errors import IngestError, ProviderError
from kb_librarian.hygiene import flag_suspect_note, scan_hygiene_queues
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.ingest import ingest, parse_ingest_file, render_report
from kb_librarian.ingest_recovery import write_state
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, read_note, write_note
from kb_librarian.providers import (
    CandidateNote,
    ClaudeCliStatus,
    ClassificationResult,
    CodexCliStatus,
    ExtractionResult,
    IntegrationResult,
    LocalProviderStatus,
)
from kb_librarian.review import ReviewStateError, add_review_item
from kb_librarian.storage import canonical_note_path, ensure_topic_layout
from kb_librarian.usage import log_note_use, log_retrieval, log_suspect_flag


def _config(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {"backend": "mock"}
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "mock", "model": f"mock-{operation}"}
    write_config_file(data_dir / ".kb" / "config.yaml", config)
    return config


def _note(
    data_dir: Path,
    *,
    note_id: str,
    title: str = "Coverage note",
    topic: str = "agent-systems",
    body: str = "## Core idea\n\nCoverage behavior.\n",
    updated: str | None = None,
    status: str = "active",
    knowledge_type: str = "technique",
    staleness_risk: str | None = None,
    confidence: str = "high",
    tags: list[str] | None = None,
    sources: list[object] | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    ensure_topic_layout(data_dir, topic)
    frontmatter: dict[str, object] = {
        "id": note_id,
        "title": title,
        "summary": "Summary text.",
        "topic": topic,
        "created": "2026-01-01",
        "updated": updated or date.today().isoformat(),
        "knowledge_type": knowledge_type,
        "status": status,
        "confidence": confidence,
        "retrieval_phrases": [title.lower()],
        "tags": tags if tags is not None else [topic],
    }
    if staleness_risk is not None:
        frontmatter["staleness_risk"] = staleness_risk
    if sources is not None:
        frontmatter["sources"] = sources
    if extra:
        frontmatter.update(extra)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, Note(frontmatter=frontmatter, body=body))
    return path


def _candidate(title: str = "Coverage ingest") -> CandidateNote:
    return CandidateNote(
        title=title,
        summary="Provider candidate summary.",
        knowledge_type="technique",
        body="## Core idea\n\nProvider candidate body.\n",
        retrieval_phrases=[title.lower()],
        tags=["agent-systems"],
        confidence="high",
        utility_score="high",
    )


class _IntegrationErrorProvider:
    def extract_candidates(self, **kwargs):  # noqa: ANN003
        return ExtractionResult([_candidate()])

    def classify_candidate(self, **kwargs):  # noqa: ANN003
        return ClassificationResult("agent-systems", "technique", "high")

    def integration_verdict(self, **kwargs):  # noqa: ANN003
        raise ProviderError("integration backend refused verdict")


class _UnknownVerdictProvider(_IntegrationErrorProvider):
    def __init__(self, target_note_id: str) -> None:
        self.target_note_id = target_note_id

    def integration_verdict(self, **kwargs):  # noqa: ANN003
        return IntegrationResult("merge_later", [self.target_note_id])


class _LowUtilityProvider(_IntegrationErrorProvider):
    def extract_candidates(self, **kwargs):  # noqa: ANN003
        return ExtractionResult([_candidate("Skip me"), _candidate("Create me")])

    def integration_verdict(self, **kwargs):  # noqa: ANN003
        candidate = kwargs["candidate"]
        if candidate.title == "Skip me":
            return IntegrationResult("unrelated", [])
        return IntegrationResult("unrelated", [])


class _FixedVerdictProvider(_IntegrationErrorProvider):
    def __init__(self, verdict: str, target_note_id: str, *, rationale: str = "coverage rationale") -> None:
        self.verdict = verdict
        self.target_note_id = target_note_id
        self.rationale = rationale

    def extract_candidates(self, **kwargs):  # noqa: ANN003
        return ExtractionResult([_candidate()])

    def classify_candidate(self, **kwargs):  # noqa: ANN003
        return ClassificationResult("agent-systems", "technique", "high")

    def integration_verdict(self, **kwargs):  # noqa: ANN003
        return IntegrationResult(self.verdict, [self.target_note_id], self.rationale)


def test_parse_ingest_file_rejects_bad_utf8_and_empty_text(tmp_path):
    bad_utf8 = tmp_path / "bad.txt"
    bad_utf8.write_bytes(b"\xff\xfe\x00")
    empty = tmp_path / "empty.md"
    empty.write_text("\n\t\n", encoding="utf-8")

    with pytest.raises(IngestError, match="UTF-8"):
        parse_ingest_file(bad_utf8)
    with pytest.raises(IngestError, match="empty"):
        parse_ingest_file(empty)


def test_ingest_resume_and_selection_edge_cases(tmp_path):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    source = tmp_path / "raw" / "resume.md"
    source.write_text("# Resume\n\nContent for resume edge case.\n", encoding="utf-8")

    with pytest.raises(IngestError, match="cannot be combined"):
        ingest(tmp_path, config=config, file_path=source, resume=True)
    with pytest.raises(IngestError, match="No interrupted ingest checkpoint"):
        ingest(tmp_path, config=config, resume=True)
    with pytest.raises(IngestError, match="does not exist"):
        ingest(tmp_path, config=config, file_path=tmp_path / "missing.md")

    archived = tmp_path / "raw" / "processed" / "2026-06" / "already.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("# Archived\n\nAlready moved.\n", encoding="utf-8")
    write_state(
        tmp_path,
        {
            "ingest": {
                "operation_id": "ingest-coverage",
                "status": "error",
                "current_raw_file": str(source),
                "pending_files": [str(source)],
                "archive_target": str(archived),
                "last_completed_stage": "archived",
            }
        },
    )
    source.unlink()

    resumed = ingest(tmp_path, config=config, resume=True)

    assert resumed.resumed is True
    assert resumed.processed_files == 0
    assert resumed.errors == 0


def test_ingest_reports_provider_integration_failures_as_classification_reviews(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    source = tmp_path / "raw" / "integration-error.md"
    source.write_text("# Coverage ingest\n\nProvider cannot decide integration.\n", encoding="utf-8")
    monkeypatch.setattr("kb_librarian.ingest.provider_from_config", lambda *args, **kwargs: _IntegrationErrorProvider())

    report = ingest(tmp_path, config=config)
    rendered = render_report(report)

    assert report.errors == 1
    assert report.processed_files == 1
    assert len(report.classification_items) == 1
    assert "integration verdict error" in (tmp_path / "review" / "pending-classification.md").read_text(encoding="utf-8")
    assert "recovery:" in rendered
    assert "parser_failure_review_item_ids:" not in rendered


def test_ingest_unknown_verdict_and_low_utility_candidate_are_reviewed_or_skipped(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    _note(tmp_path, note_id="2026-01-01-coverage-ingest", title="Coverage ingest")
    reindex_data_dir(tmp_path)
    source = tmp_path / "raw" / "unknown-verdict.md"
    source.write_text("# Coverage ingest\n\nProvider returns an unknown verdict.\n", encoding="utf-8")
    monkeypatch.setattr(
        "kb_librarian.ingest.provider_from_config",
        lambda *args, **kwargs: _UnknownVerdictProvider("2026-01-01-coverage-ingest"),
    )

    first = ingest(tmp_path, config=config)

    assert first.errors == 1
    assert len(first.classification_items) == 1
    state = json.loads((tmp_path / "review" / "review-items.json").read_text(encoding="utf-8"))
    assert any(
        item["payload"].get("reason") == "unknown integration verdict: merge_later"
        for item in state["items"]
    )

    second_source = tmp_path / "raw" / "low-utility.md"
    second_source.write_text("# Skip me\n\nThis should be skipped before classification.\n", encoding="utf-8")
    low = _candidate("Skip me")
    create = _candidate("Create me")
    monkeypatch.setattr(
        "kb_librarian.ingest.provider_from_config",
        lambda *args, **kwargs: type(
            "Provider",
            (),
            {
                "extract_candidates": lambda self, **kw: ExtractionResult(
                    [
                        CandidateNote(
                            title=low.title,
                            summary=low.summary,
                            knowledge_type=low.knowledge_type,
                            body=low.body,
                            retrieval_phrases=low.retrieval_phrases,
                            tags=low.tags,
                            confidence=low.confidence,
                            utility_score="low",
                        ),
                        create,
                    ]
                ),
                "classify_candidate": lambda self, **kw: ClassificationResult("agent-systems", "technique", "high"),
                "integration_verdict": lambda self, **kw: IntegrationResult("unrelated", []),
            },
        )(),
    )

    second = ingest(tmp_path, config=config, force=True)

    assert second.skipped_candidates == 1
    assert len(second.created_notes) == 1


def test_ingest_warns_on_same_filename_with_changed_hash_and_dedupes_archive_names(tmp_path):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    previous = tmp_path / "raw" / "changed.md"
    previous.write_text("# Changed file\n\nPrefer task-shaped context for coding agents.\n", encoding="utf-8")
    first = ingest(tmp_path, config=config)
    assert first.processed_files == 1

    processed_dir = next((tmp_path / "raw" / "processed").glob("*/"))
    existing_archive = processed_dir / "changed.md"
    assert existing_archive.exists()
    changed = tmp_path / "raw" / "changed.md"
    changed.write_text("# Changed file\n\nPrefer updated task-shaped context for coding agents.\n", encoding="utf-8")
    second = ingest(tmp_path, config=config)

    assert second.warnings == ["Possible updated version: changed.md"]
    assert second.archived_paths[0].name == "changed-2.md"


def test_hygiene_ignores_archived_invalid_and_recently_used_or_linked_notes(tmp_path):
    initialize_data_dir(tmp_path)
    old = (date.today() - timedelta(days=240)).isoformat()
    _note(
        tmp_path,
        note_id="2026-01-01-archived",
        title="Archived",
        status="archived",
        knowledge_type="fact",
        staleness_risk="high",
        updated=old,
    )
    linked = _note(
        tmp_path,
        note_id="2026-01-01-linked",
        title="Linked",
        topic="isolated-linked",
        updated=date.today().isoformat(),
        body="References 2026-01-01-archived.\n",
    )
    log_retrieval(
        tmp_path,
        command="search",
        query="linked",
        returned_note_ids=["2026-01-01-linked"],
        result_count=3,
        top_score=10.0,
    )
    log_note_use(tmp_path, note_id="2026-01-01-linked")
    reindex_data_dir(tmp_path)

    result = scan_hygiene_queues(tmp_path, config=default_config(tmp_path))

    assert result.item_ids == []
    assert linked.exists()


def test_hygiene_low_utility_priority_and_reason_deduplication(tmp_path):
    initialize_data_dir(tmp_path)
    _note(tmp_path, note_id="2026-01-01-corrected", title="Corrected utility")
    for index in range(5):
        log_retrieval(
            tmp_path,
            command="context",
            query="corrected utility",
            returned_note_ids=["2026-01-01-corrected"],
            result_count=1,
            top_score=3.0,
            timestamp=f"2026-05-0{index + 1}T10:00:00",
        )
    with (tmp_path / ".kb" / "usage.log").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "event": "retrieval",
                    "timestamp": "not-a-date",
                    "command": "search",
                    "query": "corrected utility",
                    "returned_note_ids": ["2026-01-01-corrected"],
                    "result_count": "many",
                    "top_score": "bad-score",
                }
            )
            + "\n"
        )
    log_note_use(tmp_path, note_id="", timestamp="2026-05-02T10:00:00")
    log_suspect_flag(tmp_path, note_id="", reason="wrong", timestamp="2026-05-02T10:00:00")
    log_suspect_flag(tmp_path, note_id="2026-01-01-corrected", reason="wrong answer", timestamp="2026-05-03T10:00:00")
    log_suspect_flag(tmp_path, note_id="2026-01-01-corrected", reason="wrong answer", timestamp="2026-05-04T10:00:00")
    reindex_data_dir(tmp_path)

    result = scan_hygiene_queues(tmp_path, config=default_config(tmp_path))

    assert result.low_utility_item_ids == ["lowutility-" + date.today().isoformat() + "-001"]
    state = json.loads((tmp_path / "review" / "review-items.json").read_text(encoding="utf-8"))
    item = next(item for item in state["items"] if item["queue"] == "low_utility")
    assert item["priority"] == "high"
    assert item["payload"]["correction_count"] == 2
    assert item["payload"]["use_ratio"] == 0.0
    assert item["payload"]["reasons"].count("wrong answer") == 1
    assert "low retrieval-to-use conversion" in item["payload"]["reasons"]


def test_flag_suspect_rejects_empty_reason_and_unknown_note(tmp_path):
    initialize_data_dir(tmp_path)
    _note(tmp_path, note_id="2026-01-01-known", title="Known")
    reindex_data_dir(tmp_path)

    with pytest.raises(ReviewStateError, match="non-empty reason"):
        flag_suspect_note(tmp_path, note_id="2026-01-01-known", reason=" ")
    with pytest.raises(ReviewStateError, match="Unknown note ID"):
        flag_suspect_note(tmp_path, note_id="2026-01-01-missing", reason="not useful")


def test_doctor_reports_missing_data_dir_file_data_dir_and_missing_artifacts(tmp_path):
    missing = tmp_path / "missing"
    file_root = tmp_path / "file-root"
    file_root.write_text("not a directory", encoding="utf-8")

    missing_report = run_doctor(missing, env={})
    file_report = run_doctor(file_root, env={})

    assert missing_report.error_count == 1
    assert missing_report.findings[0].code == "data-dir-missing"
    assert file_report.error_count == 1
    assert file_report.findings[0].code == "data-dir-not-directory"

    initialize_data_dir(tmp_path)
    (tmp_path / ".kb" / "backlinks.json").unlink()
    (tmp_path / ".kb" / "index-manifest.json").unlink()
    (tmp_path / ".kb" / "errors.log").unlink()
    (tmp_path / "INDEX-999.md").write_text("# stale\n", encoding="utf-8")

    rendered = render_doctor_report(run_doctor(tmp_path, env={}))

    assert "backlinks-missing" in rendered
    assert "manifest-missing" in rendered
    assert "errors-log-missing" in rendered
    assert "fts-empty-kb" in rendered
    assert "markdown-index-stale-page" in rendered


def test_doctor_reports_malformed_artifacts_and_interrupted_recovery(tmp_path):
    initialize_data_dir(tmp_path)
    _config(tmp_path)
    _note(tmp_path, note_id="2026-01-01-doctor", title="Doctor")
    reindex_data_dir(tmp_path)
    (tmp_path / ".kb" / "backlinks.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / ".kb" / "index-manifest.json").write_text("[]", encoding="utf-8")
    (tmp_path / ".kb" / "fts.sqlite").write_text("not sqlite", encoding="utf-8")
    (tmp_path / ".kb" / "ingest.lock").write_text("{not-json", encoding="utf-8")
    write_state(
        tmp_path,
        {
            "ingest": {
                "operation_id": "ingest-doctor",
                "status": "interrupted",
                "current_raw_file": str(tmp_path / "raw" / "stuck.md"),
                "last_completed_stage": "parsed",
            }
        },
    )
    (tmp_path / ".kb" / "errors.log").write_text("previous failure\n", encoding="utf-8")

    rendered = render_doctor_report(run_doctor(tmp_path, env={}))

    assert "ingest-lock-stale" in rendered
    assert "ingest-checkpoint-interrupted" in rendered
    assert "backlinks-unreadable" in rendered
    assert "manifest-invalid" in rendered
    assert "fts-unreadable" in rendered
    assert "raw-ingest-errors" in rendered


def test_doctor_reports_review_targets_embedding_config_and_provider_auth_branches(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    config["retrieval"]["embedding_provider"] = "local"
    config["retrieval"]["embedding_model"] = "nomic-embed-text"
    config["retrieval"]["embedding_dimensions"] = 768
    config["providers"]["codex"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
        "base_url": "https://api.openai.com/v1",
    }
    config["operations"]["extract"] = {"provider": "codex", "model": "gpt-5-codex"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    add_review_item(
        tmp_path,
        queue="classification",
        title="Missing target",
        target_notes=["2026-01-01-missing"],
        proposed_action="review classification manually",
        payload={"reason": "coverage"},
        created="2026-01-01",
        fingerprint="coverage-review-target",
    )
    monkeypatch.setattr(
        "kb_librarian.doctor.codex_cli_status",
        lambda *args, **kwargs: CodexCliStatus(
            available=True,
            authenticated=True,
            code="authenticated",
            message="Codex CLI is authenticated.",
            command_path="/usr/bin/codex",
        ),
    )

    rendered = render_doctor_report(run_doctor(tmp_path, env={}))

    assert "review-target-missing" in rendered
    assert "embedding-seam-configured" in rendered
    assert "codex-cli-authenticated" in rendered


def test_doctor_reports_local_model_listing_and_auto_commit_scope_warnings(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "ollama"
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "missing-model"}
    config["git"]["auto_commit"] = True
    config["git"]["commit_ingests"] = False
    config["git"]["commit_reviews"] = False
    config["git"]["commit_reindexes"] = False
    config["git"]["commit_topic_reorganizations"] = False
    config["git"]["allow_unrelated_changes"] = True
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(True, ["other-model"], "Ollama backend is reachable."),
    )

    rendered = render_doctor_report(run_doctor(tmp_path, env={}))

    assert "local-provider-model-missing" in rendered
    assert "listed by Ollama: missing-model" in rendered
    assert "auto-commit-no-scopes" in rendered
    assert "auto-commit-unrelated-allowed" in rendered


def test_ingest_identical_merge_and_contradiction_decisions_update_notes_and_reviews(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _config(tmp_path)
    target_id = "2026-01-01-coverage-ingest"
    target_path = _note(
        tmp_path,
        note_id=target_id,
        title="Coverage ingest",
        sources=[{"type": "ingest", "ref": "raw/original.md", "hash": "original"}],
        extra={"disputes": []},
    )
    reindex_data_dir(tmp_path)

    source = tmp_path / "raw" / "identical.md"
    source.write_text("# Coverage ingest\n\nA second source for the same note.\n", encoding="utf-8")
    monkeypatch.setattr(
        "kb_librarian.ingest.provider_from_config",
        lambda *args, **kwargs: _FixedVerdictProvider("identical", target_id),
    )

    identical = ingest(tmp_path, config=config)

    assert identical.source_appends == [target_id]
    assert read_note(target_path).frontmatter["sources"][-1]["ref"].endswith("raw/identical.md")

    merge_source = tmp_path / "raw" / "merge.md"
    merge_source.write_text("# Coverage ingest\n\nA nuance that should be reviewed before merging.\n", encoding="utf-8")
    monkeypatch.setattr(
        "kb_librarian.ingest.provider_from_config",
        lambda *args, **kwargs: _FixedVerdictProvider("adds_nuance", target_id),
    )

    merge = ingest(tmp_path, config=config)

    assert merge.merge_proposals == ["merge-" + date.today().isoformat() + "-001"]
    assert (tmp_path / "review" / "pending-merge.md").is_file()

    dispute_source = tmp_path / "raw" / "dispute.md"
    dispute_source.write_text("# Coverage ingest\n\nA contradiction that should mark the target disputed.\n", encoding="utf-8")
    monkeypatch.setattr(
        "kb_librarian.ingest.provider_from_config",
        lambda *args, **kwargs: _FixedVerdictProvider("contradicts", target_id, rationale="conflicts with target"),
    )

    dispute = ingest(tmp_path, config=config)
    disputed_note = read_note(target_path)

    assert dispute.disputes == [target_id]
    assert dispute.dispute_items == ["dispute-" + date.today().isoformat() + "-001"]
    assert disputed_note.frontmatter["status"] == "disputed"
    assert disputed_note.frontmatter["disputes"][0]["rationale"] == "conflicts with target"


def test_hygiene_stale_orphan_and_dispute_suspect_payloads(tmp_path):
    initialize_data_dir(tmp_path)
    today = date(2026, 6, 27)
    old = "2025-10-01"
    _note(
        tmp_path,
        note_id="2025-10-01-stale-fact",
        title="Stale fact",
        updated=old,
        knowledge_type="fact",
        staleness_risk="medium",
        confidence="low",
        sources=[{"type": "paper"}, {"type": "docs"}],
    )
    _note(
        tmp_path,
        note_id="2025-10-01-orphan",
        title="Orphan",
        topic="isolated",
        updated=old,
        staleness_risk="low",
        tags=["orphan"],
    )
    reindex_data_dir(tmp_path)

    result = scan_hygiene_queues(tmp_path, config=default_config(tmp_path), today=today)

    assert result.stale_item_ids == ["stale-2026-06-27-001", "stale-2026-06-27-002"]
    assert result.orphan_item_ids == ["orphan-2026-06-27-001", "orphan-2026-06-27-002"]
    state = json.loads((tmp_path / "review" / "review-items.json").read_text(encoding="utf-8"))
    stale = next(item for item in state["items"] if item["target_notes"] == ["2025-10-01-stale-fact"])
    orphan = next(item for item in state["items"] if item["queue"] == "orphan")
    assert stale["priority"] == "high"
    assert stale["payload"]["source_quality"] == "high"
    assert stale["payload"]["stale_after_days"] < 180
    assert orphan["payload"]["topic_neighbors"] == 0
    assert "archive if obsolete" in orphan["payload"]["suggested_actions"]

    suspect = flag_suspect_note(
        tmp_path,
        note_id="2025-10-01-stale-fact",
        reason="This contradicts the current docs.",
        timestamp="2026-06-27T12:00:00",
    )

    assert suspect.queue == "dispute"
    assert suspect.suspect_count == 1
    updated_state = json.loads((tmp_path / "review" / "review-items.json").read_text(encoding="utf-8"))
    dispute = next(item for item in updated_state["items"] if item["queue"] == "dispute")
    assert dispute["priority"] == "high"
    assert dispute["payload"]["reasons"] == ["This contradicts the current docs."]


def test_doctor_reports_active_lock_cloud_privacy_and_cli_credentials(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["privacy"]["cloud_llm_allowed"] = False
    config["providers"]["mock"] = {}
    config["providers"]["anthropic"] = {
        "backend": "vendor_cli",
        "credential_source": "token_env",
        "token_env": "CLAUDE_TOKEN_FOR_TEST",
    }
    config["providers"]["codex"] = {
        "backend": "direct_http",
        "credential_source": "api_key_env",
        "api_key_env": "OPENAI_TOKEN_FOR_TEST",
        "base_url": "https://api.openai.com/v1",
    }
    config["providers"]["policy"]["fallback"] = {
        "extract": [{"provider": "codex", "model": "gpt-test"}],
    }
    config["operations"]["extract"] = {"provider": "anthropic", "model": "claude-test"}
    config["operations"]["classify"] = {"provider": "codex", "model": "gpt-test"}
    config["operations"]["integrate"] = {"provider": "mock", "model": "mock-integrate"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)
    (tmp_path / ".kb" / "ingest.lock").write_text(
        json.dumps(
            {
                "operation_id": "ingest-active",
                "pid": __import__("os").getpid(),
                "hostname": __import__("socket").gethostname(),
                "command": "kb ingest",
                "current_raw_file": str(tmp_path / "raw" / "active.md"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "kb_librarian.doctor.claude_cli_status",
        lambda *args, **kwargs: ClaudeCliStatus(
            available=False,
            authenticated=False,
            code="missing_command",
            message="Claude CLI is missing.",
        ),
    )

    rendered_without_env = render_doctor_report(run_doctor(tmp_path, env={}))
    rendered_with_env = render_doctor_report(
        run_doctor(
            tmp_path,
            env={"CLAUDE_TOKEN_FOR_TEST": "set", "OPENAI_TOKEN_FOR_TEST": "set"},
        )
    )

    assert "ingest-lock-active" in rendered_without_env
    assert "anthropic-cli-token-unset" in rendered_without_env
    assert "codex-provider-api-key-unset" in rendered_without_env
    assert "cloud-provider-blocked-by-privacy" in rendered_without_env
    assert "provider-policy" in rendered_without_env
    assert "anthropic-cli-token-present" in rendered_with_env
    assert "codex-provider-credentials" in rendered_with_env
