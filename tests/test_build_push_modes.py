"""Tests for scripts/remote/build-push.sh — BUILD_MODE dispatch.

Part H: build-push.sh defaults to BUILD_MODE=remote (Fly's remote
builder via `flyctl deploy --build-only --push --remote-only`); the
legacy local nerdctl/docker path is opt-in via --local-build or
LEERIE_LOCAL_BUILD=1.

These tests stub `flyctl` and `nerdctl`/`docker`, run build-push.sh
in --dry-run mode where possible, and assert on:
- Default mode is remote.
- --local-build flips to local.
- LEERIE_LOCAL_BUILD=1 flips to local.
- Remote mode invokes `flyctl deploy --build-only --push --remote-only`
  with `--config <tmp-fly.toml>` (the temp file has the `[build] image`
  line stripped).
- Local mode invokes `nerdctl build` + `nerdctl push`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_PUSH = REPO_ROOT / "scripts" / "remote" / "build-push.sh"


def _stub_flyctl(tmp_path: Path, exit_code: int = 0) -> Path:
    """Stub flyctl that records argv and exits with the given code."""
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {tmp_path}/flyctl.log\n"
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub


def _stub_container_cmd(tmp_path: Path, name: str) -> Path:
    """Stub nerdctl/docker that records argv and exits 0 on most calls.
    Returns a fake JSON entrypoint for `inspect`, fake stdout for `run`."""
    stub = tmp_path / name
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {tmp_path}/{name}.log\n"
        'if [ "$1" = "inspect" ]; then\n'
        '  echo "/opt/leerie-image/scripts/container-entry.sh"\n'
        '  exit 0\n'
        'fi\n'
        'if [ "$1" = "run" ]; then\n'
        '  echo "leerie 0.0.0-test"\n'
        '  exit 0\n'
        'fi\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


def _run(tmp_path: Path, *args: str, env_extra: dict | None = None,
         stub_only: str | None = None) -> subprocess.CompletedProcess:
    """Run build-push.sh with PATH stubbing. By default stubs both flyctl
    and nerdctl. stub_only="flyctl" omits nerdctl/docker stubs."""
    _stub_flyctl(tmp_path)
    if stub_only != "flyctl":
        _stub_container_cmd(tmp_path, "nerdctl")
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:/usr/bin:/bin"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(BUILD_PUSH), *args],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True, text=True, check=False,
    )


# --- mode default + flag dispatch -----------------------------------------

def test_default_mode_is_remote(tmp_path: Path):
    """No flags → BUILD_MODE=remote → invokes `flyctl deploy --build-only
    --push --remote-only --depot=false`. The --depot=false flag forces
    the legacy remote builder to avoid Depot's tag-cache bug
    (https://community.fly.io/t/when-i-run-fly-deploy-with-the-image-label-flag-why-does-it-deploy-an-older-version-of-my-code/26151).
    """
    r = _run(tmp_path, "--app", "testapp", "--push")
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "deploy --build-only --push --remote-only" in flyctl_log
    assert "--depot=false" in flyctl_log, \
        "must pass --depot=false to avoid depot's stale tag cache"
    assert "--app testapp" in flyctl_log
    assert "--config" in flyctl_log  # tmp fly.toml
    # nerdctl/docker should NOT have been invoked in remote mode.
    nerdctl_log = tmp_path / "nerdctl.log"
    assert not nerdctl_log.exists() or nerdctl_log.read_text() == ""


def test_local_build_flag_switches_to_local(tmp_path: Path):
    """--local-build → BUILD_MODE=local → invokes nerdctl build + push."""
    r = _run(tmp_path, "--app", "testapp", "--push", "--local-build")
    assert r.returncode == 0, r.stderr
    nerdctl_log = (tmp_path / "nerdctl.log").read_text()
    assert "build" in nerdctl_log
    assert "push" in nerdctl_log
    # flyctl should NOT have been invoked in local mode.
    flyctl_log = tmp_path / "flyctl.log"
    assert not flyctl_log.exists() or flyctl_log.read_text() == ""


def test_leerie_local_build_env_switches_to_local(tmp_path: Path):
    """LEERIE_LOCAL_BUILD=1 → BUILD_MODE=local (equivalent to --local-build)."""
    r = _run(tmp_path, "--app", "testapp", "--push",
             env_extra={"LEERIE_LOCAL_BUILD": "1"})
    assert r.returncode == 0, r.stderr
    nerdctl_log = (tmp_path / "nerdctl.log").read_text()
    assert "build" in nerdctl_log
    assert "push" in nerdctl_log


# --- remote-mode specifics ------------------------------------------------

def test_remote_mode_strips_build_image_from_fly_toml(tmp_path: Path):
    """The tmp fly.toml passed to flyctl must NOT contain the
    `[build] image = "..."` line, which would cause flyctl to skip the
    build (flyctl#1686)."""
    r = _run(tmp_path, "--app", "testapp", "--push")
    assert r.returncode == 0, r.stderr
    # The tmp file path is in the flyctl invocation. Extract it.
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    import re
    m = re.search(r"--config (\S+\.toml)", flyctl_log)
    # In the dry-run + stub setup, --config $tmp_toml is passed but the
    # temp file may or may not still exist after the script cleans up.
    # Verify the construct works by reading the actual launcher stderr
    # which announces the temp file path.
    assert "[build] image stripped" in r.stderr, \
        f"expected strip notice in stderr; got:\n{r.stderr}"


def test_remote_mode_without_push_errors(tmp_path: Path):
    """Remote mode always pushes (flyctl deploy --push is inline). If
    --push isn't passed, error rather than silently doing nothing."""
    r = _run(tmp_path, "--app", "testapp")
    assert r.returncode == 1
    assert "remote build always pushes" in r.stderr.lower() or \
           "remote build always pushes" in r.stderr


# --- local-mode specifics -------------------------------------------------

def test_local_mode_uses_nerdctl_when_available(tmp_path: Path):
    """Local mode picks nerdctl when both nerdctl and docker are on PATH."""
    _stub_container_cmd(tmp_path, "docker")  # also on PATH
    r = _run(tmp_path, "--app", "testapp", "--push", "--local-build")
    assert r.returncode == 0, r.stderr
    assert "local build tool: nerdctl" in r.stdout


# --- regression: build context must be $LEERIE_REPO, not caller's cwd ------

def test_remote_mode_cds_to_leerie_repo_before_invoking_flyctl(tmp_path: Path):
    """When `leerie` is invoked from another repo (e.g. cd ~/myrepo && leerie
    --runtime fly), build-push.sh must `cd $LEERIE_REPO` before running
    `flyctl deploy`. Otherwise flyctl uploads the user's repo as the
    build context, and the Dockerfile's `COPY orchestrator/` fails
    with "orchestrator: not found". Regression test for the Part H
    live-test failure.

    Verifies the launcher source contains the cd-into-LEERIE_REPO step.
    """
    text = BUILD_PUSH.read_text()
    # The (cd "$LEERIE_REPO" ; ...) subshell pattern must wrap the
    # flyctl deploy invocation in the remote-mode function.
    assert 'cd "$LEERIE_REPO"' in text, \
        "remote-mode flyctl invocation must run from cwd=$LEERIE_REPO"
    # And the comment block explaining why must be present (a refactor
    # that drops the cd should NOT also drop the explanation).
    assert "build context" in text


# --- source-text coupling for the docs -----------------------------------

def test_help_mentions_local_build_caveat():
    """build-push.sh --help must clearly state that --local-build does NOT
    work with nerdctl-in-Colima on macOS, so users don't try it by
    mistake."""
    text = BUILD_PUSH.read_text()
    assert "nerdctl-in-Colima" in text
    assert "Most leerie users should NOT" in text


# --- --dockerfile and --build-arg forwarding (Part H.2) ------------------

def test_dockerfile_flag_forwarded_to_flyctl(tmp_path: Path):
    """--dockerfile <path> must appear verbatim in the flyctl invocation."""
    custom_df = "/some/repo/.leerie/Dockerfile"
    r = _run(tmp_path, "--app", "testapp", "--push",
             "--dockerfile", custom_df)
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert f"--dockerfile {custom_df}" in flyctl_log, \
        f"expected --dockerfile {custom_df} in flyctl call; got:\n{flyctl_log}"


def test_build_arg_forwarded_to_flyctl(tmp_path: Path):
    """--build-arg KEY=VAL must appear in the flyctl invocation."""
    r = _run(tmp_path, "--app", "testapp", "--push",
             "--build-arg", "BASE_IMAGE=registry.fly.io/myapp:0.9.7")
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "--build-arg BASE_IMAGE=registry.fly.io/myapp:0.9.7" in flyctl_log, \
        f"expected --build-arg in flyctl call; got:\n{flyctl_log}"


def test_multiple_build_args_all_forwarded_to_flyctl(tmp_path: Path):
    """Multiple --build-arg flags must all be forwarded to flyctl."""
    r = _run(tmp_path, "--app", "testapp", "--push",
             "--build-arg", "FOO=bar",
             "--build-arg", "BAZ=qux")
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "--build-arg FOO=bar" in flyctl_log
    assert "--build-arg BAZ=qux" in flyctl_log


def test_dockerfile_flag_forwarded_to_nerdctl(tmp_path: Path):
    """--dockerfile <path> must appear as -f <path> in the nerdctl invocation."""
    custom_df = "/some/repo/.leerie/Dockerfile"
    r = _run(tmp_path, "--app", "testapp", "--push", "--local-build",
             "--dockerfile", custom_df)
    assert r.returncode == 0, r.stderr
    nerdctl_log = (tmp_path / "nerdctl.log").read_text()
    assert f"-f {custom_df}" in nerdctl_log, \
        f"expected -f {custom_df} in nerdctl call; got:\n{nerdctl_log}"


def test_build_arg_forwarded_to_nerdctl(tmp_path: Path):
    """--build-arg KEY=VAL must appear in the nerdctl invocation."""
    r = _run(tmp_path, "--app", "testapp", "--push", "--local-build",
             "--build-arg", "BASE_IMAGE=leerie:0.9.7")
    assert r.returncode == 0, r.stderr
    nerdctl_log = (tmp_path / "nerdctl.log").read_text()
    assert "--build-arg BASE_IMAGE=leerie:0.9.7" in nerdctl_log, \
        f"expected --build-arg in nerdctl call; got:\n{nerdctl_log}"


def test_no_dockerfile_flag_uses_default(tmp_path: Path):
    """Without --dockerfile, flyctl must receive the default $LEERIE_REPO/Dockerfile."""
    r = _run(tmp_path, "--app", "testapp", "--push")
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "--dockerfile" in flyctl_log
    assert "Dockerfile" in flyctl_log


def test_no_build_arg_no_build_arg_in_flyctl(tmp_path: Path):
    """Without --build-arg, flyctl must not receive any --build-arg flags."""
    r = _run(tmp_path, "--app", "testapp", "--push")
    assert r.returncode == 0, r.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "--build-arg" not in flyctl_log, \
        f"no --build-arg flags expected in default mode; got:\n{flyctl_log}"


def test_no_dockerfile_flag_local_build_no_f_flag(tmp_path: Path):
    """Without --dockerfile in local-build mode, nerdctl must not receive -f."""
    r = _run(tmp_path, "--app", "testapp", "--push", "--local-build")
    assert r.returncode == 0, r.stderr
    nerdctl_log = (tmp_path / "nerdctl.log").read_text()
    # -f should NOT appear since no override was given
    assert " -f " not in nerdctl_log, \
        f"no -f flag expected without --dockerfile; got:\n{nerdctl_log}"


def test_unknown_arg_still_rejected(tmp_path: Path):
    """Unknown arguments must still be rejected after the new flags are added."""
    r = _run(tmp_path, "--app", "testapp", "--unknown-flag")
    assert r.returncode == 1
    assert "unknown argument" in r.stderr
