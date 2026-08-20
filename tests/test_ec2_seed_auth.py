"""Tests for scripts/remote/ec2-seed-auth.sh.

EC2 counterpart to tests/test_seed_auth_sh.py — same payload-level
assertions (STAGE round-trip, plugins/cache + plugins/marketplaces
exclusion, token fallback, git identity), exercised against a stubbed
EC2 transport (`aws` for ec2_remote_exec's SSM path, `ssh` for
ec2_tar_pipe's bulk-data path) instead of a stubbed `flyctl`.

The stub `aws` decodes and actually executes the base64-wrapped command
ec2_remote_exec sends via `--parameters command=[...]` (mirroring
test_ec2_transport.py's `_stub_aws_ssm` / test_ec2_seed_repo.py's
`_make_stub_aws`), with any `/home/leerie` path in the executed command
rewritten to point inside the test's `dest` directory so the resulting
tree on disk is real and inspectable.

The stub `ssh` drains stdin (a gzipped tar of $STAGE, per
ec2_tar_pipe's contract) into the rewritten dest directory when invoked
by ec2_tar_pipe.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests import ec2_stub
from tests.conftest import run_bash
from tests.stub_helpers import _make_stub_timeout
from tests.stub_helpers import _stub_timeout as _make_killing_stub_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_SEED_AUTH_SH = REPO_ROOT / "scripts" / "remote" / "ec2-seed-auth.sh"
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"


def _make_stub_aws(stub_path: Path, exec_log: Path, dest_dir: Path,
                    chown_log: Path | None = None) -> None:
    """Stub `aws` rewriting /home/leerie to dest_dir. `chown -R leerie:`
    /`chown leerie:`/`runuser -u leerie --` calls are replaced with a
    logging no-op (rather than silently swallowed) when `chown_log` is
    given, so tests can assert the real script actually issued the
    ownership fix rather than merely grepping its source. See
    tests/ec2_stub.py::make_ssm_stub_aws for the shared mechanics.
    """
    ec2_stub.make_ssm_stub_aws(
        stub_path,
        exec_log,
        dest_dir,
        {"/home/leerie": "$DEST/home/leerie"},
        mkdir_subdir="home/leerie",
        chown_log=chown_log,
        swallow_runuser=True,
    )


def _make_stub_ssh(stub_path: Path, exec_log: Path, dest_dir: Path) -> None:
    """Stub `ssh` used as ec2_tar_pipe's transport: extracts a gzipped
    tar from stdin into the rewritten dest dir.

    ec2_tar_pipe passes its whole `sh -c '...'` receiver as a SINGLE
    argv element — see ec2-lib.sh's ec2_tar_pipe body. This stub
    matches that shape (same technique as test_ec2_seed_repo.py's
    `_make_stub_ssh`). See tests/ec2_stub.py::make_ssm_stub_ssh for the
    shared mechanics.
    """
    ec2_stub.make_ssm_stub_ssh(
        stub_path,
        exec_log,
        dest_dir,
        {"/home/leerie": "$DEST/home/leerie"},
    )


def _make_stub_git(stub_path: Path, *, name: str = "Test User",
                    email: str = "test@example.com") -> None:
    stub_path.write_text(
        f"""#!/usr/bin/env bash
if [ "$1" = 'config' ] && [ "$2" = 'user.name' ]; then echo '{name}'; exit 0; fi
if [ "$1" = 'config' ] && [ "$2" = 'user.email' ]; then echo '{email}'; exit 0; fi
exit 0
"""
    )
    stub_path.chmod(0o755)


def _base_env(tmp_path: Path, stage: Path, **extra: str) -> dict:
    env = {
        "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
        "STAGE": str(stage),
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "PATH": f"{tmp_path}:/usr/bin:/bin",
    }
    env.update(extra)
    return env


def test_ec2_seed_auth_sh_exists():
    assert EC2_SEED_AUTH_SH.exists(), "scripts/remote/ec2-seed-auth.sh is missing"


def test_ec2_seed_auth_sh_is_executable():
    assert os.access(EC2_SEED_AUTH_SH, os.X_OK), (
        "scripts/remote/ec2-seed-auth.sh is not executable"
    )


def test_ec2_seed_auth_fails_without_instance_id(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env={
            "LEERIE_EC2_INSTANCE_ID": "",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "STAGE": str(stage),
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_INSTANCE_ID" in result.stderr


def test_ec2_seed_auth_fails_without_ssh_target(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "",
            "STAGE": str(stage),
        },
    )
    assert result.returncode != 0
    assert "LEERIE_EC2_SSH_TARGET" in result.stderr


def test_ec2_seed_auth_fails_without_stage(tmp_path):
    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "STAGE": "",
        },
    )
    assert result.returncode != 0
    assert "STAGE" in result.stderr


def test_ec2_seed_auth_fails_when_aws_missing(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env={
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
            "STAGE": str(stage),
            "PATH": "/usr/bin:/bin",  # no aws here
        },
    )
    assert result.returncode != 0
    assert "aws" in result.stderr.lower()


def test_ec2_seed_auth_fails_without_credentials_or_token(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    # .credentials.json absent; CLAUDE_CODE_OAUTH_TOKEN unset

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode != 0
    assert "credentials" in result.stderr.lower() or "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr


def test_ec2_seed_auth_fails_without_git_identity(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    # stub git: returns 1 for user.name / user.email (not set)
    fake_git = tmp_path / "git"
    fake_git.write_text("#!/usr/bin/env bash\nexit 1\n")
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode != 0
    assert "user.name" in result.stderr or "user.email" in result.stderr


def test_ec2_seed_auth_succeeds_with_credentials_file(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')
    (stage / ".claude.json").write_text('{"projects":{}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "ec2_seed_auth complete" in result.stderr

    home = dest / "home" / "leerie"
    assert (home / ".claude" / ".credentials.json").exists()
    assert (home / ".claude.json").exists()


def test_ec2_seed_auth_uses_token_fallback_when_no_credentials_file(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    # No .credentials.json in $STAGE

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage, CLAUDE_CODE_OAUTH_TOKEN="my-oauth-token"),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.stderr
    assert "ec2_seed_auth complete" in result.stderr

    creds_path = dest / "home" / "leerie" / ".claude" / ".credentials.json"
    assert creds_path.exists(), f"credentials file not written; landed: {list((dest/'home'/'leerie').rglob('*'))}"
    import json
    payload = json.loads(creds_path.read_text())
    # The scopes field is mandatory — CLI 2.1.210's file-auth path rejects a
    # scope-less blob with "Not logged in" (must match leerie's synthesized
    # shape in _extract_claude_credentials_json).
    assert payload == {
        "claudeAiOauth": {"accessToken": "my-oauth-token", "scopes": ["user:inference"]}
    }


def test_ec2_seed_auth_plugin_cache_and_marketplaces_not_re_excluded_by_tar(tmp_path):
    """seed-auth.sh (the Fly original) does not itself exclude
    plugins/cache or plugins/marketplaces in its tar command — those
    are excluded upstream by the launcher's $STAGE assembly (CLAUDE_SKIP,
    documented in IMPLEMENTATION.md and seed-auth.sh's own header) before
    seed_auth ever runs, and rebuilt afterward in step 4. ec2_seed_auth
    is a byte-identical port of that payload logic, so if $STAGE (the
    test's stand-in for a post-CLAUDE_SKIP stage) happens to contain
    those dirs, the tar step delivers them uncritically — proving the
    tar-exclude list was ported verbatim rather than gaining a new
    exclusion this script was never asked to own."""
    stage = tmp_path / "stage"
    (stage / ".claude" / "plugins" / "cache").mkdir(parents=True)
    (stage / ".claude" / "plugins" / "cache" / "big-blob.bin").write_text("x" * 100)
    (stage / ".claude" / "plugins" / "marketplaces").mkdir(parents=True)
    (stage / ".claude" / "plugins" / "marketplaces" / "mkt.json").write_text("{}")
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    home = dest / "home" / "leerie"
    assert (home / ".claude" / "plugins" / "cache" / "big-blob.bin").exists(), (
        "ec2-seed-auth.sh's tar step must match seed-auth.sh's exclude "
        "list exactly (no plugins/cache exclusion of its own — that's "
        "the launcher's $STAGE-assembly responsibility)"
    )
    assert (home / ".claude" / "plugins" / "marketplaces" / "mkt.json").exists()


def test_ec2_seed_auth_tar_exclude_list_matches_fly_original(tmp_path):
    """Both seed-auth.sh (Fly) and ec2-seed-auth.sh (EC2) must invoke the
    single shared `_seed_auth_tar_excludes` helper in seed-common.sh
    rather than each hard-coding the exclude flags — the drift risk this
    test used to catch by comparing two literal lists is now removed by
    construction (one definition site)."""
    fly_src = (REPO_ROOT / "scripts" / "remote" / "seed-auth.sh").read_text()
    ec2_src = EC2_SEED_AUTH_SH.read_text()
    seed_common_src = (REPO_ROOT / "scripts" / "remote" / "seed-common.sh").read_text()

    assert "_seed_auth_tar_excludes() {" in seed_common_src, (
        "seed-common.sh must define the single-owner _seed_auth_tar_excludes helper"
    )
    assert "$(_seed_auth_tar_excludes)" in fly_src, (
        "seed-auth.sh must consume the shared _seed_auth_tar_excludes helper"
    )
    assert "$(_seed_auth_tar_excludes)" in ec2_src, (
        "ec2-seed-auth.sh must consume the shared _seed_auth_tar_excludes helper"
    )
    # Neither script may hard-code the exclude flags directly any more.
    assert "--exclude='.gitconfig'" not in fly_src
    assert "--exclude='.gitconfig'" not in ec2_src

    excludes = [
        "--exclude=.gitconfig",
        "--exclude=.gitconfig.local",
        "--exclude=.gitignore",
        "--exclude=.gitignore_global",
        "--exclude=.git-credentials",
        "--exclude=.netrc",
        "--exclude=.ssh",
        "--exclude=.gnupg",
        "--exclude=.config",
    ]
    for excl in excludes:
        assert excl in seed_common_src, (
            f"seed-common.sh's _seed_auth_tar_excludes is missing exclude flag: {excl}"
        )


def test_ec2_seed_auth_fixes_ownership_to_leerie(tmp_path):
    """The seed-auth payload must chown the delivered tree to leerie:.

    There is no leerie user in the test sandbox for a real chown to
    succeed against, so the stub replaces `chown -R leerie:` /
    `chown leerie:` with a logging no-op — this asserts the real script
    actually issued both ownership-fix calls (bulk tree + gitconfig),
    not merely that the literal string appears in the source."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    chown_log = tmp_path / "chown_log.txt"
    chown_log.write_text("")
    _make_stub_aws(tmp_path / "aws", exec_log, dest, chown_log=chown_log)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    chown_calls = chown_log.read_text().splitlines()
    assert "chown-R" in chown_calls, (
        f"chown -R leerie: /home/leerie was never issued; calls: {chown_calls}"
    )
    assert "chown" in chown_calls, (
        f"chown leerie: /home/leerie/.gitconfig was never issued; calls: {chown_calls}"
    )


def test_ec2_seed_auth_uses_ec2_transport_never_flyctl(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)
    _make_stub_git(tmp_path / "git")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log_text = exec_log.read_text()
    assert "aws " in log_text
    assert "ssh " in log_text
    assert "flyctl" not in log_text


def test_ec2_seed_auth_writes_git_identity(tmp_path):
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

    dest = tmp_path / "instance"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    _make_stub_aws(tmp_path / "aws", exec_log, dest)
    _make_stub_ssh(tmp_path / "ssh", exec_log, dest)
    _make_stub_timeout(tmp_path)

    # Real git binary would be nice, but stub is deterministic across CI —
    # use a stub that also actually executes `git config --file ...` for
    # the remote-side identity write via a real, separate git found on
    # PATH after the stub. Simplify: keep the stub for host reads and
    # rely on the remote side finding real git on rest of PATH.
    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.name' ] && [ \"$#\" -eq 2 ]; then echo 'O'\"'\"'Brien'; exit 0; fi\n"
        "if [ \"$1\" = 'config' ] && [ \"$2\" = 'user.email' ] && [ \"$#\" -eq 2 ]; then echo 'obrien@example.com'; exit 0; fi\n"
        "exec /usr/bin/git \"$@\"\n"
    )
    fake_git.chmod(0o755)

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=_base_env(tmp_path, stage),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "O'Brien" in result.stderr or "git identity set" in result.stderr

    gitconfig = dest / "home" / "leerie" / ".gitconfig"
    assert gitconfig.exists(), f"gitconfig not written; stderr={result.stderr}"
    content = gitconfig.read_text()
    assert "O'Brien" in content
    assert "obrien@example.com" in content


def test_ec2_seed_auth_transport_stall_yields_124_or_137(tmp_path):
    """A stalled ec2_tar_pipe (ssh) transport during the STAGE tar pipe
    must be killed by the timeout wrapper and produce a non-hanging
    failure — ec2_seed_auth returns non-zero rather than hanging."""
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    creds = stage / ".claude" / ".credentials.json"
    creds.write_text('{"claudeAiOauth":{"accessToken":"tok"}}')

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
    _make_stub_git(tmp_path / "git")

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

    env = _base_env(tmp_path, stage, LEERIE_SEED_TIMEOUT_S="1")

    result = run_bash(
        f"source {EC2_LIB_SH}; source {EC2_SEED_AUTH_SH}; ec2_seed_auth",
        env=env,
    )
    assert result.returncode != 0, "a stalled transport must not silently succeed"
