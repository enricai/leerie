"""I3, the relay half (DESIGN §8 *Nothing a held-out evaluation produces
reaches a fixer but its verdict and leerie's own identifiers*).

Each test drives the real consumer that builds a worker's input with a
synthetic held-back set whose declared names, file path and runner output
carry a sentinel, and asserts the sentinel is absent from what the worker
would read — and, against a shown set, that the legitimate names still
arrive (a test that only asserts absence passes on a consumer that hands
on nothing).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

SENTINEL = "ZQX_HELD_BACK_SENTINEL"
SHOWN_NAME = "shown case adds two ints"


def _sets() -> list[dict]:
    # Four valid sets: 3 and 4 are held back (`_acceptance_shown_indices`).
    out = []
    for i in (1, 2, 3, 4):
        rel = (f"acc/{SENTINEL}_set{i}.py" if i == 4 else f"acc/set{i}.py")
        names = ([f"{SENTINEL} expects 42"] if i == 4 else [SHOWN_NAME])
        out.append({"index": i, "valid": True, "defect_files": [rel],
                    "control_files": [], "cases": {rel: names}})
    return out


def _results(sets: list[dict], failing: set[int]) -> list[dict]:
    return [{"index": s_["index"], "passed": s_["index"] not in failing,
             "failing_files": (list(s_["defect_files"])
                               if s_["index"] in failing else [])}
            for s_ in sets]


def _state(leerie, root: Path, run_id: str, **data):
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = {"task": "fix the bug in report.md", "verbosity": "quiet",
               **data}
    st.save()
    return st


def _prior_run(root: Path, task: str, residual: dict, sets: list[dict]
               ) -> None:
    d = root / "runs" / "run-prev"
    d.mkdir(parents=True)
    (d / "orchestrator.exit_code").write_text("0")
    (d / "state.json").write_text(json.dumps({
        "task": task,
        "acceptance": {"sets": sets, "gate": {"residual": residual}}}))


# === the next run's planner ===============================================

def test_the_residual_names_shown_sets_only(leerie):
    sets = _sets()
    res = leerie._acceptance_residual_of(_results(sets, {1, 4}), sets)
    blob = json.dumps(res["cases"]) + json.dumps(res["cases_by_set"])
    assert SENTINEL not in blob
    assert res["cases"] == [SHOWN_NAME]
    assert res["failing_sets"] == 2
    assert res["held_back_failing_sets"] == 1


def test_the_planner_gets_no_held_back_name(leerie, tmp_path):
    """The consumer: `_prior_delivery_residual`, whose `acceptance_unmet` is
    the planner's context."""
    sets = _sets()
    residual = leerie._acceptance_residual_of(_results(sets, {1, 4}), sets)
    st = _state(leerie, tmp_path / "state", "run-new")
    _prior_run(tmp_path / "state", st.data["task"], residual, sets)
    got = leerie._prior_delivery_residual(st)["acceptance_unmet"]
    assert SENTINEL not in json.dumps(got)
    assert got["cases"] == [SHOWN_NAME]
    assert got["held_back_failing_sets"] == 1
    assert got["failing_sets"] == 2


def test_a_residual_recorded_before_the_rule_is_re_derived(leerie,
                                                           tmp_path):
    """A prior run's state.json written before this rule names every set's
    cases; it is filtered when it is read, not trusted."""
    sets = _sets()
    shown, held = sets[0], sets[3]
    legacy = {"failing_sets": 2, "total_sets": 4,
              "cases": [SHOWN_NAME, f"{SENTINEL} expects 42"],
              "sets": [1, 4],
              "cases_by_set": {"1": [SHOWN_NAME],
                               "4": [f"{SENTINEL} expects 42"]},
              "files_by_set": {"1": shown["defect_files"],
                               "4": held["defect_files"]}}
    st = _state(leerie, tmp_path / "state", "run-new")
    _prior_run(tmp_path / "state", st.data["task"], legacy, sets)
    got = leerie._prior_delivery_residual(st)["acceptance_unmet"]
    assert SENTINEL not in json.dumps(got)
    assert got["cases"] == [SHOWN_NAME]
    assert got["held_back_failing_sets"] == 1
    # Older still: no per-set record, so no name can be told shown.
    oldest = {"failing_sets": 1, "total_sets": 4,
              "cases": [f"{SENTINEL} expects 42"]}
    assert leerie._acceptance_residual_unbroken(oldest, sets)["cases"] == []


# === the delivery judge's record ==========================================

def _staging(tmp_path: Path) -> tuple[Path, str]:
    t = tmp_path / "staging"
    t.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "c"]):
        subprocess.run(["git", "-C", str(t), *args], check=True)
    sha = subprocess.run(["git", "-C", str(t), "rev-parse", "HEAD"],
                         capture_output=True, text=True,
                         check=True).stdout.strip()
    return t, sha


def test_the_judge_record_gives_a_held_back_set_its_verdict_only(
        leerie, tmp_path):
    sets = _sets()
    res = _results(sets, {1, 4})
    res[0]["failing_cases"] = {"acc/set1.py": [SHOWN_NAME]}
    staging, sha = _staging(tmp_path)
    st = _state(leerie, tmp_path / "state", "run-a", acceptance={
        "sets": sets, "gate": {
            "final_sha": sha, "final": res,
            "residual": leerie._acceptance_residual_of(res, sets)}})
    d = leerie._orchestrator_results_digest(st, staging)
    assert SENTINEL not in d
    lines = [ln for ln in d.splitlines() if "acceptance set" in ln]
    assert lines == [
        "[orchestrator held-out acceptance set 1] result on the final "
        f"tree: failed: acc/set1.py (cases: {SHOWN_NAME})",
        "[orchestrator held-out acceptance set 2] result on the final "
        "tree: passed",
        "[orchestrator held-out acceptance set 3] result on the final "
        "tree: passed",
        "[orchestrator held-out acceptance set 4] result on the final "
        "tree: failed (a held-back set: its tests are not named)"]


# === the logs the record reads ============================================

class _Stream:
    def __init__(self, lines: list[str]):
        self._lines = [(ln + "\n").encode() for ln in lines]

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self, n: int = -1) -> bytes:
        out = b"".join(self._lines)
        self._lines = []
        return out


class _Proc:
    def __init__(self, lines: list[str]):
        self.stdout = _Stream(lines)
        self.stderr = _Stream([])
        self.stdin = None
        self.returncode = 0
        self.pid = 999_999_991

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _transcript(sid: str) -> list[str]:
    # A worker that ran a held-out file: its tool_result is the runner's
    # assertion diff, expected value and all.
    return [json.dumps(e) for e in (
        {"type": "assistant", "session_id": sid, "message": {"content": [
            {"type": "tool_use", "id": f"t-{sid}", "name": "Bash",
             "input": {"command": "pytest -q acc/set4.py"}}]}},
        {"type": "user", "session_id": sid, "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t-{sid}",
             "content": f"assert 41 == 42  # {sid} {SENTINEL}"}]}},
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 1, "result": "{}", "structured_output": {},
         "usage": {"input_tokens": 1, "output_tokens": 1}})]


def _claude_p_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.repo_root = "/leerie-test-user-repo"
    st.run_id = "relay-run"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {"telemetry": {"calls": 0, "cost_usd": 0.0,
                             "input_tokens": 0, "output_tokens": 0},
               "verbosity": "quiet"}
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


def test_held_out_transcripts_never_reach_the_record(leerie, tmp_path,
                                                     monkeypatch):
    """Through the real `claude_p` → `_invoke_admitted` → `_invoke` (only
    the process is faked), then the real `_executed_command_entries`: the
    transcripts of the workers that read a set stay out of the delivery
    judge's record; a fix-side worker's run stays in it."""
    run_dir = tmp_path / "runs" / "relay-run"
    st = _claude_p_state(leerie, run_dir)

    async def fake_exec(*cmd, **kw):
        return _Proc(_transcript(fake_exec.sid))
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr(leerie.State, "bump_workers", lambda self, caps: None)
    monkeypatch.setattr(leerie, "_blt_verbs", lambda repo_root: ["pytest"])
    kinds = sorted(leerie.HELD_OUT_LOG_WORKERS) + ["classifier"]
    for kind in kinds:
        fake_exec.sid = f"relay-{kind}"
        asyncio.run(leerie.claude_p(
            user_prompt="u", system_prompt="s", schema_key=kind,
            cwd=str(run_dir), allowed_tools="Read", max_turns=2,
            autonomous=False, caps={"worker_timeout_sec": 60,
                                    "max_total_workers": 99},
            st=st, model="sonnet", sid=fake_exec.sid))
    for kind in kinds:
        assert leerie._worker_log_path(
            run_dir, f"relay-{kind}", kind).is_file(), kind
    text = leerie._executed_commands_digest(
        leerie._executed_command_entries(run_dir, tmp_path))
    assert "relay-classifier" in text
    assert text.count(SENTINEL) == 1
    for kind in leerie.HELD_OUT_LOG_WORKERS:
        assert f"relay-{kind}" not in text


def test_leerie_runs_of_set_files_log_on_the_held_out_side(leerie, tmp_path):
    """A line the evaluated code prints in the shape of a tool call is
    still never read as a worker's run: leerie's runs of set files are
    logged where the record does not look."""
    run_dir = tmp_path / "runs" / "r"
    log = leerie._held_out_logs(run_dir) / "acceptance-final.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(_transcript("forged")) + "\n")
    assert leerie._executed_command_entries(
        run_dir, tmp_path, repro_command="pytest -q") == []
    src = Path(leerie.__file__).read_text()
    assert 'st.run_dir / "logs" / f"acceptance-' not in src
    assert src.count('_held_out_logs(st.run_dir) / f"acceptance-{label}.log"'
                     ) == 4
