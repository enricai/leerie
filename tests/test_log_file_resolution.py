"""The launcher's --log-file / LEERIE_LOG_FILE / leerie.toml log_file
resolution (N5b, docs/IMPLEMENTATION.md "--log-file / LEERIE_LOG_FILE
(N5b)").

N5 (PR #188) added a startup warning for a tee log left inside $USER_REPO
-- bind-mounted whole into every worker container, letting a worker read
its own orchestration log and defeat judge independence. N5b is the
promised follow-up: a --log-file knob, resolved with the same CLI > env >
leerie.toml precedence every other leerie knob uses, whose *default* lands
outside the repo -- specifically under LEERIE_STATE_HOST_DIR, settling N5's
own stated residual over whether "outside the repo" should mean the state
dir specifically.

Resolution-only in this subtask: no I/O/teeing wiring is exercised here,
only that LEERIE_LOG_FILE_RESOLVED comes out right for a given
CLI/env/toml/none combination.

The resolution block is extracted verbatim from the launcher (the
`_extract_forwarding_loop` convention in `test_launcher_env_forwarding.py`,
also used by `test_stale_install_warning.py`'s `_extract_guard` and
`test_log_in_repo_warning.py`'s `_extract_guard`) and driven against a real
temp directory standing in for $USER_REPO. No network, no container.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_resolver() -> str:
    """Pull the --log-file resolution block verbatim from the launcher."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(# --- --log-file / LEERIE_LOG_FILE / leerie\.toml log_file "
        r"\(N5b\) -----------\n.*?\nexport LEERIE_LOG_FILE_RESOLVED\n)",
        src, re.DOTALL)
    assert m, "could not locate the --log-file resolution block in the launcher"
    return m.group(1)


def _value_flags_line() -> str:
    src = LAUNCHER.read_text()
    m = re.search(r"_value_flags=\"[^\"]*\"", src, re.DOTALL)
    assert m, "could not locate _value_flags in the launcher"
    return m.group(0)


_HARNESS = r"""#!/usr/bin/env bash
set -euo pipefail

USER_REPO="__REPO__"
LEERIE_STATE_HOST_DIR="__STATE__"

__RESOLVER__

printf '%s\n' "$LEERIE_LOG_FILE_RESOLVED"
"""


def _run(repo: Path, state_dir: Path, args, env=None):
    script = repo.parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__RESOLVER__", _extract_resolver())
        .replace("__REPO__", str(repo))
        .replace("__STATE__", str(state_dir))
    )
    merged_env = os.environ.copy()
    merged_env.update(env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True, text=True, timeout=30, env=merged_env,
    )


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return repo, state_dir


def test_default_lands_under_state_dir_not_repo(tmp_path):
    repo, state_dir = _setup(tmp_path)
    result = _run(repo, state_dir, [])
    assert result.returncode == 0, result.stderr
    resolved = result.stdout.strip()
    assert resolved.startswith(str(state_dir) + "/logs/leerie-"), resolved
    assert resolved.endswith(".log")
    assert str(repo) not in resolved


def test_toml_wins_over_default(tmp_path):
    repo, state_dir = _setup(tmp_path)
    (repo / "leerie.toml").write_text('log_file = "/tmp/from-toml.log"\n')
    result = _run(repo, state_dir, [])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp/from-toml.log"


def test_env_wins_over_toml(tmp_path):
    repo, state_dir = _setup(tmp_path)
    (repo / "leerie.toml").write_text('log_file = "/tmp/from-toml.log"\n')
    result = _run(repo, state_dir, [], env={"LEERIE_LOG_FILE": "/tmp/from-env.log"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp/from-env.log"


def test_cli_wins_over_env_and_toml(tmp_path):
    repo, state_dir = _setup(tmp_path)
    (repo / "leerie.toml").write_text('log_file = "/tmp/from-toml.log"\n')
    result = _run(
        repo, state_dir, ["--log-file", "/tmp/from-cli.log"],
        env={"LEERIE_LOG_FILE": "/tmp/from-env.log"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp/from-cli.log"


def test_cli_equals_form(tmp_path):
    repo, state_dir = _setup(tmp_path)
    result = _run(repo, state_dir, ["--log-file=/tmp/eq-form.log"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "/tmp/eq-form.log"


def test_precedence_is_falsified_by_reverting_the_order(tmp_path):
    """The positive falsifier: swapping which tier is consulted first must
    change the outcome, proving the precedence chain is load-bearing rather
    than accidentally satisfied by every tier agreeing."""
    repo, state_dir = _setup(tmp_path)
    (repo / "leerie.toml").write_text('log_file = "/tmp/from-toml.log"\n')
    result = _run(
        repo, state_dir, ["--log-file", "/tmp/from-cli.log"],
        env={"LEERIE_LOG_FILE": "/tmp/from-env.log"},
    )
    resolved = result.stdout.strip()
    assert resolved == "/tmp/from-cli.log"
    assert resolved != "/tmp/from-env.log"
    assert resolved != "/tmp/from-toml.log"


def test_log_file_registered_in_value_flags():
    """--log-file must be in the launcher's _value_flags list so the
    task-argument-extraction loop skips its value rather than mistaking it
    for the task positional (leerie:3865-3872)."""
    line = _value_flags_line()
    assert "--log-file" in line
