"""Tests for the --log-file / LEERIE_LOG_FILE / leerie.toml `log_file`
resolution block in the launcher (N5b).

The resolution logic lives in the bash launcher (`leerie`), extracted
verbatim via regex (mirroring `_extract_guard()` in
tests/test_log_in_repo_warning.py and the LEERIE_STATE_HOST_DIR block
extraction in tests/test_resolve_state_dir.py) into a minimal harness that
echoes the resolved LEERIE_LOG_FILE_RESOLVED value.

Precedence (lowest → highest):
  default ($LEERIE_STATE_HOST_DIR/logs/leerie-<pid>.log)
  → leerie.toml `log_file = ...`
  → LEERIE_LOG_FILE env var
  → --log-file CLI flag
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_block() -> str:
    """Pull the --log-file resolution block verbatim from the launcher, from
    its section header comment through the final `export
    LEERIE_LOG_FILE_RESOLVED` line."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(# --- --log-file / LEERIE_LOG_FILE / leerie\.toml log_file.*?"
        r"\nexport LEERIE_LOG_FILE_RESOLVED\n)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate the --log-file resolution block in leerie"
    return m.group(1)


_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
USER_REPO="$1"
HOME="$2"
LEERIE_STATE_HOST_DIR="$3"
export HOME LEERIE_STATE_HOST_DIR
shift 3   # remaining args are simulated CLI

__BLOCK__

echo "$LEERIE_LOG_FILE_RESOLVED"
"""


def _run(
    user_repo: Path,
    fake_home: Path,
    state_dir: Path,
    env: dict,
    cli_args: list[str],
) -> str:
    script = _HARNESS.replace("__BLOCK__", _extract_block())
    result = subprocess.run(
        ["bash", "-c", script, "--", str(user_repo), str(fake_home), str(state_dir)]
        + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── default resolution ────────────────────────────────────────────────────────


def test_default_is_under_state_dir(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out.startswith(str(state_dir) + "/logs/leerie-")
    assert out.endswith(".log")


def test_default_is_outside_the_repo(tmp_path):
    """N5's residual: the default must not land inside $USER_REPO, since
    the repo is bind-mounted whole into every worker container."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert not out.startswith(str(user_repo))


def test_default_key_is_stable_modulo_pid(tmp_path):
    """The default filename embeds $$ (the harness's own subshell pid), so
    it is not byte-identical across invocations -- but it must always
    resolve under the state dir's logs/ subdirectory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out1 = _run(user_repo, fake_home, state_dir, {}, [])
    out2 = _run(user_repo, fake_home, state_dir, {}, [])
    assert out1.startswith(str(state_dir) + "/logs/leerie-")
    assert out2.startswith(str(state_dir) + "/logs/leerie-")


# ── leerie.toml `log_file` override ──────────────────────────────────────────


def test_toml_log_file_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    toml_log = tmp_path / "custom.log"
    (user_repo / "leerie.toml").write_text(f"log_file = {toml_log}\n")
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out == str(toml_log)


def test_toml_log_file_quoted_value(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    toml_log = str(tmp_path / "quoted.log")
    (user_repo / "leerie.toml").write_text(f'log_file = "{toml_log}"\n')
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out == toml_log


def test_toml_log_file_tilde_expansion(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    (user_repo / "leerie.toml").write_text("log_file = ~/mylog.log\n")
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out == str(fake_home) + "/mylog.log"


def test_toml_unrelated_key_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    (user_repo / "leerie.toml").write_text("runtime = local\n")
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out.startswith(str(state_dir) + "/logs/leerie-")


# ── LEERIE_LOG_FILE env override ──────────────────────────────────────────────


def test_env_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    env_log = str(tmp_path / "env.log")
    out = _run(user_repo, fake_home, state_dir, {"LEERIE_LOG_FILE": env_log}, [])
    assert out == env_log


def test_env_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    toml_log = str(tmp_path / "toml.log")
    env_log = str(tmp_path / "env.log")
    (user_repo / "leerie.toml").write_text(f"log_file = {toml_log}\n")
    out = _run(user_repo, fake_home, state_dir, {"LEERIE_LOG_FILE": env_log}, [])
    assert out == env_log


def test_env_empty_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out = _run(user_repo, fake_home, state_dir, {"LEERIE_LOG_FILE": ""}, [])
    assert out.startswith(str(state_dir) + "/logs/leerie-")


# ── CLI --log-file override ───────────────────────────────────────────────────


def test_cli_equals_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    cli_log = str(tmp_path / "cli.log")
    out = _run(user_repo, fake_home, state_dir, {}, [f"--log-file={cli_log}"])
    assert out == cli_log


def test_cli_space_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    cli_log = str(tmp_path / "cli.log")
    out = _run(user_repo, fake_home, state_dir, {}, ["--log-file", cli_log])
    assert out == cli_log


def test_cli_overrides_env(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    env_log = str(tmp_path / "env.log")
    cli_log = str(tmp_path / "cli.log")
    out = _run(
        user_repo, fake_home, state_dir,
        {"LEERIE_LOG_FILE": env_log}, [f"--log-file={cli_log}"],
    )
    assert out == cli_log


def test_cli_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    toml_log = str(tmp_path / "toml.log")
    cli_log = str(tmp_path / "cli.log")
    (user_repo / "leerie.toml").write_text(f"log_file = {toml_log}\n")
    out = _run(user_repo, fake_home, state_dir, {}, [f"--log-file={cli_log}"])
    assert out == cli_log


def test_cli_tilde_expansion(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out = _run(user_repo, fake_home, state_dir, {}, ["--log-file", "~/clilog.log"])
    assert out == str(fake_home) + "/clilog.log"


def test_env_tilde_expansion(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    out = _run(user_repo, fake_home, state_dir, {"LEERIE_LOG_FILE": "~/envlog.log"}, [])
    assert out == str(fake_home) + "/envlog.log"


# ── precedence summary ────────────────────────────────────────────────────────


def test_precedence_cli_beats_env_beats_toml_beats_default(tmp_path):
    """Full precedence ladder: CLI wins over env wins over toml wins over
    default. Each tier is independently falsifiable -- remove the CLI
    parse and the first assertion fails, remove the env check and the
    second fails, remove the toml parse and the third fails."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    state_dir = tmp_path / "home" / ".leerie" / "myproject"
    toml_log = str(tmp_path / "toml.log")
    env_log = str(tmp_path / "env.log")
    cli_log = str(tmp_path / "cli.log")
    (user_repo / "leerie.toml").write_text(f"log_file = {toml_log}\n")

    # All three set; CLI should win.
    out = _run(
        user_repo, fake_home, state_dir,
        {"LEERIE_LOG_FILE": env_log}, [f"--log-file={cli_log}"],
    )
    assert out == cli_log

    # Remove CLI; env should win over toml.
    out = _run(user_repo, fake_home, state_dir, {"LEERIE_LOG_FILE": env_log}, [])
    assert out == env_log

    # Remove env; toml should win over default.
    out = _run(user_repo, fake_home, state_dir, {}, [])
    assert out == toml_log
