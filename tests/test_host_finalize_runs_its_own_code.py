"""host-finalize.sh runs the leerie code it was sourced from (DESIGN §6 *A
launch runs only the leerie code it started with*).

A local launch sources `host-finalize.sh` from its own snapshot
(`. "$_code_dir/scripts/host-finalize.sh"`), but the rebaser it started
loaded `$LEERIE_REPO/orchestrator/leerie.py`: the live install. An install
upgraded or edited during the run (the 2026-11-03 incident shape) ran the
update's `run_rebaser` and `prompts/rebaser.md` at this run's finalize
(review round 40, execution F1).

The harness is test_host_finalize_rebase's own (a diverged repository and a
stub `python3` that records its argv), with two directories standing for
the launch's snapshot (`code`) and the live install (`install`,
`LEERIE_REPO`), and the file sourced from the snapshot, as the launcher
sources it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from tests.test_host_finalize_rebase import (
    REPO_ROOT, _make_diverged_repo, _make_run, _make_stub_bin,
    _make_stub_python3, _rebaser_result)


def _two_trees(tmp_path: Path) -> tuple[Path, Path]:
    code = tmp_path / "stage" / "leerie-code"     # the launch's snapshot
    install = tmp_path / "install"                # $LEERIE_REPO
    for d in (code, install):
        (d / "orchestrator").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / "scripts", d / "scripts")
        (d / "orchestrator" / "leerie.py").write_text("# stand-in\n")
    return code, install


def _run(tmp_path: Path, code: Path, install: Path, bin_dir: Path,
         repo: Path, call: str) -> subprocess.CompletedProcess:
    script = (f'set -euo pipefail; _code_dir="{code}"; '
              '. "$_code_dir/scripts/host-finalize.sh"; ' + call)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False,
        timeout=120,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "USER_REPO": str(repo),
             "HOME": str(tmp_path), "LEERIE_REPO": str(install),
             "LEERIE_STATE_HOST_DIR": str(repo / ".leerie")})


def _orchestrator_args(bin_dir: Path, marker: str) -> list[str]:
    calls = (bin_dir / "python3.log").read_text().splitlines()
    hits = [c for c in calls if marker in c]
    assert hits, calls
    return [a for a in hits[0].split() if a.endswith("leerie.py")]


def test_the_rebaser_loads_the_snapshot_not_the_install(tmp_path):
    code, install = _two_trees(tmp_path)
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_python3(bin_dir, _rebaser_result("rebased"))
    _make_stub_bin(bin_dir, "gh", "echo https://github.com/o/r/pull/1")
    _make_stub_bin(bin_dir, "sleep", "exit 0")
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run", "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00"})
    r = _run(tmp_path, code, install, bin_dir, repo,
             f'host_finalize "{run_dir}"')
    assert r.returncode == 0, r.stderr
    assert _orchestrator_args(bin_dir, "rebaser.py") == [
        str(code / "orchestrator" / "leerie.py")]


def test_the_capture_seam_loads_the_tree_it_was_sourced_from(tmp_path):
    code, install = _two_trees(tmp_path)
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_python3(bin_dir, _rebaser_result("rebased"))
    run_dir = _make_run(tmp_path, repo, "cap-run", run_json={})
    (run_dir / "captured-deps.json").write_text(json.dumps({}))
    # The stand-in orchestrator has no apply_captured_deps, so the seam
    # fails; only the path it was given is asserted.
    _run(tmp_path, code, install, bin_dir, repo,
         f'host_apply_captured_deps "{run_dir}" || true')
    assert _orchestrator_args(bin_dir, str(run_dir)) == [
        str(code / "orchestrator" / "leerie.py")]
