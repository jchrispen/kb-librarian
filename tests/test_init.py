from __future__ import annotations

import json

import yaml

from kb_librarian.config import default_config, write_config_file
from kb_librarian.init import initialize_data_dir
from kb_librarian.paths import DIRECTORIES, LOG_FILES, REVIEW_QUEUE_FILES, ROOT_FILES, STATE_FILES


def test_initialize_data_dir_creates_phase_1_layout(tmp_path):
    created = initialize_data_dir(tmp_path)

    assert created
    for directory in DIRECTORIES:
        assert (tmp_path / directory).is_dir()
    for filename in ROOT_FILES:
        assert (tmp_path / filename).is_file()
    for filename in REVIEW_QUEUE_FILES:
        assert (tmp_path / filename).is_file()
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
