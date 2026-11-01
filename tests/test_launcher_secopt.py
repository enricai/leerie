"""The launcher's security options on every local `nerdctl run` (DESIGN §6
*Every confined process has its own `/tmp`, `/dev/shm` and IPC*, *A held-out
evaluation runs alone, in a world that dies with it (I3)* — *What the runtime
must allow*).

- `SECOPT`: `seccomp=unconfined` on every local run, rootless, rootful and
  Colima alike. It was rootless-only (`ROOTLESS_SECOPT`, for the entry's
  `unshare --user`); the confinement shim now makes a user namespace for
  every confined process, which the OCI default profile refuses on rootful
  (`unshare(CLONE_NEWUSER)`: EPERM, measured).
- `APPARMOR_OPT`: `apparmor=leerie-container` wherever the containers' kernel
  runs AppArmor; a missing or stale profile is installed (with a terminal) or
  named with its commands, and the launcher stops.

The block is extracted from the launcher and executed with the runtime
helpers stubbed (substance), and `scripts/runtime-install.sh`'s helpers are
executed with the kernel stubbed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.launcher_argv_extract import extract_run_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
RUNTIME_INSTALL = REPO_ROOT / "scripts" / "runtime-install.sh"
PROFILE = REPO_ROOT / "scripts" / "apparmor" / "leerie-container"


def _secopt_block() -> str:
    src = LAUNCHER.read_text()
    start = src.index("  SECOPT=(")
    end = src.index("\n  fi\n", src.index("  APPARMOR_OPT=()", start)) + 6
    return src[start:end]


def _run_block(*, has_aa: bool, current: bool, install_ok: bool = True,
               no_install: bool = False) -> subprocess.CompletedProcess:
    # stdin is never a terminal here: the path a terminal takes (install,
    # then go on) differs from this one only by the `[ -t 0 ]` test.
    script = f"""
set -euo pipefail
LEERIE_REPO=/opt/leerie
NO_RUNTIME_INSTALL={'true' if no_install else 'false'}
remote_log() {{ echo "LOG: $*" >&2; }}
runtime_has_apparmor() {{ return {0 if has_aa else 1}; }}
runtime_apparmor_current() {{ echo "CHECK $1" >&2; return {0 if current else 1}; }}
runtime_install_apparmor() {{ echo "INSTALL $1" >&2; return {0 if install_ok else 1}; }}
runtime_apparmor_hint() {{ echo "HINT $1"; }}
exec 0</dev/null
{_secopt_block()}
echo "SECOPT=${{SECOPT[*]}}"
echo "APPARMOR_OPT=${{APPARMOR_OPT[*]-}}"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


def test_seccomp_unconfined_on_every_runtime():
    """No sentinel gates it: with and without AppArmor, `SECOPT` is set."""
    for has_aa in (False, True):
        r = _run_block(has_aa=has_aa, current=True)
        assert r.returncode == 0, r.stderr
        assert "SECOPT=--security-opt seccomp=unconfined" in r.stdout
    assert "containerd-rootless/child_pid" not in _secopt_block()


def test_apparmor_profile_applied_when_loaded():
    r = _run_block(has_aa=True, current=True)
    assert r.returncode == 0, r.stderr
    assert "APPARMOR_OPT=--security-opt apparmor=leerie-container" in r.stdout
    assert "CHECK /opt/leerie/scripts/apparmor/leerie-container" in r.stderr
    assert "INSTALL" not in r.stderr


def test_no_apparmor_option_without_apparmor():
    r = _run_block(has_aa=False, current=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1] == "APPARMOR_OPT="
    assert "CHECK" not in r.stderr


def test_missing_profile_without_a_terminal_stops_naming_the_commands():
    """No terminal (plugin mode): nothing is installed, the launcher exits 1
    printing the load commands; it never runs a container without it."""
    r = _run_block(has_aa=True, current=False)
    assert r.returncode == 1
    assert "INSTALL" not in r.stderr
    assert "HINT /opt/leerie/scripts/apparmor/leerie-container" in r.stderr
    assert "APPARMOR_OPT=" not in r.stdout


def test_missing_profile_with_no_runtime_install_stops():
    r = _run_block(has_aa=True, current=False, no_install=True)
    assert r.returncode == 1
    assert "INSTALL" not in r.stderr


def test_both_options_expanded_in_the_run_argv():
    argv = extract_run_argv()
    assert '${SECOPT[@]+"${SECOPT[@]}"}' in argv
    assert '${APPARMOR_OPT[@]+"${APPARMOR_OPT[@]}"}' in argv
    assert "ROOTLESS_SECOPT" not in LAUNCHER.read_text()


def _helpers(body: str, *, kernel: str) -> subprocess.CompletedProcess:
    """Run `body` after sourcing runtime-install.sh, with the containers'
    kernel stubbed: `_runtime_on_kernel` answers from `kernel` (a shell
    function body)."""
    script = f"""
set -uo pipefail
. {RUNTIME_INSTALL}
_runtime_on_kernel() {{ {kernel} }}
{body}
"""
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30)


def test_runtime_has_apparmor_reads_the_lsm_list():
    on = _helpers("runtime_has_apparmor && echo YES || echo NO",
                  kernel='echo "lockdown,capability,landlock,apparmor";')
    off = _helpers("runtime_has_apparmor && echo YES || echo NO",
                   kernel='echo "lockdown,capability,landlock,selinux";')
    assert on.stdout.strip() == "YES" and off.stdout.strip() == "NO"


def test_runtime_apparmor_current_needs_loaded_and_identical(tmp_path):
    stale = tmp_path / "stale"
    stale.write_text("old profile\n")
    kernel = (f'case "$1" in sh) echo nerdctl-default; echo "$LOADED";; '
              f'cat) cat "$INSTALLED";; esac;')
    for loaded, installed, want in (
            ("leerie-container", str(PROFILE), "YES"),
            ("other", str(PROFILE), "NO"),
            ("leerie-container", str(stale), "NO")):
        r = _helpers(f'LOADED={loaded} INSTALLED={installed}; '
                     f'runtime_apparmor_current {PROFILE} && echo YES '
                     f'|| echo NO', kernel=kernel)
        assert r.stdout.strip() == want, (loaded, installed, r.stderr)


def test_install_runs_install_then_parser_in_that_order():
    r = _helpers(f"DRY_RUN=true; runtime_install_apparmor {PROFILE}",
                 kernel=":;")
    assert r.returncode == 0, r.stderr
    lines = [ln.strip() for ln in r.stdout.splitlines() if "$ " in ln]
    assert lines == [
        f"$ sudo install -m 0644 {PROFILE} /etc/apparmor.d/leerie-container",
        "$ sudo apparmor_parser -r -W /etc/apparmor.d/leerie-container"]


def test_install_refuses_a_missing_profile(tmp_path):
    r = _helpers(f"DRY_RUN=true; runtime_install_apparmor {tmp_path}/none",
                 kernel=":;")
    assert r.returncode == 1
    assert "missing" in r.stderr


def _parser() -> str | None:
    import shutil
    return shutil.which("apparmor_parser") or (
        "/usr/sbin/apparmor_parser"
        if Path("/usr/sbin/apparmor_parser").exists() else None)


def test_profile_compiles():
    """The profile the install step loads is valid policy (compiled, not
    loaded: `-Q` skips the kernel, so no root is needed)."""
    parser = _parser()
    if parser is None:
        import pytest
        pytest.skip("apparmor_parser is not installed")
    r = subprocess.run([parser, "-Q", "-K", str(PROFILE)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_profile_allows_exactly_the_shims_mounts():
    """Each mount the shim makes has its rule, by type and target, and the
    profile keeps nerdctl-default's denials (substance: the rules, read as
    lines, against the shim's own mount targets)."""
    rules = {ln.strip() for ln in PROFILE.read_text().splitlines()}
    for rule in (
            "userns,", "pivot_root,", "remount /,", "remount /**,",
            "mount options=(rw, rprivate) -> /,",
            "mount fstype=tmpfs options=(rw, nosuid, nodev) tmpfs -> /tmp/,",
            "mount fstype=tmpfs options=(rw, nosuid, nodev) tmpfs -> "
            "/dev/shm/,",
            "mount fstype=devpts options=(rw, nosuid, noexec) devpts -> "
            "/dev/pts/,",
            "mount fstype=overlay overlay -> /leerie-state/**,",
            "deny /sys/kernel/security/** rwklx,",
            "deny @{PROC}/kcore rwklx,"):
        assert rule in rules, rule
    assert "profile leerie-container flags=(attach_disconnected," \
           "mediate_deleted) {" in rules
    assert not any(r.startswith("mount,") or r == "mount," for r in rules)
