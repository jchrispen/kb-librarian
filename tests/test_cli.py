from __future__ import annotations

import json
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
    assert (tmp_path / ".kb" / "usage.log").is_file()
    assert (tmp_path / ".kb" / "search-misses.log").is_file()
    assert (tmp_path / "review" / "pending-merge.md").is_file()
    assert second.returncode == 0
    assert "Already initialized" in second.stdout


def test_kb_init_hooks_installs_agent_preamble_guidance(tmp_path):
    result = run_cli("init", "--hooks", "--data-dir", str(tmp_path))
    second = run_cli("init", "--hooks", "--data-dir", str(tmp_path))

    preamble = (tmp_path / "PREAMBLE.md").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert "Agent preamble installed at" in result.stdout
    assert "Include that file in agent session instructions" in result.stdout
    assert "kb context" in preamble
    assert "kb explore" in preamble
    assert second.returncode == 0
    assert "Agent preamble installed at" in second.stdout


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

    cited_search = run_cli("search", "CLI contract", "--with-citations", "--data-dir", str(tmp_path))
    assert cited_search.returncode == 0
    assert "## Source notes" in cited_search.stdout
    assert "**KB sources:**" in cited_search.stdout
    assert "title:" in cited_search.stdout

    json_search = run_cli("search", "CLI contract", "--json", "--data-dir", str(tmp_path))
    assert json_search.returncode == 0
    assert "\"citation\":" in json_search.stdout
    assert "\"note_id\":" in json_search.stdout

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


def test_kb_reindex_scan_clusters_and_compact_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    config = default_config(tmp_path)
    config["providers"]["mock"] = {}
    config["operations"]["compact"] = {"provider": "mock", "model": "mock-compact"}
    config["review"]["duplicate_cluster_threshold"] = 2
    write_config_file(tmp_path / ".kb" / "config.yaml", config)

    for suffix in ("a", "b"):
        source = tmp_path / f"agent-context-{suffix}.md"
        source.write_text(
            "# Agent context retrieval\n\n"
            "Prefer compact task-shaped context before loading full notes. "
            "Cite source note IDs in agent responses.\n",
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
            str(source),
        )
        assert add_result.returncode == 0

    scan_result = run_cli("reindex", "--scan-clusters", "--data-dir", str(tmp_path))
    assert scan_result.returncode == 0
    assert "Compaction scan: 1 cluster(s), 1 new review item(s)." in scan_result.stdout

    rendered = (tmp_path / "review" / "pending-compaction.md").read_text(encoding="utf-8")
    match = re.search(r"cluster_id: (cluster-[a-f0-9]+)", rendered)
    assert match is not None
    cluster_id = match.group(1)

    compact_result = run_cli("compact", cluster_id, "--data-dir", str(tmp_path))
    assert compact_result.returncode == 0
    assert "Created compaction proposal" in compact_result.stdout
    assert "diff_summary:" in compact_result.stdout

    review_result = run_cli("review", "--data-dir", str(tmp_path))
    assert review_result.returncode == 0
    assert "compaction: 2" in review_result.stdout


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
    assert "created_note_ids:" in result.stdout
    assert "errors: 0" in result.stdout
    assert not source.exists()
    assert list((tmp_path / "topics" / "agent-systems").glob("*.md"))

    json_source = tmp_path / "raw" / "json-agent-context.md"
    json_source.write_text(
        "# JSON context retrieval\n\nPrefer cited JSON reports for coding agents.\n",
        encoding="utf-8",
    )
    json_result = run_cli("ingest", "--json", "--data-dir", str(tmp_path))
    payload = json.loads(json_result.stdout)

    assert json_result.returncode == 0
    assert payload["processed_files"] == 1
    assert payload["created_notes"]["count"] == 1
    assert payload["created_notes"]["note_ids"]
    assert payload["errors"] == 0


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
    assert "**KB sources:**" in result.stdout
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
    assert "\"title\":" in json_result.stdout


def test_kb_explore_mock_provider_smoke(tmp_path):
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
        "explore",
        "agent retrieval alternatives",
        "--budget",
        "900",
        "--data-dir",
        str(tmp_path),
    )
    assert result.returncode == 0
    assert "# Exploration" in result.stdout
    assert "## Adjacent patterns" in result.stdout
    assert "## Source notes" in result.stdout
    assert "**KB sources:**" in result.stdout
    assert note_id in result.stdout

    json_result = run_cli(
        "explore",
        "agent retrieval alternatives",
        "--budget",
        "900",
        "--json",
        "--data-dir",
        str(tmp_path),
    )
    assert json_result.returncode == 0
    assert "\"selected_notes\":" in json_result.stdout
    assert "\"citations\":" in json_result.stdout
    assert "\"problem\":" in json_result.stdout


def test_kb_log_use_and_usage_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    source = tmp_path / "seed.md"
    source.write_text(
        "# CLI usage logging\n\nPrefer logging note usage after citing a source note.\n",
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
    match = NOTE_ID_PATTERN.search(add_result.stdout)
    assert match is not None
    note_id = match.group(1)

    search_result = run_cli("search", "usage logging", "--data-dir", str(tmp_path))
    assert search_result.returncode == 0
    assert note_id in search_result.stdout

    log_use_result = run_cli(
        "log-use",
        note_id,
        "--task",
        "cited in a CLI smoke test",
        "--data-dir",
        str(tmp_path),
    )
    assert log_use_result.returncode == 0
    assert f"Logged use of note {note_id}." in log_use_result.stdout

    usage_result = run_cli("usage", "--since", "7d", "--data-dir", str(tmp_path))
    assert usage_result.returncode == 0
    assert "Usage summary" in usage_result.stdout
    assert "retrievals: 1" in usage_result.stdout
    assert "logged uses: 1" in usage_result.stdout
    assert note_id in usage_result.stdout

    note_usage_result = run_cli("usage", "--note", note_id, "--data-dir", str(tmp_path))
    assert note_usage_result.returncode == 0
    assert f"note: {note_id}" in note_usage_result.stdout

    events = [
        json.loads(line)
        for line in (tmp_path / ".kb" / "usage.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["retrieval", "note-use"]
    assert events[0]["command"] == "search"
    assert events[0]["returned_note_ids"] == [note_id]
    assert "Prefer logging note usage" not in json.dumps(events)


def test_kb_search_misses_promote_to_review_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    for _ in range(3):
        result = run_cli("search", "quantum gardening", "--data-dir", str(tmp_path))
        assert result.returncode == 0
        assert "No results." in result.stdout

    miss_events = [
        json.loads(line)
        for line in (tmp_path / ".kb" / "search-misses.log").read_text(encoding="utf-8").splitlines()
    ]
    assert len(miss_events) == 3
    assert {event["reason"] for event in miss_events} == {"zero-results"}

    rendered = (tmp_path / "review" / "search-misses.md").read_text(encoding="utf-8")
    assert "quantum gardening" in rendered
    assert "- misses: 3" in rendered

    review_result = run_cli("review", "--data-dir", str(tmp_path))
    assert review_result.returncode == 0
    assert "searchmiss: 1" in review_result.stdout


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
    (tmp_path / "review" / "review-items.json").unlink()
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


def test_kb_review_actionable_workflow_smoke(tmp_path):
    init_result = run_cli("init", "--data-dir", str(tmp_path))
    assert init_result.returncode == 0

    (tmp_path / "review" / "review-items.json").write_text(
        "{\n"
        "  \"version\": 1,\n"
        "  \"items\": [\n"
        "    {\n"
        "      \"id\": \"classification-2026-05-04-001\",\n"
        "      \"queue\": \"classification\",\n"
        "      \"status\": \"pending\",\n"
        "      \"priority\": \"medium\",\n"
        "      \"title\": \"Candidate A\",\n"
        "      \"created\": \"2026-05-04\",\n"
        "      \"updated\": \"2026-05-04\",\n"
        "      \"target_notes\": [],\n"
        "      \"proposed_action\": \"review classification manually\",\n"
        "      \"payload\": {\n"
        "        \"title\": \"Candidate A\",\n"
        "        \"summary\": \"Candidate A summary.\",\n"
        "        \"source\": \"raw/a.md\",\n"
        "        \"source_hash\": \"abc\",\n"
        "        \"fingerprint\": \"fp-a\"\n"
        "      },\n"
        "      \"history\": []\n"
        "    },\n"
        "    {\n"
        "      \"id\": \"merge-2026-05-04-001\",\n"
        "      \"queue\": \"merge\",\n"
        "      \"status\": \"pending\",\n"
        "      \"priority\": \"medium\",\n"
        "      \"title\": \"Merge Candidate\",\n"
        "      \"created\": \"2026-05-04\",\n"
        "      \"updated\": \"2026-05-04\",\n"
        "      \"target_notes\": [],\n"
        "      \"proposed_action\": \"review and merge manually\",\n"
        "      \"payload\": {\n"
        "        \"candidate_title\": \"Merge Candidate\",\n"
        "        \"candidate_body\": \"body\",\n"
        "        \"fingerprint\": \"fp-b\"\n"
        "      },\n"
        "      \"history\": []\n"
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    explain_result = run_cli("review", "explain", "classification-2026-05-04-001", "--data-dir", str(tmp_path))
    assert explain_result.returncode == 0
    assert "Review item: classification-2026-05-04-001" in explain_result.stdout

    defer_result = run_cli(
        "review",
        "defer",
        "classification-2026-05-04-001",
        "--days",
        "30",
        "--data-dir",
        str(tmp_path),
    )
    assert defer_result.returncode == 0
    assert "Deferred until" in defer_result.stdout

    list_after_defer = run_cli("review", "list", "--data-dir", str(tmp_path))
    assert list_after_defer.returncode == 0
    assert "Review items: 1" in list_after_defer.stdout
    assert "merge-2026-05-04-001" in list_after_defer.stdout
    assert "classification-2026-05-04-001" not in list_after_defer.stdout

    accept_result = run_cli(
        "review",
        "accept",
        "classification-2026-05-04-001",
        "--topic",
        "agent-systems",
        "--type",
        "technique",
        "--data-dir",
        str(tmp_path),
    )
    assert accept_result.returncode == 0
    assert "creating note" in accept_result.stdout
    assert list((tmp_path / "topics" / "agent-systems").glob("*.md"))

    reject_result = run_cli("review", "reject", "merge-2026-05-04-001", "--data-dir", str(tmp_path))
    assert reject_result.returncode == 0
    assert "rejected" in reject_result.stdout.lower()

    final_list = run_cli("review", "--data-dir", str(tmp_path))
    assert final_list.returncode == 0
    assert "Review items: 0" in final_list.stdout
    assert (tmp_path / "review" / "rejected" / "merge-2026-05-04-001.md").is_file()


def test_kb_context_requires_positive_budget(tmp_path):
    run_cli("init", "--data-dir", str(tmp_path))
    result = run_cli("context", "agent context task", "--budget", "0", "--data-dir", str(tmp_path))

    assert result.returncode == 1
    assert "Context budget must be a positive integer." in result.stderr

    explore_result = run_cli("explore", "agent context task", "--budget", "0", "--data-dir", str(tmp_path))
    assert explore_result.returncode == 1
    assert "Explore budget must be a positive integer." in explore_result.stderr
