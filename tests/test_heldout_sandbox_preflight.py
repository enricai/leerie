"""Preflight's held-out sandbox gate, `_require_heldout_sandbox`, and the
argv that carries isolation to every confined spawn (DESIGN §6 *A held-out
evaluation runs alone, in a world that dies with it (I3)* — *What the runtime
must allow*; *Every confined process has its own `/tmp`, `/dev/shm` and
IPC*).

The die messages are driven with the shim's real failure lines, measured on
2026-11-01 in the barnacle and leerie images:
- rootful, default seccomp: `unshare(CLONE_NEWUSER): EPERM`;
- rootless or rootful under `nerdctl-default`: `make / rprivate: EACCES`;
- the host's own shell (Ubuntu's user-namespace restriction):
  `write setgroups: EACCES`.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest

from tests.test_landlock_isolation import LL

# The subject is the real gate (its subprocess faked where the host cannot
# build a sandbox), not conftest's stub of it.
pytestmark = pytest.mark.real_heldout_sandbox

_PREFIX = ("leerie: cannot isolate the command, so it does not run: ")
_SUFFIX = " (DESIGN §6 *Every confined process has its own `/tmp`, " \
          "`/dev/shm` and IPC*)"


@pytest.mark.parametrize("line,fix", [
    ("unshare(CLONE_NEWUSER): EPERM", "seccomp=unconfined"),
    ("make / rprivate: EACCES", "leerie-container"),
    ("write setgroups: EACCES", "restricts unprivileged user namespaces"),
    ("no seccomp table for the architecture 'riscv64'", "architecture"),
    ("unshare(CLONE_NEWUSER): ENOSPC", "no unprivileged user namespaces"),
    ("overlay /leerie-state/runs/x/view: EINVAL", "overlay's lower layer"),
])
def test_each_measured_failure_names_its_fix(leerie, line, fix):
    assert fix in leerie._sandbox_fix(_PREFIX + line + _SUFFIX)


def test_setgroups_is_not_mistaken_for_the_profile(leerie):
    """`write setgroups: EACCES` also ends in `: EACCES`; the more specific
    rule must win (the order of `_SANDBOX_FIXES` is the contract)."""
    fix = leerie._sandbox_fix(_PREFIX + "write setgroups: EACCES" + _SUFFIX)
    assert "kernel.apparmor_restrict_unprivileged_userns" in fix
    assert "apparmor_parser" not in fix


def _fake_run(leerie, monkeypatch, *, rc: int = 0, stderr: str = "",
              report: dict | None = None, seen: list | None = None):
    real = subprocess.run

    def run(argv, *a, **kw):
        if "--sandbox" not in argv:
            return real(argv, *a, **kw)
        if seen is not None:
            seen.append(list(argv))
        fd = int(argv[argv.index("--report-fd") + 1])
        assert fd in kw["pass_fds"]
        if report is not None:
            os.write(fd, json.dumps(report).encode())
        return subprocess.CompletedProcess(argv, rc, "", stderr)
    monkeypatch.setattr(leerie.subprocess, "run", run)


def _good(leerie) -> dict:
    return {"ipc_ns": "ipc:[1]", "interfaces": ["lo"], "tmp_private": True,
            "shm_private": True, "erofs": True, "add_key": True,
            "placed": True}


@pytest.mark.parametrize("line,fix", [
    ("unshare(CLONE_NEWUSER): EPERM", "--security-opt seccomp=unconfined"),
    ("make / rprivate: EACCES", "apparmor=leerie-container"),
])
def test_a_runtime_that_cannot_build_one_stops_the_run(
        leerie, monkeypatch, tmp_path, capsys, line, fix):
    _fake_run(leerie, monkeypatch, rc=126, stderr=_PREFIX + line + _SUFFIX)
    with pytest.raises(SystemExit) as ex:
        leerie._require_heldout_sandbox(tmp_path)
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert line in err and fix in err
    assert "never runs a held-out file outside a sandbox" in err
    assert os.environ.get(leerie.ISOLATE_ENV) is None
    assert not (tmp_path / leerie._SANDBOX_PROBE_DIRNAME).exists()


def test_fly_is_told_it_does_not_start(leerie, monkeypatch, tmp_path,
                                       capsys):
    monkeypatch.setenv("LEERIE_RUNTIME", "fly")
    _fake_run(leerie, monkeypatch, rc=126,
              stderr=_PREFIX + "unshare(CLONE_NEWUSER): EINVAL" + _SUFFIX)
    with pytest.raises(SystemExit):
        leerie._require_heldout_sandbox(tmp_path)
    assert "Fly runs do not start" in capsys.readouterr().err


@pytest.mark.parametrize("field,value,named", [
    ("ipc_ns", "SAME", "IPC namespace"),
    ("interfaces", ["lo", "eth0"], "not only lo"),
    ("tmp_private", False, "/tmp is not its own"),
    ("shm_private", False, "/dev/shm is not its own"),
    ("erofs", "EACCES", "not EROFS"),
    ("add_key", "added", "add_key is allowed"),
    ("placed", False, "placed file is not in the view"),
])
def test_a_sandbox_missing_a_property_stops_the_run(
        leerie, monkeypatch, tmp_path, capsys, field, value, named):
    rep = _good(leerie)
    rep[field] = (os.readlink("/proc/self/ns/ipc") if value == "SAME"
                  else value)
    _fake_run(leerie, monkeypatch, report=rep)
    with pytest.raises(SystemExit):
        leerie._require_heldout_sandbox(tmp_path)
    err = capsys.readouterr().err
    assert named in err
    assert os.environ.get(leerie.ISOLATE_ENV) is None


def test_a_good_sandbox_turns_isolation_on_and_cleans_up(
        leerie, monkeypatch, tmp_path):
    seen: list = []
    _fake_run(leerie, monkeypatch, report=_good(leerie), seen=seen)
    # The network probe runs after the sandbox's, before isolation is on
    # (tests/test_confined_network.py runs it for real).
    relay: list = []
    monkeypatch.setattr(leerie, "_require_egress_relay", lambda: relay.append(
        (len(seen), os.environ.get(leerie.ISOLATE_ENV))))
    leerie._require_heldout_sandbox(tmp_path)
    assert relay == [(1, None)]
    assert os.environ[leerie.ISOLATE_ENV] == "1"
    assert not (tmp_path / leerie._SANDBOX_PROBE_DIRNAME).exists()
    (argv,) = seen
    assert argv[argv.index("--place") + 1].endswith("=probe/placed.txt")
    assert "--isolate" not in argv


# --- the argv ---------------------------------------------------------------


def test_no_isolation_until_preflight_turned_it_on(leerie, monkeypatch):
    g = leerie.WorkerGrants(rw=("/w",), ro=("/r",))
    argv = leerie._confined_argv(g, ["cmd"])
    assert "--isolate" not in argv and "--sandbox" not in argv
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    argv = leerie._confined_argv(g, ["cmd"])
    assert argv[4:] == ["--isolate", "--rw", "/w", "--ro", "/r", "--", "cmd"]


def test_a_held_out_run_is_always_sandboxed(leerie, monkeypatch):
    """With or without preflight's switch: a held-out file never runs
    outside a sandbox (the shim refuses where it cannot build one)."""
    g = leerie.WorkerGrants(rw=("/v",), ro=("/",))
    sb = leerie.HeldOutSandbox(view="/v", place=(("/a/s.py", "t/s.py"),),
                               report="/v/r.json")
    for on in (False, True):
        if on:
            monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
        argv = leerie._confined_argv(g, ["run"], sandbox=sb, report_fd=7)
        assert argv[4:] == ["--sandbox", "--view", "/v", "--place",
                            "/a/s.py=t/s.py", "--report", "/v/r.json",
                            "--report-fd", "7", "--rw", "/v", "--ro", "/",
                            "--", "run"]


def test_the_entry_runs_landlock_the_state_root_check_then_the_gate(
        leerie, monkeypatch, tmp_path):
    """Every invocation's entry (`_require_confinement`), no longer a
    fresh-only preflight step: tests/test_confinement_entry.py drives each
    of `main()`'s dispatch paths through it."""
    order: list = []

    class Stop(Exception):
        pass

    monkeypatch.setattr(leerie, "_require_landlock",
                        lambda: order.append("landlock"))
    monkeypatch.setattr(leerie, "_require_state_root_apart",
                        lambda s, c: order.append(("apart", s, c)))

    def gate(run_dir):
        order.append(("sandbox", run_dir))
        raise Stop

    monkeypatch.setattr(leerie, "_require_heldout_sandbox", gate)
    state, repo = tmp_path / "state", tmp_path / "repo"
    with pytest.raises(Stop):
        leerie._require_confinement(tmp_path, state, repo)
    assert order == ["landlock", ("apart", state, repo),
                     ("sandbox", tmp_path)]


# --- the real gate, where the runtime can build one -------------------------


@pytest.mark.skipif(LL.isolation_error() is not None,
                    reason="this host cannot build the sandbox; run in the "
                           "container under the leerie-container profile")
def test_the_real_gate_passes_in_a_runtime_that_allows_it(leerie, tmp_path):
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("the probe's view must lie outside /tmp")
    leerie._require_heldout_sandbox(tmp_path)
    assert os.environ[leerie.ISOLATE_ENV] == "1"
    assert not (tmp_path / leerie._SANDBOX_PROBE_DIRNAME).exists()
