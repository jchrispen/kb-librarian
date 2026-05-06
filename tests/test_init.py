from __future__ import annotations

import json

import yaml

from kb_librarian.config import default_config, write_config_file
from kb_librarian.init import LEGACY_PREAMBLE_CONTENT, initialize_data_dir, render_preamble
from kb_librarian.paths import DIRECTORIES, LOG_FILES, REVIEW_QUEUE_FILES, REVIEW_STATE_FILE, ROOT_FILES, STATE_FILES


def test_initialize_data_dir_creates_phase_1_layout(tmp_path):
    created = initialize_data_dir(tmp_path)

    assert created
    for directory in DIRECTORIES:
        assert (tmp_path / directory).is_dir()
    for filename in ROOT_FILES:
        assert (tmp_path / filename).is_file()
    for filename in REVIEW_QUEUE_FILES:
        assert (tmp_path / filename).is_file()
    assert (tmp_path / REVIEW_STATE_FILE).is_file()
    for filename in STATE_FILES:
        assert (tmp_path / filename).is_file()
    for filename in LOG_FILES:
        assert (tmp_path / filename).is_file()

    config = yaml.safe_load((tmp_path / ".kb" / "config.yaml").read_text(encoding="utf-8"))
    assert config["data_dir"] == str(tmp_path)

    ingested = json.loads((tmp_path / ".kb" / "ingested.json").read_text(encoding="utf-8"))
    assert ingested == []


def test_initialize_data_dir_is_idempotent_and_preserves_user_files(tmp_path):
    initialize_data_dir(tmp_path)
    config_path = tmp_path / ".kb" / "config.yaml"
    config = default_config(tmp_path)
    config["retrieval"]["default_budget_tokens"] = 321
    write_config_file(config_path, config)
    config_before = config_path.read_text(encoding="utf-8")

    review_path = tmp_path / "review" / "pending-classification.md"
    review_path.write_text("# Custom Review\n\nDo not overwrite.\n", encoding="utf-8")
    review_before = review_path.read_text(encoding="utf-8")

    created = initialize_data_dir(tmp_path)

    assert created == []
    assert config_path.read_text(encoding="utf-8") == config_before
    assert review_path.read_text(encoding="utf-8") == review_before


def test_initialize_data_dir_hooks_flag_only_affects_new_config(tmp_path):
    initialize_data_dir(tmp_path, hooks=True)

    config = yaml.safe_load((tmp_path / ".kb" / "config.yaml").read_text(encoding="utf-8"))

    assert config["hooks"]["session_start_ingest"] is True


def test_initialize_data_dir_writes_revised_preamble(tmp_path):
    initialize_data_dir(tmp_path)

    preamble = (tmp_path / "PREAMBLE.md").read_text(encoding="utf-8")

    assert preamble == render_preamble(tmp_path)
    assert "kb context" in preamble
    assert "kb explore" in preamble
    assert str(tmp_path) in preamble


def test_initialize_data_dir_hooks_refreshes_only_managed_preamble(tmp_path):
    initialize_data_dir(tmp_path)
    preamble_path = tmp_path / "PREAMBLE.md"
    preamble_path.write_text(LEGACY_PREAMBLE_CONTENT, encoding="utf-8")

    first = initialize_data_dir(tmp_path, hooks=True)
    second = initialize_data_dir(tmp_path, hooks=True)

    assert preamble_path in first
    assert preamble_path.read_text(encoding="utf-8") == render_preamble(tmp_path)
    assert second == []


def test_initialize_data_dir_hooks_preserves_user_preamble(tmp_path):
    initialize_data_dir(tmp_path)
    preamble_path = tmp_path / "PREAMBLE.md"
    user_preamble = "# Custom Preamble\n\nDo not overwrite.\n"
    preamble_path.write_text(user_preamble, encoding="utf-8")

    created = initialize_data_dir(tmp_path, hooks=True)

    assert created == []
    assert preamble_path.read_text(encoding="utf-8") == user_preamble
