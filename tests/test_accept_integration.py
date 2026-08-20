"""Tests for the accept-integration launcher verb (local path).

Mirrors tests/test_accept_blocked.py's local-path harness exactly — the
verb has the identical shape (same allowlist validation, same
ACCEPTED:/NOOP:/ERROR: sentinel convention, same local/Fly/EC2
dispatch) but mutates a different field: `state.data["integration_gate"]
[sid]["accepted"]`, flipped so `integrate_wave`'s resume-time gate check
(orchestrator/leerie.py) stops re-invoking `integration_judge` for that
sid and treats the already-committed merge as fine to advance past.

The classes below (N23) go one step further than the launcher-only tests
above: they compose a REAL `leerie accept-integration` invocation with a
REAL in-process `integrate_wave` call (against a real staging git repo),
mirroring `tests/test_wiring_gate_resume.py`'s gate-passed-skips-the-gate
shape for `wiring_gate`. `tests/test_integrate_wave_judge_gate.py`'s
`test_accepted_finding_skips_the_judge_entirely` already pins this
resume-skip behavior against *hand-set* state; the composition tests here
additionally prove the real launcher verb produces that exact state
shape, and that `--skip-integration-check` bypasses the gate independent
of the accept-integration/audit-key mechanism.

Falsify by reverting the audit-key write (either in the launcher's
`_ai_mutate` script or in `_run_integration_judge_gate`'s persistence):
`test_accept_then_resume_skips_the_judge` must then fail by re-invoking
the judge.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

from functools import partial

from tests.test_accept_blocked import (
    _expected_remote_state,
    _flyctl_stub,
    _make_state as _make_state_base,
    _run_accept,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# accept-integration's local-path invocation shares _run_accept's argv
# shape with accept-blocked (tests/test_accept_blocked.py), differing
# only in which launcher verb is dispatched.
_run_accept = partial(_run_accept, verb="accept-integration")

# accept-integration writes state.json's "integration_gate" field where
# accept-blocked writes "subtask_status" -- everything else about state
# construction is shared.
_make_state = partial(_make_state_base, key="integration_gate")


def test_accepts_a_rejected_finding(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["INTEGRATION_DEFECT dropped call site"],
                      "advisories": [], "merge_commit_sha": "abc123",
                      "accepted": False}},
        integration_defects={"feat-001": ["INTEGRATION_DEFECT dropped call site"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is True
    assert "feat-001" not in st.get("integration_defects", {})


def test_cleans_up_empty_integration_defects_dict(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "abc123", "accepted": False}},
        integration_defects={"feat-001": ["x"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert "integration_defects" not in st


def test_preserves_other_defect_entries(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False},
         "feat-002": {"defects": ["y"], "advisories": [],
                      "merge_commit_sha": "b", "accepted": False}},
        integration_defects={"feat-001": ["x"], "feat-002": ["y"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is True
    assert st["integration_gate"]["feat-002"]["accepted"] is False
    assert st["integration_defects"] == {"feat-002": ["y"]}


def test_noop_on_already_accepted(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": [], "advisories": [],
                      "merge_commit_sha": "abc123", "accepted": True}})
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0
    assert "already accepted" in r.stdout


def test_errors_on_unknown_subtask(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    r = _run_accept(sf, "nonexistent")
    assert r.returncode != 0
    assert "not found" in r.stderr


def test_errors_on_missing_state_file(tmp_path):
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    sf = run_dir / "state.json"
    r = _run_accept(sf, "feat-001")
    assert r.returncode != 0
    assert "no state.json" in r.stderr


def test_errors_on_missing_run_dir(tmp_path):
    """No run dir at all -- distinct from test_errors_on_missing_state_file
    above, which has a run dir but no state.json inside it."""
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(tmp_path)
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", "never-existed",
         "feat-001", "--runtime", "local"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "no run dir" in r.stderr


def test_rejects_invalid_runtime_value(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(sf.parent.parent.parent)
    run_id = sf.parent.name
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", run_id, "feat-001",
         "--runtime", "bogus"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in (r.stdout + r.stderr)
    assert json.loads(sf.read_text())["integration_gate"]["feat-001"]["accepted"] is False


def test_fly_runtime_requires_app_env(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(sf.parent.parent.parent)
    env.pop("LEERIE_FLY_APP", None)
    run_id = sf.parent.name
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", run_id, "feat-001",
         "--runtime", "fly"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "LEERIE_FLY_APP is required" in (r.stdout + r.stderr)
    assert json.loads(sf.read_text())["integration_gate"]["feat-001"]["accepted"] is False


def test_records_accepted_defects_and_timestamp(tmp_path):
    """The accepted-finding record must preserve what was waived (the
    dropped defects list) and when, not just flip the bool
    (docs/POSTMORTEM-2026-08-14.md, F16)."""
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["INTEGRATION_DEFECT dropped call site"],
                      "advisories": [], "merge_commit_sha": "abc123",
                      "accepted": False}},
        integration_defects={"feat-001": ["INTEGRATION_DEFECT dropped call site"]},
    )
    r = _run_accept(sf, "feat-001")
    assert r.returncode == 0, r.stderr
    entry = json.loads(sf.read_text())["integration_gate"]["feat-001"]
    assert entry["accepted"] is True
    assert entry["accepted_at"]
    assert entry["accepted_defects"] == ["INTEGRATION_DEFECT dropped call site"]


def test_rejects_invalid_sid_before_touching_filesystem(tmp_path):
    sf = _make_state(
        tmp_path,
        {"feat-001": {"defects": ["x"], "advisories": [],
                      "merge_commit_sha": "a", "accepted": False}})
    r = _run_accept(sf, "feat/../001")
    assert r.returncode != 0
    assert "invalid subtask-id" in r.stderr
    # state.json must be untouched
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is False


# --- STATE_FIELDS granularity: read the real entry, don't assume ----------- #

def test_integration_gate_is_declared_per_sid_keyed_by_sid(leerie):
    """The producing domain decides per-sid vs per-run granularity; this
    reads STATE_FIELDS and the real write site rather than assuming.
    `integration_gate` is a dict keyed by subtask id (verified against
    `_run_integration_judge_gate`'s own write:
    `st.data.setdefault("integration_gate", {})[sid] = {...}`), matching
    what `_run_accept`/`_make_state` above already assume."""
    assert "integration_gate" in leerie.STATE_FIELDS
    import inspect
    src = inspect.getsource(leerie._run_integration_judge_gate)
    assert 'st.data.setdefault("integration_gate", {})[sid] = {' in src


# --- end-to-end composition: real launcher write -> real integrate_wave read #

MODELS = {"integrator": "sonnet", "integration_judge": "opus"}
EFFORTS = {"integrator": "low", "integration_judge": "medium"}


def _staging_repo(leerie_dir: Path) -> Path:
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                    capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                    cwd=str(staging), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                    cwd=str(staging), check=True, capture_output=True)
    (staging / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=str(staging), check=True,
                    capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(staging),
                    check=True, capture_output=True)
    return staging


def _caps(leerie) -> dict:
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


def _rejected_gate_state(sid: str, merge_sha: str = "abc123") -> dict:
    return {
        "task": "t", "worker_count": 0,
        "integration_gate": {
            sid: {
                "defects": ["INTEGRATION_DEFECT (dropped_change) x"],
                "advisories": [],
                "merge_commit_sha": merge_sha,
                "accepted": False,
            }
        },
        "integration_defects": {
            sid: ["INTEGRATION_DEFECT (dropped_change) x"]
        },
    }


def _run_accept_in_leerie_dir(leerie_dir: Path, run_id: str,
                               sid: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(leerie_dir)
    return subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", run_id, sid,
         "--runtime", "local"],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_accept_then_resume_skips_the_judge(leerie, tmp_path, monkeypatch):
    """THE LOAD-BEARING PIN. A real `leerie accept-integration` invocation
    writes state.json to accepted=True; a subsequent real `integrate_wave`
    call reads that exact file back and must not re-invoke
    integration_judge, mirroring
    `test_wiring_gate_resume.py::test_resume_after_gate_passed_skips_the_gate`'s
    gate-passed-skips-the-gate shape."""
    sid = "feat-001"
    leerie_dir = tmp_path / ".leerie"
    run_id = "acc-int-e2e"
    (leerie_dir / "runs" / run_id).mkdir(parents=True)
    state_file = leerie_dir / "runs" / run_id / "state.json"
    state_file.write_text(json.dumps(_rejected_gate_state(sid), indent=2))
    _staging_repo(leerie_dir)

    # Real launcher invocation — not a hand-edited fixture.
    r = _run_accept_in_leerie_dir(leerie_dir, run_id, sid)
    assert r.returncode == 0, r.stderr
    assert json.loads(state_file.read_text())[
        "integration_gate"][sid]["accepted"] is True

    st = leerie.State(leerie_dir, run_id)
    st.load()
    results = {sid: {"status": "complete"}}
    calls = {"judge": 0, "integrate_sh": 0}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            calls["judge"] += 1
        return {}

    async def fake_run_script(script, s, rid):
        calls["integrate_sh"] += 1
        mock = AsyncMock()
        mock.returncode = 0
        mock.stderr = ""
        mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)

    async def _run():
        return await leerie.integrate_wave(
            [sid], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert calls["judge"] == 0, (
        "a resume after accept-integration must not re-invoke "
        "integration_judge for the accepted sid")
    assert calls["integrate_sh"] == 0, (
        "an accepted finding must not re-drive integrate.sh either")
    assert sid in out


def test_without_accept_resume_reinvokes_the_judge(
        leerie, tmp_path, monkeypatch):
    """Anti-vacuity control for the pin above: with no accept-integration
    run, the same resume must re-invoke the judge — proving the prior test
    measures the accept, not a dead stub."""
    sid = "feat-001"
    leerie_dir = tmp_path / ".leerie"
    run_id = "acc-int-e2e-2"
    (leerie_dir / "runs" / run_id).mkdir(parents=True)
    (leerie_dir / "runs" / run_id / "state.json").write_text(
        json.dumps(_rejected_gate_state(sid), indent=2))
    _staging_repo(leerie_dir)

    st = leerie.State(leerie_dir, run_id)
    st.load()
    results = {sid: {"status": "complete"}}
    calls = {"judge": 0}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            calls["judge"] += 1
            return {"merge_reviewed": True, "defects": [],
                    "rationale": "clean on re-review"}
        return {}

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = "abc123\n" if "rev-parse" in cmd else "Merge\n\ndiff\n"
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    async def _run():
        return await leerie.integrate_wave(
            [sid], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert calls["judge"] == 1
    assert sid in out


def test_skip_integration_check_reaches_finalize_with_zero_judge_calls(
        leerie, tmp_path, monkeypatch):
    """`--skip-integration-check` suppresses the gate entirely, independent
    of the accept-integration/audit-key mechanism — even a sid with a
    PRIOR rejected finding must not re-invoke the judge when the flag is
    set, and integrate_wave must still reach a normal return (the
    finalize-reaching path) rather than dying, verified via a stubbed
    claude_p call count."""
    sid = "feat-001"
    leerie_dir = tmp_path / ".leerie"
    run_id = "acc-int-skip"
    data = _rejected_gate_state(sid)
    data["skip_integration_check"] = True
    (leerie_dir / "runs" / run_id).mkdir(parents=True)
    (leerie_dir / "runs" / run_id / "state.json").write_text(
        json.dumps(data, indent=2))
    _staging_repo(leerie_dir)

    st = leerie.State(leerie_dir, run_id)
    st.load()
    results = {sid: {"status": "complete"}}
    calls = {"judge": 0}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            calls["judge"] += 1
        return {}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    async def _run():
        return await leerie.integrate_wave(
            [sid], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert calls["judge"] == 0, (
        "--skip-integration-check must short-circuit before any "
        "integration_judge invocation, even for a sid with a prior "
        "rejected finding")
    assert sid in out, "the skip must still let the sid reach finalize"


# ---------------------------------------------------------------------------
# Remote transports. `accept-integration` mirrors `accept-blocked`'s
# local/Fly/EC2 machinery, and until now only the local arm was exercised --
# the same coverage asymmetry that let three structurally-identical copies
# drift elsewhere in this repo. The Fly stub is imported from
# tests/test_accept_blocked.py rather than reimplemented (the convention
# tests/test_fetch_branch_leerie_streamback.py follows), so a change to the
# transport's shape breaks both files together instead of one silently.
# ---------------------------------------------------------------------------

def _run_accept_integration_fly(state_path: Path, sid: str,
                                flyctl: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_path.parent.parent.parent)
    env["LEERIE_FLY_APP"] = "test-app"
    env["LEERIE_NONINTERACTIVE"] = "1"
    env["MACHINE_START_TIMEOUT"] = "10"
    env["PATH"] = f"{flyctl.parent}:{env.get('PATH', '')}"
    run_id = state_path.parent.name
    return subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-integration", run_id, sid,
         "--runtime", "fly"],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_fly_path_accepts_an_integration_finding(tmp_path):
    """Wakes the machine, pipes the mutate program over stdin, passes both
    `-C` positionals correctly (the stub asserts arg1 is the expected remote
    state path), and flips `accepted` on the machine's copy."""
    sf = _make_state(tmp_path, {
        "feat-001": {"defects": [{"what": "races on shutdown"}],
                     "accepted": False},
    })
    flyctl = _flyctl_stub(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_integration_fly(sf, "feat-001", flyctl)

    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    st = json.loads(sf.read_text())
    assert st["integration_gate"]["feat-001"]["accepted"] is True
    assert "ACCEPTED:" in (r.stdout + r.stderr)


def test_fly_path_reports_noop_on_already_accepted(tmp_path):
    sf = _make_state(tmp_path, {
        "feat-001": {"defects": [], "accepted": True},
    })
    flyctl = _flyctl_stub(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_integration_fly(sf, "feat-001", flyctl)

    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "NOOP:" in (r.stdout + r.stderr)


def test_fly_path_rejects_an_injection_sid_before_any_transport(tmp_path):
    """The `-C` string is double-parsed by the remote shell, so the sid
    allowlist has to run before flyctl is invoked at all."""
    sf = _make_state(tmp_path, {"feat-001": {"defects": [], "accepted": False}})
    flyctl = _flyctl_stub(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_integration_fly(sf, "feat-001'; touch pwned; '", flyctl)

    assert r.returncode != 0
    assert "invalid subtask-id" in (r.stdout + r.stderr)
    assert not (tmp_path / "pwned").exists()
    assert json.loads(sf.read_text())["integration_gate"]["feat-001"]["accepted"] is False


def test_all_three_runtimes_are_wired_for_accept_integration():
    """Structural pin, mirroring test_accept_blocked.py's transport sweep:
    the verb must dispatch local, Fly AND EC2. A missing arm fails closed at
    runtime with an unrelated error, which is hard to attribute."""
    src = (REPO_ROOT / "leerie").read_text()
    start = src.index("  accept-integration)")
    end = src.index("\n  *)", start)
    arm = src[start:end]
    for needle in ("ec2_remote_exec", "flyctl", "RUNTIME"):
        assert needle in arm, (
            f"accept-integration's launcher arm never mentions {needle!r} -- "
            "one of the three transports is unwired")
