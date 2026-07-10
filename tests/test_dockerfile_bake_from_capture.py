"""Tests for Dockerfile bake driven by persisted per-manager language_installs.

Distinct from test_dockerfile_autogen.py (which covers setup_packages-only
auto-generation). This file covers the bake-from-persisted-installs path:
  - The per-repo image block reads language_installs from config.toml (written
    by the dep_capture worker) and emits a COPY+RUN layer per manager with
    copy-input-sha comments embedded in the Dockerfile.
  - copy_input paths that don't exist are silently dropped from COPY while
    the RUN line is always emitted (p.exists() guard).
  - Identical regen produces an identical Dockerfile (sha stability → no
    needless rebuild).
  - A committed .leerie/Dockerfile is left untouched (authoritative).

Uses the same bash-harness extraction pattern as test_dockerfile_autogen.py.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Harness helpers (mirrors test_dockerfile_autogen.py)
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_autogen_block(text: str) -> str:
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    s = text.index(marker_start)
    e = text.index(marker_end, s)
    return text[s:e]


_HARNESS_PREFIX = r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

nerdctl() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image)  return "${NERDCTL_INSPECT_RC:-0}" ;;
    build)  return "${NERDCTL_BUILD_RC:-0}" ;;
    run)    echo "nerdctl-run: image=${REPO_IMAGE_TAG:-$IMAGE_TAG}"; return 0 ;;
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


def _make_config_toml(
    setup_packages: str = "",
    language_installs: list[dict] | None = None,
) -> str:
    """Build a config.toml string with the given setup_packages and language_installs."""
    parts = []
    if setup_packages:
        parts.append(f'setup_packages = "{setup_packages}"')
    if language_installs:
        val = json.dumps(language_installs, separators=(",", ":"))
        parts.append(f'language_installs = "{val}"')
    return "\n".join(parts) + "\n"


def _run_harness(body: str, env: dict | None = None) -> subprocess.CompletedProcess:
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    script = _HARNESS_PREFIX + block + "\n" + body
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


# ---------------------------------------------------------------------------
# Coupling tests — key sentinel strings must exist in the live launcher block
# ---------------------------------------------------------------------------

def test_coupling_persisted_installs_in_launcher():
    """The persisted_installs path must be present in the extracted block."""
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    assert "persisted_installs" in block


def test_coupling_p_exists_guard_in_launcher():
    """The p.exists() copy-input guard must be present in the extracted block."""
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    assert "p.exists()" in block or ".exists()" in block


def test_coupling_copy_input_shas_comment_in_launcher():
    """The copy-input-shas comment literal must be present in the extracted block."""
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    assert "copy-input-shas" in block


def test_coupling_language_installs_key_in_launcher():
    """The language_installs TOML key reader must be present in the extracted block."""
    launcher = _launcher_text()
    block = _extract_autogen_block(launcher)
    assert "language_installs" in block


# ---------------------------------------------------------------------------
# (a) Single-manager: pip with requirements.txt — apt layer + COPY+RUN
# ---------------------------------------------------------------------------

def test_pip_install_emits_copy_run_layer(tmp_path):
    """Persisted pip install with requirements.txt → COPY + RUN layer."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "COPY requirements.txt ./" in result.stdout
    assert "RUN pip install -r requirements.txt" in result.stdout


def test_pip_install_emits_apt_layer_alongside(tmp_path):
    """The apt layer from setup_packages is emitted alongside the pip COPY+RUN layer."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="libpq-dev",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "apt-get install" in result.stdout
    assert "libpq-dev" in result.stdout
    assert "RUN pip install -r requirements.txt" in result.stdout


def test_pip_install_emits_sha_comment(tmp_path):
    """The COPY layer includes a copy-input-shas comment for rebuild tracking."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "# copy-input-shas:" in result.stdout
    assert "requirements.txt=" in result.stdout


# ---------------------------------------------------------------------------
# (b) Hallucinated copy_input: absent file dropped from COPY, RUN still emitted
# ---------------------------------------------------------------------------

def test_hallucinated_copy_input_drops_copy_keeps_run(tmp_path):
    """A copy_input that doesn't exist is dropped from COPY but RUN is still emitted."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    # requirements.txt does NOT exist — hallucinated by the worker.
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    # COPY line must be absent (no valid copy inputs).
    assert "COPY requirements.txt" not in result.stdout
    # RUN line must still be emitted.
    assert "RUN pip install -r requirements.txt" in result.stdout


def test_hallucinated_copy_input_does_not_crash(tmp_path):
    """A fully-hallucinated copy_inputs list does not crash — returns rc 0."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["DOES_NOT_EXIST_A.txt", "DOES_NOT_EXIST_B.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'true',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr


def test_partial_hallucination_only_existing_files_in_copy(tmp_path):
    """Only existing copy_inputs appear in COPY; hallucinated paths are dropped."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    # HALLUCINATED.txt does not exist.
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt", "HALLUCINATED.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    # Existing file present in COPY.
    assert "requirements.txt" in result.stdout
    # Hallucinated file absent from COPY.
    assert "HALLUCINATED.txt" not in result.stdout
    # RUN always emitted.
    assert "RUN pip install -r requirements.txt" in result.stdout


# ---------------------------------------------------------------------------
# (c) Multi-manager: pnpm + pip → two COPY+RUN layers
# ---------------------------------------------------------------------------

def test_multi_manager_emits_both_layers(tmp_path):
    """Two language_installs entries produce two COPY+RUN layers."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0\n")
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[
            {
                "manager": "pnpm",
                "command": "pnpm install --frozen-lockfile",
                "copy_inputs": ["pnpm-lock.yaml"],
            },
            {
                "manager": "pip",
                "command": "pip install -r requirements.txt",
                "copy_inputs": ["requirements.txt"],
            },
        ],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "RUN pnpm install --frozen-lockfile" in result.stdout
    assert "RUN pip install -r requirements.txt" in result.stdout
    assert "COPY pnpm-lock.yaml ./" in result.stdout
    assert "COPY requirements.txt ./" in result.stdout


def test_multi_manager_emits_two_sha_comments(tmp_path):
    """Each language_install layer gets its own copy-input-shas comment."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: 6.0\n")
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[
            {
                "manager": "pnpm",
                "command": "pnpm install --frozen-lockfile",
                "copy_inputs": ["pnpm-lock.yaml"],
            },
            {
                "manager": "pip",
                "command": "pip install -r requirements.txt",
                "copy_inputs": ["requirements.txt"],
            },
        ],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    sha_lines = [l for l in result.stdout.splitlines() if "copy-input-shas:" in l]
    assert len(sha_lines) == 2, f"expected 2 sha comments, got: {sha_lines}"
    assert any("pnpm-lock.yaml=" in l for l in sha_lines)
    assert any("requirements.txt=" in l for l in sha_lines)


# ---------------------------------------------------------------------------
# (d) Pip-only (no lockfile): pip without a lockfile-detected install
#     The persisted path takes priority over lockfile detection — ensures that
#     a repo with only requirements.txt (no lockfile) gets a COPY+RUN layer
#     from the worker-decided install (not the lockfile-detection fallback).
# ---------------------------------------------------------------------------

def test_pip_without_lockfile_still_emits_layer(tmp_path):
    """Pip install with no lockfile present is still baked via persisted installs."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\nfastapi\n")
    # No pnpm-lock.yaml, no uv.lock, no poetry.lock — just requirements.txt.
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="python3-dev",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "RUN pip install -r requirements.txt" in result.stdout
    assert "COPY requirements.txt ./" in result.stdout


# ---------------------------------------------------------------------------
# (e) Copy-input-sha rebuild stability: identical regen → identical sha
# ---------------------------------------------------------------------------

def _run_and_get_dockerfile(user_repo: "Path", state_dir: "Path") -> str:
    """Run the harness and return the generated Dockerfile content."""
    # Delete any existing generated Dockerfile so regeneration fires.
    df = user_repo / ".leerie" / "Dockerfile"
    if df.exists():
        df.unlink()
    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
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


def test_identical_regen_produces_same_sha(tmp_path):
    """Two identical regen passes produce bit-identical Dockerfiles (sha stable)."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    content1 = _run_and_get_dockerfile(user_repo, state_dir)
    content2 = _run_and_get_dockerfile(user_repo, state_dir)
    assert content1 == content2, "identical regen must produce identical Dockerfile"


def test_copy_input_change_produces_different_sha(tmp_path):
    """Changing requirements.txt content yields a different sha comment."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    content_before = _run_and_get_dockerfile(user_repo, state_dir)

    (user_repo / "requirements.txt").write_text("pytest\nfastapi\n")
    content_after = _run_and_get_dockerfile(user_repo, state_dir)

    sha_before = [l for l in content_before.splitlines() if "copy-input-shas:" in l]
    sha_after = [l for l in content_after.splitlines() if "copy-input-shas:" in l]
    assert sha_before != sha_after, "changing requirements.txt must update the sha comment"


# ---------------------------------------------------------------------------
# (f) Committed Dockerfile is authoritative — bake does not overwrite it
# ---------------------------------------------------------------------------

def test_committed_dockerfile_not_overwritten_by_language_installs(tmp_path):
    """A committed .leerie/Dockerfile is not overwritten even when language_installs exist."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
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
        'cat "$USER_REPO/.leerie/Dockerfile"',
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
        "committed Dockerfile must not be overwritten by language_installs bake"
    )


# ---------------------------------------------------------------------------
# Node ancillary inputs on the persisted path (Bug: patches/ omitted → bake
# fails ENOENT → silent apt-only fallback). DESIGN §6½ requires the full input
# set (patches/, workspace children, .npmrc) — a persisted copy_inputs list
# that names only lockfile+manifest must be augmented for node managers.
# ---------------------------------------------------------------------------

def test_persisted_pnpm_pulls_in_patches_dir(tmp_path):
    """A persisted pnpm language_install whose copy_inputs omit patches/ must
    still COPY patches/ — pnpm.patchedDependencies reads them and a frozen
    install ENOENTs without them."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0\n")
    (user_repo / "package.json").write_text('{"name": "x"}\n')
    (user_repo / "patches").mkdir()
    (user_repo / "patches" / "foo.patch").write_text("--- a\n+++ b\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="tini",
        language_installs=[{
            "manager": "pnpm",
            "command": "pnpm install --frozen-lockfile",
            "copy_inputs": ["package.json", "pnpm-lock.yaml"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    # patches/ copied into its own subdir (not flattened into ./).
    assert "COPY patches/ ./patches/" in result.stdout, result.stdout
    # Its sha participates in the rebuild-trigger comment.
    assert "patches/foo.patch=" in result.stdout


def test_persisted_pnpm_does_not_flatten_patches(tmp_path):
    """The dir must NOT be emitted as a flattened `COPY ... patches/ ./`."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: 9.0\n")
    (user_repo / "package.json").write_text('{"name": "x"}\n')
    (user_repo / "patches").mkdir()
    (user_repo / "patches" / "foo.patch").write_text("p\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="tini",
        language_installs=[{
            "manager": "pnpm",
            "command": "pnpm install --frozen-lockfile",
            "copy_inputs": ["pnpm-lock.yaml"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    # No COPY line ends with `patches/ ./` (the flattening footgun).
    for line in result.stdout.splitlines():
        assert not (line.startswith("COPY ") and line.rstrip().endswith("patches/ ./")), (
            f"patches/ must not be flattened into ./: {line!r}"
        )


def test_persisted_non_node_ignores_patches(tmp_path):
    """A poetry (non-node) install must NOT pull in a patches/ dir even if one
    exists — _node_ancillary applies only to node managers."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "poetry.lock").write_text("[[package]]\n")
    (user_repo / "pyproject.toml").write_text("[tool.poetry]\n")
    (user_repo / "patches").mkdir()
    (user_repo / "patches" / "foo.patch").write_text("p\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="tini",
        language_installs=[{
            "manager": "poetry",
            "command": "poetry install",
            "copy_inputs": ["pyproject.toml", "poetry.lock"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "patches/" not in result.stdout, result.stdout


def test_persisted_node_workspace_child_path_preserved(tmp_path):
    """A workspace child package.json must be COPYed to its own path, not
    flattened into ./ where its basename would clobber the root manifest."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "yarn.lock").write_text("# yarn\n")
    (user_repo / "package.json").write_text('{"workspaces": ["packages/*"]}\n')
    (user_repo / "packages" / "a").mkdir(parents=True)
    (user_repo / "packages" / "a" / "package.json").write_text('{"name": "a"}\n')
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="tini",
        language_installs=[{
            "manager": "yarn",
            "command": "yarn install --frozen-lockfile",
            "copy_inputs": ["package.json", "yarn.lock"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "COPY packages/a/package.json ./packages/a/package.json" in result.stdout, result.stdout


def test_autogen_apt_layer_has_no_trailing_user_leerie(tmp_path):
    """The bake path's apt layer must not end with USER leerie either (PID-1
    must stay root — DESIGN §6)."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / "requirements.txt").write_text("pytest\n")
    (user_repo / ".leerie" / "config.toml").write_text(_make_config_toml(
        setup_packages="git",
        language_installs=[{
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt"],
        }],
    ))
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
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
