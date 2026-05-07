"""Filesystem paths used by KB Librarian."""

from __future__ import annotations

from pathlib import Path

DEFAULT_ROOT = Path.home()

DEFAULT_DATA_DIR = Path(".kb") / ".library"

KB_DIR_NAME = ".kb"

ROOT_FILES = ("INDEX.md", "PREAMBLE.md")

DIRECTORIES = (
    "topics",
    "raw",
    "raw/processed",
    "review",
    KB_DIR_NAME,
)

REVIEW_QUEUE_FILES = (
    "review/pending-classification.md",
    "review/pending-merge.md",
    "review/pending-compaction.md",
    "review/pending-topic.md",
    "review/disputes.md",
    "review/stale.md",
    "review/orphans.md",
    "review/search-misses.md",
    "review/low-utility.md",
)

REVIEW_STATE_FILE = "review/review-items.json"

STATE_FILES = {
    ".kb/ingested.json": [],
    ".kb/backlinks.json": {},
    ".kb/stats.json": {"notes": 0, "topics": 0},
    ".kb/state.json": {"version": 1},
    ".kb/index-manifest.json": {"version": 1, "indexes": {}},
}

LOG_FILES = (".kb/errors.log", ".kb/usage.log", ".kb/search-misses.log")


def config_path(data_dir: Path) -> Path:
    """Return the path to the YAML config for a data directory."""

    return data_dir / KB_DIR_NAME / "config.yaml"


def default_data_dir(default_root: str | Path = DEFAULT_ROOT) -> Path:
    """Return the default library directory under the default config root."""

    return Path(default_root).expanduser() / DEFAULT_DATA_DIR


def default_config_path(default_root: str | Path = DEFAULT_ROOT) -> Path:
    """Return the default config file path outside the default library."""

    return Path(default_root).expanduser() / KB_DIR_NAME / "config.yaml"


def is_control_dir(path: str | Path) -> bool:
    """Return true when a path is a .kb control directory."""

    return Path(path).expanduser().name == KB_DIR_NAME


def is_library_dir_next_to_control_dir(path: str | Path) -> bool:
    """Return true when a path is a .library directory under a .kb control directory."""

    candidate = Path(path).expanduser()
    return candidate.name == ".library" and candidate.parent.name == KB_DIR_NAME
