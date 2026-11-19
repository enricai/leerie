"""No value finalize records or swaps reaches jq through argv
(`scripts/host-finalize.sh`, IMPLEMENTATION `scripts/host-finalize.sh` row).

CI (3.11, run 44408751581) failed
`test_oversized_push_output_still_writes_the_sidecar` with
"host-finalize.sh: line 61: /usr/bin/jq: Argument list too long". The push
error was bounded with `tail -c 32768` and then handed to jq as one `--arg`.
GNU `tail -c` is exact, but uutils coreutils' `tail` (Ubuntu 26.04) sometimes
returns most of a piped input: measured, up to 187 KB of 200 KB. A single
argv string past MAX_ARG_STRLEN (131,072 bytes, a kernel constant) cannot be
exec'd, so jq never ran and `set -e` ended finalize before its diagnostic.

These tests do not depend on which `tail` the host has or how big its
environment is:
- the helper is handed a value past MAX_ARG_STRLEN directly;
- a `tail` that returns its whole input stands in for the inexact one, on
  the real `host_finalize` push path, where the byte bounds must still hold
  (#416's CI failed `test_persist_bound_is_measured_in_bytes_not_characters`
  on 3.10 and 3.12 the same way: the 32 KiB bound came back longer);
- the PR end-state refresh is driven with a PR body past MAX_ARG_STRLEN.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ, init_git_repo, run_git_repo_first
from tests.test_accept_heldout import _GH
from tests.test_host_finalize_sh import HOST_FINALIZE_SH, _make_stub_bin
from tests.test_push_output_capture import _run_with_hook

pytestmark = pytest.mark.skipif(not HAS_JQ,
                                reason="host-only script: needs real `jq`")

MAX_ARG_STRLEN = 131_072


def _bash(script: str, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail; . {HOST_FINALIZE_SH}; {script}"],
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True, text=True, check=False)


def test_a_value_past_max_arg_strlen_is_recorded_whole(tmp_path):
    sidecar = tmp_path / "run.json"
    sidecar.write_text(json.dumps({"branch": "b", "pushed_at": "t"}))
    # Built inside bash (`printf -v`, a builtin): the value never passes
    # through any argv on its way to the helper.
    r = _bash(
        "printf -v v '%*s' 200000 ''; v=\"${v// /x}\"$'\\n\"quoted\" \\\\ end'; "
        f"_host_finalize_update_run_json {sidecar} "
        "\"push_error=$v\" \"pushed_at=\" \"pr_url=\"", tmp_path)
    assert r.returncode == 0, r.stderr
    got = json.loads(sidecar.read_text())
    assert got["push_error"] == "x" * 200000 + '\n"quoted" \\ end'
    assert len(got["push_error"].encode()) > MAX_ARG_STRLEN
    assert got["pushed_at"] is None and got["pr_url"] is None
    assert got["branch"] == "b"
    # The value files are gone, and nothing is left beside the sidecar.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json"]


def test_a_failing_jq_leaves_the_sidecar_and_no_value_files(tmp_path):
    sidecar = tmp_path / "run.json"
    sidecar.write_text("not json")
    r = _bash(f"_host_finalize_update_run_json {sidecar} push_error=x "
              "|| echo rc=$?", tmp_path)
    assert "rc=" in r.stdout and "rc=0" not in r.stdout
    assert sidecar.read_text() == "not json"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.json",
                                                          "run.json.tmp"]


def test_an_inexact_tail_changes_nothing(tmp_path):
    # Both CI failures, made deterministic: a `tail` that returns its whole
    # input, as uutils' `tail -c` did for most of a 200 KB pipe. The push
    # error is recorded (no argv), and its bound still holds in bytes,
    # because finalize cuts with bash, never `tail`.
    _make_stub_bin(tmp_path, "tail", "exec cat")
    huge = "\n".join(f"error TS2307: line {i} of a very long compiler report"
                     for i in range(4000))
    assert len(huge) > MAX_ARG_STRLEN
    run_dir, r = _run_with_hook(
        tmp_path, "feat-t-aaaaaa", push_stdout=huge + "\nFINAL-TAIL-MARKER",
        push_stderr="error: failed to push some refs to 'origin'")
    assert "Argument list too long" not in r.stderr
    assert r.returncode == 1, r.stderr[-2000:]
    assert "git push failed" in r.stderr
    recorded = json.loads((run_dir / "run.json").read_text())["push_error"]
    marker, _, kept = recorded.partition("\n")
    assert marker == "…(truncated to the last 32 KiB)…"
    assert len(kept.encode()) == 32768
    assert kept.endswith("FINAL-TAIL-MARKER") and "line 0 of" not in kept
    # The printed copy keeps its 4000-byte bound too.
    assert "line 0 of" not in r.stderr and "FINAL-TAIL-MARKER" in r.stderr
    assert not (tmp_path / "tail.log").exists(), "finalize ran `tail`"


RUN = "argv-run-001"

_GH_FROM_FILE = _GH.replace('printf \'%s\' "$GH_VIEW"', 'cat "$GH_VIEW_FILE"')


def test_a_pr_body_past_max_arg_strlen_is_refreshed(tmp_path):
    assert _GH_FROM_FILE != _GH
    user = init_git_repo(tmp_path / "user")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    run_git_repo_first(user, "remote", "add", "origin", str(origin))
    run_git_repo_first(user, "branch", f"leerie/runs/{RUN}")
    run_git_repo_first(user, "push", "-q", "origin", f"leerie/runs/{RUN}")
    run_dir = tmp_path / "state" / "runs" / RUN
    run_dir.mkdir(parents=True)
    old = "## Unconverged\n\n- 1 held-out set fails\n"
    new = "## Held-out cases accepted by the operator\n\n- set 1\n"
    (run_dir / "run.json").write_text(json.dumps({
        "branch": f"leerie/runs/{RUN}", "working_branch": "main",
        "finished_at": "2026-11-12T10:00:00Z",
        "pushed_at": "2026-11-12T10:01:00Z",
        "pr_url": "https://github.com/o/r/pull/7", "outcome": "done",
        "pr_end_state": new,
        "pr_refresh": {"end_state_was": old, "outcome": "done"}}))
    # A body GitHub allows (65,536 characters) that is past MAX_ARG_STRLEN
    # in bytes: three-byte characters.
    summary = "## Summary\n" + "語" * 60000 + "\n\n"
    assert len(summary.encode()) > MAX_ARG_STRLEN
    view = tmp_path / "view.json"
    view.write_text(json.dumps({"body": summary + old, "isDraft": False}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(_GH_FROM_FILE)
    (bindir / "gh").chmod(0o755)
    r = subprocess.run(
        ["bash", "-c", f"set -euo pipefail; . {HOST_FINALIZE_SH}; "
                       f"host_finalize {run_dir}"],
        env={"PATH": f"{bindir}:/usr/bin:/bin", "USER_REPO": str(user),
             "HOME": str(tmp_path), "GH_LOG": str(tmp_path / "gh.log"),
             "GH_BODY": str(tmp_path / "body.md"),
             "GH_VIEW_FILE": str(view)},
        capture_output=True, text=True, check=False)
    assert "Argument list too long" not in r.stderr
    assert r.returncode == 0, r.stderr[-2000:]
    body = (tmp_path / "body.md").read_text()
    assert body == summary + new
    assert json.loads((run_dir / "run.json").read_text())["pr_refresh"] is None
