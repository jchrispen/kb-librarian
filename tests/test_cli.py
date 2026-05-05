from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "kb_librarian.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_kb_help_smoke():
    result = run_cli("--help")

    assert result.returncode == 0
    assert "init" in result.stdout
    assert "context" in result.stdout


def test_kb_init_smoke_and_idempotency(tmp_path):
    first = run_cli("init", "--data-dir", str(tmp_path))
    second = run_cli("init", "--data-dir", str(tmp_path))

    assert first.returncode == 0
    assert "Initialized KB at" in first.stdout
    assert (tmp_path / ".kb" / "config.yaml").is_file()
    assert (tmp_path / "review" / "pending-merge.md").is_file()
    assert second.returncode == 0
    assert "Already initialized" in second.stdout


def test_placeholder_command_returns_nonzero_with_clear_error():
    result = run_cli("search", "agent context")

    assert result.returncode == 1
    assert "kb search is not implemented in Phase 01a" in result.stderr
