"""The ratchet's test IDs when the test command is a wrapper script (DESIGN §9
*Which failures are new*).

Five delivery re-drive groups of `web` (6f147a0c twice, 984a352a, deb96d3e,
f7d839df; leerie 0.44.3 to 0.45.0) recorded `unmeasured: ["test ids"]`, and
no `redrive-ids0` command is in any of their logs. The repository's test
command is `pnpm run test`, whose script body is
`./bin/test-with-dedicated-ports.sh`: a shell script that starts a dev
server and then runs `pnpm run test:unit` (jest). Leerie finds a runner on a
command's own words and resolves package-manager script calls, never a script
file, so it names no runner. The ratchet then measured its IDs only through a
report run (`_capture_failing_test_ids`), which returns before running
anything when it knows no runner: no command, no IDs, on trees whose whole
suite passed every time leerie ran it (5316 to 5324 tests, none failing).

Since #329 the ratchet reads the test axis's own measurement
(`_ratchet_test_ids` -> `_measure_axes`): a whole run that passes has no
failing ID, whatever the runner, so those groups are measured. A red whole run
behind such a script still has no report, and is no measurement of its IDs:
never zero failing IDs.

Real `_redrive_test_ids` and `_ratchet_exec` over a real git repository and a
real pytest behind a real shell script, confined as a measurement is. Nothing
is stubbed.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tests.conftest import run_git_cwd_first_stdout as _git  # noqa: E402
from tests.test_one_measurement_per_change import Run  # noqa: E402

# A wrapper that does setup of its own, then runs the runner. The runner is
# named by an absolute interpreter path, so the script resolves nothing on
# PATH.
WRAPPER = ("#!/bin/sh\nset -e\necho 'starting the test environment'\n"
           f"exec {sys.executable} -m pytest -q -p no:cacheprovider tests\n")


class Wrapped(Run):
    """`Run`'s repository, its test command a script leerie cannot see
    through, its scoped form the runner's own."""

    def __init__(self, leerie, tmp_path, monkeypatch, **kw):
        super().__init__(leerie, tmp_path, monkeypatch, **kw)
        (self.st.repo_root / ".leerie" / "config.toml").write_text(
            'test = "./bin/test.sh"\n'
            'test_scoped = "python3 -m pytest -q -p no:cacheprovider'
            ' {test_files}"\n')
        script = self.staging / "bin" / "test.sh"
        script.parent.mkdir()
        script.write_text(WRAPPER)
        script.chmod(0o755)
        _git(self.staging, "add", "-A")
        _git(self.staging, "commit", "-qm", "the test wrapper")

    def ids(self, label: str) -> dict | None:
        return asyncio.run(self.leerie._redrive_test_ids(
            self.st, self.caps, self.staging, label))

    def head(self) -> str:
        return _git(self.staging, "rev-parse", "HEAD")


def _wrapper_ran(w: Wrapped) -> int:
    return sum("bin/test.sh" in c for c in w.runs)


def test_leerie_sees_no_runner_in_the_wrapper(leerie, tmp_path, monkeypatch):
    """The premise, as on `web`: neither the command's words nor a script
    body name a runner, so no report can be asked of the run itself."""
    w = Wrapped(leerie, tmp_path, monkeypatch)
    cmd, _env = leerie._runner_command("./bin/test.sh", w.staging,
                                       leerie._RUNNER_REPORTS)
    assert leerie._acceptance_runner(cmd, leerie._RUNNER_REPORTS) is None


def test_a_green_tree_behind_a_wrapper_has_its_ids_measured(
        leerie, tmp_path, monkeypatch):
    """The re-drive's baseline (`redrive-ids0`) and a group's after-side on a
    suite that passes: the wrapper runs, and its pass is zero failing IDs."""
    w = Wrapped(leerie, tmp_path, monkeypatch)
    pre = w.head()
    before = w.ids("redrive-ids0")
    assert _wrapper_ran(w) == 1
    assert before is not None and before["ids"] == []
    w.commit(leaf_DOT_py="L = 1\n# the group's change\n")
    after = w.ids("redrive-r1g0-ids")
    assert after is not None and after["ids"] == []
    out = asyncio.run(leerie._ratchet_exec(
        w.st, w.caps, w.staging, pre, before, after, label="redrive-r1g0",
        log_path=w.run_dir / "logs" / "r.log"))
    assert out["unmeasured"] is False
    assert out["new"] == []


def test_a_tree_measured_before_is_read_without_running_it_again(
        leerie, tmp_path, monkeypatch):
    """The re-drive starts on the tree the final conformer measured last: the
    recorded pass answers for its IDs, as it does for any runner."""
    w = Wrapped(leerie, tmp_path, monkeypatch)
    first = w.ids("final")
    n = _wrapper_ran(w)
    again = w.ids("redrive-ids0")
    assert _wrapper_ran(w) == n == 1
    assert first == again and again["ids"] == []


def test_a_red_tree_behind_a_wrapper_is_unmeasured_never_zero_ids(
        leerie, tmp_path, monkeypatch):
    """A failing whole run with no report says that something failed, not
    which test: its IDs are no measurement, and the group's ID diff is
    unmeasured rather than empty."""
    w = Wrapped(leerie, tmp_path, monkeypatch, test_other_ok="False")
    pre = w.head()
    before = w.ids("redrive-ids0")
    assert _wrapper_ran(w) == 1             # it really ran, and failed
    assert before is None
    w.commit(leaf_DOT_py="L = 1\n# the group's change\n")
    after = w.ids("redrive-r1g0-ids")
    assert after is None
    out = asyncio.run(leerie._ratchet_exec(
        w.st, w.caps, w.staging, pre, before, after, label="redrive-r1g0",
        log_path=w.run_dir / "logs" / "r.log"))
    assert out["unmeasured"] is True
