"""Filesystem paths used by KB Librarian."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DATA_DIR = Path("/mnt/c/workspace/source/internal/kb")

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
    "review/disputes.md",
)

STATE_FILES = {
    ".kb/ingested.json": [],
    ".kb/backlinks.json": {},
    ".kb/stats.json": {"notes": 0, "topics": 0},
    ".kb/state.json": {"version": 1},
    ".kb/index-manifest.json": {"version": 1, "indexes": {}},
}

LOG_FILES = (".kb/errors.log",)


def config_path(data_dir: Path) -> Path:
    """Return the path to the YAML config for a data directory."""

    return data_dir / KB_DIR_NAME / "config.yaml"
