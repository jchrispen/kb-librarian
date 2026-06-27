from __future__ import annotations

import json

import yaml

from kb_librarian.config import default_config, write_config_file
from kb_librarian.init import (
    HOOK_TEMPLATES,
    LEGACY_PREAMBLE_CONTENT,
    initialize_data_dir,
    render_preamble,
)
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


def test_initialize_default_data_dir_writes_config_next_to_library(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    library_dir = tmp_path / ".kb" / ".library"

    created = initialize_data_dir(library_dir)

    assert (tmp_path / ".kb" / ".library").is_dir()
    assert (tmp_path / ".kb" / "config.yaml") in created
    assert (tmp_path / ".kb" / "config.yaml").is_file()
    assert not (tmp_path / ".kb" / ".library" / ".kb" / "config.yaml").exists()
    config = yaml.safe_load((tmp_path / ".kb" / "config.yaml").read_text(encoding="utf-8"))
    assert config["data_dir"] == str(library_dir)


def test_initialize_default_control_dir_redirects_to_library(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    created = initialize_data_dir(tmp_path / ".kb")

    assert (tmp_path / ".kb" / ".library" / "INDEX.md").is_file()
    assert (tmp_path / ".kb" / ".library" / "topics").is_dir()
    assert not (tmp_path / ".kb" / "INDEX.md").exists()
    config = yaml.safe_load((tmp_path / ".kb" / "config.yaml").read_text(encoding="utf-8"))
    assert config["data_dir"] == str(tmp_path / ".kb" / ".library")
    assert tmp_path / ".kb" / "config.yaml" in created


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

    assert created
    assert preamble_path.read_text(encoding="utf-8") == user_preamble
    for rel_path in HOOK_TEMPLATES:
        assert (tmp_path / rel_path) in created


def test_initialize_data_dir_hooks_generates_templates(tmp_path):
    initialize_data_dir(tmp_path, hooks=True)
    for rel_path in HOOK_TEMPLATES:
        assert (tmp_path / rel_path).is_file()

    session_start = (tmp_path / ".kb" / "hooks" / "session-start.sh").read_text(encoding="utf-8")
    assert str(tmp_path) in session_start
    assert "kb doctor --data-dir" in session_start
    assert "kb context" in session_start

    cron = (tmp_path / ".kb" / "hooks" / "cron.template").read_text(encoding="utf-8")
    assert "kb ingest --data-dir" in cron
    assert "kb reindex --scan-clusters --data-dir" in cron
    assert "kb doctor --data-dir" in cron


def test_initialize_data_dir_hooks_templates_are_idempotent(tmp_path):
    initialize_data_dir(tmp_path, hooks=True)
    hooks_dir = tmp_path / ".kb" / "hooks"
    before = {path.name: path.read_text(encoding="utf-8") for path in hooks_dir.iterdir()}

    created = initialize_data_dir(tmp_path, hooks=True)
    after = {path.name: path.read_text(encoding="utf-8") for path in hooks_dir.iterdir()}

    assert created == []
    assert before == after


def test_initialize_data_dir_hooks_updates_stale_hook_template(tmp_path):
    initialize_data_dir(tmp_path, hooks=True)
    hook_path = tmp_path / ".kb" / "hooks" / "session-start.sh"
    hook_path.write_text("# outdated content\n", encoding="utf-8")

    created = initialize_data_dir(tmp_path, hooks=True)

    assert hook_path in created
    assert "outdated content" not in hook_path.read_text(encoding="utf-8")


def test_managed_preamble_detected_by_prefix_and_tail(tmp_path):
    # A preamble from a different data_dir path has the same managed prefix/tail
    # and should be recognised as managed and updated.
    path_a = tmp_path / "a"
    path_b = tmp_path / "b"
    initialize_data_dir(path_a)

    preamble_from_a = (path_a / "PREAMBLE.md").read_text(encoding="utf-8")
    path_b.mkdir(parents=True)
    (path_b / "PREAMBLE.md").write_text(preamble_from_a, encoding="utf-8")

    created = initialize_data_dir(path_b, hooks=True)

    preamble_b = path_b / "PREAMBLE.md"
    assert preamble_b in created
    assert str(path_b) in preamble_b.read_text(encoding="utf-8")
