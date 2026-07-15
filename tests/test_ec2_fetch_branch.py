"""Tests for scripts/remote/ec2-fetch-branch.sh.

EC2 counterpart to tests/test_fetch_branch_sh.py +
tests/test_fetch_branch_leerie_streamback.py — same scenario coverage
(run discovery, git-bundle stream-back, run-state tar stream-back,
best-effort .leerie/config.toml + Dockerfile stream-back, no_push
stripping), exercised against a stubbed EC2 transport modeled on
tests/test_ec2_seed_repo.py's `aws` (ec2_remote_exec's SSM path) /
`ssh` (ec2_tar_pipe's bulk-data path, and this file's raw
download-direction ssh calls) harness instead of a stubbed `flyctl`.

The instance-side git repo (standing in for /work on the instance) and
the instance-side .leerie/ tree are real directories on disk; the stub
`aws`/`ssh` rewrite /work and /work/.leerie/... path references in the
commands ec2-fetch-branch.sh sends to point at those directories, so the
resulting host-side state after fetch_state_ec2 is real and inspectable.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_FETCH_SH = REPO_ROOT / "scripts" / "remote" / "ec2-fetch-branch.sh"
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"


def _run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _make_stub_aws(stub_path: Path, exec_log: Path, instance_work: Path) -> None:
    """Stub `aws` that decodes+executes ec2_remote_exec's base64-wrapped
    command locally against instance_work (standing in for /work on the
    instance), re-encoding the real exit code as the rc sentinel
    ec2_remote_exec expects while itself always exiting 0 (mirroring the
    real SSM session-manager-plugin's documented always-exit-0 behavior).
    Modeled on tests/test_ec2_seed_repo.py's _make_stub_aws.
    """
    dest = str(instance_work.parent.resolve())
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "aws $*" >> {exec_log}

DEST={dest!r}

param=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--parameters" ]; then
    param="$arg"
  fi
  prev="$arg"
done

if [ -z "$param" ]; then
  exit 0
fi

inner="${{param#command=[\\"}}"
inner="${{inner%\\"]}}"
b64="${{inner#echo }}"
b64="${{b64%% | base64*}}"
decoded="$(printf '%s' "$b64" | base64 -d)"

decoded="${{decoded//\\/work/$DEST/work}}"

bash -c "$decoded"
exit 0
"""
    )
    stub_path.chmod(0o755)


def _make_stub_ssh(stub_path: Path, exec_log: Path, instance_work: Path) -> None:
    """Stub `ssh` for both ec2-fetch-branch.sh's raw download calls
    (`ssh <target> "<cmd>"` — a single command-string argv element,
    stdout streamed straight back) and, defensively, ec2_tar_pipe's
    upload `sh -c '...'` receiver shape (unused by fetch-branch.sh
    itself but kept for parity with the seed-repo stub in case a shared
    helper is exercised).
    """
    dest = str(instance_work.parent.resolve())
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "ssh $*" >> {exec_log}
DEST={dest!r}

args=("$@")
i=0
while [ $i -lt ${{#args[@]}} ]; do
  case "${{args[$i]}}" in
    -o|-l) i=$((i+2)); continue ;;
    -*) i=$((i+1)); continue ;;
    *) break ;;
  esac
done
target="${{args[$i]}}"
rest=("${{args[@]:$((i+1))}}")

if [ "${{#rest[@]}}" -eq 0 ]; then
  cat > /dev/null
  exit 0
fi

case "${{rest[0]}}" in
  sh*)
    remote_cmd="${{rest[0]}}"
    remote_cmd="${{remote_cmd//\\/work/$DEST/work}}"
    eval "$remote_cmd"
    exit $?
    ;;
  *)
    # Raw command form: ssh <target> "<cmd>" — a single argv element
    # containing the whole remote command (git bundle create, tar -cC,
    # test -f, cat). Rewrite /work path references and eval, streaming
    # this stub's own stdout straight back (binary-safe: no command
    # substitution in this path).
    remote_cmd="${{rest[*]}}"
    remote_cmd="${{remote_cmd//\\/work/$DEST/work}}"
    eval "$remote_cmd"
    exit $?
    ;;
esac
"""
    )
    stub_path.chmod(0o755)


def _make_stub_timeout(stub_dir: Path) -> None:
    stub = stub_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
while [[ "$1" == --* ]]; do shift; done
shift
exec "$@"
"""
    )
    stub.chmod(0o755)


def _make_git_repo(tmp_path: Path, subdir: str = "myrepo") -> Path:
    repo = tmp_path / subdir
    repo.mkdir()
    for cmd in [
        ["git", "-C", str(repo), "init"],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "T"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


def _base_env(tmp_path: Path, repo: Path) -> dict:
    return {
        "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
        "USER_REPO": str(repo),
        "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
    }


def _setup_instance(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (instance_root, instance_work) — instance_work stands in
    for /work on the instance; instance_root is its parent (used for
    path rewriting in the stubs)."""
    instance_root = tmp_path / "instance"
    instance_work = instance_root / "work"
    instance_work.mkdir(parents=True)
    return instance_root, instance_work


def _init_instance_repo_with_run(
    instance_work: Path, run_id: str, run_branch: str,
    no_push: bool = False,
) -> None:
    subprocess.run(["git", "-C", str(instance_work), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(instance_work), "config", "user.email", "t@t.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(instance_work), "config", "user.name", "T"],
        check=True, capture_output=True,
    )
    (instance_work / "README.md").write_text("hello from instance")
    subprocess.run(["git", "-C", str(instance_work), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(instance_work), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    (instance_work / "extra.txt").write_text("run work")
    subprocess.run(["git", "-C", str(instance_work), "add", "extra.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(instance_work), "commit", "-m", "run commit"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(instance_work), "branch", run_branch],
        check=True, capture_output=True,
    )

    runs_dir = instance_work / ".leerie" / "runs" / run_id
    runs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-01-01T00:00:00Z",
        "branch": run_branch,
        "working_branch": "main",
        **({"no_push": True} if no_push else {}),
    }))
    (runs_dir / "state.json").write_text(json.dumps({"task": "test task"}))


def test_ec2_fetch_branch_sh_exists():
    assert EC2_FETCH_SH.exists(), "scripts/remote/ec2-fetch-branch.sh is missing"


def test_ec2_fetch_branch_sh_is_executable():
    assert os.access(EC2_FETCH_SH, os.X_OK), (
        "scripts/remote/ec2-fetch-branch.sh is not executable"
    )


def test_ec2_fetch_branch_defines_fetch_state_ec2():
    src = EC2_FETCH_SH.read_text()
    assert "fetch_state_ec2()" in src


def test_fetch_state_ec2_fails_without_instance_id():
    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env={
            "LEERIE_EC2_INSTANCE_ID": "",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": "/tmp",
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_INSTANCE_ID" in result.stderr


def test_fetch_state_ec2_fails_without_ssh_target():
    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "",
            "USER_REPO": "/tmp",
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_SSH_TARGET" in result.stderr


def test_fetch_state_ec2_fails_without_user_repo():
    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": "",
        },
    )
    assert result.returncode != 0
    assert "USER_REPO" in result.stderr


def test_fetch_state_ec2_fails_when_aws_missing(tmp_path):
    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": str(tmp_path),
            "PATH": "/usr/bin:/bin",  # no aws here
        },
    )
    assert result.returncode != 0
    assert "aws" in result.stderr.lower()


def test_fetch_state_ec2_streams_bundle_and_state(tmp_path):
    """The run branch committed on the instance round-trips to the host
    as a fetchable bundle whose tip matches the instance-side tip, and
    the run-state tar extracts under the host repo's .leerie/runs/."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-test-abc123"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)

    instance_tip = subprocess.run(
        ["git", "-C", str(instance_work), "rev-parse", run_branch],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_tip = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", run_branch],
        capture_output=True, text=True,
    )
    assert host_tip.returncode == 0, (
        f"run branch {run_branch} not fetched to host repo. stderr:\n{result.stderr}"
    )
    assert host_tip.stdout.strip() == instance_tip, (
        "host-side branch tip does not match instance-side tip"
    )

    host_run_dir = repo / ".leerie" / "runs" / run_id
    assert host_run_dir.exists(), f"host run dir not found: {host_run_dir}"
    assert (host_run_dir / "run.json").exists()
    assert (host_run_dir / "state.json").exists()


def test_fetch_state_ec2_uses_leerie_state_host_dir(tmp_path):
    """Run-state tar extracts under LEERIE_STATE_HOST_DIR when set."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-hostdir-001"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    custom_host_dir = tmp_path / "custom_leerie_state"
    custom_host_dir.mkdir()

    env = _base_env(tmp_path, repo)
    env["LEERIE_STATE_HOST_DIR"] = str(custom_host_dir)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=env,
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_run_dir = custom_host_dir / "runs" / run_id
    assert host_run_dir.exists(), f"run state not extracted under LEERIE_STATE_HOST_DIR: {host_run_dir}"
    assert (host_run_dir / "run.json").exists()
    assert not (repo / ".leerie" / "runs" / run_id).exists(), (
        "run state must not also land under USER_REPO/.leerie when "
        "LEERIE_STATE_HOST_DIR is set"
    )


def test_fetch_state_ec2_strips_no_push_when_branch_present(tmp_path):
    """Defense-in-depth stripper fires when a branch was actually
    fetched (mirrors fetch-branch.sh's positive-control test)."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-stripper-001"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch, no_push=True)

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_run_dir = repo / ".leerie" / "runs" / run_id
    host_rj = json.loads((host_run_dir / "run.json").read_text())
    assert "no_push" not in host_rj, (
        "stripper must remove no_push=true on the branch-present path"
    )


def test_fetch_state_ec2_skips_bundle_and_preserves_no_push_when_branch_missing(tmp_path):
    """Cleared-but-empty terminal-state run (DESIGN §8): no run branch
    was materialized on the instance. fetch_state_ec2 must skip the
    bundle step, still stream the state dir, and preserve
    no_push=true intent (not strip it)."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)
    subprocess.run(["git", "-C", str(instance_work), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(instance_work), "config", "user.email", "t@t.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(instance_work), "config", "user.name", "T"],
        check=True, capture_output=True,
    )
    (instance_work / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(instance_work), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(instance_work), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    # Deliberately no run branch created — the no-work scenario.

    run_id = "bugfix-ec2-already-done-cafe42"
    run_branch = f"leerie/runs/{run_id}"
    runs_dir = instance_work / ".leerie" / "runs" / run_id
    runs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text(json.dumps({
        "finished_at": "2026-05-31T20:00:00Z",
        "no_push": True,
        "branch": run_branch,
        "working_branch": "main",
    }))
    (runs_dir / "state.json").write_text(json.dumps({
        "task": "fix already-done thing",
        "no_work_required": True,
    }))

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, (
        f"fetch_state_ec2 should return 0 for no-branch runs. stderr:\n{result.stderr}"
    )
    assert "not present on instance" in result.stderr or "skipping bundle" in result.stderr

    ls_branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", run_branch],
        capture_output=True, text=True,
    )
    assert run_branch not in ls_branches.stdout, (
        "run branch should NOT be created for a no_push/no-branch run"
    )

    host_run_dir = repo / ".leerie" / "runs" / run_id
    assert host_run_dir.exists()
    host_rj = json.loads((host_run_dir / "run.json").read_text())
    assert host_rj.get("no_push") is True, (
        "no_push=true intent must be preserved when no branch was materialized"
    )


def test_fetch_state_ec2_streams_config_and_dockerfile_to_host(tmp_path):
    """Step 4: .leerie/config.toml and .leerie/Dockerfile stream back
    when the host has neither."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-streamback-001"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)

    (instance_work / ".leerie" / "config.toml").write_text("[config]\nsetup_packages = ['curl']\n")
    (instance_work / ".leerie" / "Dockerfile").write_text("FROM debian:13\n")

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_leerie = repo / ".leerie"
    assert (host_leerie / "config.toml").exists()
    assert (host_leerie / "Dockerfile").exists()
    assert (host_leerie / "config.toml").read_text() == "[config]\nsetup_packages = ['curl']\n"
    assert (host_leerie / "Dockerfile").read_text() == "FROM debian:13\n"


def test_fetch_state_ec2_never_clobbers_existing_host_file(tmp_path):
    """Step 4 never overwrites a pre-existing host-side config.toml, but
    still writes an absent Dockerfile."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-noclobber-002"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)

    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    original_content = "# hand-edited\n[config]\nsetup_packages = ['git']\n"
    (host_leerie / "config.toml").write_text(original_content)

    (instance_work / ".leerie" / "config.toml").write_text("[config]\nsetup_packages = []\n")
    (instance_work / ".leerie" / "Dockerfile").write_text("FROM debian:13\n")

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert (host_leerie / "config.toml").read_text() == original_content, (
        "must not clobber an existing host-side config.toml"
    )
    assert (host_leerie / "Dockerfile").exists(), (
        "Dockerfile absent on host should still be written"
    )


def test_fetch_state_ec2_nonfatal_when_instance_leerie_files_absent(tmp_path):
    """Step 4 is non-fatal: fetch_state_ec2 still returns 0 when no
    .leerie/ files exist on the instance."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-nonfatal-003"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)
    # Deliberately no .leerie/config.toml or .leerie/Dockerfile on the instance.

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, (
        f"fetch_state_ec2 must return 0 even when no instance .leerie/ files exist; "
        f"stderr:\n{result.stderr}"
    )
    host_leerie = repo / ".leerie"
    assert not (host_leerie / "config.toml").exists()
    assert not (host_leerie / "Dockerfile").exists()


def test_fetch_state_ec2_uses_ec2_transport_only(tmp_path):
    """fetch_state_ec2 goes through aws (SSM) and ssh — never flyctl."""
    repo = _make_git_repo(tmp_path)
    instance_root, instance_work = _setup_instance(tmp_path)

    run_id = "feat-ec2-transport-001"
    run_branch = f"leerie/runs/{run_id}"
    _init_instance_repo_with_run(instance_work, run_id, run_branch)

    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, instance_work)
    _make_stub_ssh(tmp_path / "ssh", exec_log, instance_work)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_LIB_SH}; source {EC2_FETCH_SH}; fetch_state_ec2",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    log_text = exec_log.read_text()
    assert "aws " in log_text
    assert "ssh " in log_text
    assert "flyctl" not in log_text
