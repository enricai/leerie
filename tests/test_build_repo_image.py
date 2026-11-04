"""Tests for build_repo_image() invocation, error handling, and hash update.

Focuses on three checkable conditions not fully covered by
test_launcher_per_repo_image.py:
  (a) exact nerdctl argv on success: build, --build-arg BASE_IMAGE=<base>,
      -f <repo>/.leerie/Dockerfile, -t <repo-tag>, and <repo> as context.
  (b) .dockerfile-hash written with <version>:<sha256> <digest> on success; NOT
      updated when nerdctl build fails.
  (c) Coupling: base-image build error guard (Fix 1) sentinel present in
      launcher (the live behaviour is covered by test_launcher_base_image_build_error.py).

Uses an argv-recording nerdctl stub (writes "$@" to a log file) rather than
the exit-code-only stub in test_launcher_per_repo_image.py.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from tests.committed_tree import commit_tree
from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.repo_image_block_extract import (EMPTY_CONTEXT_SHA,
                                            NERDCTL_IMAGE_INSPECT_STUB,
                                            REPO_DIGEST)
from tests.repo_image_block_extract import extract_block as _extract_block

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Launcher source helpers
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


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
{LOG_SH_SOURCE}
{FRONTEND_SH_SOURCE}
{BUILDCTL_STUB}
remote_log() {{ echo "[leerie] $*" >&2; }}

# argv-recording nerdctl stub.  `return` (not `exit`) so set -e does not
# kill the shell on a deliberately non-zero exit.
nerdctl() {{
  # Record the full argv for later inspection.
  echo "$*" >> "$NERDCTL_LOG"
  # Skip a leading `--namespace <ns>` so the real subcommand is inspected
  # (leerie's images are in `--namespace leerie`; ensure_base_in_worker_ns
  # also inspects the worker's, `$STUB_WORKER_NS`, default `default`).
  local ns=""
  if [ "${{1:-}}" = "--namespace" ]; then ns="${{2:-}}"; shift 2 || true; fi
  local cmd="${{1:-}}"; shift || true
  case "$cmd" in
    image)
      # In the worker's namespace: NERDCTL_WORKER_INSPECT_RC (if set) answers
      # every query (the base absent there), STUB_WORKER_BASE_ID (if set) is
      # the id of the base copy there.
      if [ "$ns" = "${{STUB_WORKER_NS:-default}}" ]; then
        if [ -n "${{NERDCTL_WORKER_INSPECT_RC:-}}" ]; then
          return "$NERDCTL_WORKER_INSPECT_RC"
        fi
        if [ -n "${{STUB_WORKER_BASE_ID:-}}" ]; then
          echo "$STUB_WORKER_BASE_ID"; return 0
        fi
      fi
      # `image rm <name>`: fails for a name a container still uses.
      if [ "${{1:-}}" = rm ]; then
        case " ${{STUB_IN_USE:-}} " in *" $2 "*) return 1 ;; esac
        return 0
      fi
      _stub_image_inspect "$@" && return 0
      return "${{NERDCTL_INSPECT_RC:-0}}"
      ;;
    images)
      # The worker's namespace's images: $STUB_WORKER_IMAGES, less those
      # an `image rm` removed (all but $STUB_IN_USE).
      if [ "$ns" = "${{STUB_WORKER_NS:-default}}" ]; then
        local _i
        for _i in ${{STUB_WORKER_IMAGES:-}}; do
          case "$(/usr/bin/cat "$NERDCTL_LOG")" in
            *"image rm $_i"*)
              case " ${{STUB_IN_USE:-}} " in *" $_i "*) echo "$_i" ;; esac ;;
            *) echo "$_i" ;;
          esac
        done
      fi
      return 0
      ;;
    build)
      # What the build's Docker credentials are: the config's path, its
      # mode, the names it holds and config.json's text, and
      # DOCKER_AUTH_CONFIG (builtins and absolute paths only).
      {{
        printf 'DOCKER_CONFIG=%s' "${{DOCKER_CONFIG-unset}}"
        if [ -n "${{DOCKER_CONFIG:-}}" ] && [ -d "$DOCKER_CONFIG" ]; then
          printf ' mode=%s files=' "$(/usr/bin/stat -c %a "$DOCKER_CONFIG")"
          local _e
          for _e in "$DOCKER_CONFIG"/* "$DOCKER_CONFIG"/.[!.]*; do
            [ -e "$_e" ] && printf '%s,' "${{_e##*/}}"
          done
          [ -f "$DOCKER_CONFIG/config.json" ] \
            && printf ' config.json=%s' "$(< "$DOCKER_CONFIG/config.json")"
        fi
        printf ' DOCKER_AUTH_CONFIG=%s' "${{DOCKER_AUTH_CONFIG-unset}}"
        echo
      }} >> "$NERDCTL_LOG.env"
      # NERDCTL_FAIL_LANG_LAYER: a build whose -f file holds a language-dep
      # layer (its `# copy-input-shas:` line) fails, as a layer that does not
      # build would (builtins only).
      if [ -n "${{NERDCTL_FAIL_LANG_LAYER:-}}" ]; then
        local _a _prev="" _f="" _l
        for _a in "$@"; do [ "$_prev" = -f ] && _f="$_a"; _prev="$_a"; done
        if [ -n "$_f" ]; then
          while IFS= read -r _l; do
            case "$_l" in "# copy-input-shas:"*) return 1 ;; esac
          done < "$_f"
        fi
      fi
      _stub_build_done "$@"
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

    if env and env.get("USER_REPO"):
        commit_tree(env["USER_REPO"])
    script = (_harness_prefix(tmp_path) + NERDCTL_IMAGE_INSPECT_STUB + block
              + "\n" + body)

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
    operator_cfg = tmp_path / "operator-docker"
    operator_cfg.mkdir()
    (operator_cfg / "config.json").write_text('{"auths":{"r.example":{"auth":"eDp5"}}}')

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
            # The operator's Docker config and DOCKER_AUTH_CONFIG, which no
            # repository build reads.
            "DOCKER_CONFIG": str(operator_cfg),
            "DOCKER_AUTH_CONFIG": '{"auths":{"r.example":{"auth":"eDp5"}}}',
        },
    )
    assert result.returncode == 0, result.stderr

    nerdctl_log = tmp_path / "nerdctl.log"
    assert nerdctl_log.exists(), "nerdctl was never called"
    log_content = nerdctl_log.read_text()

    # Two `nerdctl build`s (not just inspect): the repository's file, then
    # leerie's (DESIGN §6 *One image, two roots*).
    build_lines = [l for l in log_content.splitlines() if l.startswith("--namespace leerie build ")]
    assert len(build_lines) == 2, f"not two `build` calls:\n{log_content}"
    first, second = build_lines

    # BASE_IMAGE build-arg must use the base IMAGE_TAG.
    assert "--build-arg BASE_IMAGE=leerie:1.2.3" in first, first

    # The first build's -f is the repository's Dockerfile, copied into a
    # build directory of the launcher's own under the state root; no
    # target, so BuildKit builds the file's own default target, exported as
    # an OCI layout there.
    import re
    m = re.search(r"-f (\S+/\.image-build/b\.\d+\.\w+)/repo/Dockerfile ", first)
    assert m and m.group(1).startswith(str(state_dir)), first
    d = m.group(1)
    assert "--target" not in first and "--target" not in second
    assert f'-o type=oci,"dest={d}/worker",tar=false ' in first, first
    assert " -t " not in first, first

    # The build context is the committed tree exported there, never the
    # checkout (last positional arg).
    assert first.endswith(f"{d}/context"), first
    assert str(user_repo) not in first + second

    # The second is leerie's own file, its worker stage that layout, and
    # it alone is tagged: the per-repo leerie-repo/<id>:<version> form.
    assert f"--build-context leerie-worker=oci-layout://{d}/worker " in second
    assert f"-f {d}/wrap/Dockerfile {d}/wrap" in second, second
    assert "-t leerie-repo/" in second and ":1.2.3" in second, second
    # Both run on the daemon leerie_buildkit_check vetted (DESIGN §6 *No
    # build consumes what another build left in BuildKit*)...
    for b in (first, second):
        assert b.startswith("--namespace leerie build --buildkit-host unix://"), b
    # ...and with a 0700 Docker config of leerie's own in the build
    # directory, never the operator's, whose one file names one auth entry
    # with no credential (so buildctl looks for no default credential helper
    # on PATH), and DOCKER_AUTH_CONFIG empty, though the operator's is set
    # (DESIGN §6 *A repository's build carries no operator credential*).
    envs = (tmp_path / "nerdctl.log.env").read_text().splitlines()
    assert envs == [f"DOCKER_CONFIG={d}/registry-auth mode=700 "
                    'files=config.json, config.json={"auths":'
                    '{"leerie.invalid":{}}} DOCKER_AUTH_CONFIG='] * 2, envs
    # And the build directory is gone afterwards.
    assert not list((state_dir / ".image-build").glob("b.*"))


# ---------------------------------------------------------------------------
# (a) the base copied into the worker's namespace before the derived build (Bug B)
# ---------------------------------------------------------------------------

def test_base_copied_into_the_workers_namespace_before_build(tmp_path):
    """Before the derived build, the base is copied into the namespace
    BuildKit's worker reads.

    The base lives in leerie's namespace, which no worker reads (DESIGN §6
    *No build consumes what another build left in BuildKit*); without this
    copy `FROM $BASE_IMAGE` 401s against the registry. Assert the launcher
    probes the worker's namespace (`default` here, from `buildctl debug
    workers`) and (when absent) runs `nerdctl --namespace leerie save |
    nerdctl --namespace default load`, before the `nerdctl build`."""
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
            "NERDCTL_WORKER_INSPECT_RC": "1",     # base absent in the worker's ns → copy fires
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    log_content = (tmp_path / "nerdctl.log").read_text()
    lines = log_content.splitlines()
    # The copy path: `--namespace leerie save -o <tmpfile> leerie:1.2.3` and
    # a `--namespace default load -i <tmpfile>`.
    assert any(l.startswith("--namespace leerie save ") and l.endswith(" leerie:1.2.3") for l in lines), log_content
    assert any(l.startswith("--namespace default load -i ") for l in lines), log_content
    # The probe is `--namespace default image inspect -f {{.Id}} leerie:1.2.3`.
    assert any(l.startswith("--namespace default image inspect ")
               and l.endswith(" leerie:1.2.3") for l in lines), log_content
    # Copy must precede the derived build.
    save_idx = next(i for i, l in enumerate(lines) if l.startswith("--namespace leerie save "))
    build_idx = next(i for i, l in enumerate(lines) if l.startswith("--namespace leerie build "))
    assert save_idx < build_idx, f"copy did not precede build:\n{log_content}"


def test_base_not_recopied_when_present_in_the_workers_namespace(tmp_path):
    """Idempotency: when the base, by id, is already in the worker's ns,
    skip save|load."""
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
            # base already in the worker's ns, same id → no copy
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    log_content = (tmp_path / "nerdctl.log").read_text()
    # Base present in the worker's ns → no save|load copy.
    assert not any(l.startswith("--namespace leerie save ") for l in log_content.splitlines()), log_content
    assert " load " not in log_content, log_content
    # But the build still happens.
    assert any(l.startswith("--namespace leerie build ") for l in log_content.splitlines()), log_content


def test_base_copied_again_when_the_workers_copy_is_another_image(tmp_path):
    """The copy in the worker's namespace is the base only when its id is
    the base's: a base rebuilt under the same tag is copied again, or the
    repository image would be built on another base than the one
    `_check_repo_image` compares it with. Falsified (2026-11-03): the
    probe reduced to presence (`image inspect` succeeding) skips the copy."""
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
            "NERDCTL_INSPECT_RC": "1",
            "STUB_WORKER_BASE_ID": "sha256:an-older-base",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    lines = (tmp_path / "nerdctl.log").read_text().splitlines()
    assert any(l.startswith("--namespace leerie save ") for l in lines), lines
    assert any(l.startswith("--namespace default load -i ") for l in lines), lines


@pytest.mark.parametrize("in_use", ["", "leerie-repo/enricai-barnacle:0.43.0"])
def test_old_repository_images_are_swept_before_a_build(tmp_path, in_use):
    """Before a repository build, the launcher removes the images an earlier
    leerie tagged where BuildKit's worker reads (`leerie-repo/…`), and runs
    no build while one a container uses is left (DESIGN §6 *No build
    consumes what another build left in BuildKit*). Falsified (2026-11-03):
    the sweep's call dropped from the build decision (no `image rm`, and the
    in-use case builds)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    old = "leerie-repo/enricai-barnacle:0.43.0 leerie-repo/repo-d07714eaab33:0.43.0"
    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "STUB_WORKER_IMAGES": old + " leerie:1.2.3 company/app:1",
            "STUB_IN_USE": in_use,
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    lines = (tmp_path / "nerdctl.log").read_text().splitlines()
    rms = sorted(l for l in lines if l.startswith("--namespace default image rm "))
    assert rms == [f"--namespace default image rm {n}" for n in sorted(old.split())], lines
    builds = [l for l in lines if l.startswith("--namespace leerie build ")]
    if in_use:
        assert result.returncode == 1, result.stderr
        assert in_use in result.stderr and "still holds an image" in result.stderr, result.stderr
        assert builds == [], lines
    else:
        assert result.returncode == 0, result.stderr
        assert len(builds) == 2, lines


# ---------------------------------------------------------------------------
# (a) .dockerfile-hash written with <version>:<sha256> <digest> on success
# ---------------------------------------------------------------------------

def test_hash_file_written_on_success(tmp_path):
    """After a successful build, .dockerfile-hash contains <version>:<sha256>
    and the digest of the image built."""
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
    # <version>:<layout>:<base image id>:<sha256>:<context sha256> (the
    # stub's base id; this Dockerfile reads nothing from the tree), then
    # the digest of the image built.
    expected = (f"1.2.3:worker-root-2:sha256:base-id:{expected_sha}:"
                f"{EMPTY_CONTEXT_SHA} "
                f"{REPO_DIGEST}")
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
    assert '_repo_build_fail "per-repo container image build failed"' in launcher_text, (
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
# Review round 35, execution R35E-1: leerie's generated Dockerfile is tested
# only by its own build (DESIGN §6½ *Language deps*). A separate probe build
# ran on every launch, before the rebuild check; under `--no-cache` it
# reinstalled every language dependency each launch and threw it away.
# ensure_base_in_worker_ns keeps its save+load-through-files rule (N1).
# ---------------------------------------------------------------------------

def _lang_repo(tmp_path: Path) -> tuple[Path, Path]:
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    _write_config_toml(leerie_dir, setup_packages="jq")
    (user_repo / "uv.lock").write_text("")
    (user_repo / "pyproject.toml").write_text('[project]\nname = "x"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return user_repo, state_dir


def _take_builds(tmp_path: Path) -> list[str]:
    log = tmp_path / "nerdctl.log"
    builds = [line for line in log.read_text().splitlines()
              if line.startswith("--namespace leerie build ")] if log.exists() else []
    log.unlink(missing_ok=True)
    return builds


def test_an_up_to_date_generated_image_runs_no_build(tmp_path):
    """A launch whose generated file, context, version and base match the
    recorded image runs no build at all; the one that builds runs the
    repository image's two builds and nothing else. Its record carries no
    `apt-only` mark, and its reuse says nothing of a fallback (the converse
    of review round 36's X3). Falsified: the probe restored (the first
    launch runs 3 builds, the second 1); the mark written for every
    generated image (fails)."""
    user_repo, state_dir = _lang_repo(tmp_path)
    r = _generate_dockerfile(tmp_path, user_repo, state_dir)
    assert r.returncode == 0, r.stderr
    assert "COPY " in (user_repo / ".leerie" / "Dockerfile").read_text()
    assert len(_take_builds(tmp_path)) == 2, r.stderr
    key = (state_dir / ".dockerfile-hash").read_text()
    assert re.fullmatch(r"\S+ sha256:\S+\n", key), key
    r = _generate_dockerfile(tmp_path, user_repo, state_dir,
                             {"NERDCTL_INSPECT_RC": "0"})
    assert r.returncode == 0, r.stderr
    assert "up-to-date (" in r.stderr and "skipping build" in r.stderr, \
        r.stderr
    assert "apt-only" not in r.stderr, r.stderr
    assert _take_builds(tmp_path) == []


def test_a_language_layer_that_does_not_build_falls_back_once(tmp_path):
    """When the generated file's own build fails, the launch builds it
    without the language-dep layer and records that image under the
    generated file's key, marked `apt-only`: the next launch, which
    generates the same file, reuses it and runs no build, and says, on
    every such launch, that the image is the apt-only fallback and which
    file to remove to retry (review round 36, execution X3). Falsified:
    the fallback image recorded under its own file's key (the second
    launch rebuilds); the probe restored (the second launch runs it); the
    mark not written, or the reuse warning removed (no warning on the
    second and third launches)."""
    user_repo, state_dir = _lang_repo(tmp_path)
    hash_file = state_dir / ".dockerfile-hash"
    fail = {"NERDCTL_FAIL_LANG_LAYER": "1"}
    r = _generate_dockerfile(tmp_path, user_repo, state_dir, fail)
    assert r.returncode == 0, r.stderr
    assert "falling back to apt-only bake" in r.stderr, r.stderr
    assert "for this run" not in r.stderr, r.stderr
    assert f"remove {hash_file}" in r.stderr, r.stderr
    builds = _take_builds(tmp_path)
    assert len(builds) == 3, builds
    df = (user_repo / ".leerie" / "Dockerfile").read_text()
    assert "COPY " not in df and "apt-get install" in df and " jq " in df, df
    key = hash_file.read_text()
    assert re.fullmatch(r"\S+ sha256:\S+ apt-only\n", key), key
    for _ in range(2):
        r = _generate_dockerfile(tmp_path, user_repo, state_dir,
                                 {**fail, "NERDCTL_INSPECT_RC": "0"})
        assert r.returncode == 0, r.stderr
        assert "up-to-date (" in r.stderr and "skipping build" in r.stderr, \
            r.stderr
        assert "image is the apt-only fallback" in r.stderr, r.stderr
        assert f"remove {hash_file}" in r.stderr, r.stderr
        assert _take_builds(tmp_path) == []
        assert hash_file.read_text() == key
    assert "COPY " in (user_repo / ".leerie" / "Dockerfile").read_text()



def test_save_load_uses_tempfile_and_logs_stderr_on_failure(tmp_path):
    """ensure_base_in_worker_ns must save/load through temp files (not a
    bare `nerdctl save | nerdctl load` pipe) so a failure's stderr survives,
    and must log that captured stderr via remote_log on failure. Falsify by
    reverting to the piped, stderr-discarding form."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # A nerdctl stub whose `save` subcommand prints a diagnostic to stderr
    # and fails, simulating a real save failure.
    body = r"""
nerdctl() {
  echo "$*" >> "$NERDCTL_LOG"
  local ns=""
  if [ "${1:-}" = "--namespace" ]; then ns="${2:-}"; shift 2 || true; fi
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image) [ "$ns" = "default" ] && return 1; _stub_image_inspect "$@" && return 0; return "${NERDCTL_INSPECT_RC:-0}" ;;
    save) echo "nerdctl-save-failure: disk quota exceeded" >&2; return 1 ;;
    build) return "${NERDCTL_BUILD_RC:-0}" ;;
    *) return 0 ;;
  esac
}
leerie_buildkit_resolve
ensure_base_in_worker_ns
"""
    result = _run_harness(
        tmp_path,
        body,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    log_content = (tmp_path / "nerdctl.log").read_text()
    # A file-based save is used (an `-o <path>` output flag), not a bare pipe.
    save_lines = [l for l in log_content.splitlines() if l.startswith("--namespace leerie save ")]
    assert save_lines, f"no save call found:\n{log_content}"
    assert " -o " in save_lines[0], save_lines[0]
    # load must never be reached — save failed, so the `&&` guard short-circuits.
    assert not any(l.startswith("--namespace default load") or "load -i" in l
                    for l in log_content.splitlines()), log_content

    # The captured stderr diagnostic must be surfaced via remote_log.
    assert "nerdctl-save-failure: disk quota exceeded" in result.stderr, result.stderr
# must point at /opt/bundle only when the Dockerfile actually baked there,
# else keep the pre-existing cache-mount fallback path.
# ---------------------------------------------------------------------------

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
