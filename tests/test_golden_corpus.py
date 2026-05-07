from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from kb_librarian.config import default_config
from kb_librarian.context import build_context, build_explore
from kb_librarian.ingest import ingest
from kb_librarian.init import initialize_data_dir
from kb_librarian.search_index import query_candidates, score_document, tokenize_query
from kb_librarian.storage import load_note_records


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "golden_corpus"
RAW_FIXTURE_DIR = FIXTURE_ROOT / "raw"
EXPECTATIONS_PATH = FIXTURE_ROOT / "expectations.json"


class _FixedDate(date):
    @classmethod
    def today(cls) -> "_FixedDate":
        return cls(2026, 5, 7)


def _mock_provider_config(data_dir: Path) -> dict[str, object]:
    config = default_config(data_dir)
    config["providers"]["mock"] = {}
    for operation in ("extract", "classify", "integrate", "synthesize"):
        config["operations"][operation] = {"provider": "mock", "model": f"mock-{operation}"}
    config["providers"]["retry"] = {
        "max_attempts": 3,
        "base_delay_seconds": 0.0,
        "max_delay_seconds": 0.0,
        "jitter_seconds": 0.0,
    }
    return config


def _copy_raw_fixtures(data_dir: Path) -> None:
    destination = data_dir / "raw"
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(RAW_FIXTURE_DIR.iterdir()):
        if path.is_file():
            shutil.copy2(path, destination / path.name)


def _search_top_note_id(data_dir: Path, *, config: dict[str, object], query: str) -> str | None:
    fts_path = data_dir / ".kb" / "fts.sqlite"
    candidates = query_candidates(fts_path, query=query, topic=None, knowledge_type=None)
    retrieval = config["retrieval"]
    tokens = tokenize_query(query)
    scored = [
        (str(item.get("id", "")), score_document(item, query=query, tokens=tokens, weights=retrieval))
        for item in candidates
    ]
    scored = [item for item in scored if item[0] and item[1] > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[0][0]


def test_golden_corpus_mock_provider_harness_is_deterministic(tmp_path, monkeypatch):
    initialize_data_dir(tmp_path)
    config = _mock_provider_config(tmp_path)
    expectations = json.loads(EXPECTATIONS_PATH.read_text(encoding="utf-8"))
    _copy_raw_fixtures(tmp_path)

    monkeypatch.setattr("kb_librarian.ingest.date", _FixedDate)

    report = ingest(tmp_path, config=config)

    assert report.errors == 0
    assert report.processed_files == expectations["expected_note_count"]
    records = load_note_records(tmp_path, validate=True)
    by_id = {record.note_id: record for record in records}
    assert sorted(by_id) == sorted(expectations["expected_knowledge_types"])

    for note_id, expected_type in expectations["expected_knowledge_types"].items():
        assert by_id[note_id].note.frontmatter["knowledge_type"] == expected_type

    for note_id, expected_phrase in expectations["expected_title_phrases"].items():
        phrases = by_id[note_id].note.frontmatter.get("retrieval_phrases", [])
        assert isinstance(phrases, list)
        assert expected_phrase in phrases

    for item in expectations["search_expectations"]:
        top = _search_top_note_id(tmp_path, config=config, query=item["query"])
        assert top == item["top_note_id"]

    context = build_context(
        tmp_path,
        config=config,
        task="reduce token burn while keeping citation grounding for agents",
        mode="coding",
        budget=1100,
    )
    assert context.citations
    assert all(entry["note_id"] in by_id for entry in context.citations)
    assert "## Directly relevant techniques" in context.synthesis_markdown
    selected_token_budget = sum(
        len(by_id[item.note_id].note.body.split()) for item in context.selected_notes if item.note_id in by_id
    )
    synthesis_tokens = len(context.synthesis_markdown.split())
    assert synthesis_tokens < selected_token_budget

    explore = build_explore(
        tmp_path,
        config=config,
        problem="find adjacent ways to preserve grounding with lower context cost",
        budget=1500,
    )
    assert explore.citations
    assert "## Adjacent patterns" in explore.synthesis_markdown
    assert any(f"[{entry['note_id']}]" in explore.synthesis_markdown for entry in explore.citations)


@pytest.mark.skipif(
    os.getenv("KB_GOLDEN_LIVE") != "1" or not os.getenv("ANTHROPIC_API_KEY"),
    reason="Set KB_GOLDEN_LIVE=1 and ANTHROPIC_API_KEY to run live-provider golden checks.",
)
def test_golden_corpus_live_provider_opt_in_smoke(tmp_path):
    initialize_data_dir(tmp_path)
    config = default_config(tmp_path)
    config["providers"]["retry"] = {
        "max_attempts": 4,
        "base_delay_seconds": 0.2,
        "max_delay_seconds": 2.0,
        "jitter_seconds": 0.05,
    }

    source = RAW_FIXTURE_DIR / "01-agent-context-pattern.md"
    shutil.copy2(source, tmp_path / "raw" / source.name)

    report = ingest(tmp_path, config=config, env=os.environ)
    assert report.errors == 0
    assert report.processed_files == 1
    assert report.created_notes

    context = build_context(
        tmp_path,
        config=config,
        task="apply task-shaped context in coding work",
        mode="coding",
        budget=900,
        env=os.environ,
    )
    assert context.selected_notes
    assert context.citations
    assert context.synthesis_markdown
