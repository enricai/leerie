"""Tests for the generalized runtime auto-detection helper in `leerie`.

DESIGN §6 "Run identifier" flags that the historical `_auto_detect_fly_runtime`
helper (and the `--runtime` enum validation on `--stop`, `--kill`,
`--accept-blocked`, `--finalize`, and `--resume`) is hardcoded to
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
subtask's scope note names, plus each verb's EC2 dispatch behavior: `--stop`
and `--kill` have real EC2 actions wired (test-001/feat-005 and feat-006
respectively) and so proceed past the detection gate into the action itself;
`--accept-blocked` and `--finalize` still fail closed with a "does not
support EC2 runs yet" message; `--resume` fails closed with its own
resume-specific message. None of these verbs need `LEERIE_FLY_APP`/stubbed
`flyctl`/`aws` to reach the enum-validation and detection-promotion
assertions, since those fire before any remote dispatch.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

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
    (e.g. --resume) rely on this to avoid misrouting."""
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


def _run_launcher(args: list[str], state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(REPO_ROOT_LAUNCHER)] + args,
        env=_launcher_env(state_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_stop_rejects_bogus_runtime_value(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _run_launcher(["--stop", "r1", "--runtime", "bogus"], state_dir)
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


def test_stop_accepts_explicit_ec2_enum_but_needs_a_sidecar(tmp_path):
    """--runtime ec2 must clear the enum-validation gate (not rejected as
    unknown). --stop's EC2 action is wired (test-001/feat-005) and resolves
    AWS credentials before resolving ec2_instance_id from the sidecar, so
    it proceeds past enum validation and fails on credential resolution
    (no `aws` binary / credentials set up in this test's env) rather than
    an unknown-runtime rejection."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _run_launcher(["--stop", "r1", "--runtime", "ec2"], state_dir)
    assert r.returncode != 0
    assert "must be" not in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_stop_autodetects_ec2_sidecar_and_proceeds_past_detection(tmp_path):
    """--stop's EC2 action is wired (test-001/feat-005), so an
    auto-detected EC2 run proceeds past the detection gate into AWS
    credential resolution (which fails here since no `aws` binary /
    credentials are set up in this test's env, unrelated to detection).
    The full stop happy path is covered end-to-end in
    tests/test_ec2_launcher_stop.py against a stubbed `aws`."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _run_launcher(["--stop", "r1"], state_dir)
    assert r.returncode != 0
    assert "auto-detected ec2 run" in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_stop_fly_sidecar_still_promotes_to_fly_no_regression(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", fly=True)
    r = _run_launcher(["--stop", "r1"], state_dir)
    # No LEERIE_FLY_APP set, so it still fails — but via the pre-existing
    # Fly-specific error, proving detection promoted to "fly" and reached
    # the Fly branch rather than the (now EC2-aware) fallthrough.
    assert r.returncode != 0
    assert "auto-detected fly run" in r.stderr
    assert "LEERIE_FLY_APP is required" in r.stderr


def test_kill_accepts_explicit_ec2_enum_but_needs_a_sidecar(tmp_path):
    """feat-006 wires --kill's EC2 action (terminate_instance with
    fetch-before-terminate ordering — tests/test_ec2_launcher_kill.py),
    so --runtime ec2 no longer fails closed with "does not support EC2
    runs yet" — it now requires an ec2_instance_id to act on, which this
    run dir (no ec2-instance.json / run.json ec2_instance_id) does not
    have."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _run_launcher(["--kill", "r1", "--runtime", "ec2", "--force"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" not in r.stderr
    assert "no ec2_instance_id found" in r.stderr


def test_kill_autodetects_ec2_sidecar_and_proceeds_past_detection(tmp_path):
    """feat-006 wires --kill's EC2 action, so an auto-detected EC2 run no
    longer fails closed at the detection gate — it proceeds to resolve
    AWS credentials (which fails here since no `aws` binary / credentials
    are set up in this test's env, unrelated to detection). The full
    fetch-before-terminate happy path is covered end-to-end in
    tests/test_ec2_launcher_kill.py against a stubbed `aws`."""
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _run_launcher(["--kill", "r1", "--force"], state_dir)
    assert r.returncode != 0
    assert "auto-detected ec2 run" in r.stderr
    assert "does not support EC2 runs yet" not in r.stderr


def test_accept_blocked_rejects_bogus_runtime_value(tmp_path):
    state_dir = tmp_path / "state"
    run_dir = _make_e2e_run(state_dir, "r1")
    (run_dir / "state.json").write_text(
        json.dumps({"subtask_status": {"s1": "blocked"}})
    )
    r = _run_launcher(
        ["--accept-blocked", "r1", "s1", "--runtime", "bogus"], state_dir
    )
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


def test_accept_blocked_autodetects_ec2_sidecar(tmp_path):
    # --accept-blocked's EC2 action is wired (see
    # tests/test_ec2_launcher_readonly_verbs.py for its end-to-end
    # coverage against a stubbed aws), so detection no longer fails
    # closed. As with the --stop/--kill cases above, this env has no aws
    # binary or credentials: reaching AWS credential resolution is the
    # proof that detection promoted to ec2 and entered the EC2 branch
    # rather than silently defaulting to local.
    state_dir = tmp_path / "state"
    run_dir = _make_e2e_run(state_dir, "r1", ec2=True)
    (run_dir / "state.json").write_text(
        json.dumps({"subtask_status": {"s1": "blocked"}})
    )
    r = _run_launcher(["--accept-blocked", "r1", "s1"], state_dir)
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
    r = _run_launcher(["--finalize", "r1", "--runtime", "bogus"], state_dir)
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in r.stderr


def test_finalize_accepts_explicit_ec2_enum_but_fails_closed(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1")
    r = _run_launcher(["--finalize", "r1", "--runtime", "ec2"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" in r.stderr


def test_finalize_autodetects_ec2_sidecar_and_fails_closed(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True)
    r = _run_launcher(["--finalize", "r1"], state_dir)
    assert r.returncode != 0
    assert "does not support EC2 runs yet" in r.stderr


def test_resume_autodetects_ec2_sidecar_and_fails_closed(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", ec2=True, with_state=True)
    r = _run_launcher(["--resume", "r1"], state_dir)
    assert r.returncode != 0
    assert "ec2-instance.json present" in r.stderr
    assert "does not support EC2 runs yet" in r.stderr


def test_resume_fly_sidecar_still_promotes_to_fly_no_regression(tmp_path):
    state_dir = tmp_path / "state"
    _make_e2e_run(state_dir, "r1", fly=True, with_state=True)
    r = _run_launcher(["--resume", "r1"], state_dir)
    assert r.returncode != 0
    assert "auto-detected Fly run" in r.stderr
    assert "LEERIE_FLY_APP is required" in r.stderr
