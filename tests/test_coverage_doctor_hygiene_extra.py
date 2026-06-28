from __future__ import annotations

import builtins
import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import kb_librarian.doctor as doctor
import kb_librarian.hygiene as hygiene
from kb_librarian.config import default_config, write_config_file
from kb_librarian.errors import IngestError, ProviderError
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.provider_seams import ProviderSeam
from kb_librarian.providers import ClaudeCliStatus, CodexCliStatus, LocalProviderStatus
from kb_librarian.review import ReviewStateError, STATE_VERSION
from kb_librarian.storage import NoteRecord, canonical_note_path, ensure_topic_layout
from kb_librarian.usage import log_note_use, log_retrieval, log_suspect_flag


def _write_config(data_dir: Path, config: dict[str, object] | None = None) -> dict[str, object]:
    cfg = default_config(data_dir) if config is None else config
    cfg["providers"]["mock"] = {"backend": "mock"}  # type: ignore[index]
    for operation in cfg["operations"]:  # type: ignore[index]
        cfg["operations"][operation] = {"provider": "mock", "model": f"mock-{operation}"}  # type: ignore[index]
    write_config_file(data_dir / ".kb" / "config.yaml", cfg)
    return cfg


def _note(
    data_dir: Path,
    *,
    note_id: str = "2026-01-01-extra",
    topic: str = "agent-systems",
    updated: str = "2026-01-01",
    body: str = "## Core idea\n\nExtra coverage note.\n",
    frontmatter: dict[str, object] | None = None,
) -> Path:
    ensure_topic_layout(data_dir, topic)
    fm: dict[str, object] = {
        "id": note_id,
        "title": "Extra coverage",
        "summary": "Summary text.",
        "topic": topic,
        "created": "2026-01-01",
        "updated": updated,
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": ["extra coverage"],
        "tags": ["coverage"],
    }
    if frontmatter:
        fm.update(frontmatter)
    path = canonical_note_path(data_dir, topic, note_id)
    write_note(path, Note(frontmatter=fm, body=body))
    return path


def _record(data_dir: Path, *, note_id: str = "2026-01-01-extra", **kwargs: object) -> NoteRecord:
    path = _note(data_dir, note_id=note_id, **kwargs)
    return NoteRecord(path=path, topic_path=path.parent.relative_to(data_dir / "topics").as_posix(), note=doctor.read_note(path))


def _codes(findings: list[doctor.DoctorFinding]) -> set[str]:
    return {finding.code for finding in findings}


def test_run_doctor_continues_without_config_and_reports_layout_gaps(tmp_path):
    initialize_data_dir(tmp_path)
    (tmp_path / ".kb" / "config.yaml").unlink()
    shutil.rmtree(tmp_path / "raw")
    (tmp_path / "INDEX.md").unlink(missing_ok=True)
    (tmp_path / "PREAMBLE.md").unlink(missing_ok=True)
    (tmp_path / ".kb" / "usage.log").unlink()

    report = doctor.run_doctor(tmp_path, env={})

    codes = _codes(report.findings)
    assert {"directory-missing", "root-file-missing", "log-file-missing", "config-missing"} <= codes
    assert "provider-routes" not in codes


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("[]", "Review state must be a JSON object."),
        ({"version": 999, "items": []}, "unsupported version"),
        ({"version": STATE_VERSION, "items": {}}, "items must be a list"),
        ({"version": STATE_VERSION, "items": ["bad"]}, "items must contain only objects"),
    ],
)
def test_doctor_reports_review_state_shape_errors(tmp_path, payload, expected):
    initialize_data_dir(tmp_path)
    path = tmp_path / "review" / "review-items.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")

    findings: list[doctor.DoctorFinding] = []
    doctor._check_review_state(tmp_path, set(), findings)

    assert findings[0].code == "review-state-unreadable"
    assert expected in findings[0].message


def test_doctor_artifact_error_branches(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    _write_config(tmp_path)
    record = _record(tmp_path)
    findings: list[doctor.DoctorFinding] = []

    monkeypatch.setattr(doctor, "resumable_ingest_checkpoint", lambda data_dir: (_ for _ in ()).throw(IngestError("bad state")))
    doctor._check_recovery_state(tmp_path, findings)

    page = SimpleNamespace(path=tmp_path / "INDEX-001.md", content="# expected\n")
    page.path.write_text("# stale\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "group_records_for_indexing", lambda *args, **kwargs: {})
    monkeypatch.setattr(doctor, "render_index_pages", lambda *args, **kwargs: [page])
    original_read_text = Path.read_text
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("cannot read"))
        if self == page.path
        else original_read_text(self, *args, **kwargs),
    )
    doctor._check_markdown_indexes(tmp_path, [record], {}, findings)
    monkeypatch.setattr(Path, "read_text", original_read_text)

    (tmp_path / ".kb" / "index-manifest.json").write_text("{bad", encoding="utf-8")
    doctor._check_manifest(tmp_path, [record], {}, findings)
    (tmp_path / ".kb" / "backlinks.json").write_text(json.dumps({"other": ["x"]}), encoding="utf-8")
    doctor._check_backlinks(tmp_path, [record], findings)
    (tmp_path / ".kb" / "errors.log").unlink()
    monkeypatch.setattr(Path, "exists", lambda self: True if self == tmp_path / ".kb" / "errors.log" else original_exists(self))
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("cannot read"))
        if self == tmp_path / ".kb" / "errors.log"
        else original_read_text(self, *args, **kwargs),
    )
    doctor._check_raw_ingest_errors(tmp_path, findings)

    codes = _codes(findings)
    assert {"ingest-state-unreadable", "markdown-index-unreadable", "manifest-unreadable", "backlinks-stale", "errors-log-unreadable"} <= codes


@pytest.mark.parametrize(
    ("failing_stage", "expected_code"),
    [
        ("ingest", "ingest"),
        ("reindex", "reindex"),
        ("search", "search"),
        ("doctor", "doctor"),
    ],
)
def test_doctor_self_test_reports_each_failure_stage(monkeypatch, failing_stage, expected_code):
    monkeypatch.setattr(doctor, "ingest", lambda *args, **kwargs: SimpleNamespace(errors=failing_stage == "ingest", created_notes=[] if failing_stage == "ingest" else ["note"]))
    monkeypatch.setattr(doctor, "reindex_data_dir", lambda *args, **kwargs: SimpleNamespace(note_count=0 if failing_stage == "reindex" else 1))
    monkeypatch.setattr(doctor, "query_candidates", lambda *args, **kwargs: [] if failing_stage == "search" else ["hit"])
    monkeypatch.setattr(
        doctor,
        "run_doctor",
        lambda *args, **kwargs: SimpleNamespace(
            error_count=1 if failing_stage == "doctor" else 0,
            findings=[doctor.DoctorFinding("Config", "error", "config-invalid", "bad config")],
        ),
    )

    report = doctor.run_doctor_self_test()

    assert report.error_count == 1
    assert report.findings[-1].code == expected_code
    assert ("failed" if failing_stage else "passed") in doctor.render_self_test_report(report)


def test_doctor_provider_route_edge_branches(monkeypatch):
    config = {
        "providers": {
            "anthropic": {"backend": "vendor_cli", "credential_source": "vendor_cli"},
            "codex": {"backend": "vendor_cli", "credential_source": "vendor_cli"},
            "local": {"backend": "vllm"},
            "mock": {"backend": "mock"},
            "broken": {"backend": "direct_http"},
            "unsupported": {},
        },
        "operations": {
            "missing": {"provider": "missing", "model": "m"},
            "unsupported": {"provider": "unsupported", "model": "m"},
            "broken": {"provider": "broken", "model": "m"},
            "anthropic": {"provider": "anthropic", "model": "m"},
            "codex": {"provider": "codex", "model": "m"},
            "local": {"provider": "local", "model": "missing"},
            "mock": {"provider": "mock", "model": "m"},
        },
    }
    findings: list[doctor.DoctorFinding] = []

    def fake_runtime(provider_name, provider_config):
        if provider_name == "broken":
            raise ProviderError("bad backend")
        if provider_name == "mock":
            raise ProviderError("mock config exploded")
        if provider_name == "anthropic":
            return ProviderSeam(provider_name, "vendor_cli", "vendor_cli", cli_command="claude"), None
        if provider_name == "codex":
            return ProviderSeam(provider_name, "vendor_cli", "vendor_cli", cli_command="codex"), None
        if provider_name == "local":
            return ProviderSeam(provider_name, "vllm", None), None
        return ProviderSeam(provider_name, provider_name, None), None

    monkeypatch.setattr(doctor, "provider_runtime_support", fake_runtime)
    monkeypatch.setattr(doctor, "claude_cli_status", lambda *args, **kwargs: ClaudeCliStatus(False, False, "command_error", "boom"))
    monkeypatch.setattr(doctor, "codex_cli_status", lambda *args, **kwargs: CodexCliStatus(False, False, "missing_command", "missing"))
    monkeypatch.setattr(doctor, "local_provider_status", lambda *args, **kwargs: LocalProviderStatus(False, [], "vLLM offline."))

    doctor._check_provider_routes(config, {}, findings)

    codes = _codes(findings)
    assert {
        "provider-missing",
        "provider-unsupported",
        "provider-config-invalid",
        "anthropic-cli-command_error",
        "codex-cli-missing_command",
        "local-provider-unreachable",
    } <= codes
    assert "Start the vLLM server" in next(f.message for f in findings if f.code == "local-provider-unreachable")


def test_hygiene_usage_signals_recent_use_corrections_and_invalid_inputs(tmp_path):
    initialize_data_dir(tmp_path)
    log_note_use(tmp_path, note_id="2026-01-01-extra", timestamp="2026-06-20T10:00:00")
    log_suspect_flag(tmp_path, note_id="2026-01-01-extra", reason="incorrect answer", timestamp="2026-06-21T10:00:00")
    with (tmp_path / ".kb" / "usage.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "retrieval", "returned_note_ids": "not-list"}) + "\n")
        handle.write(json.dumps({"event": "note-use", "note_id": "ignored", "timestamp": "not-a-date"}) + "\n")
        handle.write(json.dumps({"event": "suspect-flag", "note_id": "2026-01-01-extra", "reason": ""}) + "\n")

    signals = hygiene._usage_signals(tmp_path, now=date(2026, 6, 27))

    assert signals.logged_use_counts["2026-01-01-extra"] == 1
    assert signals.recent_use_counts["2026-01-01-extra"] == 1
    assert signals.correction_counts["2026-01-01-extra"] == 1
    assert hygiene._event_note_ids({"returned_note_ids": "bad"}) == []
    assert hygiene._event_date({"timestamp": ""}) is None
    assert hygiene._event_date({"timestamp": "bad"}) is None


def test_hygiene_helpers_and_suspect_fallback_paths(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    path = tmp_path / "topics" / "agent-systems" / "2026-01-01-extra.md"
    record = NoteRecord(
        path=path,
        topic_path="agent-systems",
        note=Note(
            frontmatter={
                "id": "2026-01-01-extra",
                "title": "Extra coverage",
                "summary": "Summary text.",
                "topic": "agent-systems",
                "created": "2026-01-01",
                "updated": "not-a-date",
                "knowledge_type": "technique",
                "status": "active",
                "confidence": "high",
                "retrieval_phrases": ["extra coverage"],
                "tags": ["coverage"],
                "sources": ["bad", {"type": "conversation"}, {"type": "unknown"}],
                "nested": {"refs": ["2026-01-01-linked", " "]},
            },
            body="References 2026-01-01-extra and self 2026-01-01-extra.\n",
        ),
    )
    usage = hygiene._UsageSignals({}, {}, {"2026-01-01-extra": 1}, {}, {}, {}, {}, {}, {}, {})

    assert hygiene._maybe_upsert_stale(data_dir=tmp_path, record=record, stale_after_days=180, today=date(2026, 6, 27)) is None
    assert hygiene._maybe_upsert_orphan(data_dir=tmp_path, record=record, backlinks={}, usage=usage, by_topic={}, orphan_after_days=30, today=date(2026, 6, 27)) is None
    assert hygiene._source_quality(record.note) == "medium"
    assert hygiene._load_backlinks(tmp_path) == {}
    (tmp_path / ".kb" / "backlinks.json").write_text("[]", encoding="utf-8")
    assert hygiene._load_backlinks(tmp_path) == {}
    (tmp_path / ".kb" / "backlinks.json").write_text(json.dumps({"a": ["b", ""]}), encoding="utf-8")
    assert hygiene._load_backlinks(tmp_path) == {"a": ["b"]}
    assert hygiene._topic_neighbor_count(record, by_topic={record.topic_path: [record]}) == 0
    assert hygiene._string_list("not-list") == []
    assert hygiene._dedupe_preserve_order(["  a  ", "a", " ", "b"]) == ["a", "b"]

    monkeypatch.setattr(hygiene, "upsert_hygiene_review_item", lambda *args, **kwargs: None)
    with pytest.raises(ReviewStateError, match="dispute review item"):
        hygiene._upsert_suspect_item(data_dir=tmp_path, record=record, usage=usage, reason="wrong", queue="dispute")
    with pytest.raises(ReviewStateError, match="low-utility review item"):
        hygiene._upsert_suspect_item(data_dir=tmp_path, record=record, usage=usage, reason="needs review", queue="low_utility")


def test_doctor_config_notes_review_and_index_file_edge_branches(tmp_path):
    initialize_data_dir(tmp_path)

    config_file = tmp_path / ".kb" / "config.yaml"
    config_file.write_text("not: [valid\n", encoding="utf-8")
    findings: list[doctor.DoctorFinding] = []
    assert doctor._check_config(tmp_path, findings) is None
    assert findings[-1].code == "config-invalid"

    cfg = default_config(tmp_path)
    cfg["data_dir"] = str(tmp_path / "other")
    write_config_file(config_file, cfg)
    findings = []
    assert doctor._check_config(tmp_path, findings) is not None
    assert {"data-dir-mismatch", "config-valid"} <= _codes(findings)

    bad_note = tmp_path / "topics" / "agent-systems" / "bad.md"
    bad_note.parent.mkdir(parents=True, exist_ok=True)
    bad_note.write_text("---\nid: bad\n---\nBody\n", encoding="utf-8")
    findings = []
    assert doctor._check_notes(tmp_path, findings) == []
    assert findings[-1].code == "note-invalid"

    review_path = tmp_path / "review" / "review-items.json"
    review_path.unlink()
    findings = []
    doctor._check_review_state(tmp_path, set(), findings)
    assert findings[-1].code == "review-state-missing"

    review_path.write_text(
        json.dumps(
            {
                "version": STATE_VERSION,
                "items": [
                    {
                        "id": "stale-1",
                        "queue": "stale",
                        "status": "pending",
                        "priority": "medium",
                        "title": "Missing target",
                        "created": "2026-01-01",
                        "updated": "2026-01-01",
                        "target_notes": ["2026-01-01-missing"],
                        "proposed_action": "reverify or refresh note",
                        "payload": {},
                        "history": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    findings = []
    doctor._check_review_state(tmp_path, set(), findings)
    assert "review-target-missing" in _codes(findings)

    (tmp_path / "INDEX-001.md").write_text("root", encoding="utf-8")
    (tmp_path / "INDEX-draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "topics" / "agent-systems" / "INDEX-002.md").write_text("topic", encoding="utf-8")
    assert [path.name for path in doctor._generated_index_page_files(tmp_path)] == ["INDEX-001.md", "INDEX-002.md"]
    assert doctor._is_numbered_index_page(Path("README.md")) is False


def test_doctor_provider_policy_runtime_and_credential_branches(monkeypatch):
    findings: list[doctor.DoctorFinding] = []
    doctor._check_provider_routes({"providers": [], "operations": {}}, {}, findings)
    assert findings == []

    monkeypatch.setattr(doctor, "operation_routes", lambda *_args, **_kwargs: (_ for _ in ()).throw(ProviderError("bad policy")))
    findings = []
    doctor._check_provider_routes({"providers": {}, "operations": {"ingest": {}}}, {}, findings)
    assert findings[-1].code == "provider-policy-invalid"

    def fake_routes(_config, operation):
        if operation == "fallback":
            return [SimpleNamespace(provider="mock", model="m1"), SimpleNamespace(provider="mock", model="m2")]
        if operation == "anthropic":
            return [SimpleNamespace(provider="anthropic", model="m")]
        if operation == "codex":
            return [SimpleNamespace(provider="codex", model="m")]
        if operation == "local":
            return [SimpleNamespace(provider="local", model="model-a")]
        return [SimpleNamespace(provider="mock", model="m")]

    def fake_runtime(provider_name, _provider_config):
        if provider_name == "mock":
            return ProviderSeam(provider_name, "mock", None), "mock cannot run"
        if provider_name == "anthropic":
            return ProviderSeam(provider_name, "vendor_cli", "token_env", token_env="CLAUDE_TOKEN"), None
        if provider_name == "codex":
            return ProviderSeam(provider_name, "direct_http", "api_key_env", api_key_env="OPENAI_TOKEN"), None
        return ProviderSeam(provider_name, "lm_studio", None), None

    monkeypatch.setattr(doctor, "operation_routes", fake_routes)
    monkeypatch.setattr(doctor, "provider_runtime_support", fake_runtime)
    monkeypatch.setattr(
        doctor,
        "local_provider_status",
        lambda *_args, **_kwargs: LocalProviderStatus(True, [], "local is reachable"),
    )
    findings = []
    doctor._check_provider_routes(
        {
            "providers": {"mock": {}, "anthropic": {}, "codex": {}, "local": {}},
            "operations": {"fallback": {}, "anthropic": {}, "codex": {}, "local": {}},
            "privacy": {"cloud_llm_allowed": False},
        },
        {"CLAUDE_TOKEN": "token", "OPENAI_TOKEN": "token"},
        findings,
    )

    codes = _codes(findings)
    assert {
        "provider-runtime-unsupported",
        "anthropic-cli-token-present",
        "codex-provider-credentials",
        "local-provider-model-missing",
        "cloud-provider-blocked-by-privacy",
        "provider-policy",
    } <= codes
    assert "LM Studio" in next(f.message for f in findings if f.code == "local-provider-model-missing")


def test_doctor_parser_auto_commit_and_local_helper_branches(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    findings: list[doctor.DoctorFinding] = []
    doctor._check_parser_dependencies(findings)
    assert findings[-1].code == "pdf-parser-missing"

    assert doctor._local_provider_unreachable_hint("other") == "Check providers.local.base_url or switch local operation routes."
    assert doctor._local_provider_missing_model_hint("lm_studio") == "Load the routed model in LM Studio or update operation routes."
    assert doctor._local_provider_missing_model_hint("other") == "Update operation routes."
    assert doctor._local_backend_name("lm_studio") == "LM Studio"
    assert doctor._local_backend_name("custom") == "custom"

    findings = []
    doctor._check_auto_commit_config(tmp_path, {"git": []}, findings)
    assert findings == []

    monkeypatch.setattr(doctor.shutil, "which", lambda command: None)
    findings = []
    doctor._check_auto_commit_config(tmp_path, {"git": {"auto_commit": True}}, findings)
    assert "git-missing" in _codes(findings)

    monkeypatch.setattr(doctor.shutil, "which", lambda command: "/usr/bin/git")
    monkeypatch.setattr(doctor, "git_worktree_root", lambda data_dir: None)
    findings = []
    doctor._check_auto_commit_config(
        tmp_path,
        {
            "git": {
                "auto_commit": True,
                "commit_ingests": False,
                "commit_reviews": False,
                "commit_reindexes": False,
                "commit_topic_reorganizations": False,
                "allow_unrelated_changes": True,
            }
        },
        findings,
    )
    assert {"auto-commit-no-worktree", "auto-commit-no-scopes", "auto-commit-unrelated-allowed"} <= _codes(findings)

    monkeypatch.setattr(doctor, "git_worktree_root", lambda data_dir: tmp_path)
    findings = []
    doctor._check_auto_commit_config(tmp_path, {"git": {"auto_commit": True}}, findings)
    assert findings[-1].code == "auto-commit-ready"


def test_hygiene_remaining_usage_orphan_backlink_and_helper_branches(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    record = _record(
        tmp_path,
        note_id="2026-01-01-orphan",
        updated="2025-01-01",
        frontmatter={"tags": [], "sources": [{"type": "unknown"}], "staleness_risk": "high"},
        body="Nested ref only in frontmatter.\n",
    )
    neighbor = _record(
        tmp_path,
        note_id="2026-01-02-neighbor",
        updated="2025-01-02",
        frontmatter={"tags": ["other"]},
    )
    usage = hygiene._UsageSignals(
        {},
        {},
        {},
        {},
        {},
        {},
        {"2026-01-01-orphan": 1},
        {},
        {},
        {},
    )
    assert (
        hygiene._maybe_upsert_orphan(
            data_dir=tmp_path,
            record=record,
            backlinks={},
            usage=usage,
            by_topic={},
            orphan_after_days=30,
            today=date(2026, 6, 27),
        )
        is None
    )

    log_retrieval(
        tmp_path,
        command="context",
        query="q",
        returned_note_ids=["2026-01-01-orphan"],
        result_count=1,
        top_score=4.0,
        timestamp="2026-06-20T10:00:00",
    )
    signals = hygiene._usage_signals(tmp_path, now=date(2026, 6, 27))
    assert signals.retrieval_counts["2026-01-01-orphan"] == 1
    assert signals.weak_retrieval_counts["2026-01-01-orphan"] == 1
    assert signals.recent_retrieval_counts["2026-01-01-orphan"] == 1

    backlinks_file = tmp_path / ".kb" / "backlinks.json"
    backlinks_file.write_text("{bad", encoding="utf-8")
    assert hygiene._load_backlinks(tmp_path) == {}
    original_read_text = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("cannot read"))
        if self == backlinks_file
        else original_read_text(self, *args, **kwargs),
    )
    assert hygiene._load_backlinks(tmp_path) == {}

    assert hygiene._stale_threshold_days(record.note, 100) == 90
    assert hygiene._stale_threshold_days(Note({**record.note.frontmatter, "knowledge_type": "fact", "staleness_risk": "low"}, ""), 100) == 100
    assert hygiene._stale_priority(note=record.note) == "high"
    assert hygiene._source_quality(record.note) == "low"
    assert hygiene._records_by_topic([record, neighbor])[record.topic_path] == [record, neighbor]
    assert hygiene._topic_neighbor_count(record, by_topic={record.topic_path: [record, neighbor]}) == 1
    tagged = NoteRecord(
        path=record.path,
        topic_path=record.topic_path,
        note=Note({**record.note.frontmatter, "tags": ["shared"]}, record.note.body),
    )
    tagged_neighbor = NoteRecord(
        path=neighbor.path,
        topic_path=neighbor.topic_path,
        note=Note({**neighbor.note.frontmatter, "tags": ["shared"]}, neighbor.note.body),
    )
    assert hygiene._topic_neighbor_count(tagged, by_topic={tagged.topic_path: [tagged, tagged_neighbor]}) == 1
    assert hygiene._outbound_references(
        NoteRecord(
            path=record.path,
            topic_path=record.topic_path,
            note=Note(
                {**record.note.frontmatter, "nested": {"ids": ["2026-02-03-nested"]}},
                "Body refs 2026-02-04-body and 2026-01-01-orphan.",
            ),
        )
    ) == {"2026-02-03-nested", "2026-02-04-body"}
    assert hygiene._string_list([" a ", "", 3]) == ["a", "3"]
    assert hygiene._is_weak_retrieval({"top_score": object(), "result_count": object()}) is False
