"""`leerie finalize` for EC2 runs — the launcher arm, driven end to end.

`finalize` refused EC2 runs outright until this change ("finalize does not
support EC2 runs yet"), on both the explicit `--runtime ec2` path and the
`ec2-instance.json` autodetect path. The EC2 arm now streams the completed run
back with `fetch_state_ec2()` (the documented analog of `fetch_branch()`) and
falls through to the same `host_finalize` the Fly path uses.

Two behaviours here are load-bearing and easy to get wrong:

**Waking.** A *stopped* instance has no reachable SSH/SSM target, and EC2
reassigns the public IP on every stop/start cycle — which is why
`resume_instance()` re-resolves it. So a paused run must be woken before
`fetch_state_ec2` can reach it, and re-stopped afterwards **only if finalize is
what woke it**. `accept-blocked`'s EC2 arm already does this wake → act →
re-pause-if-we-woke-it dance; this mirrors it rather than inventing one.

**The teardown trap.** `resume_instance()` re-arms
`trap 'decide_ec2_teardown' EXIT INT TERM` for the normal launch path. Inside
`finalize` that trap could TERMINATE the instance on exit — destroying the very
run being finalized. The arm neutralizes it two ways (the
`LEERIE_TEARDOWN_DONE` short-circuit plus clearing the trap), and
`test_finalize_never_terminates_the_instance` is the pin.

`--force` is deliberately refused: the Fly force path drives
`force_finalize_remote()` and `collect_subtrees_remote()`, both `flyctl
ssh console`-transport with zero EC2 references. Finalizing without collecting
un-integrated subtask branches would push an INCOMPLETE branch, so failing
closed is the honest outcome, not a TODO.

These drive the **real** `leerie` binary rather than an extracted block —
`finalize` exits inside the early verb-dispatch region, so it is reachable the
way `stop`/`kill` are. That distinction matters: `tests/test_ec2_launcher_resume.py`
documents how an extracted-block test stayed green against an unreachable path.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
RUN_ID = "ec2-run-finalize-0001"


def _repo(tmp_path: Path) -> Path:
    """finalize inspects USER_REPO with git, so it must be a real repo."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"],
                   check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"],
                   check=True)
    return tmp_path


def _fixture(tmp_path: Path, *, instance_state: str = "stopped",
             with_iid: bool = True):
    _repo(tmp_path)
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = "i-00000000000000077"
    st = read_state(aws_dir)
    st["instances"][iid] = {"state": instance_state, "public_ip": "203.0.113.7"}
    (aws_dir / "state.json").write_text(json.dumps(st))

    state_dir = tmp_path / ".leerie" / "myrepo"
    run_dir = state_dir / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    sidecar = {"region": "us-east-1", "run_id": RUN_ID}
    if with_iid:
        sidecar["ec2_instance_id"] = iid
    (run_dir / "ec2-instance.json").write_text(json.dumps(sidecar))
    (run_dir / "run.json").write_text(json.dumps(
        {"run_id": RUN_ID, "branch": f"leerie/runs/{RUN_ID}",
         **({"ec2_instance_id": iid} if with_iid else {})}))

    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(home),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "AWS_ACCESS_KEY_ID": "AKIASTUBFIXTURE",
        "AWS_SECRET_ACCESS_KEY": "stubfixturesecret",
        "AWS_REGION": "us-east-1",
    }
    return env, aws_dir, run_dir, iid


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(LAUNCHER)] + args,
                          env=env, capture_output=True, text=True, timeout=120)


class TestTheArmIsReachable:
    def test_autodetected_ec2_run_promotes_and_enters_the_ec2_arm(self, tmp_path):
        env, _, _, _ = _fixture(tmp_path)
        err = _run(["finalize", RUN_ID], env).stderr or ""
        assert "promoting --runtime to ec2" in err, err
        assert "does not support EC2 runs yet" not in err, err

    def test_explicit_runtime_ec2_is_accepted(self, tmp_path):
        env, _, _, _ = _fixture(tmp_path)
        err = _run(["finalize", RUN_ID, "--runtime", "ec2"], env).stderr or ""
        assert "does not support EC2 runs yet" not in err, err

    def test_the_retired_failclosed_strings_are_gone(self):
        src = LAUNCHER.read_text()
        assert "finalize does not support EC2 runs yet" not in src
        assert "is an EC2 run; finalize does not support EC2" not in src


class TestForceIsRefused:
    """A transport boundary, not a TODO — see the module docstring."""

    def test_force_fails_closed_with_an_actionable_reason(self, tmp_path):
        env, aws_dir, _, _ = _fixture(tmp_path)
        r = _run(["finalize", RUN_ID, "--force"], env)
        err = r.stderr or ""
        assert r.returncode != 0
        assert "--force is not supported for EC2" in err, err
        assert "flyctl-transport only" in err, err

    def test_force_refusal_happens_before_any_aws_call(self, tmp_path):
        """Refusing after waking an instance would be worse than refusing."""
        env, aws_dir, _, _ = _fixture(tmp_path)
        _run(["finalize", RUN_ID, "--force"], env)
        assert read_log(aws_dir) == [], read_log(aws_dir)


class TestFailClosed:
    def test_missing_instance_id_fails_closed(self, tmp_path):
        env, aws_dir, _, _ = _fixture(tmp_path, with_iid=False)
        r = _run(["finalize", RUN_ID], env)
        assert r.returncode != 0
        assert "no ec2_instance_id found" in (r.stderr or ""), r.stderr

    def test_failed_fetch_does_not_push(self, tmp_path):
        """One-way ratchet: no transport stub is on PATH, so fetch_state_ec2
        cannot succeed — and nothing may be pushed on that path."""
        env, _, run_dir, _ = _fixture(tmp_path)
        r = _run(["finalize", RUN_ID], env)
        assert r.returncode != 0
        assert "nothing pushed" in (r.stderr or ""), r.stderr
        assert "pushed_at" not in json.loads((run_dir / "run.json").read_text())


class TestWakeCycle:
    def test_stopped_instance_is_woken_then_restopped(self, tmp_path):
        env, aws_dir, _, iid = _fixture(tmp_path, instance_state="stopped")
        _run(["finalize", RUN_ID], env)
        log = read_log(aws_dir)
        assert any(l.startswith("ec2 start-instances") for l in log), log
        assert any(l.startswith("ec2 stop-instances") for l in log), (
            "finalize woke the instance, so it must put it back", log)
        assert read_state(aws_dir)["instances"][iid]["state"] == "stopped"

    def test_running_instance_is_NOT_stopped(self, tmp_path):
        """ANTI-VACUITY for the wake cycle: re-stopping an instance the
        operator left running would pause a live run behind their back.

        Scope note — this exercises the **failed-fetch** re-stop guard, not
        the success-path one. No transport stub is on PATH, so
        `fetch_state_ec2` always fails here and the arm exits through its
        failure branch. Verified by mutation: flipping the failed-fetch guard
        to `if true` fails this test, while flipping the *success-path* guard
        does not — that line is unreachable from this fixture.
        `test_both_restop_sites_are_guarded_on_having_woken_it` below is what
        covers the other one."""
        env, aws_dir, _, iid = _fixture(tmp_path, instance_state="running")
        _run(["finalize", RUN_ID], env)
        log = read_log(aws_dir)
        assert not any(l.startswith("ec2 start-instances") for l in log), log
        assert not any(l.startswith("ec2 stop-instances") for l in log), log
        assert read_state(aws_dir)["instances"][iid]["state"] == "running"

    def test_both_restop_sites_are_guarded_on_having_woken_it(self):
        """The EC2 arm re-stops in TWO places — after a failed fetch and after
        a successful one — and only the first is behaviourally reachable
        without a full SSM/SSH transport stub. An unguarded success-path
        re-stop would pause a live run the operator started themselves, and no
        test in this file would notice, so pin it structurally.

        Counts `stop_instance` calls in the arm and requires each to sit under
        an `_fin_ec2_woke` test."""
        src = LAUNCHER.read_text()
        start = src.index('elif [ "$_fin_runtime" = "ec2" ]; then')
        arm = src[start:src.index("\n    else\n", start)]
        calls = [ln for ln in arm.splitlines() if "stop_instance" in ln]
        assert len(calls) == 2, (
            f"expected exactly 2 stop_instance sites in the EC2 arm, got "
            f"{len(calls)}: {calls}")
        guards = arm.count('[ "$_fin_ec2_woke" = "true" ]')
        assert guards == 2, (
            f"each stop_instance must be guarded on having woken the instance; "
            f"found {guards} guard(s) for {len(calls)} call(s)")


class TestAwsArgsAreNotWordSplit:
    """`_aws_region_profile_args` must never be expanded as bare `$(...)`.

    A named AWS profile may legitimately contain spaces, so word splitting
    turns `--profile "My Dev Profile"` into four arguments. The helper's own
    docstring calls this out and every caller in `ec2-provision.sh` uses the
    one-token-per-line `while IFS= read -r … < <(…)` idiom instead.

    The finalize state probe first shipped as bare `$(...)`. Consequence: the
    probe returns empty, which reads as "not stopped", so a paused instance is
    never woken and the fetch then runs against an unreachable target —
    silent, and wrong in the direction that loses work.

    **This is a source guard rather than a behavioural test, deliberately.**
    Two behavioural attempts were written and both were inadequate:
    `tests/ec2_stub.py` logs argv **space-joined**
    (`" ".join(argv)`), so a shredded value is textually identical to an
    intact one; and the stub ignores `--profile` altogether, so the probe
    still succeeds with the bug present — a consequence test passes either
    way. Rather than reshape a fixture a dozen other modules depend on, pin
    the defect class at the source, repo-wide.

    Nothing else covers this: CI's shellcheck job lints `scripts/*.sh` and
    `scripts/remote/*.sh` but
    **not** the `leerie` launcher, so SC2046 never fires on it either.
    """

    @staticmethod
    def _callers(code_only: bool = True) -> list[tuple[str, str]]:
        """Call sites of the helper. With `code_only`, comment lines are
        skipped — the guard checks what bash executes, not what humans wrote
        about it. That distinction is not pedantic: the first version of this
        guard flagged the very comment *below the fix* explaining why the bare
        form is wrong, which is the same way `not yet wired` comments trip the
        launcher's own grep guards."""
        paths = [REPO_ROOT / "leerie"]
        paths += sorted((REPO_ROOT / "scripts" / "remote").glob("*.sh"))
        out = []
        for p in paths:
            for line in p.read_text().splitlines():
                if "_aws_region_profile_args" not in line:
                    continue
                if code_only and line.lstrip().startswith("#"):
                    continue
                out.append((p.name, line.strip()))
        return out

    def test_no_bare_command_substitution_of_the_helper(self):
        offenders = [
            (name, line) for name, line in self._callers()
            if "$(_aws_region_profile_args)" in line
            and '"$(_aws_region_profile_args)"' not in line
        ]
        assert not offenders, (
            "expand _aws_region_profile_args one token per line — a profile "
            f"name with spaces would be split: {offenders}")

    def test_the_guard_actually_found_call_sites(self):
        """ANTI-VACUITY: a scan that finds nothing passes every assertion."""
        names = {n for n, _ in self._callers()}
        assert "leerie" in names, "no launcher call site found — scan is broken"
        assert any(n.startswith("ec2-") for n in names), names

    def test_the_comment_skip_is_load_bearing_not_decorative(self):
        """ANTI-VACUITY for the skip itself: a commented occurrence must
        exist, or `code_only` is untested and could silently stop working."""
        commented = [
            (n, l) for n, l in self._callers(code_only=False)
            if l.startswith("#")]
        assert commented, (
            "no commented occurrence found — if the explanatory comment was "
            "removed, drop the code_only filter too rather than leaving an "
            "untested branch")

    def test_finalize_arm_uses_the_documented_idiom(self):
        src = LAUNCHER.read_text()
        start = src.index('elif [ "$_fin_runtime" = "ec2" ]; then')
        arm = src[start:src.index("\n    else\n", start)]
        assert "while IFS= read -r" in arm and "< <(_aws_region_profile_args)" in arm
        assert '${_fin_ec2_aws_args[@]+"${_fin_ec2_aws_args[@]}"}' in arm


class TestOneWayRatchet:
    def test_finalize_never_terminates_the_instance(self, tmp_path):
        """The teardown-trap pin. `resume_instance()` arms
        `trap decide_ec2_teardown EXIT INT TERM`; if that fired inside
        finalize it could terminate the run being finalized."""
        for state in ("stopped", "running"):
            env, aws_dir, _, iid = _fixture(
                tmp_path / state, instance_state=state)
            _run(["finalize", RUN_ID], env)
            log = read_log(aws_dir)
            assert not any(l.startswith("ec2 terminate-instances")
                           for l in log), (state, log)
            assert read_state(aws_dir)["instances"][iid]["state"] != "terminated"

    def test_no_volume_is_ever_deleted(self, tmp_path):
        env, aws_dir, _, _ = _fixture(tmp_path)
        _run(["finalize", RUN_ID], env)
        assert not any(l.startswith("ec2 delete-volume")
                       for l in read_log(aws_dir))


class TestFlyPathUnchanged:
    def test_fly_autodetect_still_promotes(self, tmp_path):
        """The EC2 arm shares an if/elif with the Fly arm; a malformed edit
        would silently break Fly finalize, which has more users."""
        _repo(tmp_path)
        aws_dir = tmp_path / "bin"
        aws_dir.mkdir()
        _stub_aws(aws_dir)
        state_dir = tmp_path / ".leerie" / "myrepo"
        run_dir = state_dir / "runs" / "fly-fin-0001"
        run_dir.mkdir(parents=True)
        (run_dir / "fly-machine.json").write_text(json.dumps(
            {"fly_machine_id": "5683dcd0", "run_id": "fly-fin-0001"}))
        home = tmp_path / "home"
        home.mkdir()
        env = {"PATH": f"{aws_dir}:/usr/bin:/bin", "USER_REPO": str(tmp_path),
               "LEERIE_REPO": str(REPO_ROOT), "HOME": str(home),
               "LEERIE_STATE_HOST_DIR": str(state_dir),
               "LEERIE_STATE_DIR": str(state_dir)}
        err = _run(["finalize", "fly-fin-0001"], env).stderr or ""
        assert "promoting --runtime to fly" in err, err
        assert "promoting --runtime to ec2" not in err, err
