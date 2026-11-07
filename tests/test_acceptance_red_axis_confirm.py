"""The acceptance gate's gate-wide test-axis rule is confirmed under B*, and
the delivery re-drive's test IDs never run a command the task forbids
(DESIGN §8 *Held-out acceptance tests*, *Delivery re-drive* rule 4).

Driven for real: a git repo with a pytest suite, the repo's own test
command from `.leerie/config.toml`, and the real `_ratchet_test_ids` /
`_ratchet_exec` / `_measure_axes`."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tests.conftest import measured_tmp_files, relocate_into_run_repo
from tests.conftest import run_git_out  # noqa: E402

PYTEST = "python3 -m pytest -q -p no:cacheprovider"


_git = run_git_out


def _repo(tmp_path: Path, test_cmd: str = PYTEST) -> Path:
    repo = tmp_path / "py"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def one():\n    return 1\n")
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / "test_calc.py").write_text(
        "from calc import one\n\n\ndef test_one():\n    assert one() == 1\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        f'test = "{test_cmd}"\n'
        f'test_scoped = "{PYTEST} {{test_files}}"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _st(leerie, tmp_path: Path, repo: Path, **data):
    st = leerie.State(tmp_path / "state", "run-red", repo_root=repo)
    st.data = {"verbosity": "quiet", "worker_count": 0, **data}
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    st.save()
    # The run's own repository (DESIGN §6 *The run's own repository*): the
    # confirm's base worktree is added there.
    relocate_into_run_repo(leerie, st)
    return st


def _confirm(leerie, st, repo: Path, pre: str, cmd: str = PYTEST):
    caps = {**leerie.DEFAULT_CAPS, "confirm_pairs": 2}
    return asyncio.run(leerie._acceptance_confirm_red_axis(
        st, caps, repo, pre, cmd,
        log_path=st.run_dir / "logs" / "acceptance-repair.log"))


def test_a_real_regression_is_confirmed(leerie, tmp_path):
    repo = _repo(tmp_path)
    pre = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text("def one():\n    return 1 + 1\n")
    _git(repo, "commit", "-qam", "break")
    st = _st(leerie, tmp_path, repo)
    reason, rec = _confirm(leerie, st, repo, pre)
    assert reason == "turned the test axis red"
    assert len(rec["new"]) == 1 and "test_one" in rec["new"][0]


def test_a_flaky_failure_is_not_confirmed(leerie, tmp_path):
    """Fails on its first run only (a counter outside the tree, in the run's
    own temp directory: a confined run writes nothing beside its tree,
    DESIGN §6 *Leerie's runs of the repository's commands are confined
    too*): the red run is real, but B* reruns pass, so the repair is
    kept."""
    repo = _repo(tmp_path)
    pre = _git(repo, "rev-parse", "HEAD")
    (repo / "test_flaky.py").write_text(
        "import os, pathlib\n\n\ndef test_flaky():\n"
        "    p = pathlib.Path(os.environ['TMPDIR'], 'runs.txt')\n"
        "    n = int(p.read_text()) if p.exists() else 0\n"
        "    p.write_text(str(n + 1))\n"
        "    assert n > 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "repair adds a test")
    st = _st(leerie, tmp_path, repo)
    reason, rec = _confirm(leerie, st, repo, pre)
    runs = sum(int(f.read_text())
               for f in measured_tmp_files(st.run_dir, "runs.txt"))
    assert runs >= 2      # it did fail, then reran
    assert reason == ""
    assert rec["new"] == []


def test_no_report_twice_is_confirmed(leerie, tmp_path):
    """A suite that cannot run at all (an import error at collection with a
    command that writes no report) gives no IDs; a second red run, not
    memoised, confirms it."""
    repo = _repo(tmp_path, test_cmd="sh -c 'exit 3'")
    pre = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo)
    reason, rec = _confirm(leerie, st, repo, pre, cmd="sh -c 'exit 3'")
    assert rec == {"no_report": True, "reproduced": True}
    assert reason == "turned the test axis red"


def test_redrive_test_ids_never_runs_a_forbidden_command(leerie, tmp_path,
                                                         monkeypatch):
    """N5 follow-up: `_redrive_test_ids` ran the raw test command even when
    the task forbade it. It now goes through the same replacement as the
    test axis: before the baseline there is nothing to couple to, so no
    verdict — and the forbidden command is never executed."""
    repo = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo,
             process_constraints={"forbidden_commands": [PYTEST]})
    ran: list[str] = []
    real = leerie._run_streaming

    async def spy(cmd, **kw):
        ran.append(" ".join(cmd))
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie, "_run_streaming", spy)
    caps = dict(leerie.DEFAULT_CAPS)
    got = asyncio.run(leerie._redrive_test_ids(st, caps, repo, "ids0"))
    assert got is None and ran == []
    # Allowed, it runs the repository's command as before: once, the verdict
    # and the IDs from the same run.
    st.data.pop("process_constraints")
    got = asyncio.run(leerie._redrive_test_ids(st, caps, repo, "ids1"))
    assert got is not None and got["ids"] == []
    assert len(ran) == 1 and PYTEST in ran[0]
