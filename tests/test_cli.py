from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from kb_librarian.config import default_config, write_config_file


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
NOTE_ID_PATTERN = re.compile(r"Created note (\S+) at ")


def run_cli(
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "kb_librarian.cli", *args],
        cwd=ROOT,
        env=merged_env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def test_kb_help_smoke():
    result = run_cli("--help")

    assert result.returncode == 0
    assert "init" in result.stdout
    assert "add" in result.stdout
    assert "search" in result.stdout


def test_kb_init_smoke_and_idempotency(tmp_path):
    first = run_cli("init", "--data-dir", str(tmp_path))
    second = run_cli("init", "--data-dir", str(tmp_path))

    assert first.returncode == 0
    assert "Initialized KB at" in first.stdout
    assert (tmp_path / ".kb" / "config.yaml").is_file()
    assert (tmp_path / "review" / "pending-merge.md").is_file()
    assert second.returncode == 0
    assert "Already initialized" in second.stdout


def test_kb_add_search_get_and_reindex_smoke(tmp_path):
    source = tmp_path / "seed.md"
    source.write_text(
        "# CLI contract retrieval\n\nPrefer `kb search` before loading full notes.\n",
        encoding="utf-8",
    )

    add_result = run_cli(
        "add",
        "--data-dir",
        str(tmp_path),
        "--topic",
        "agent-systems",
        "--type",
        "technique",
        "--from-file",
        str(source),
    )
    assert add_result.returncode == 0
    assert "Reindexed" in add_result.stdout

    match = NOTE_ID_PATTERN.search(add_result.stdout)
    assert match is not None
    note_id = match.group(1)

    search_result = run_cli("search", "CLI contract", "--data-dir", str(tmp_path))
    assert search_result.returncode == 0
    assert note_id in search_result.stdout
    assert "status=active" in search_result.stdout
    assert "confidence=medium" in search_result.stdout

    summary_result = run_cli("get", note_id, "--summary", "--data-dir", str(tmp_path))
    assert summary_result.returncode == 0
    assert "type: technique" in summary_result.stdout
    assert "status: active" in summary_result.stdout
    assert "retrieval_phrases:" in summary_result.stdout

    full_result = run_cli("get", note_id, "--data-dir", str(tmp_path))
    assert full_result.returncode == 0
    assert full_result.stdout.startswith("---\n")
    assert "knowledge_type: technique" in full_result.stdout

    reindex_result = run_cli("reindex", "--data-dir", str(tmp_path))
    assert reindex_result.returncode == 0
    assert ".kb/fts.sqlite" in reindex_result.stdout


def test_kb_add_without_direct_metadata_queues_raw(tmp_path):
    payload = "This is a raw capture that lacks topic/type metadata.\n"
    result = run_cli("add", "--data-dir", str(tmp_path), input_text=payload)

    assert result.returncode == 0
    assert "Queued raw input at" in result.stdout
    raw_files = sorted((tmp_path / "raw").glob("stdin-*.md"))
    assert raw_files
    assert raw_files[0].read_text(encoding="utf-8") == payload


def test_kb_ingest_mock_provider_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["extract"] = {"provider": "mock", "model": "mock-extract"}
    config["operations"]["classify"] = {"provider": "mock", "model": "mock-classify"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    source = tmp_path / "raw" / "agent-context.md"
    source.write_text(
        "# CLI context retrieval\n\nPrefer task-shaped context for coding agents.\n",
        encoding="utf-8",
    )

    result = run_cli("ingest", "--data-dir", str(tmp_path))

    assert result.returncode == 0
    assert "Ingest report:" in result.stdout
    assert "created_notes: 1" in result.stdout
    assert "errors: 0" in result.stdout
    assert not source.exists()
    assert list((tmp_path / "topics" / "agent-systems").glob("*.md"))


def test_kb_get_missing_id_returns_clear_error(tmp_path):
    run_cli("init", "--data-dir", str(tmp_path))
    result = run_cli("get", "2026-01-01-missing", "--data-dir", str(tmp_path))

    assert result.returncode == 1
    assert "was not found" in result.stderr


def test_placeholder_command_returns_nonzero_with_clear_error():
    result = run_cli("context", "agent context task")

    assert result.returncode == 1
    assert "kb context is not implemented in Phase 01c" in result.stderr
