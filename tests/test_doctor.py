from __future__ import annotations

import json

from kb_librarian.config import default_config, write_config_file
from kb_librarian.doctor import render_doctor_report, render_self_test_report, run_doctor, run_doctor_self_test
from kb_librarian.indexing import reindex_data_dir
from kb_librarian.init import initialize_data_dir
from kb_librarian.notes import Note, write_note
from kb_librarian.providers import LocalProviderStatus
from kb_librarian.storage import canonical_note_path


def _frontmatter(
    *,
    note_id: str,
    title: str,
    topic: str = "agent-systems",
) -> dict[str, object]:
    return {
        "id": note_id,
        "title": title,
        "summary": "Summary text.",
        "topic": topic,
        "created": "2026-05-05",
        "updated": "2026-05-05",
        "knowledge_type": "technique",
        "status": "active",
        "confidence": "high",
        "retrieval_phrases": ["doctor test"],
        "tags": ["diagnostics"],
    }


def _mock_config(data_dir):
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    for operation in list(config["operations"]):
        config["operations"][operation] = {"provider": "mock", "model": f"mock-{operation}"}
    write_config_file(data_dir / ".kb" / "config.yaml", config)
    return config


def test_doctor_reports_healthy_kb_after_reindex(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    note = Note(
        _frontmatter(note_id="2026-05-05-doctor-health", title="Doctor health"),
        "Local diagnostics should stay read-only.\n",
    )
    write_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-doctor-health"), note)
    reindex_data_dir(tmp_path, config=config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "[ok] config-valid" in rendered
    assert "[ok] fts-current" in rendered
    assert "embedding-seam-disabled" in rendered
    assert "provider-seam" in rendered
    assert "[ok] provider-routes" in rendered


def test_doctor_reports_embedding_seam_enabled_as_unsupported(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    config["retrieval"]["embeddings"] = True
    config["retrieval"]["embedding_provider"] = "local"
    config["retrieval"]["embedding_model"] = "nomic-embed-text"
    config["retrieval"]["embedding_dimensions"] = 768
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "embeddings-unsupported" in rendered
    assert "lexical retrieval remains active" in rendered


def test_doctor_reports_broken_links_and_stale_fts(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    first = Note(
        _frontmatter(note_id="2026-05-05-first-note", title="First note"),
        "This note links to 2026-05-05-missing-note.\n",
    )
    write_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-first-note"), first)
    reindex_data_dir(tmp_path, config=config)

    second = Note(
        _frontmatter(note_id="2026-05-05-second-note", title="Second note"),
        "This note was added after reindex.\n",
    )
    write_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-second-note"), second)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count >= 2
    assert "broken-note-link" in rendered
    assert "fts-stale" in rendered
    assert "markdown-index-stale" in rendered


def test_doctor_reports_duplicate_ids_and_unreadable_review_state(tmp_path):
    initialize_data_dir(tmp_path)
    _mock_config(tmp_path)
    note_id = "2026-05-05-duplicate-note"
    first = Note(_frontmatter(note_id=note_id, title="Duplicate A", topic="agent-systems"), "A.\n")
    second = Note(_frontmatter(note_id=note_id, title="Duplicate B", topic="coding-techniques"), "B.\n")
    write_note(canonical_note_path(tmp_path, "agent-systems", note_id), first)
    write_note(canonical_note_path(tmp_path, "coding-techniques", note_id), second)
    (tmp_path / "review" / "review-items.json").write_text("{not-json", encoding="utf-8")

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count >= 2
    assert "duplicate-note-id" in rendered
    assert "review-state-unreadable" in rendered


def test_doctor_self_test_runs_offline_fixture():
    report = run_doctor_self_test()
    rendered = render_self_test_report(report)

    assert report.error_count == 0
    assert "Self-test passed." in rendered
    assert "[ok] ingest" in rendered
    assert "[ok] search" in rendered


def test_doctor_detects_stale_manifest(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    note = Note(
        _frontmatter(note_id="2026-05-05-manifest-note", title="Manifest note"),
        "Manifest should track indexed files.\n",
    )
    write_note(canonical_note_path(tmp_path, "agent-systems", "2026-05-05-manifest-note"), note)
    reindex_data_dir(tmp_path, config=config)
    manifest_path = tmp_path / ".kb" / "index-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["indexed_files"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "manifest-stale" in rendered


def test_doctor_reports_auto_commit_enabled_outside_git(tmp_path):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    config["git"]["auto_commit"] = True
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "auto-commit-no-worktree" in rendered


def test_doctor_reports_local_provider_unreachable_when_routed(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "llama3.2"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(False, [], "Ollama backend is unreachable."),
    )

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 1
    assert "local-provider-unreachable" in rendered
    assert "Start Ollama" in rendered


def test_doctor_reports_local_provider_reachable_when_routed(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "llama3.2"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(True, ["llama3.2:latest"], "Ollama backend is reachable."),
    )

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "local-provider-reachable" in rendered


def test_doctor_reports_vllm_missing_models_when_routed(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "vllm"
    config["providers"]["local"]["base_url"] = "http://127.0.0.1:8000"
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "meta-llama/Meta-Llama-3-8B-Instruct"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(True, [], "vLLM backend is reachable."),
    )

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "local-provider-model-missing" in rendered
    assert "Start vLLM with the routed model" in rendered


def test_doctor_reports_lm_studio_unreachable_hint(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["local"]["backend"] = "lm_studio"
    config["providers"]["local"]["base_url"] = "http://127.0.0.1:1234"
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "local", "model": "qwen2.5-instruct"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(False, [], "LM Studio backend is unreachable."),
    )

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 1
    assert "local-provider-unreachable" in rendered
    assert "LM Studio local server" in rendered


def test_doctor_checks_fallback_provider_routes(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _mock_config(tmp_path)
    config["providers"]["policy"]["fallback"] = {
        "synthesize": [
            {"provider": "local", "model": "llama3.2"},
        ]
    }
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    monkeypatch.setattr(
        "kb_librarian.doctor.local_provider_status",
        lambda *args, **kwargs: LocalProviderStatus(False, [], "Ollama backend is unreachable."),
    )

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 1
    assert "provider-policy" in rendered
    assert "local-provider-unreachable" in rendered


def test_doctor_reports_codex_api_key_warning_when_routed(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "codex", "model": "gpt-5.1-codex"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "codex-provider-api-key-unset" in rendered
    assert "OPENAI_API_KEY" in rendered


def test_doctor_reports_codex_credentials_when_routed(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    for operation in config["operations"]:
        config["operations"][operation] = {"provider": "codex", "model": "gpt-5.1-codex"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={"OPENAI_API_KEY": "test-key"})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "codex-provider-credentials" in rendered
    assert "backend=direct_http credential_source=api_key_env" in rendered


def test_doctor_reports_unimplemented_vendor_cli_runtime(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["anthropic"] = {
        "backend": "vendor_cli",
        "credential_source": "vendor_cli",
    }
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 1
    assert "provider-seam" in rendered
    assert "provider-runtime-unsupported" in rendered
    assert "backend=vendor_cli credential_source=vendor_cli" in rendered


def test_doctor_warns_when_privacy_blocks_cloud_routes(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["privacy"]["cloud_llm_allowed"] = False
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    report = run_doctor(tmp_path, env={})
    rendered = render_doctor_report(report)

    assert report.error_count == 0
    assert "cloud-provider-blocked-by-privacy" in rendered
