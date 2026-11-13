"""The repository image a `.leerie/config.toml` that still holds an earlier
leerie's dependency keys builds (DESIGN §6½ *The repository image holds no
dependency*).

Until 0.65.0 the per-repo image block read `language_installs` (written by
the dep_capture worker) and `bake_language_deps`, and emitted a COPY+RUN
dependency layer per manager. Neither key is read now: the generated file is
the apt layer `setup_packages` names, the launch says once that the keys are
no longer read, and a committed `.leerie/Dockerfile` stays authoritative.

Uses the same bash-harness extraction pattern as test_dockerfile_autogen.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.committed_tree import commit_tree
from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.dockerfile_autogen_extract import extract_autogen_block as _extract_autogen_block
from tests.repo_image_block_extract import (BUILT_DOCKERFILE,
                                            NERDCTL_IMAGE_INSPECT_STUB)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Harness helpers (mirrors test_dockerfile_autogen.py)
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


_HARNESS_PREFIX = r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

nerdctl() {
  # leerie names its namespace on every call (LEERIE_IMAGE_NS).
  if [ "${1:-}" = "--namespace" ]; then shift 2; fi
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image)  _stub_image_inspect "$@" && return 0; return "${NERDCTL_INSPECT_RC:-0}" ;;
    build)  _stub_build_done "$@"; return "${NERDCTL_BUILD_RC:-0}" ;;
    run)    echo "nerdctl-run: image=${REPO_IMAGE_REF:-$IMAGE_TAG}"; return 0 ;;
    *)      return 0 ;;
  esac
}

git() {
  if [ "${1:-}" = "-C" ]; then shift 2; fi
  if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then
    echo "${FAKE_GIT_REMOTE:-}"; return 0
  fi
  command git "$@"
}

LEERIE_VERSION="${LEERIE_VERSION:-0.99.test}"
USER_REPO="${USER_REPO:-/tmp/test-user-repo}"
IMAGE_TAG="${IMAGE_TAG:-leerie:${LEERIE_VERSION}}"
LEERIE_STATE_HOST_DIR="${LEERIE_STATE_HOST_DIR:-/tmp/leerie-state-test}"
"""
_HARNESS_PREFIX = (LOG_SH_SOURCE + FRONTEND_SH_SOURCE + BUILDCTL_STUB
                   + _HARNESS_PREFIX)


def _make_config_toml(setup_packages: str = "") -> str:
    """A config.toml string with the given setup_packages."""
    return f'setup_packages = "{setup_packages}"\n' if setup_packages else "\n"


def _run_harness(body: str, env: dict | None = None) -> subprocess.CompletedProcess:
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    if env and env.get("USER_REPO"):
        commit_tree(env["USER_REPO"])
    script = (_HARNESS_PREFIX + NERDCTL_IMAGE_INSPECT_STUB + block + "\n"
              + body)
    base_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "0.99.test",
        "IMAGE_TAG": "leerie:0.99.test",
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )



def _run_and_get_dockerfile(user_repo: "Path", state_dir: "Path") -> str:
    """Run the harness and return the generated Dockerfile content."""
    # Delete any existing generated Dockerfile so regeneration fires.
    df = user_repo / ".leerie" / "Dockerfile"
    if df.exists():
        df.unlink()
    result = _run_harness(
        BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout



_OLD_KEYS = (
    'language_installs = \'[{"manager":"bundle","command":"bundle install",'
    '"copy_inputs":["Gemfile","Gemfile.lock"]},{"manager":"pip","command":'
    '"pip install -r requirements.txt","copy_inputs":["requirements.txt"]}]\'\n'
    "bake_language_deps = true\n")
_NOTE = ("note: .leerie/config.toml's language_installs and "
         "bake_language_deps are no longer read")


def _keys_repo(tmp_path: Path, old_keys: bool) -> tuple[Path, Path]:
    user_repo = tmp_path / ("old" if old_keys else "new") / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "Gemfile").write_text('source "https://rubygems.org"\n')
    (user_repo / "Gemfile.lock").write_text("GEM\n  specs:\n")
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(
        _make_config_toml(setup_packages="git")
        + (_OLD_KEYS if old_keys else ""))
    state_dir = user_repo.parent / "state"
    state_dir.mkdir()
    return user_repo, state_dir


def _launch(user_repo: Path, state_dir: Path) -> subprocess.CompletedProcess:
    return _run_harness(
        BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )


def test_earlier_dependency_keys_are_noted_once_and_change_nothing(tmp_path):
    """A config.toml holding `language_installs` and `bake_language_deps`
    generates byte for byte the file the same config without them does —
    the apt layer and nothing else — and the launch says once that the keys
    are no longer read, naming DESIGN's section. Without the keys, no note.
    Falsified against 0.65.0's launcher: the keys produced a `COPY` +
    `RUN bundle install` and a `RUN pip install -r requirements.txt` layer,
    and no note."""
    old = _launch(*_keys_repo(tmp_path, old_keys=True))
    new = _launch(*_keys_repo(tmp_path, old_keys=False))
    assert old.returncode == 0, old.stderr
    assert new.returncode == 0, new.stderr
    assert old.stdout == new.stdout, old.stdout
    assert "apt-get install" in old.stdout, old.stdout
    for word in ("COPY", "WORKDIR", "ENV", "bundle", "pip install"):
        assert word not in old.stdout, old.stdout
    assert old.stderr.count(_NOTE) == 1, old.stderr
    assert "*The repository image holds no dependency*" in old.stderr
    assert _NOTE not in new.stderr, new.stderr


def test_identical_regen_produces_same_sha(tmp_path):
    """Two identical regen passes produce bit-identical Dockerfiles (sha
    stable), so a launch whose configuration did not change rebuilds
    nothing."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(
        _make_config_toml(setup_packages="git"))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    content1 = _run_and_get_dockerfile(user_repo, state_dir)
    content2 = _run_and_get_dockerfile(user_repo, state_dir)
    assert content1 == content2, "identical regen must produce identical Dockerfile"


def test_committed_dockerfile_is_built_as_written(tmp_path):
    """A committed .leerie/Dockerfile is built as written, whatever the
    configuration holds, and the launch says setup_packages is not read."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(
        _make_config_toml(setup_packages="git") + _OLD_KEYS)
    custom_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n# COMMITTED MANUAL CONTENT\n"
    dockerfile = user_repo / ".leerie" / "Dockerfile"
    dockerfile.write_text(custom_content)

    # Commit the Dockerfile so ls-files sees it as tracked.
    subprocess.run(["git", "init", str(user_repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(user_repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(user_repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(user_repo), "add", ".leerie/Dockerfile"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(user_repo), "commit", "-m", "add Dockerfile"],
        check=True, capture_output=True,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == custom_content, (
        "committed Dockerfile must be built as written"
    )
    assert "setup_packages is not read while it is there" in result.stderr



def test_autogen_apt_layer_has_no_trailing_user_leerie(tmp_path):
    """The generated apt layer must not end with USER leerie (PID-1 must
    stay root — DESIGN §6)."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(
        _make_config_toml(setup_packages="git") + _OLD_KEYS)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "USER leerie" not in result.stdout, result.stdout
