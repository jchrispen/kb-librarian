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
    config["operations"]["integrate"] = {"provider": "mock", "model": "mock-integrate"}
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


def test_kb_context_mock_provider_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["synthesize"] = {"provider": "mock", "model": "mock-synthesize"}
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    seed = tmp_path / "seed.md"
    seed.write_text(
        "# Agent retrieval heuristic\n\nPrefer compact context and cite source note IDs.\n",
        encoding="utf-8",
    )

    add_result = run_cli(
        "add",
        "--data-dir",
        str(tmp_path),
        "--topic",
        "agent-systems",
        "--type",
        "heuristic",
        "--from-file",
        str(seed),
    )
    assert add_result.returncode == 0
    match = NOTE_ID_PATTERN.search(add_result.stdout)
    assert match is not None
    note_id = match.group(1)

    result = run_cli(
        "context",
        "agent retrieval context",
        "--mode",
        "coding",
        "--budget",
        "900",
        "--data-dir",
        str(tmp_path),
    )
    assert result.returncode == 0
    assert "# KB Context" in result.stdout
    assert "## Source notes" in result.stdout
    assert note_id in result.stdout
    assert "confidence:" in result.stdout
    assert "status:" in result.stdout

    json_result = run_cli(
        "context",
        "agent retrieval context",
        "--mode",
        "coding",
        "--budget",
        "900",
        "--json",
        "--data-dir",
        str(tmp_path),
    )
    assert json_result.returncode == 0
    assert "\"selected_notes\":" in json_result.stdout
    assert "\"citations\":" in json_result.stdout


def test_kb_get_missing_id_returns_clear_error(tmp_path):
    run_cli("init", "--data-dir", str(tmp_path))
    result = run_cli("get", "2026-01-01-missing", "--data-dir", str(tmp_path))

    assert result.returncode == 1
    assert "was not found" in result.stderr


def test_kb_review_lists_bounded_items(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    (tmp_path / "review" / "pending-classification.md").write_text(
        "# Pending Classification\n\n"
        "## item: classification-a\n\n"
        "Reason: low confidence\n\n"
        "- title: Candidate A\n\n"
        "## item: classification-b\n\n"
        "Reason: unknown topic\n\n"
        "- title: Candidate B\n\n",
        encoding="utf-8",
    )
    (tmp_path / "review" / "pending-merge.md").write_text(
        "# Pending Merge\n\n"
        "## item: merge-a\n\n"
        "- candidate_title: Merge Candidate\n\n",
        encoding="utf-8",
    )
    (tmp_path / "review" / "disputes.md").write_text(
        "# Disputes\n\n"
        "## item: dispute-a\n\n"
        "- candidate_title: Disputed Candidate\n\n",
        encoding="utf-8",
    )
    (tmp_path / ".kb" / "ingested.json").write_text(
        "[\n"
        "  {\n"
        "    \"hash\": \"abc\",\n"
        "    \"source_name\": \"dup.md\",\n"
        "    \"status\": \"duplicate\",\n"
        "    \"processed_at\": \"2026-05-05T09:00:00\"\n"
        "  }\n"
        "]\n",
        encoding="utf-8",
    )
    (tmp_path / ".kb" / "errors.log").write_text(
        "2026-05-05T09:00:01 Unsupported ingest file extension for /tmp/example.pdf\n",
        encoding="utf-8",
    )
    config = default_config(tmp_path)
    config["review"]["max_review_items_per_run"] = 3
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    result = run_cli("review", "--data-dir", str(tmp_path))
    list_result = run_cli("review", "list", "--data-dir", str(tmp_path))

    assert result.returncode == 0
    assert "Review items: 6" in result.stdout
    assert "Showing up to 3 items:" in result.stdout
    assert result.stdout.count("\n- [") == 3
    assert list_result.returncode == 0
    assert list_result.stdout == result.stdout


def test_kb_context_requires_positive_budget(tmp_path):
    run_cli("init", "--data-dir", str(tmp_path))
    result = run_cli("context", "agent context task", "--budget", "0", "--data-dir", str(tmp_path))

    assert result.returncode == 1
    assert "Context budget must be a positive integer." in result.stderr
