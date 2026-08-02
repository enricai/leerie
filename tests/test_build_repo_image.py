"""Tests for build_repo_image() invocation, error handling, and hash update.

Focuses on three checkable conditions not fully covered by
test_launcher_per_repo_image.py:
  (a) exact nerdctl argv on success: build, --build-arg BASE_IMAGE=<base>,
      -f <repo>/.leerie/Dockerfile, -t <repo-tag>, and <repo> as context.
  (b) .dockerfile-hash written with <version>:<sha256> on success; NOT
      updated when nerdctl build fails.
  (c) Coupling: base-image build error guard (Fix 1) sentinel present in
      launcher (the live behaviour is covered by test_launcher_base_image_build_error.py).

Uses an argv-recording nerdctl stub (writes "$@" to a log file) rather than
the exit-code-only stub in test_launcher_per_repo_image.py.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Launcher source helpers
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]


# ---------------------------------------------------------------------------
# Harness preamble: argv-recording nerdctl stub + minimal env setup.
# The nerdctl stub writes all arguments to $NERDCTL_LOG (one line per call).
# ---------------------------------------------------------------------------

def _harness_prefix(tmp_path: Path) -> str:
    nerdctl_log = tmp_path / "nerdctl.log"
    return rf"""
#!/usr/bin/env bash
set -euo pipefail

NERDCTL_LOG="{nerdctl_log}"

remote_log() {{ echo "[leerie] $*" >&2; }}

# argv-recording nerdctl stub.  `return` (not `exit`) so set -e does not
# kill the shell on a deliberately non-zero exit.
nerdctl() {{
  # Record the full argv for later inspection.
  echo "$*" >> "$NERDCTL_LOG"
  # Skip a leading `--namespace <ns>` so the real subcommand is inspected
  # (ensure_base_in_buildkit_ns calls `nerdctl --namespace buildkit image …`).
  local ns=""
  if [ "${{1:-}}" = "--namespace" ]; then ns="${{2:-}}"; shift 2 || true; fi
  local cmd="${{1:-}}"; shift || true
  case "$cmd" in
    image)
      # Base-image presence probe. NERDCTL_BUILDKIT_INSPECT_RC (if set) governs
      # the buildkit-ns probe specifically; else NERDCTL_INSPECT_RC.
      if [ "$ns" = "buildkit" ] && [ -n "${{NERDCTL_BUILDKIT_INSPECT_RC:-}}" ]; then
        return "$NERDCTL_BUILDKIT_INSPECT_RC"
      fi
      return "${{NERDCTL_INSPECT_RC:-0}}"
      ;;
    build)
      return "${{NERDCTL_BUILD_RC:-0}}"
      ;;
    *)
      return 0
      ;;
  esac
}}

# Stub git for repo-id derivation.
git() {{
  if [ "${{1:-}}" = "-C" ]; then shift 2; fi
  if [ "${{1:-}}" = "remote" ] && [ "${{2:-}}" = "get-url" ]; then
    echo "${{FAKE_GIT_REMOTE:-}}"
    return 0
  fi
  command git "$@"
}}

LEERIE_VERSION="${{LEERIE_VERSION:-0.99.test}}"
IMAGE_TAG="${{IMAGE_TAG:-leerie:${{LEERIE_VERSION}}}}"
USER_REPO="${{USER_REPO:-/tmp/test-user-repo}}"
LEERIE_STATE_HOST_DIR="${{LEERIE_STATE_HOST_DIR:-/tmp/leerie-state-test}}"

"""


def _run_harness(
    tmp_path: Path,
    body: str,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the per-repo image block from the real launcher + `body` in a bash subprocess."""
    launcher = _launcher_text()
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    block = _extract_block(launcher, marker_start, marker_end)

    script = _harness_prefix(tmp_path) + block + "\n" + body

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
# (a) nerdctl argv on successful build
# ---------------------------------------------------------------------------

def test_build_repo_image_argv_contains_required_flags(tmp_path):
    """build_repo_image() passes the mandatory flags to nerdctl build."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",   # image absent → build fires
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    nerdctl_log = tmp_path / "nerdctl.log"
    assert nerdctl_log.exists(), "nerdctl was never called"
    log_content = nerdctl_log.read_text()

    # Must call `nerdctl build` (not just inspect).
    build_lines = [l for l in log_content.splitlines() if l.startswith("build ")]
    assert build_lines, f"no `build` call found in nerdctl log:\n{log_content}"
    build_args = build_lines[0]

    # BASE_IMAGE build-arg must use the base IMAGE_TAG.
    assert "--build-arg BASE_IMAGE=leerie:1.2.3" in build_args, build_args

    # -f must point at the repo's .leerie/Dockerfile.
    expected_df = str(user_repo / ".leerie" / "Dockerfile")
    assert f"-f {expected_df}" in build_args, build_args

    # Tag must be the per-repo leerie-repo/<id>:<version> form.
    assert "-t leerie-repo/" in build_args, build_args
    assert ":1.2.3" in build_args, build_args

    # Build context must be the USER_REPO path (last positional arg).
    assert str(user_repo) in build_args, build_args


# ---------------------------------------------------------------------------
# (a) buildkit-namespace copy before the derived build (Bug B)
# ---------------------------------------------------------------------------

def test_base_copied_into_buildkit_namespace_before_build(tmp_path):
    """Before the derived build, the base is copied into the buildkit namespace.

    Colima's buildkitd reads the `buildkit` containerd namespace, not the
    `default` one the base is built in; without this copy `FROM $BASE_IMAGE`
    401s against the registry. Assert the launcher probes the buildkit ns and
    (when absent) runs `nerdctl save | nerdctl --namespace buildkit load`,
    and that this happens before the `nerdctl build`."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",            # derived image absent → build fires
            "NERDCTL_BUILDKIT_INSPECT_RC": "1",   # base absent in buildkit ns → copy fires
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    log_content = (tmp_path / "nerdctl.log").read_text()
    lines = log_content.splitlines()
    # The copy path: `save leerie:1.2.3` and a `--namespace buildkit load`.
    assert any(l.startswith("save leerie:1.2.3") for l in lines), log_content
    assert any("--namespace buildkit load" in l for l in lines), log_content
    # The buildkit-ns probe is `--namespace buildkit image inspect leerie:1.2.3`.
    assert any("--namespace buildkit image inspect leerie:1.2.3" in l
               for l in lines), log_content
    # Copy must precede the derived build.
    save_idx = next(i for i, l in enumerate(lines) if l.startswith("save "))
    build_idx = next(i for i, l in enumerate(lines) if l.startswith("build "))
    assert save_idx < build_idx, f"copy did not precede build:\n{log_content}"


def test_base_not_recopied_when_present_in_buildkit_namespace(tmp_path):
    """Idempotency: when the base is already in the buildkit ns, skip save|load."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",            # derived image absent → build fires
            "NERDCTL_BUILDKIT_INSPECT_RC": "0",   # base already in buildkit ns → no copy
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    log_content = (tmp_path / "nerdctl.log").read_text()
    # Base present in buildkit ns → no save|load copy.
    assert "save leerie:1.2.3" not in log_content, log_content
    assert "buildkit load" not in log_content, log_content
    # But the build still happens.
    assert any(l.startswith("build ") for l in log_content.splitlines()), log_content


# ---------------------------------------------------------------------------
# (a) .dockerfile-hash written with <version>:<sha256> on success
# ---------------------------------------------------------------------------

def test_hash_file_written_on_success(tmp_path):
    """After a successful build, .dockerfile-hash contains <version>:<sha256>."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    hash_file = state_dir / ".dockerfile-hash"
    assert hash_file.exists(), ".dockerfile-hash was not created"

    stored = hash_file.read_text().strip()
    expected_sha = hashlib.sha256(df.read_bytes()).hexdigest()
    expected = f"1.2.3:{expected_sha}"
    assert stored == expected, f"stored={stored!r}, expected={expected!r}"


# ---------------------------------------------------------------------------
# (b) hash file NOT updated on nerdctl build failure
# ---------------------------------------------------------------------------

def test_hash_file_not_written_on_failure(tmp_path):
    """When nerdctl build fails, the hash file must not be created or modified."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Pre-existing stale hash (must stay unchanged).
    hash_file = state_dir / ".dockerfile-hash"
    original_content = "0.98.old:stale\n"
    hash_file.write_text(original_content)

    result = _run_harness(
        tmp_path,
        # set -e + build_repo_image calling exit 1 ends the script; body
        # won't run and that's fine — we just care about the hash file state.
        "",
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",   # image absent → build fires
            "NERDCTL_BUILD_RC": "1",     # build fails
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 1, "expected exit 1 on build failure"
    assert "error: per-repo container image build failed" in result.stderr

    # Hash file must still contain the original stale content.
    assert hash_file.read_text() == original_content, (
        "hash file was modified despite build failure"
    )


def test_hash_file_absent_when_build_fails_from_scratch(tmp_path):
    """When build fails and no prior hash file exists, none should be created."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        "",
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "1",
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 1
    assert not (state_dir / ".dockerfile-hash").exists(), (
        "hash file must not exist when build failed"
    )


# ---------------------------------------------------------------------------
# (b) error message sentinel coupling
# ---------------------------------------------------------------------------

def test_per_repo_build_error_sentinel_in_launcher():
    """The per-repo build error message literal must be present in the launcher."""
    launcher_text = _launcher_text()
    assert 'remote_log "error: per-repo container image build failed"' in launcher_text, (
        "Per-repo build error sentinel changed in launcher — update this test to match."
    )


# ---------------------------------------------------------------------------
# (c) base-image build error guard (Fix 1) — coupling test
# ---------------------------------------------------------------------------

def test_base_image_build_error_sentinel_in_launcher():
    """Fix 1: the base image build must have error handling in the launcher.

    The live behaviour (exit 1 on failure) is exercised by
    test_launcher_base_image_build_error.py; this coupling test guards that
    the sentinel stays present in the launcher source.
    """
    launcher_text = _launcher_text()
    assert 'remote_log "error: container image build failed"' in launcher_text, (
        "Base-image build error sentinel changed in launcher — "
        "update test_launcher_base_image_build_error.py and this test to match."
    )


# ---------------------------------------------------------------------------
# Out-of-repo dependency bake (DESIGN §6½, IMPLEMENTATION.md §0.5):
# Python/Ruby/Rust/Go install to /opt/* instead of the inherited /work
# WORKDIR, plus the $_sp gating-bug fix. Runs the FULL Dockerfile-generation
# block (not just build_repo_image) via the same _run_harness pattern —
# `.leerie/config.toml` and dependency-manifest fixtures under USER_REPO
# trigger the real auto-generation path, and the resulting
# `.leerie/Dockerfile` content is asserted directly.
# ---------------------------------------------------------------------------

def _write_config_toml(leerie_dir: Path, setup_packages: str = "") -> None:
    lines = []
    if setup_packages:
        lines.append(f'setup_packages = "{setup_packages}"')
    (leerie_dir / "config.toml").write_text("\n".join(lines) + "\n")


def _generate_dockerfile(tmp_path, user_repo: Path, state_dir: Path,
                          extra_env: dict | None = None):
    env = {
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "NERDCTL_INSPECT_RC": "1",
        "NERDCTL_BUILD_RC": "0",
        "FAKE_GIT_REMOTE": "https://github.com/owner/repo.git",
        "LEERIE_VERSION": "1.2.3",
        "IMAGE_TAG": "leerie:1.2.3",
    }
    if extra_env:
        env.update(extra_env)
    result = _run_harness(tmp_path, "echo done", env=env)
    return result


def test_python_bakes_to_opt_venv(tmp_path):
    """Load-bearing: a Python fixture (uv.lock, no setup_packages) bakes to
    /opt/venv with the correct WORKDIR/ENV, not a /work-targeting RUN."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "uv.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/python-build" in df, df
    assert "ENV VIRTUAL_ENV=/opt/venv" in df, df
    assert "ENV PATH=/opt/venv/bin:$PATH" in df, df
    assert "RUN python3 -m venv /opt/venv" in df, df
    assert "RUN /opt/venv/bin/python3 -m pip install uv" in df, df
    assert "RUN uv sync --active" in df, df
    # Must NOT emit a bare install RUN against the inherited /work WORKDIR.
    assert "RUN uv sync\n" not in df, df


def test_poetry_bakes_to_opt_venv_no_active_flag(tmp_path):
    """Poetry bakes to /opt/venv via VIRTUAL_ENV detection alone — NO --active
    flag (unlike uv). Live-verified: poetry (unlike uv) correctly detects and
    installs into an active VIRTUAL_ENV with no special flag; confirmed via a
    real `poetry install` against a real /opt/venv-equivalent venv, which
    landed the dependency in the target venv rather than poetry's own
    default cache-dir-managed virtualenv."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "poetry.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[tool.poetry]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/python-build" in df, df
    assert "ENV VIRTUAL_ENV=/opt/venv" in df, df
    assert "RUN /opt/venv/bin/python3 -m pip install poetry" in df, df
    # No --active flag for poetry (that's uv-specific) — a bare "poetry
    # install" resolves correctly via VIRTUAL_ENV alone.
    assert "RUN poetry install\n" in df, df
    assert "--active" not in df, df


def test_pipenv_bakes_to_opt_venv(tmp_path):
    """Pipenv bakes to /opt/venv via VIRTUAL_ENV detection alone, same as
    poetry. Live-verified: `pipenv install` against a real /opt/venv
    equivalent prints "Pipenv found itself running within a virtual
    environment, so it will automatically use that environment" and the
    dependency lands in that venv."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "Pipfile.lock").write_text("")
    (user_repo / "Pipfile").write_text("[packages]\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/python-build" in df, df
    assert "ENV VIRTUAL_ENV=/opt/venv" in df, df
    assert "RUN /opt/venv/bin/python3 -m pip install pipenv" in df, df
    assert "RUN pipenv install\n" in df, df


def test_rust_bakes_to_opt_cargo_target_with_dummy_source(tmp_path):
    """Rust bakes to CARGO_TARGET_DIR=/opt/cargo-target via a discardable
    dummy src/main.rs (cargo cannot fetch/build with zero source files)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "Cargo.lock").write_text("")
    (user_repo / "Cargo.toml").write_text('[package]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/rust-build" in df, df
    assert "ENV CARGO_TARGET_DIR=/opt/cargo-target" in df, df
    assert "src/main.rs" in df, df
    assert "cargo fetch" in df, df
    assert "cargo build --offline" in df, df
    # The dummy source must be discarded, never left for /work.
    assert "rm -rf src" in df, df
    # Regression guard: the bake must NOT build the release profile. Cargo
    # keys its build cache by profile (debug/ vs release/ under
    # CARGO_TARGET_DIR) — a --release bake shares nothing with the
    # debug-profile `cargo build`/`cargo test` workers actually run.
    # Reproduced live during implementation: baking with --release left a
    # real worktree's `cargo build` fully recompiling every dependency
    # ("Compiling serde", not "Fresh serde"); dropping it fixed the cache
    # hit. See DESIGN §6½.
    assert "--release" not in df, df


def test_go_bakes_to_opt_go_cache_with_dummy_source(tmp_path):
    """Go bakes to GOCACHE=/opt/go-cache via a discardable dummy .go file
    (go build ./... against zero .go files is a silent no-op that warms
    nothing — go mod download alone only warms GOMODCACHE)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "go.mod").write_text("module x\n\ngo 1.21\n")
    (user_repo / "go.sum").write_text("")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/go-build" in df, df
    assert "ENV GOCACHE=/opt/go-cache" in df, df
    assert "_leerie_dummy.go" in df, df
    assert "go mod download" in df, df
    assert "go build ./..." in df, df
    # The dummy source must be discarded, never left for /work.
    assert "rm -f _leerie_dummy.go" in df, df


def test_ruby_bakes_to_opt_bundle(tmp_path):
    """Ruby bakes to /opt/bundle via BUNDLE_PATH/BUNDLE_APP_CONFIG env,
    emitted BEFORE the bundle install RUN line."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "Gemfile.lock").write_text("")
    (user_repo / "Gemfile").write_text('source "https://rubygems.org"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "WORKDIR /opt/ruby-build" in df, df
    env_idx = df.index("ENV BUNDLE_PATH=/opt/bundle")
    config_idx = df.index("ENV BUNDLE_APP_CONFIG=/opt/bundle")
    run_idx = df.index("RUN bundle install")
    assert env_idx < run_idx and config_idx < run_idx, df


def test_node_pnpm_still_targets_work_unchanged(tmp_path):
    """Node/pnpm is deliberately unchanged: node_modules must live in the
    repo tree for resolution, so no /opt/* WORKDIR/ENV is emitted for it."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "pnpm-lock.yaml").write_text("")
    (user_repo / "package.json").write_text("{}")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "pnpm install --frozen-lockfile" in df, df
    assert "/opt/" not in df, df


# ---------------------------------------------------------------------------
# $_sp gating-bug fix: a lockfile-only repo (no setup_packages) must still
# generate a Dockerfile with the language-dep layer.
# ---------------------------------------------------------------------------

def test_gating_fix_lockfile_without_setup_packages_still_generates(tmp_path):
    """Regression: previously `if [ -n "$_sp" ]` skipped generation entirely
    for a repo with a lockfile but no setup_packages."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir, setup_packages="")
    (user_repo / "uv.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df_path = leerie_dir / "Dockerfile"
    assert df_path.exists(), "Dockerfile must be generated for a lockfile-only repo"
    df = df_path.read_text()
    assert "RUN uv sync" in df, df
    # No setup_packages → no apt-get layer at all.
    assert "apt-get install" not in df, df


def test_gating_fix_bare_requirements_txt_does_not_trigger(tmp_path):
    """Negative control: bare requirements.txt (no lockfile) is deliberately
    excluded from the deterministic table (mirrors _lockfile_table_entries
    in orchestrator/leerie.py) — must NOT trigger generation on its own."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir, setup_packages="")
    (user_repo / "requirements.txt").write_text("requests==2.31.0\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr
    assert not (leerie_dir / "Dockerfile").exists(), (
        "bare requirements.txt must not trigger Dockerfile auto-generation"
    )


def test_gating_fix_setup_packages_alone_still_works(tmp_path):
    """Regression control: setup_packages alone (no lockfile) must still
    generate a Dockerfile with the apt layer, as before this fix."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir, setup_packages="curl,git")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr

    df = (leerie_dir / "Dockerfile").read_text()
    assert "apt-get install" in df, df
    assert "curl" in df and "git" in df, df


def test_gating_fix_neither_setup_packages_nor_lockfile_no_generation(tmp_path):
    """Negative control: a repo with neither setup_packages nor a lockfile
    must not generate a Dockerfile at all (no bake needed)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir, setup_packages="")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result.returncode == 0, result.stderr
    assert not (leerie_dir / "Dockerfile").exists()


# ---------------------------------------------------------------------------
# Cache-invalidation regression: a lockfile bump still changes the emitted
# Dockerfile's COPY-input SHA comment (and thus .dockerfile-hash) even
# against the new /opt/* bake target.
# ---------------------------------------------------------------------------

def test_lockfile_bump_still_changes_hash_against_opt_target(tmp_path):
    """A dependency-input change must still invalidate .dockerfile-hash even
    though the install target moved from /work to /opt/venv."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir)
    (user_repo / "uv.lock").write_text("original-lockfile-content\n")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result1 = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result1.returncode == 0, result1.stderr
    hash1 = (state_dir / ".dockerfile-hash").read_text()
    assert "/opt/venv" in (leerie_dir / "Dockerfile").read_text()

    # Remove the leerie-generated Dockerfile so it regenerates (the launcher
    # only refreshes an uncommitted generated file in place, which requires
    # `git ls-files` to report it untracked — simplest here is to delete it,
    # matching the "no Dockerfile exists" trigger).
    (leerie_dir / "Dockerfile").unlink()
    (user_repo / "uv.lock").write_text("bumped-lockfile-content-changed\n")

    result2 = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert result2.returncode == 0, result2.stderr
    hash2 = (state_dir / ".dockerfile-hash").read_text()

    assert hash1 != hash2, "lockfile bump did not change .dockerfile-hash"


# ---------------------------------------------------------------------------
# Runtime BUNDLE_PATH env reconciliation (leerie:~6042 CACHE_MOUNTS):
# must point at /opt/bundle only when the Dockerfile actually baked there,
# else keep the pre-existing cache-mount fallback path.
# ---------------------------------------------------------------------------

def test_bundle_path_runtime_env_points_at_opt_when_baked():
    """Coupling test: the CACHE_MOUNTS BUNDLE_PATH override must consult the
    on-disk Dockerfile for the baked ENV line rather than hard-coding one
    value, per the correctness risk found during implementation (a
    hard-coded /opt/bundle would break `bundle install` for repos whose
    image was NOT built with the Ruby bake — verified live: 'File system is
    read-only' against a nonexistent, unmounted /opt/bundle)."""
    launcher_text = _launcher_text()
    assert "grep -q '^ENV BUNDLE_PATH=/opt/bundle'" in launcher_text, (
        "BUNDLE_PATH runtime-env bake-detection changed — update this test"
    )
    assert 'CACHE_MOUNTS+=(-e "BUNDLE_PATH=/opt/bundle")' in launcher_text
    assert 'CACHE_MOUNTS+=(-e "BUNDLE_PATH=/home/leerie/.cache/leerie/bundle")' in launcher_text


# ---------------------------------------------------------------------------
# Regression guard: the already-shipped consumer-half functions
# (_is_baked_ecosystem_command, _format_provision_recipe_section,
# _filter_residual_deps) must remain untouched by this feature — per
# prompts/fix-dep-capture-bake.md's explicit constraint.
#
# `_is_node_offline_relink` was a fourth entry here and has been removed:
# it had no callers anywhere. `_filter_residual_deps` tests the same
# condition inline and deliberately more broadly (pnpm wants both
# --offline and --frozen-lockfile; npm and yarn each stand on one), so the
# pnpm-only helper was superseded rather than orphaned by a lost call
# site. Pinning the existence of a function nothing calls only guarantees
# it stays dead. The constraint above was scoped to that feature's work,
# which shipped long ago.
# ---------------------------------------------------------------------------

def test_consumer_half_functions_present_and_unmodified_markers():
    """These three functions must still exist with their documented
    docstrings/behavior — a byte-for-byte diff check belongs in `git diff`
    at review time (see the plan's Verification section), but this test
    guards that they were not accidentally deleted or renamed."""
    orch_text = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    for fn in (
        "def _is_baked_ecosystem_command(",
        "def _format_provision_recipe_section(",
        "def _filter_residual_deps(",
    ):
        assert fn in orch_text, f"{fn} missing from orchestrator/leerie.py"
