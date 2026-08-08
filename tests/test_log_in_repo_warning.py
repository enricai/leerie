"""The launcher's in-repo tee-log warning (N5).

Operators commonly run `leerie task | tee leerie-<task>.log`, and the log
lands in $USER_REPO by default -- which is bind-mounted whole into every
worker's container at /work. A worker can then `cat`/`grep` its own
orchestration log, including gate vocabulary and internal check names,
defeating judge independence.

`_warn_if_log_in_repo` is extracted verbatim from the launcher (the
`_extract_forwarding_loop` convention in `test_launcher_env_forwarding.py`,
also used by `test_stale_install_warning.py`'s `_extract_guard`) and driven
against a real temp directory standing in for $USER_REPO. No network, no
container.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_guard() -> str:
    """Pull `_warn_if_log_in_repo` verbatim from the launcher so this test
    exercises the real function, not a copy of it."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(_warn_if_log_in_repo\(\) \{.*?\n\}\n)", src, re.DOTALL)
    assert m, "could not locate _warn_if_log_in_repo in the launcher"
    return m.group(1)


_HARNESS = r"""#!/usr/bin/env bash
set -euo pipefail

remote_log() { printf '[leerie] %s\n' "$*" >&2; }

USER_REPO="__REPO__"

__GUARD__

_warn_if_log_in_repo || true
"""


def _run(repo: Path):
    script = repo.parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__GUARD__", _extract_guard())
        .replace("__REPO__", str(repo))
    )
    return subprocess.run(["bash", str(script)], capture_output=True,
                          text=True, timeout=30)


def test_warns_when_a_tee_log_sits_in_the_repo_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "leerie-fix-the-thing.log").write_text("some orchestration output\n")

    r = _run(repo)

    assert r.returncode == 0
    assert "leerie-fix-the-thing.log" in r.stderr
    assert "bind-mounted" in r.stderr


def test_silent_when_no_such_file_exists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello\n")

    r = _run(repo)

    assert r.returncode == 0
    assert r.stderr == ""


def test_matches_multiple_candidate_logs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "leerie-a.log").write_text("a\n")
    (repo / "leerie-b.log").write_text("b\n")

    r = _run(repo)

    assert "leerie-a.log" in r.stderr
    assert "leerie-b.log" in r.stderr


def test_ignores_files_not_matching_the_glob(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "not-a-log.txt").write_text("x\n")
    (repo / "leerie.log").write_text("x\n")  # missing the required '-' separator

    r = _run(repo)

    assert r.returncode == 0
    assert r.stderr == ""
