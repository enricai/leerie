"""Tests for scripts/remote/ec2-seed-repo.sh.

EC2 counterpart to tests/test_seed_repo_sh.py — same payload-level
assertions (gitignore-aware content, .leerie/ exclusion + whitelist,
NFC-safe submodule filenames), exercised against a stubbed EC2 transport
(`aws` for ec2_remote_exec's SSM path, `ssh` for ec2_tar_pipe's bulk-data
path and the dirty-delta rsync) instead of a stubbed `flyctl`.

The stub `aws` decodes and actually executes the base64-wrapped command
ec2_remote_exec sends via `--parameters command=[...]` (mirroring
test_ec2_transport.py's `_stub_aws_ssm`), with any `/work` or
`/tmp/leerie-*` path in the executed command rewritten to point inside
the test's `dest` directory — so the resulting clone tree on disk is
real and inspectable, exactly like test_seed_repo_sh.py's flyctl stub.

The stub `ssh` drains stdin to a file when invoked by ec2_tar_pipe (a
one-entry gzipped tar containing the bundle/git-tar payload — see
ec2-seed-repo.sh's `_ec2_pipe_file_via_tar`) and extracts it into the
rewritten dest directory; when invoked by rsync (`-e <wrapper> ...`) it
execs a real local `rsync --server` process so the two rsyncs can talk
to each other, with the trailing `/work` target rewritten to dest/work.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.test_ec2_transport import _stub_timeout as _make_killing_stub_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_SEED_SH = REPO_ROOT / "scripts" / "remote" / "ec2-seed-repo.sh"
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


def _make_stub_aws(stub_path: Path, exec_log: Path, dest_dir: Path) -> None:
    """Stub `aws` that decodes+executes ec2_remote_exec's base64-wrapped
    command locally, rewriting /work and /tmp/leerie-* paths to dest_dir
    first, then re-encodes the (possibly nonzero) real exit code as the
    rc sentinel ec2_remote_exec expects — while itself always exiting 0
    (mirroring the real SSM session-manager-plugin's documented
    always-exit-0 behavior, which is exactly what ec2_remote_exec's
    sentinel-recovery logic is designed to work around).
    """
    dest = str(dest_dir.resolve())
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "aws $*" >> {exec_log}

DEST={dest!r}
# /work is baked into the real AMI/image ahead of any seed; pre-create
# it here so the first `find /work -mindepth 1 ...` reset step (which
# assumes the directory already exists, same as production) succeeds.
mkdir -p "$DEST/work"

# Find the --parameters "command=[...]" argument.
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

# param looks like: command=["echo <b64> | base64 -d | bash"]
inner="${{param#command=[\\"}}"
inner="${{inner%\\"]}}"
# inner is: echo <b64> | base64 -d | bash
b64="${{inner#echo }}"
b64="${{b64%% | base64*}}"
decoded="$(printf '%s' "$b64" | base64 -d)"

# Rewrite absolute instance-side paths to land inside DEST. Basenames
# must match exactly what the ssh stub's `/tmp` -> $DEST rewrite
# produces (ec2_tar_pipe extracts into /tmp preserving each payload's
# basename, e.g. /tmp/leerie-seed.bundle, /tmp/leerie-subs/<bn>.bundle)
# — these are NOT renamed on the way in.
decoded="${{decoded//\\/tmp\\/leerie-subs/$DEST/leerie-subs}}"
decoded="${{decoded//\\/tmp\\/leerie-seed.bundle/$DEST/leerie-seed.bundle}}"
decoded="${{decoded//\\/tmp\\/leerie-seed-git.tar/$DEST/leerie-seed-git.tar}}"
decoded="${{decoded//\\/work/$DEST/work}}"
# No leerie user in the test sandbox — swallow chown.
decoded="${{decoded//chown -R leerie: /true }}"
decoded="${{decoded//chown leerie: /true }}"

bash -c "$decoded"
# SSM's own process always exits 0 regardless of the wrapped command's
# real exit status; the sentinel inside `decoded` already carried the
# real rc back over stdout, which ec2_remote_exec parses.
exit 0
"""
    )
    stub_path.chmod(0o755)


def _make_stub_ssh(stub_path: Path, exec_log: Path, dest_dir: Path) -> None:
    """Stub `ssh` used both as ec2_tar_pipe's transport (extracts a
    one-entry gzipped tar from stdin into the rewritten dest dir) and as
    rsync's `-e` transport (execs a real local `rsync --server` so the
    two rsync processes can talk, with `/work` rewritten to dest/work).

    ec2_tar_pipe passes its whole `sh -c '...'` receiver as a SINGLE
    argv element (not three separate sh/-c/<script> args, unlike a
    typical `ssh host sh -c script` invocation) — see ec2-lib.sh's
    ec2_tar_pipe body. This stub matches that shape.
    """
    dest = str(dest_dir.resolve())
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "ssh $*" >> {exec_log}
DEST={dest!r}

# Drop leading ssh flags to find the target and the remainder of argv.
# `-o <opt>` (our own ec2_tar_pipe/rsync -e invocation) and `-l <user>`
# (rsync's own -e-transport invocation always passes login name via
# `-l`, e.g. `ssh -o ... -o ... -l ec2-user 1.2.3.4 rsync --server
# ...`) both take a separate value argument — treat them the same as
# any other single-token flag, or `target`/`rest` end up off-by-one and
# the rsync* case below is never reached (the real bug this comment
# guards against: silently falling through to the drain-and-exit
# default, which leaves the sending rsync hung waiting on a protocol
# handshake that never arrives).
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
  # No remote command — this is ec2_tar_pipe's bare-target invocation
  # form. Shouldn't happen in production (ec2_tar_pipe always appends a
  # remote command), but guard defensively.
  cat > /dev/null
  exit 0
fi

case "${{rest[0]}}" in
  sh*)
    # ec2_tar_pipe passes its whole receiver as ONE argv element:
    # "sh -c 'mkdir -p '\\''<dir>'\\'' && tar -xzC '\\''<dir>'\\'''"
    # (not three separate sh/-c/<script> args) — rewrite paths in that
    # single string and eval it as-is.
    remote_cmd="${{rest[0]}}"
    remote_cmd="${{remote_cmd//\\/work/$DEST/work}}"
    remote_cmd="${{remote_cmd//\\/tmp/$DEST}}"
    eval "$remote_cmd"
    exit $?
    ;;
  rsync*)
    # rsync --server invocation. Rewrite the trailing /work/ target arg.
    # The replacement half of ${{var/pat/repl}} is NOT a regex and needs
    # no escaping: a `\/` there is a *literal backslash*, which sent the
    # transfer to a directory named `<dest>\` and left `<dest>/work`
    # empty — rsync exits 0, so the test failed with "untracked.txt
    # missing" and no error anywhere. Only the pattern half escapes.
    new_rest=()
    for a in "${{rest[@]}}"; do
      case "$a" in
        */work/*|*/work) a="${{a/\\/work/${{DEST}}/work}}" ;;
      esac
      new_rest+=("$a")
    done
    exec "${{new_rest[@]}}"
    ;;
  *)
    cat > /dev/null
    exit 0
    ;;
esac
"""
    )
    stub_path.chmod(0o755)


def _make_stub_timeout(stub_dir: Path) -> None:
    """No-op `timeout`: run the child, ignore the cap.

    Adequate for tests that merely need the binary to exist on the
    stubbed PATH (macOS ships no /usr/bin/timeout). A test that asserts
    the cap actually *fires* must use `_stub_timeout` from
    test_ec2_transport instead — this one would let a stalled stub run
    to completion.
    """
    stub = stub_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
while [[ "$1" == --* ]]; do shift; done
shift
exec "$@"
"""
    )
    stub.chmod(0o755)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )


def _base_env(tmp_path: Path, repo: Path) -> dict:
    return {
        "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
        "USER_REPO": str(repo),
        "PATH": f"{tmp_path}:/usr/bin:/bin",
    }


def test_ec2_seed_repo_sh_exists():
    assert EC2_SEED_SH.exists(), "scripts/remote/ec2-seed-repo.sh is missing"


def test_ec2_seed_repo_sh_is_executable():
    assert os.access(EC2_SEED_SH, os.X_OK), (
        "scripts/remote/ec2-seed-repo.sh is not executable"
    )


def test_ec2_seed_repo_fails_without_instance_id(tmp_path):
    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env={
            "LEERIE_EC2_INSTANCE_ID": "",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": "/tmp",
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_INSTANCE_ID" in result.stderr


def test_ec2_seed_repo_fails_without_ssh_target(tmp_path):
    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "",
            "USER_REPO": "/tmp",
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_SSH_TARGET" in result.stderr


def test_ec2_seed_repo_fails_without_user_repo(tmp_path):
    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": "",
        },
    )
    assert result.returncode != 0
    assert "USER_REPO" in result.stderr


def test_ec2_seed_repo_fails_when_aws_missing(tmp_path):
    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "USER_REPO": "/tmp",
            "PATH": "/usr/bin:/bin",  # no aws here
        },
    )
    assert result.returncode != 0
    assert "aws" in result.stderr.lower()


def test_ec2_seed_repo_succeeds_on_minimal_repo(tmp_path):
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (dest / "work" / "README.md").exists(), (
        f"clone target /work didn't materialize at {dest/'work'}; stderr={result.stderr}"
    )
    assert (dest / "work" / ".git").is_dir()


def test_ec2_seed_repo_uses_ec2_transport(tmp_path):
    """ec2_seed_repo goes through ec2_remote_exec (aws) for small
    commands and ec2_tar_pipe (ssh) for the bundle payload — never
    flyctl."""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log_text = exec_log.read_text()
    assert "aws " in log_text
    assert "ssh " in log_text
    assert "flyctl" not in log_text


def test_ec2_seed_repo_respects_gitignore_and_force_includes_claude(tmp_path):
    """ec2_seed_repo's combined bundle+rsync payload obeys .gitignore,
    drops non-whitelisted .leerie/ paths, and hard-includes the
    repo-local .claude/ directory via the rsync delta even when
    .gitignore excludes it — same contract as the Fly path
    (test_seed_repo_sh.py::test_seed_repo_respects_gitignore_and_force_includes_claude)."""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("build/\n*.log\n.claude/\n")
    (repo / "src.py").write_text("print('hi')")
    (repo / "untracked.txt").write_text("untracked")
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("artifact")
    (repo / "debug.log").write_text("noise")
    (repo / ".leerie" / "runs" / "old").mkdir(parents=True)
    (repo / ".leerie" / "runs" / "old" / "state.json").write_text("{}")
    (repo / ".claude" / "hooks").mkdir(parents=True)
    (repo / ".claude" / "settings.local.json").write_text('{"x": 1}')
    (repo / ".claude" / "hooks" / "pre-tool-use.sh").write_text("#!/bin/sh\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", ".gitignore", "src.py"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    work = dest / "work"
    landed = {
        str(p.relative_to(work))
        for p in work.rglob("*")
        if p.is_file()
    }

    assert "src.py" in landed, f"committed src.py missing; got: {landed}"
    assert "untracked.txt" in landed, (
        f"untracked.txt missing from rsync delta; got: {landed}"
    )
    assert "build/out.bin" not in landed, (
        f"gitignored build artifact leaked into seed: {landed}"
    )
    assert "debug.log" not in landed, (
        f"gitignored log file leaked into seed: {landed}"
    )
    assert not any(p.startswith(".leerie/") for p in landed), (
        f"non-whitelisted .leerie/ path leaked into seed: {landed}"
    )
    assert ".claude/settings.local.json" in landed, (
        f".claude/ should be force-included; got: {landed}"
    )
    assert ".claude/hooks/pre-tool-use.sh" in landed, (
        f".claude/hooks/* should be force-included; got: {landed}"
    )


def test_ec2_seed_repo_whitelists_leerie_config_files(tmp_path):
    """The dirty-file filter passes .leerie/config.toml,
    .leerie/Dockerfile, and .leerie/.leerie-setup.sh through but still
    drops non-whitelisted .leerie/* paths — same filter as
    test_seed_repo_sh.py::test_seed_repo_whitelists_leerie_config_files."""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    (repo / "README.md").write_text("hello")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text('[leerie]\nbuild = "make"\n')
    (repo / ".leerie" / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    (repo / ".leerie" / ".leerie-setup.sh").write_text("#!/bin/sh\npip install black\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "README.md", ".leerie"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )

    (repo / ".leerie" / "runs" / "abc").mkdir(parents=True)
    (repo / ".leerie" / "runs" / "abc" / "state.json").write_text("{}")
    (repo / ".leerie" / "config.toml").write_text('[leerie]\nbuild = "make build"\n')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env=_base_env(tmp_path, repo),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    work = dest / "work"
    landed = {
        str(p.relative_to(work))
        for p in work.rglob("*")
        if p.is_file()
    }

    assert ".leerie/config.toml" in landed
    assert ".leerie/Dockerfile" in landed
    assert ".leerie/.leerie-setup.sh" in landed
    assert ".leerie/runs/abc/state.json" not in landed


def test_ec2_seed_repo_preserves_nfc_unicode_filenames_in_submodule(tmp_path):
    """Regression coverage for the NFC->NFD bug the bundle transport
    sidesteps (see seed-repo.sh header + the Fly-path regression test)
    — the EC2 payload logic reuses the same bundle-based mechanism, so
    it must preserve NFC filenames the same way."""
    nfc_name = b"\xf0\x9f\x93\x84Plan de implementaci\xc3\xb3n.pdf"
    nfc_str = nfc_name.decode("utf-8")

    sub = tmp_path / "submodule-source"
    _init_repo(sub)
    (sub / nfc_str).write_text("fake pdf content")
    subprocess.run(["git", "-C", str(sub), "add", nfc_str], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(sub), "commit", "-m", "add pdf"],
        check=True, capture_output=True,
    )

    parent = tmp_path / "myrepo"
    _init_repo(parent)
    (parent / "README.md").write_text("parent")
    subprocess.run(["git", "-C", str(parent), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(parent), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(parent), "-c", "protocol.file.allow=always",
            "submodule", "add", str(sub), "data",
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(parent), "commit", "-m", "add submodule"],
        check=True, capture_output=True,
    )

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo",
        env=_base_env(tmp_path, parent),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    pdf_path = dest / "work" / "data" / nfc_str
    landed = sorted(
        os.fsencode(p.name)
        for p in (dest / "work" / "data").rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )
    assert pdf_path.exists(), (
        f"submodule's NFC-named PDF did not land at {pdf_path}\n"
        f"actually landed in data/: {landed}"
    )
    assert nfc_name in landed, (
        f"NFC filename bytes were not preserved.\n"
        f"expected to find: {nfc_name!r}\n"
        f"actually landed: {landed}"
    )

    porcelain = subprocess.run(
        ["git", "-C", str(dest / "work"), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert " M data" not in porcelain and "M  data" not in porcelain, (
        f"parent repo flags the submodule dirty after seed\nporcelain:\n{porcelain}"
    )


def test_ec2_seed_repo_transport_stall_yields_124_or_137(tmp_path):
    """A stalled ec2_tar_pipe (ssh) transport during the bundle pipe
    must be killed by the timeout wrapper and produce a non-hanging
    failure — ec2_seed_repo_clone returns non-zero rather than hanging."""
    repo = tmp_path / "myrepo"
    _init_repo(repo)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    # A *killing* timeout stub, not this file's no-op passthrough: the
    # point of this test is that the cap fires. It cannot rely on the
    # real GNU binary — the tests pin PATH to a controlled set and macOS
    # ships no /usr/bin/timeout, so `_seed_timeout_prefix` correctly
    # no-ops and the stalled ssh below runs its full `sleep 600`,
    # hanging the suite instead of failing.
    _make_killing_stub_timeout(tmp_path)

    # Stalled ssh: never returns. The $(_seed_timeout_prefix) wrapper
    # around ec2_tar_pipe is what's supposed to kill it.
    stub_ssh = tmp_path / "ssh"
    stub_ssh.write_text(
        f"""#!/usr/bin/env bash
echo "ssh $*" >> {exec_log}
cat >/dev/null
sleep 600
"""
    )
    stub_ssh.chmod(0o755)

    env = _base_env(tmp_path, repo)
    env["LEERIE_SEED_TIMEOUT_S"] = "1"

    result = _run_bash(
        f"source {EC2_SEED_SH}; ec2_seed_repo_clone",
        env=env,
    )
    assert result.returncode != 0, "a stalled transport must not silently succeed"
    assert result.returncode != 0
