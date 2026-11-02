"""A resumed run continues under the choices it was started with.

DESIGN §6 *A resumed run continues under the choices it was started with*;
IMPLEMENTATION §2½ *Run choices on resume*.

The defect this pins: `resume` re-resolved every run-level choice from the
resume's own command line, env and `leerie.toml`, and overwrote what the
fresh run had recorded. A run started with `--no-push` and resumed with a
plain `leerie resume <id>` wrote `run.json.no_push=false` at finalize, and
the host pushed it. Every skip flag, cap and model went the same way.

The end-to-end tests drive the real `main()` twice on one run directory: a
fresh run (through the real `_run_phases` state seed, stopped at the
containment gate) and then a `resume` (stopped at the `_orchestrate`
boundary, where every choice has been resolved and continued). The env on
the resume deliberately DISAGREES with the recorded choices, so a test can
tell a continued value from a re-resolved one. Host-side: the real
`host_finalize` and the launcher's real NO_PUSH block read what `main()`
recorded.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ, fake_claude_on_path, init_git_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
LAUNCHER = REPO_ROOT / "leerie"
IMPL_MD = REPO_ROOT / "docs" / "IMPLEMENTATION.md"
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"

RUN_ID = "run-choices-001"


# --------------------------------------------------------------------------
# Real main(): fresh, then resume
# --------------------------------------------------------------------------

class _ReachedGate(Exception):
    """The fresh run's state seed has been evaluated and saved."""


class _Captured(Exception):
    """The resume reached `_orchestrate` with every choice resolved."""


@pytest.fixture
def run_env(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    repo = init_git_repo(tmp_path / "repo")
    fake_claude_on_path(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    state = tmp_path / "state"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    for name in list(os.environ):
        if name.startswith(("LEERIE_MODEL", "LEERIE_EFFORT")) or name in (
                "LEERIE_NO_PUSH", "LEERIE_MAX_WORKERS",
                "LEERIE_SKIP_OVERLAP_JUDGE", "LEERIE_CONFIDENCE_ROUNDS"):
            monkeypatch.delenv(name, raising=False)
    return repo, state / "runs" / RUN_ID


def _fresh(leerie, monkeypatch, argv: list[str]) -> None:
    """Real `main()` on a fresh run, through the real `_run_phases` seed."""
    async def _orchestrate(args, caps, leerie_dir, st, sot_pref, verbosity,
                           models, efforts):
        try:
            await leerie._run_phases(args, caps, leerie_dir, st, sot_pref,
                                     verbosity, models, efforts)
        finally:
            # The pytest traceback keeps `st` alive; its flock would refuse
            # the resume below, as a live orchestrator's does.
            st.release_lock()

    def _gate(*_a, **_k):
        raise _ReachedGate()

    monkeypatch.setattr(leerie, "_orchestrate", _orchestrate)
    monkeypatch.setattr(leerie, "_enforce_and_record_cgroup_containment",
                        _gate)
    monkeypatch.setattr(sys, "argv",
                        ["leerie", "a task", "--run-id", RUN_ID, *argv])
    with pytest.raises(_ReachedGate):
        leerie.main()


def _resume(leerie, monkeypatch, argv: list[str]) -> dict:
    """Real `main()` on `resume <id>`; returns what `_orchestrate` got."""
    got: dict = {}

    async def _orchestrate(args, caps, leerie_dir, st, sot_pref, verbosity,
                           models, efforts):
        got.update(args=args, caps=dict(caps), sot_pref=sot_pref,
                   models=dict(models), efforts=dict(efforts))
        st.release_lock()
        raise _Captured()

    monkeypatch.setattr(leerie, "_orchestrate", _orchestrate)
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    monkeypatch.setattr(sys, "argv", ["leerie", "resume", RUN_ID, *argv])
    with pytest.raises(_Captured):
        leerie.main()
    return got


FRESH_ARGV = ["--no-push", "--max-workers", "7", "--model-planner", "opus",
              "--effort-planner", "high", "--skip-overlap-judge",
              "--source-of-truth", "codebase", "--confidence-rounds", "3"]


def _start(leerie, monkeypatch, run_env):
    _repo, run_dir = run_env
    _fresh(leerie, monkeypatch, FRESH_ARGV)
    state = json.loads((run_dir / "state.json").read_text())
    return run_dir, state


def _ambient_env_disagrees(monkeypatch):
    """The resume's env says the opposite of every recorded choice."""
    monkeypatch.setenv("LEERIE_NO_PUSH", "0")
    monkeypatch.setenv("LEERIE_MAX_WORKERS", "50")
    monkeypatch.setenv("LEERIE_MODEL", "haiku")
    monkeypatch.setenv("LEERIE_SKIP_OVERLAP_JUDGE", "0")
    monkeypatch.setenv("LEERIE_SOURCE_OF_TRUTH", "research")


class TestFreshRunRecordsItsChoices:
    def test_every_sticky_choice_is_in_state(self, leerie, monkeypatch,
                                             run_env):
        run_dir, state = _start(leerie, monkeypatch, run_env)
        for key in leerie.RUN_CHOICES_STICKY:
            if key == "pr_base_branch":
                continue  # recorded with the run's identity, after the gate
            assert key in state, f"fresh run did not record {key!r}"
        assert state["no_push"] is True
        assert state["run_caps"]["max_total_workers"] == 7
        assert state["run_caps"]["confidence_rounds"] == 3
        assert state["models"]["planner"] == "opus"
        assert state["models"]["implementer"] == "sonnet"
        assert state["efforts"]["planner"] == "high"
        assert state["skip_overlap_judge"] is True
        assert state["source_of_truth_pref"] == "codebase"
        rj = json.loads((run_dir / "run.json").read_text())
        assert rj["no_push"] is True, (
            "the host's record of the push choice was not written")


class TestResumeContinuesTheRecordedChoices:
    def test_a_plain_resume_runs_under_the_fresh_runs_choices(
            self, leerie, monkeypatch, run_env):
        run_dir, _state = _start(leerie, monkeypatch, run_env)
        _ambient_env_disagrees(monkeypatch)

        got = _resume(leerie, monkeypatch, [])

        assert got["args"].no_push is True, "a --no-push run would push"
        assert got["caps"]["max_total_workers"] == 7, (
            "the recorded budget lost to LEERIE_MAX_WORKERS")
        assert got["caps"]["confidence_rounds"] == 3
        assert got["models"]["planner"] == "opus"
        assert got["models"]["implementer"] == "sonnet", (
            "LEERIE_MODEL on the resume overrode a recorded model")
        assert got["efforts"]["planner"] == "high"
        assert got["args"].skip_overlap_judge is True
        assert got["sot_pref"] == "codebase"
        rj = json.loads((run_dir / "run.json").read_text())
        assert rj["no_push"] is True
        state = json.loads((run_dir / "state.json").read_text())
        assert not state.get("run_choice_overrides"), (
            "nothing on the command line changed, so nothing is an override")

    def test_a_resume_flag_overrides_and_is_recorded(
            self, leerie, monkeypatch, run_env):
        run_dir, _state = _start(leerie, monkeypatch, run_env)

        got = _resume(leerie, monkeypatch,
                      ["--max-workers", "9", "--model", "haiku"])

        assert got["caps"]["max_total_workers"] == 9
        assert got["caps"]["confidence_rounds"] == 3, (
            "an override of one cap must leave the others recorded")
        assert set(got["models"].values()) == {"haiku"}, (
            "--model sets every model, the recorded per-worker one included")
        assert got["args"].no_push is True
        overrides = json.loads(
            (run_dir / "state.json").read_text())["run_choice_overrides"]
        by_choice = {o["choice"]: o for o in overrides}
        assert by_choice["run_caps.max_total_workers"]["recorded"] == 7
        assert by_choice["run_caps.max_total_workers"]["now"] == 9
        assert by_choice["models.planner"] == {
            **by_choice["models.planner"], "recorded": "opus", "now": "haiku"}
        assert all(o.get("at") for o in overrides)

    def test_a_global_flag_on_resume_is_not_outranked_by_a_workers_env(
            self, leerie, monkeypatch, run_env):
        """Review round 24 F2. On a fresh run `LEERIE_MODEL_JUDGE` outranks
        `--model` for `judge` (and likewise heal, pr_writer, dep_capture).
        On a resume the override must come from the command line alone, or
        the resume's env changes a recorded entry under an override's name."""
        run_dir, _state = _start(leerie, monkeypatch, run_env)
        for w in ("JUDGE", "HEAL", "PR_WRITER", "DEP_CAPTURE"):
            monkeypatch.setenv(f"LEERIE_MODEL_{w}", "haiku")

        got = _resume(leerie, monkeypatch, ["--model", "opus"])

        for w in ("judge", "heal", "pr_writer", "dep_capture", "planner"):
            assert got["models"][w] == "opus", (w, got["models"][w])
        overrides = json.loads(
            (run_dir / "state.json").read_text())["run_choice_overrides"]
        assert all(o["now"] == "opus" for o in overrides
                   if o["choice"].startswith("models.")), overrides

    def test_push_overrides_a_recorded_no_push(self, leerie, monkeypatch,
                                               run_env):
        run_dir, _state = _start(leerie, monkeypatch, run_env)

        got = _resume(leerie, monkeypatch, ["--push"])

        assert got["args"].no_push is False
        assert json.loads((run_dir / "run.json").read_text())["no_push"] \
            is False
        overrides = json.loads(
            (run_dir / "state.json").read_text())["run_choice_overrides"]
        assert {"choice": "no_push", "recorded": True, "now": False} in [
            {k: o[k] for k in ("choice", "recorded", "now")}
            for o in overrides]

    def test_no_push_wins_over_push(self, leerie, monkeypatch, run_env):
        _start(leerie, monkeypatch, run_env)
        got = _resume(leerie, monkeypatch, ["--push", "--no-push"])
        assert got["args"].no_push is True

    def test_a_run_recorded_before_the_field_falls_back_to_run_json(
            self, leerie, monkeypatch, run_env):
        """A finished `--no-push` run from before `state.json` carried
        `no_push`: finalize's run.json intent is its record (round 23's A')."""
        run_dir, state = _start(leerie, monkeypatch, run_env)
        del state["no_push"]
        (run_dir / "state.json").write_text(json.dumps(state))

        got = _resume(leerie, monkeypatch, [])

        assert got["args"].no_push is True


# --------------------------------------------------------------------------
# Host side: the real host_finalize and the launcher's NO_PUSH block
# --------------------------------------------------------------------------

def _finished(run_dir: Path) -> None:
    """Make run.json look like a finished run, keeping its no_push."""
    rj = json.loads((run_dir / "run.json").read_text())
    rj.update(finished_at="2026-11-01T00:00:00+00:00",
              branch=f"leerie/runs/{RUN_ID}", working_branch="main")
    (run_dir / "run.json").write_text(json.dumps(rj))


def _host_finalize(tmp_path: Path, run_dir: Path, user_repo: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("git", "gh"):
        p = bin_dir / name
        p.write_text(f'#!/usr/bin/env bash\necho "$@" >> {bin_dir}/{name}.log\n'
                     "exit 0\n")
        p.chmod(0o755)
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bin_dir / "sleep").chmod(0o755)
    r = subprocess.run(
        ["bash", "-c",
         f"set -euo pipefail; . {HOST_FINALIZE_SH}; host_finalize {run_dir}"],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "USER_REPO": str(user_repo),
             "HOME": str(tmp_path)},
        capture_output=True, text=True, errors="replace", check=False)
    git_log = bin_dir / "git.log"
    return r, (git_log.read_text() if git_log.exists() else "")


@pytest.mark.skipif(not HAS_JQ, reason="host-only script: needs real `jq`")
class TestHostFinalizeSeesTheRecordedChoice:
    def test_a_no_push_run_resumed_without_the_flag_does_not_push(
            self, leerie, monkeypatch, run_env, tmp_path):
        repo, _ = run_env
        run_dir, _state = _start(leerie, monkeypatch, run_env)
        _ambient_env_disagrees(monkeypatch)
        _resume(leerie, monkeypatch, [])
        _finished(run_dir)

        r, git_log = _host_finalize(tmp_path, run_dir, repo)

        assert "no_push=true" in r.stderr, r.stderr
        assert " push " not in f" {git_log} ", git_log

    def test_control_the_same_run_resumed_with_push_does_push(
            self, leerie, monkeypatch, run_env, tmp_path):
        """Anti-vacuity: the harness above can observe a push."""
        repo, _ = run_env
        run_dir, _state = _start(leerie, monkeypatch, run_env)
        _resume(leerie, monkeypatch, ["--push"])
        _finished(run_dir)

        r, git_log = _host_finalize(tmp_path, run_dir, repo)

        assert "no_push=true" not in r.stderr
        assert re.search(r"(^|\s)push(\s|$)", git_log, re.M), (r.stderr,
                                                              git_log)


def _launcher_no_push_block() -> str:
    """The launcher's NO_PUSH resolution, from `NO_PUSH=false` through the
    recorded-choice step, sliced out of the real launcher."""
    text = LAUNCHER.read_text()
    start = text.index("\nNO_PUSH=false\n")
    end = text.index("unset _NO_PUSH_CLI\n", start) + len("unset _NO_PUSH_CLI\n")
    return text[start:end]


def _launcher_no_push(tmp_path: Path, *, args: list[str], is_resume: bool,
                      run_id: str, run_json: dict | None,
                      env_no_push: str = "") -> str:
    host = tmp_path / "host-state"
    if run_json is not None:
        d = host / "runs" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps(run_json))
    user_repo = tmp_path / "launcher-repo"
    user_repo.mkdir(exist_ok=True)
    script = ("set -euo pipefail\n"
              "remote_log() { echo \"$*\" >&2; }\n"
              f"USER_REPO={user_repo}\n"
              f"LEERIE_STATE_HOST_DIR={host}\n"
              f"IS_RESUME={'true' if is_resume else 'false'}\n"
              f"LEERIE_RUN_ID={run_id}\n"
              + _launcher_no_push_block() + '\necho "$NO_PUSH"\n')
    env = {"PATH": "/usr/bin:/bin"}
    if env_no_push:
        env["LEERIE_NO_PUSH"] = env_no_push
    r = subprocess.run(["bash", "-c", script, "--", *args], env=env,
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class TestLauncherResolvesTheRecordedPushChoice:
    def test_resume_without_a_flag_continues_no_push(self, tmp_path):
        assert _launcher_no_push(
            tmp_path, args=["resume", "r1"], is_resume=True, run_id="r1",
            run_json={"no_push": True}) == "true"

    def test_the_record_beats_the_ambient_env(self, tmp_path):
        """Inputs disagree: the record says push, the env says not to."""
        assert _launcher_no_push(
            tmp_path, args=["resume", "r1"], is_resume=True, run_id="r1",
            run_json={"no_push": False}, env_no_push="1") == "false"

    def test_push_on_the_resume_overrides(self, tmp_path):
        assert _launcher_no_push(
            tmp_path, args=["resume", "r1", "--push"], is_resume=True,
            run_id="r1", run_json={"no_push": True}) == "false"

    def test_no_push_wins_over_push(self, tmp_path):
        assert _launcher_no_push(
            tmp_path, args=["--push", "--no-push"], is_resume=True,
            run_id="r1", run_json={"no_push": False}) == "true"

    def test_a_fresh_run_ignores_another_runs_record(self, tmp_path):
        assert _launcher_no_push(
            tmp_path, args=["a task"], is_resume=False, run_id="r1",
            run_json={"no_push": True}) == "false"

    def test_no_record_falls_back_to_env(self, tmp_path):
        assert _launcher_no_push(
            tmp_path, args=["resume", "r1"], is_resume=True, run_id="r1",
            run_json=None, env_no_push="1") == "true"


# --------------------------------------------------------------------------
# The pure continuation
# --------------------------------------------------------------------------

def _resolved(leerie, **over) -> dict:
    base = {k: False for k in leerie.RUN_CHOICES_STICKY}
    base.update(source_of_truth_pref="both", subtask_tests="scoped",
                pr_template=None, pr_base_branch=None,
                run_caps={k: 1 for k in leerie.RUN_CAPS_STICKY},
                models={"planner": "sonnet", "judge": "sonnet"},
                efforts={"planner": None})
    base.update(over)
    return base


class TestContinueRunChoices:
    def test_recorded_wins_when_not_given(self, leerie):
        recorded = _resolved(leerie, no_push=True, subtask_tests="full")
        out, ov = leerie._continue_run_choices(
            recorded, _resolved(leerie), {})
        assert out["no_push"] is True and out["subtask_tests"] == "full"
        assert ov == []

    def test_given_wins_and_is_an_override(self, leerie):
        recorded = _resolved(leerie, subtask_tests="full")
        out, ov = leerie._continue_run_choices(
            recorded, _resolved(leerie, subtask_tests="off"),
            {"subtask_tests": "off"})
        assert out["subtask_tests"] == "off"
        assert ov == [{"choice": "subtask_tests", "recorded": "full",
                       "now": "off"}]

    def test_given_but_equal_is_not_an_override(self, leerie):
        recorded = _resolved(leerie, skip_repo_map=True)
        out, ov = leerie._continue_run_choices(
            recorded, _resolved(leerie, skip_repo_map=True),
            {"skip_repo_map": True})
        assert out["skip_repo_map"] is True and ov == []

    def test_unrecorded_choice_takes_the_resolved_value(self, leerie):
        recorded = _resolved(leerie)
        del recorded["strict_conformer"]
        out, _ = leerie._continue_run_choices(
            recorded, _resolved(leerie, strict_conformer=True), {})
        assert out["strict_conformer"] is True

    def test_dict_choices_continue_per_entry(self, leerie):
        recorded = _resolved(leerie, models={"planner": "opus",
                                             "judge": "opus"})
        out, ov = leerie._continue_run_choices(
            recorded,
            _resolved(leerie, models={"planner": "sonnet", "judge": "haiku"}),
            {"judge_model": "haiku"})
        assert out["models"] == {"planner": "opus", "judge": "haiku"}
        assert [o["choice"] for o in ov] == ["models.judge"]

    def test_an_override_takes_the_command_lines_value_not_the_resolved(
            self, leerie):
        """`resolved` carries the env's value for `judge` (its own env var
        outranks `--model`); the override is the flag's value."""
        recorded = _resolved(leerie, models={"planner": "sonnet",
                                             "judge": "sonnet"})
        out, ov = leerie._continue_run_choices(
            recorded,
            _resolved(leerie, models={"planner": "opus", "judge": "haiku"}),
            {"model": "opus"})
        assert out["models"] == {"planner": "opus", "judge": "opus"}
        assert {o["choice"]: o["now"] for o in ov} == {
            "models.planner": "opus", "models.judge": "opus"}

    def test_an_entrys_own_flag_beats_the_global_one(self, leerie):
        recorded = _resolved(leerie, models={"planner": "sonnet",
                                             "judge": "sonnet"})
        out, _ = leerie._continue_run_choices(
            recorded, _resolved(leerie),
            {"model": "opus", "judge_model": "haiku"})
        assert out["models"] == {"planner": "opus", "judge": "haiku"}

    def test_env_only_cap_is_never_overridden(self, leerie):
        recorded = _resolved(leerie)
        recorded["run_caps"] = {**recorded["run_caps"],
                                "judgment_check_rounds": 4}
        resolved = _resolved(leerie)
        resolved["run_caps"] = {**resolved["run_caps"],
                                "judgment_check_rounds": 9}
        out, _ = leerie._continue_run_choices(recorded, resolved, {})
        assert out["run_caps"]["judgment_check_rounds"] == 4


# --------------------------------------------------------------------------
# Rule: every flag and knob is classified, in code and in the spec
# --------------------------------------------------------------------------

def _main_argparse_dests(leerie) -> dict[str, list[str]]:
    """dest -> option strings, for every `add_argument` in `main()`, derived
    from the source (per-worker f-string options expanded)."""
    tree = ast.parse(LEERIE_PY.read_text())
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    out: dict[str, list[str]] = {}
    for n in ast.walk(main):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_argument"):
            continue
        first = n.args[0]
        if isinstance(first, ast.JoinedStr):
            # `f"--model-{_w}"` for every worker type.
            prefix = "".join(v.value for v in first.values
                             if isinstance(v, ast.Constant))
            for w in leerie.WORKER_TYPES:
                opt = f"{prefix}{w}"
                out[opt.lstrip("-").replace("-", "_")] = [opt]
            continue
        opts = [a.value for a in n.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        dest_kw = next((k.value.value for k in n.keywords
                        if k.arg == "dest"), None)
        longs = [o for o in opts if o.startswith("--")]
        dest = dest_kw or (longs[0] if longs else opts[0]).lstrip(
            "-").replace("-", "_")
        out[dest] = opts
    return out


def _sticky_dests(leerie) -> set[str]:
    out: set[str] = set()
    for key in leerie.RUN_CHOICES_STICKY:
        if key == "run_caps":
            entries = list(leerie.RUN_CAPS_STICKY)
        elif key in ("models", "efforts"):
            entries = list(leerie.WORKER_TYPES) + [
                "judge", "heal", "pr_writer", "dep_capture"]
        else:
            out |= set(leerie._run_choice_dests(key))
            continue
        for e in entries:
            out |= set(leerie._run_choice_dests(key, e))
    return out


def _code_class(leerie, dest: str) -> str | None:
    if dest in _sticky_dests(leerie):
        return "sticky"
    if dest in leerie.RUN_OPTIONS_PER_INVOCATION:
        return "per-invocation"
    if dest in leerie.RUN_OPTIONS_SIDECAR:
        return "sidecar"
    return None


def test_every_orchestrator_option_is_classified_exactly_once(leerie):
    dests = set(_main_argparse_dests(leerie))
    classes = [_sticky_dests(leerie), set(leerie.RUN_OPTIONS_PER_INVOCATION),
               set(leerie.RUN_OPTIONS_SIDECAR)]
    assert len(dests) > 60, "the argparse walk found too little"
    for i, a in enumerate(classes):
        for b in classes[i + 1:]:
            assert not (a & b), sorted(a & b)
    every = set().union(*classes)
    assert dests - every == set(), (
        "orchestrator options with no run-choice class (add each to "
        "RUN_CHOICES_STICKY, RUN_OPTIONS_PER_INVOCATION or "
        "RUN_OPTIONS_SIDECAR, and to IMPLEMENTATION §2½ *Run choices on "
        f"resume*): {sorted(dests - every)}")
    assert every - dests == set(), (
        f"classified dests main() has no option for: {sorted(every - dests)}")


def _section_2_5() -> str:
    text = IMPL_MD.read_text()
    start = text.index("\n## 2½. Configuration reference\n")
    end = text.index("\n## 3. ", start)
    return text[start:end]


def _classification_rows() -> list[dict]:
    sec = _section_2_5()
    sub = sec[sec.index("### Run choices on resume"):]
    sub = sub[:sub.index("\n### ", 10)]
    rows = []
    for line in sub.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[3] not in (
                "sticky", "per-invocation", "sidecar"):
            continue
        rows.append({"choice": cells[0],
                     "tokens": set(re.findall(r"`([^`]+)`",
                                              cells[1] + " " + cells[2])),
                     "flags": set(re.findall(r"`([^`]+)`", cells[1])),
                     "class": cells[3]})
    assert len(rows) > 25, "the classification table was not found"
    return rows


def _documented_knobs() -> set[str]:
    """Every flag and env var the rest of §2½ documents."""
    sec = _section_2_5()
    sec = sec[:sec.index("### Run choices on resume")]
    out: set[str] = set()
    for line in sec.splitlines():
        m = re.match(r"\|\s*`([^`]+)`", line)
        if not m:
            continue
        tok = m.group(1).split()[0]
        if tok.startswith("-") or re.match(r"^[A-Z][A-Z0-9_<>]+$", tok) \
                or tok in ("task", "resume", "list"):
            out.add(tok)
    assert len(out) > 80, "the §2½ tables were not found"
    return out


def _env_and_flag_tokens(rows) -> set[str]:
    out: set[str] = set()
    for r in rows:
        for t in r["tokens"]:
            out.add(t.split()[0])
    return out


def test_every_documented_flag_and_knob_is_classified():
    rows = _classification_rows()
    classified = _env_and_flag_tokens(rows)
    missing = _documented_knobs() - classified
    assert not missing, (
        "§2½ documents flags/knobs that *Run choices on resume* does not "
        f"classify: {sorted(missing)}")


def test_spec_sticky_rows_match_the_code(leerie):
    rows = _classification_rows()
    spec_sticky = {r["choice"].strip("`") for r in rows
                   if r["class"] == "sticky"}
    assert spec_sticky == set(leerie.RUN_CHOICES_STICKY)


def test_each_orchestrator_option_has_the_class_the_code_gives_it(leerie):
    rows = _classification_rows()
    flag_class: dict[str, str] = {}
    for r in rows:
        for f in r["flags"]:
            flag_class[f] = r["class"]
    wrong = []
    for dest, opts in _main_argparse_dests(leerie).items():
        opt = next((o for o in opts if o.startswith("--")), opts[0])
        for prefix in ("--model-", "--effort-"):
            if opt.startswith(prefix) and opt[len(prefix):] in \
                    leerie.WORKER_TYPES:
                opt = f"{prefix}<worker>"
        cls = flag_class.get(opt)
        if cls is None and opt.startswith("-") and len(opts) > 1:
            cls = flag_class.get(opts[0])
        if dest == "task":
            cls = flag_class.get("task")
        if cls is None:
            wrong.append((opt, "unclassified"))
        elif cls != _code_class(leerie, dest):
            wrong.append((opt, cls, _code_class(leerie, dest)))
    assert not wrong, wrong


# --------------------------------------------------------------------------
# Rule: main() reads no sticky choice before the continuation
# --------------------------------------------------------------------------

def _sticky_reads(node: ast.AST, sticky_args: set[str],
                  sticky_caps: set[str]) -> set[str]:
    """Every read of a sticky choice in `node`: `args.<k>` (or
    `getattr(args, "<k>", ...)`), `caps["<k>"]`, and the names `models`,
    `efforts`, `sot_pref`."""
    out: set[str] = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Name) and n.value.id == "args"
                and n.attr in sticky_args):
            out.add(f"args.{n.attr}")
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "getattr" and len(n.args) >= 2
              and isinstance(n.args[0], ast.Name) and n.args[0].id == "args"
              and isinstance(n.args[1], ast.Constant)
              and n.args[1].value in sticky_args):
            out.add(f"args.{n.args[1].value}")
        elif (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)
              and isinstance(n.value, ast.Name) and n.value.id == "caps"
              and isinstance(n.slice, ast.Constant)
              and n.slice.value in sticky_caps):
            out.add(f'caps["{n.slice.value}"]')
        elif (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
              and n.id in ("models", "efforts", "sot_pref")):
            out.add(n.id)
    return out


def _targets(stmt: ast.stmt) -> set[str]:
    """What an assignment statement writes, spelled as `_sticky_reads`
    spells a read."""
    out: set[str] = set()
    if not isinstance(stmt, ast.Assign):
        return out
    for t in stmt.targets:
        if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                and t.value.id == "args"):
            out.add(f"args.{t.attr}")
        elif (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
              and t.value.id == "caps" and isinstance(t.slice, ast.Constant)):
            out.add(f'caps["{t.slice.value}"]')
        elif isinstance(t, ast.Name):
            out.add(t.id)
    return out


def _reads_before_continuation(leerie, source: str) -> list[str]:
    tree = ast.parse(source)
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    sticky_args = set(leerie._RUN_CHOICE_ARG_KEYS) | {
        "source_of_truth", "dangerously_force_strict_output", "push",
        "max_workers", "confidence_rounds", "planner_samples", "model",
        "effort", "judge_model", "heal_model", "pr_writer_model"}
    sticky_args |= {f"{p}_{w}" for p in ("model", "effort")
                    for w in leerie.WORKER_TYPES}
    sticky_caps = set(leerie.RUN_CAPS_STICKY) | {"force_strict_output",
                                                 "strict_conformer"}
    found: list[str] = []
    for stmt in main.body:
        if "_continue_run_choices" in ast.unparse(stmt):
            return found
        # A resolution step — an assignment to a sticky choice's holder —
        # reads that choice's raw command-line value; that is how the value
        # the continuation starts from is made. Anything else is a use.
        holders = {f"args.{k}" for k in sticky_args} | {
            f'caps["{k}"]' for k in sticky_caps} | {
            "models", "efforts", "sot_pref"}
        if _targets(stmt) & holders:
            continue
        for r in sorted(_sticky_reads(stmt, sticky_args, sticky_caps)):
            found.append(f"line {stmt.lineno}: {r}")
    raise AssertionError("the continuation was not found in main()")


def test_main_reads_no_sticky_choice_before_the_continuation(leerie):
    found = _reads_before_continuation(leerie, LEERIE_PY.read_text())
    assert not found, (
        "main() reads a sticky choice before `_continue_run_choices`, so a "
        "resume sees its own env/leerie.toml value there, not the run's "
        f"(DESIGN §6): {found}")


def test_the_read_rule_sees_a_read_before_the_continuation(leerie):
    """Anti-vacuity: the round 24 F4 log line, put back above the
    continuation, is found."""
    src = LEERIE_PY.read_text()
    anchor = "    args.skip_overlap_judge = resolve_skip_overlap_judge("
    assert src.count(anchor) == 1
    bad = src.replace(anchor, "    if args.dangerously_skip_permissions:\n"
                              "        log('ON')\n" + anchor)
    found = _reads_before_continuation(leerie, bad)
    assert any("args.dangerously_skip_permissions" in f for f in found), found
