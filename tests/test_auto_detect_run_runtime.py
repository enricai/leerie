"""Tests for the generalized runtime auto-detection helper in `leerie`.

DESIGN §6 "Run identifier" flags that the historical `_auto_detect_fly_runtime`
helper (and the `--runtime` enum validation on `stop`, `kill`,
`accept-blocked`, `finalize`, and `resume`) is hardcoded to
`fly-machine.json` and needs widening to also recognize `ec2-instance.json`
(written unconditionally by `ec2-provision.sh`'s `provision_instance()`) so a
run-id-bearing verb invoked without an explicit `--runtime` resolves to the
runtime that actually owns the run.

This module extracts `_auto_detect_run_runtime` / `_auto_detect_fly_runtime`
verbatim from the real launcher (mirroring `tests/test_oom_wedge_prevention.py`'s
`_reaper_fn_source` approach) and exercises them against fixture run dirs —
no full launcher CLI dispatch, no stubbed `nerdctl`/`flyctl`/`aws` needed,
since the helper is pure filesystem probing.

The second half of this module invokes the real `leerie` launcher end to end
(mirroring `tests/test_accept_blocked.py`'s local-path pattern) to pin the
`--runtime` enum validation across the five run-id-bearing verbs this
subtask's scope note names, plus each verb's EC2 dispatch behavior: `stop`
and `kill` have real EC2 actions wired (test-001/feat-005 and feat-006
respectively) and so proceed past the detection gate into the action itself;
`accept-blocked` and `finalize` still fail closed with a "does not
support EC2 runs yet" message; `resume` fails closed with its own
resume-specific message. None of these verbs need `LEERIE_FLY_APP`/stubbed
`flyctl`/`aws` to reach the enum-validation and detection-promotion
assertions, since those fire before any remote dispatch.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ
from tests.ec2_stub import run_launcher as _run_launcher_shared

REPO_ROOT_LAUNCHER = Path(__file__).resolve().parent.parent / "leerie"

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_fn(name: str) -> str:
    text = LAUNCHER.read_text()
    marker = f"{name}() {{"
    start = text.index(marker)
    end = text.index("\n}", start) + 2
    return text[start:end]


def _helpers_source() -> str:
    return _extract_fn("_auto_detect_run_runtime") + "\n" + _extract_fn("_auto_detect_fly_runtime")


def _run(script: str, state_dir: Path) -> subprocess.CompletedProcess:
    full = (
        "set -u\n"
        f'LEERIE_STATE_HOST_DIR="{state_dir}"\n'
        f"{_helpers_source()}\n"
        f"{script}\n"
    )
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, timeout=30
    )


def _make_run_dir(state_dir: Path, run_id: str, *, fly: bool = False, ec2: bool = False) -> Path:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if fly:
        (run_dir / "fly-machine.json").write_text('{"fly_machine_id": "abc123"}\n')
    if ec2:
        (run_dir / "ec2-instance.json").write_text('{"ec2_instance_id": "i-0123456789abcdef0"}\n')
    return run_dir


# --- _auto_detect_run_runtime: core detection contract ---------------------


def test_ec2_only_sidecar_detects_as_ec2(tmp_path):
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-ec2", ec2=True)
    result = _run(
        '_auto_detect_run_runtime "run-ec2" "" && echo "RC=0" || echo "RC=$?"',
        state_dir,
    )
    assert "ec2" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "RC=0" in result.stdout


def test_fly_only_sidecar_still_detects_as_fly_no_regression(tmp_path):
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-fly", fly=True)
    result = _run(
        '_auto_detect_run_runtime "run-fly" "" && echo "RC=0" || echo "RC=$?"',
        state_dir,
    )
    assert "fly" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "ec2" not in result.stdout
    assert "RC=0" in result.stdout


def test_neither_sidecar_detects_nothing_and_returns_nonzero(tmp_path):
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-plain")
    result = _run(
        'out="$(_auto_detect_run_runtime "run-plain" "")"; rc=$?; '
        'echo "OUT=[$out]"; echo "RC=$rc"',
        state_dir,
    )
    assert "OUT=[]" in result.stdout, f"stdout={result.stdout!r}"
    assert "RC=0" not in result.stdout


def test_unknown_run_id_detects_nothing(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "runs").mkdir(parents=True, exist_ok=True)
    result = _run(
        'out="$(_auto_detect_run_runtime "no-such-run" "")"; rc=$?; '
        'echo "OUT=[$out]"; echo "RC=$rc"',
        state_dir,
    )
    assert "OUT=[]" in result.stdout
    assert "RC=0" not in result.stdout


def test_explicit_runtime_short_circuits_detection(tmp_path):
    """An explicit --runtime must skip detection entirely, even when a
    sidecar for a *different* runtime is present (the explicit value wins)."""
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-ec2", ec2=True)
    result = _run(
        'out="$(_auto_detect_run_runtime "run-ec2" "local")"; rc=$?; '
        'echo "OUT=[$out]"; echo "RC=$rc"',
        state_dir,
    )
    assert "OUT=[]" in result.stdout, f"stdout={result.stdout!r}"
    assert "RC=0" not in result.stdout


def test_empty_run_id_detects_nothing(tmp_path):
    state_dir = tmp_path / "state"
    result = _run(
        'out="$(_auto_detect_run_runtime "" "")"; rc=$?; '
        'echo "OUT=[$out]"; echo "RC=$rc"',
        state_dir,
    )
    assert "OUT=[]" in result.stdout
    assert "RC=0" not in result.stdout


def test_fly_wins_when_both_sidecars_present(tmp_path):
    """Not expected in practice (a run has exactly one runtime), but Fly
    is checked first for backward compatibility with the pre-EC2 helper."""
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-both", fly=True, ec2=True)
    result = _run(
        '_auto_detect_run_runtime "run-both" ""',
        state_dir,
    )
    assert result.stdout.strip() == "fly"


# --- _auto_detect_fly_runtime: back-compat wrapper --------------------------


def test_fly_wrapper_still_returns_0_for_fly_run(tmp_path):
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-fly", fly=True)
    result = _run(
        '_auto_detect_fly_runtime "run-fly" "" && echo "RC=0" || echo "RC=$?"',
        state_dir,
    )
    assert "RC=0" in result.stdout


def test_fly_wrapper_returns_nonzero_for_ec2_run():
    """The Fly-only wrapper must not treat an EC2 run as a Fly run —
    callers that haven't been migrated to the generalized helper yet
    (e.g. resume) rely on this to avoid misrouting."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        state_dir = Path(td) / "state"
        _make_run_dir(state_dir, "run-ec2", ec2=True)
        result = _run(
            '_auto_detect_fly_runtime "run-ec2" "" && echo "RC=0" || echo "RC=$?"',
            state_dir,
        )
        assert "RC=0" not in result.stdout


def test_fly_wrapper_returns_nonzero_when_explicit_runtime_given(tmp_path):
    state_dir = tmp_path / "state"
    _make_run_dir(state_dir, "run-fly", fly=True)
    result = _run(
        '_auto_detect_fly_runtime "run-fly" "local" && echo "RC=0" || echo "RC=$?"',
        state_dir,
    )
    assert "RC=0" not in result.stdout


# --- End-to-end: real launcher invocation across all five verbs ------------


def _launcher_env(state_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_dir)
    env.pop("LEERIE_FLY_APP", None)
    return env


def _make_e2e_run(state_dir: Path, run_id: str, *, ec2: bool = False,
                  fly: bool = False, with_state: bool = False) -> Path:
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if ec2:
        (run_dir / "ec2-instance.json").write_text(
            json.dumps({"ec2_instance_id": "i-0123456789abcdef0"})
        )
    if fly:
        (run_dir / "fly-machine.json").write_text(
            json.dumps({"fly_machine_id": "m1"})
        )
    if with_state:
        (run_dir / "state.json").write_text(json.dumps({"subtask_status": {}}))
    return run_dir


def _launcher(args: list[str], state_dir: Path) -> subprocess.CompletedProcess:
    return _run_launcher_shared(
        args, _launcher_env(state_dir), launcher=REPO_ROOT_LAUNCHER, timeout=30
    )


def test_stop_rejects_bogus_runtime_value(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _launcher(["stop", "r1", "--runtime", "bogus"], state_dir)
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


def test_stop_accepts_explicit_ec2_enum_but_needs_a_sidecar(tmp_path):
    """--runtime ec2 must clear the enum-validation gate (not rejected as
    unknown). stop's EC2 action is wired (test-001/feat-005) and resolves
    AWS credentials before resolving ec2_instance_id from the sidecar, so
    it proceeds past enum validation and fails on credential resolution
    (no `aws` binary / credentials set up in this test's env) rather than
    an unknown-runtime rejection."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _launcher(["stop", "r1", "--runtime", "ec2"], state_dir)
    assert r.returncode != 0
    assert "must be" not in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_stop_autodetects_ec2_sidecar_and_proceeds_past_detection(tmp_path):
    """stop's EC2 action is wired (test-001/feat-005), so an
    auto-detected EC2 run proceeds past the detection gate into AWS
    credential resolution (which fails here since no `aws` binary /
    credentials are set up in this test's env, unrelated to detection).
    The full stop happy path is covered end-to-end in
    tests/test_ec2_launcher_stop.py against a stubbed `aws`."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _launcher(["stop", "r1"], state_dir)
    assert r.returncode != 0
    assert "auto-detected ec2 run" in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_stop_fly_sidecar_still_promotes_to_fly_no_regression(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", fly=True)
    r = _launcher(["stop", "r1"], state_dir)
    # No LEERIE_FLY_APP set, so it still fails — but via the pre-existing
    # Fly-specific error, proving detection promoted to "fly" and reached
    # the Fly branch rather than the (now EC2-aware) fallthrough.
    assert r.returncode != 0
    assert "auto-detected fly run" in r.stderr
    assert "LEERIE_FLY_APP is required" in r.stderr


def test_kill_accepts_explicit_ec2_enum_but_needs_a_sidecar(tmp_path):
    """feat-006 wires kill's EC2 action (terminate_instance with
    fetch-before-terminate ordering — tests/test_ec2_launcher_kill.py),
    so --runtime ec2 no longer fails closed with "does not support EC2
    runs yet" — it now requires an ec2_instance_id to act on, which this
    run dir (no ec2-instance.json / run.json ec2_instance_id) does not
    have."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _launcher(["kill", "r1", "--runtime", "ec2", "--force"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" not in r.stderr
    assert "no ec2_instance_id found" in r.stderr


def test_kill_autodetects_ec2_sidecar_and_proceeds_past_detection(tmp_path):
    """feat-006 wires kill's EC2 action, so an auto-detected EC2 run no
    longer fails closed at the detection gate — it proceeds to resolve
    AWS credentials (which fails here since no `aws` binary / credentials
    are set up in this test's env, unrelated to detection). The full
    fetch-before-terminate happy path is covered end-to-end in
    tests/test_ec2_launcher_kill.py against a stubbed `aws`."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _launcher(["kill", "r1", "--force"], state_dir)
    assert r.returncode != 0
    assert "auto-detected ec2 run" in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_accept_blocked_rejects_bogus_runtime_value(tmp_path):
    state_dir = tmp_path / "state"
    run_dir = _make_e2e_run(state_dir, "r1")
    (run_dir / "state.json").write_text(
        json.dumps({"subtask_status": {"s1": "blocked"}})
    )
    r = _launcher(
        ["accept-blocked", "r1", "s1", "--runtime", "bogus"], state_dir
    )
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


def test_accept_blocked_autodetects_ec2_sidecar(tmp_path):
    # accept-blocked's EC2 action is wired (see
    # tests/test_ec2_launcher_readonly_verbs.py for its end-to-end
    # coverage against a stubbed aws), so detection no longer fails
    # closed. As with the stop/kill cases above, this env has no aws
    # binary or credentials: reaching AWS credential resolution is the
    # proof that detection promoted to ec2 and entered the EC2 branch
    # rather than silently defaulting to local.
    state_dir = tmp_path / "state"
    run_dir = _make_e2e_run(state_dir, "r1", ec2=True)
    (run_dir / "state.json").write_text(
        json.dumps({"subtask_status": {"s1": "blocked"}})
    )
    r = _launcher(["accept-blocked", "r1", "s1"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" not in r.stderr
    assert "aws" in r.stderr.lower()
    # The local-path mutation never happened — the EC2 branch mutates
    # state.json on the instance over SSM, not the host copy directly.
    st = json.loads((run_dir / "state.json").read_text())
    assert st["subtask_status"]["s1"] == "blocked"


def test_finalize_rejects_bogus_runtime_value(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _launcher(["finalize", "r1", "--runtime", "bogus"], state_dir)
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


# `finalize` and `resume` used to fail closed on EC2 runs. Both now wire a
# real EC2 action, so these three assert the *new* contract: the run reaches
# the EC2 arm (rather than the Fly path, which would misdirect an instance id
# to `flyctl`, or `local`, which would mislabel the run), and any failure is a
# specific one from inside that arm — never the retired blanket refusal.
#
# Full behavioural coverage lives in tests/test_ec2_launcher_finalize.py and
# tests/test_ec2_launcher_resume.py; these stay narrow, matching this file's
# scope (detection + enum validation).


def test_finalize_accepts_explicit_ec2_enum_and_enters_the_ec2_arm(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _launcher(["finalize", "r1", "--runtime", "ec2"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" not in r.stderr
    # This fixture writes no ec2_instance_id, so the arm's own fail-closed
    # fires — proving we reached it rather than the blanket refusal.
    assert "no ec2_instance_id found" in r.stderr, r.stderr


def test_finalize_autodetects_ec2_sidecar_and_promotes(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _launcher(["finalize", "r1"], state_dir)
    assert "auto-detected EC2 run; promoting --runtime to ec2" in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def _launcher_stderr_after_detection(args: list[str], state_dir: Path,
                                     timeout: int = 20) -> str:
    """Run the launcher and return whatever it wrote to stderr, even if it is
    still going when the timeout fires.

    Every other verb in this file exits promptly inside the early
    verb-dispatch region. `resume` does not: once detection promotes the
    runtime it falls through into the launch path, which performs an
    unconditional local container-image build (not runtime-gated, no skip
    flag). This file's scope is the DETECTION decision, so waiting on a build
    irrelevant to the assertion would just make the test slow and flaky."""
    try:
        return subprocess.run(
            [str(REPO_ROOT_LAUNCHER)] + args, env=_launcher_env(state_dir),
            capture_output=True, text=True, timeout=timeout).stderr
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or ""
        return err.decode(errors="replace") if isinstance(err, bytes) else err


def test_resume_autodetects_ec2_sidecar_and_promotes(tmp_path):
    """The regression this replaces: the old guard exited 1 here, making the
    `RUNTIME=ec2` dispatch block's fully-implemented resume path unreachable
    from any real invocation. See tests/test_ec2_launcher_resume.py."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True, with_state=True)
    err = _launcher_stderr_after_detection(["resume", "r1"], state_dir)
    assert "ec2-instance.json present" in err, err
    assert "promoting --runtime to ec2" in err, err
    assert "does not support EC2 runs yet" not in err, err


def test_resume_fly_sidecar_still_promotes_to_fly_no_regression(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", fly=True, with_state=True)
    r = _launcher(["resume", "r1"], state_dir)
    assert r.returncode != 0
    assert "auto-detected Fly run" in r.stderr
    assert "LEERIE_FLY_APP is required" in r.stderr


# --- finalize: the local arm ----------------------------------------------
#
# Before this arm existed, `_auto_detect_run_runtime` had no local case, so a
# local run left `_fin_runtime` empty and fell through the dispatch chain's
# bare `else` into the Fly path — which calls `require_flyctl` and offers to
# INSTALL flyctl for a run that never touched Fly. The only thing that kept
# an ordinary local run out of that branch was the `_already_synced` probe,
# which requires `finished_at`; a run interrupted before `phase_finalize`
# (Ctrl-C after the waves integrated) has none, so it hit flyctl.
#
# These drive the real launcher end to end. `USER_REPO` is honored by the
# finalize arm (`USER_REPO="${USER_REPO:-$PWD}"`), which is what lets the
# fixtures point it at a scratch repo instead of this one.


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


def _make_local_run(state_dir: Path, tmp_path: Path, run_id: str, *,
                    create_branch: bool = True,
                    finished: bool = False,
                    no_push: bool = False) -> tuple[Path, Path]:
    """A local run dir (no Fly/EC2 sidecar) plus a scratch USER_REPO.

    Deliberately writes NO `finished_at` unless asked: that is the exact
    shape of the incident this arm exists for, and it is what makes the
    `_already_synced` probe miss so the dispatch has to classify the runtime.
    """
    repo = tmp_path / "user_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")

    branch = f"leerie/runs/{run_id}"
    if create_branch:
        _git(repo, "checkout", "-q", "-b", branch)
        (repo / "f.txt").write_text("work\n")
        _git(repo, "commit", "-q", "-am", "work")
        _git(repo, "checkout", "-q", "main")

    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_json = {
        "run_id": run_id,
        "branch": branch,
        "working_branch": "main",
        "pr_base_branch": "main",
    }
    if finished:
        run_json["finished_at"] = "2026-08-24T22:00:00+00:00"
    if no_push:
        run_json["no_push"] = True
    (run_dir / "run.json").write_text(json.dumps(run_json))
    (run_dir / "state.json").write_text(
        json.dumps({"completed_waves": 1, "waves": [["s1"]]})
    )
    return run_dir, repo


def _finalize(args, state_dir: Path, repo: Path):
    env = _launcher_env(state_dir)
    env["USER_REPO"] = str(repo)
    return _run_launcher_shared(
        args, env, launcher=REPO_ROOT_LAUNCHER, timeout=60
    )


@pytest.mark.skipif(not HAS_JQ, reason="host_finalize parses run.json with jq")
def test_finalize_local_run_without_finished_at_reaches_host_finalize(tmp_path):
    """THE reported bug: a local run interrupted before `finished_at` was
    written must finalize locally, never ask for flyctl.

    `no_push` is set so `host_finalize` short-circuits at its own early gate
    (host-finalize.sh:294) — that return is *inside* host_finalize, so
    observing it proves the launcher handed off, without this test needing a
    git remote or a rebaser worker.

    Note the existing local-shaped fixtures in
    tests/test_launcher_finalize_no_work.py cannot catch this regression:
    every one of them exits early via `_already_synced` / `pushed_at` / argv
    validation and never reaches the runtime dispatch at all.
    """
    state_dir = tmp_path / "state"
    run_dir, repo = _make_local_run(state_dir, tmp_path, "r1", no_push=True)
    assert "finished_at" not in json.loads((run_dir / "run.json").read_text())

    r = _finalize(["finalize", "r1"], state_dir, repo)

    combined = r.stdout + r.stderr
    assert "flyctl" not in combined.lower(), combined
    assert "state and branch already on this host" in r.stderr, r.stderr
    assert "no_push=true; skipping push + PR" in r.stderr, r.stderr
    assert r.returncode == 0, combined


@pytest.mark.skipif(not HAS_JQ, reason="host_finalize parses run.json with jq")
def test_finalize_local_accepts_explicit_runtime_local(tmp_path):
    """`--runtime local` used to be refused outright ("no local-runtime
    equivalent yet"). It is now a supported selector for the same arm."""
    state_dir = tmp_path / "state"
    _, repo = _make_local_run(state_dir, tmp_path, "r1", no_push=True)
    r = _finalize(["finalize", "r1", "--runtime", "local"], state_dir, repo)
    assert "no local-runtime equivalent" not in r.stderr, r.stderr
    assert "flyctl" not in (r.stdout + r.stderr).lower()
    assert r.returncode == 0, r.stdout + r.stderr


def test_finalize_local_refuses_force(tmp_path):
    """--force exists to SIGTERM a detached remote orchestrator; a local
    run's orchestrator exits with its container. Refused, not ignored — and
    with the local message, not the EC2 one."""
    state_dir = tmp_path / "state"
    _, repo = _make_local_run(state_dir, tmp_path, "r1")
    r = _finalize(["finalize", "r1", "--force"], state_dir, repo)
    assert r.returncode != 0
    assert "not supported for local runs" in r.stderr, r.stderr
    assert "flyctl-transport only" not in r.stderr, r.stderr
    assert "flyctl" not in (r.stdout + r.stderr).lower()


def test_finalize_local_fails_closed_when_run_branch_absent(tmp_path):
    """No branch and no `no_push` intent: fail with a message about the
    missing branch, not a flyctl prompt and not a confusing run.json error
    from deeper inside host_finalize."""
    state_dir = tmp_path / "state"
    _, repo = _make_local_run(state_dir, tmp_path, "r1", create_branch=False)
    r = _finalize(["finalize", "r1"], state_dir, repo)
    assert r.returncode != 0
    assert "has no run branch" in r.stderr, r.stderr
    assert "flyctl" not in (r.stdout + r.stderr).lower()


def test_finalize_fly_sidecar_still_promotes_to_fly_no_regression(tmp_path):
    """The local default must not swallow a genuine Fly run."""
    state_dir = tmp_path / "state"
    _, repo = _make_local_run(state_dir, tmp_path, "r1")
    (state_dir / "runs" / "r1" / "fly-machine.json").write_text(
        json.dumps({"fly_machine_id": "m1"})
    )
    r = _finalize(["finalize", "r1"], state_dir, repo)
    assert "auto-detected Fly run; promoting --runtime to fly" in r.stderr
    assert "state and branch already on this host" not in r.stderr
    assert r.returncode != 0
    assert "LEERIE_FLY_APP is required" in r.stderr, r.stderr


def test_finalize_usage_strings_offer_local(tmp_path):
    """Paired with the behavioural tests above: the advertised enum must
    match what the arm now accepts. Driven through a real rejection so this
    asserts what a user is actually shown, not a source substring."""
    state_dir = tmp_path / "state"
    _, repo = _make_local_run(state_dir, tmp_path, "r1")
    r = _finalize(["finalize", "r1", "--foorce"], state_dir, repo)
    assert r.returncode != 0
    assert "--runtime local|fly|ec2" in r.stderr, r.stderr
