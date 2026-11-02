"""The launcher-run world decides its own AppArmor state, not the host
(tests/launcher_stub_run.py, `make_world(apparmor=...)`).

The tests that run the real launcher against a stub `nerdctl` are not about
AppArmor, but the launcher's AppArmor gate stands before `nerdctl run`, and
its probes read the kernel. Read from the host, those tests passed only
where the loaded `leerie-container` profile was this checkout's: every one
stopped with "not loaded (or is stale)" on a host whose profile was older,
and would on any runner whose kernel lists `apparmor` without the profile.

Each world state is driven through the real launcher and its real probes,
and the argv it hands `nerdctl run` (or its stop) is the evidence. The three
states disagree with each other, so whatever the host's own state, at least
two of these fail if the probes stop reading the world.
"""
from __future__ import annotations

import sys

import pytest

from tests.launcher_stub_run import make_world, run_argv, run_to_end

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")

AA_OPT = "apparmor=leerie-container"


def _secopts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1])
            if a == "--security-opt"]


def test_a_world_with_the_current_profile_runs_under_it(tmp_path):
    w = make_world(tmp_path, apparmor="current")
    rc, out = run_to_end(w, ["--no-push", "fix it"])
    assert rc == 0, out
    assert _secopts(run_argv(w)) == ["seccomp=unconfined", AA_OPT], out


def test_a_world_without_apparmor_runs_without_the_profile(tmp_path):
    w = make_world(tmp_path, apparmor="none")
    rc, out = run_to_end(w, ["--no-push", "fix it"])
    assert rc == 0, out
    assert _secopts(run_argv(w)) == ["seccomp=unconfined"], out


def test_a_world_with_a_stale_profile_stops_before_the_container(tmp_path):
    w = make_world(tmp_path, apparmor="stale")
    rc, out = run_to_end(w, ["--no-push", "fix it"])
    assert rc == 1, out
    assert "profile is not loaded (or is stale)" in out, out
    assert run_argv(w) == []


def test_an_unknown_state_is_refused(tmp_path):
    with pytest.raises(ValueError):
        make_world(tmp_path, apparmor="loaded")
