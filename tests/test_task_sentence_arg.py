"""A multi-word sentence ending in a path-like token is a literal task.

Both decision sites are executed, not read: `resolve_task_argument` in the
orchestrator and the launcher's fail-fast "task file not found" block. Only a
whitespace-free path with a task-file suffix, or an existing file, may take
the file-or-die branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator import leerie

REPO = Path(__file__).resolve().parent.parent
SENTENCE = ("we need to fix the critical security issue reported in "
            "./knox_report.txt")


def _launcher_block() -> str:
    src = (REPO / "leerie").read_text()
    start = src.index("# --- fail fast on missing task file")
    end = src.index("esac", start) + len("esac")
    # remote_log is stubbed to stdout; `exit 1` is the fail signal.
    return "remote_log() { echo \"$*\"; }\n" + src[start:end]


def _run_launcher_block(task_arg: str, cwd: Path) -> subprocess.CompletedProcess:
    script = f"LEERIE_TASK_ARG=$1\n{_launcher_block()}\n"
    return subprocess.run(["bash", "-c", script, "bash", task_arg], cwd=cwd,
                          capture_output=True, text=True)


@pytest.mark.parametrize("suffix", [".txt", ".md", ".TXT"])
def test_orchestrator_sentence_is_literal(tmp_path, monkeypatch, suffix):
    monkeypatch.chdir(tmp_path)
    sentence = SENTENCE.replace(".txt", f"{suffix}")
    assert leerie.resolve_task_argument(sentence) == sentence


def test_orchestrator_sentence_with_existing_trailing_file_is_literal(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "knox_report.txt").write_text("file contents")
    assert leerie.resolve_task_argument(SENTENCE) == SENTENCE


def test_orchestrator_single_missing_token_dies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument("./missing.txt")


def test_orchestrator_existing_file_is_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.txt").write_text("  do the thing\n")
    assert leerie.resolve_task_argument("t.txt") == "do the thing"


@pytest.mark.parametrize("suffix", [".txt", ".md", ".TXT"])
def test_launcher_sentence_does_not_fail_fast(tmp_path, suffix):
    r = _run_launcher_block(SENTENCE.replace(".txt", suffix), tmp_path)
    assert r.returncode == 0
    assert "task file not found" not in r.stdout


def test_launcher_single_missing_token_fails(tmp_path):
    r = _run_launcher_block("./missing.txt", tmp_path)
    assert r.returncode == 1
    assert "task file not found" in r.stdout


def test_launcher_existing_file_passes(tmp_path):
    (tmp_path / "t.txt").write_text("x")
    r = _run_launcher_block("t.txt", tmp_path)
    assert r.returncode == 0


@pytest.mark.parametrize("name", ["./missing.Md", "./missing.Txt", "./missing.MD"])
def test_launcher_mixed_case_missing_token_fails(tmp_path, name):
    r = _run_launcher_block(name, tmp_path)
    assert r.returncode == 1
    assert "task file not found" in r.stdout
