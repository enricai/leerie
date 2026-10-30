"""N3 integration: the integration judge gate is a repair loop, not
detect-and-die (DESIGN §6 *When integration cannot succeed*).

Executed against a real git staging tree holding a real two-parent merge;
only the workers (`claude_p`) and the conflicted-area test run are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

SID = "feat-002"

# Staging is on the run branch (`_compute_run_branch`): the clean-tree gate
# holds every worker there (DESIGN §8 *A worker leaves its tree clean*).
STAGING_BRANCH = "leerie/runs/run-int-repair"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


BASE = "def compute():\n    return 1\n\ndef other():\n    return 2\n"
OURS = ("def compute():\n    return 1\n\ndef other():\n    return 2\n\n"
        "def integrated_helper_function():\n    return 'integrated'\n")
THEIRS = ("def compute():\n    return 1\n\ndef other():\n    return 2\n\n"
          "def incoming_helper_function():\n    return 'incoming'\n")
# The committed merge keeps ours and drops the incoming side's function.
MERGED = OURS


def _setup(leerie, tmp_path: Path):
    root = tmp_path / ".leerie"
    st = leerie.State(root, "run-int-repair")
    st.data = {"task": "t", "worker_count": 0,
               "subtask_status": {SID: "complete"}}
    st.save()
    leerie_dir = st.run_dir
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    _git(staging, "init", "-q", "-b", STAGING_BRANCH)
    _git(staging, "config", "user.email", "t@x")
    _git(staging, "config", "user.name", "t")
    (staging / "app.py").write_text(BASE)
    _git(staging, "add", "-A")
    _git(staging, "commit", "-q", "-m", "base")
    _git(staging, "checkout", "-q", "-b", "incoming")
    (staging / "app.py").write_text(THEIRS)
    _git(staging, "commit", "-q", "-am", "incoming")
    _git(staging, "checkout", "-q", STAGING_BRANCH)
    (staging / "app.py").write_text(OURS)
    _git(staging, "commit", "-q", "-am", "integrated")
    p1 = _git(staging, "rev-parse", "HEAD").strip()
    subprocess.run(["git", "merge", "--no-ff", "-m",
                    f"leerie: integrate {SID}", "incoming"],
                   cwd=str(staging), capture_output=True, text=True)
    (staging / "app.py").write_text(MERGED)
    _git(staging, "add", "-A")
    _git(staging, "commit", "-q", "--no-edit")
    merge = _git(staging, "rev-parse", "HEAD").strip()
    (leerie_dir / "subtasks").mkdir(parents=True, exist_ok=True)
    (leerie_dir / "subtasks" / f"{SID}.json").write_text(json.dumps(
        {"id": SID, "intent": "add the incoming helper",
         "criteria": ["helper exists"]}))
    return st, staging, p1, merge


DEFECT = {"kind": "dropped_change", "location": "app.py",
          "concrete_scenario": "incoming_helper_function was dropped",
          "why_broken": "callers of the incoming helper fail"}


class _Workers:
    """Scripted `claude_p`: judge verdicts in order; the integrator's repair
    applies `fix` to the staging tree and commits it."""

    def __init__(self, staging: Path, verdicts: list[list[dict]],
                 fix: str | None = THEIRS.rstrip("\n") + "\n\n"
                 "def integrated_helper_function():\n"
                 "    return 'integrated'\n",
                 status: str = "resolved", commit: bool = True,
                 superseded: list[dict] | None = None):
        self.staging = staging
        self.verdicts = list(verdicts)
        self.fix = fix
        self.status = status
        self.commit = commit
        self.superseded = superseded or []
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        if kw["schema_key"] == "integration_judge":
            defects = self.verdicts.pop(0) if self.verdicts else []
            return {"merge_reviewed": True, "defects": defects,
                    "rationale": "r"}
        assert kw["schema_key"] == "integrator"
        if self.status == "resolved" and self.commit and self.fix:
            n = sum(1 for c in self.calls if c["schema_key"] == "integrator")
            # A distinct tree per round, so every round has a commit to make.
            (self.staging / "app.py").write_text(
                self.fix + ("" if n == 1 else f"# repair {n}\n"))
            _git(self.staging, "commit", "-q", "-am",
                 f"leerie: integration repair {SID}")
        return {"incoming_subtask": SID, "status": self.status,
                "diagnosis": "intents collide" if self.status != "resolved"
                else None, "superseded": self.superseded}

    def kinds(self) -> list[str]:
        return [c["schema_key"] for c in self.calls]


def _no_tests(result=None):
    async def fake(*a, **kw):
        return result or {"test_failure": [], "stale_by_other_side": [],
                          "scope": [], "skipped": "no test command"}
    return fake


def _run(leerie, st, staging, monkeypatch, workers, tests=None, caps=None):
    monkeypatch.setattr(leerie, "claude_p", workers)
    monkeypatch.setattr(leerie, "_integration_test_failures",
                        tests or _no_tests())
    caps = caps or dict(leerie.DEFAULT_CAPS)
    incoming = leerie._incoming_subtask_view(st.run_dir, SID, None)
    return asyncio.run(leerie._run_integration_judge_gate(
        SID, staging, incoming, ["feat-001"], caps, st,
        {"integrator": "sonnet", "integration_judge": "sonnet"},
        {"integrator": "low", "integration_judge": "medium"}))


def test_clean_verdict_keeps_the_merge(leerie, tmp_path, monkeypatch):
    st, staging, _p1, merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[]])
    assert _run(leerie, st, staging, monkeypatch, w) is True
    assert w.kinds() == ["integration_judge"]
    rec = st.data["integration_gate"][SID]
    assert rec["accepted"] is True and rec["outcome"] == "clean"
    assert _git(staging, "rev-parse", "HEAD").strip() == merge


def test_finding_is_repaired_and_rejudged_whole(leerie, tmp_path,
                                               monkeypatch):
    st, staging, p1, merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[DEFECT], []])
    assert _run(leerie, st, staging, monkeypatch, w) is True
    # Order: judge, integrator repair, re-judge.
    assert w.kinds() == ["integration_judge", "integrator",
                         "integration_judge"]
    head = _git(staging, "rev-parse", "HEAD").strip()
    assert head != merge
    assert _git(staging, "rev-parse", "HEAD^").strip() == merge
    # The repair prompt carries the typed finding and the dropped lines.
    prompt = w.calls[1]["user_prompt"]
    assert '"kind": "dropped_change"' in prompt
    assert "def incoming_helper_function():" in prompt
    assert w.calls[1]["sid"] == f"integrator-repair-{SID}"
    # The re-judge reviewed a synthetic two-parent merge of the repaired
    # tree, not the repair commit itself.
    rejudge = w.calls[2]["user_prompt"]
    syn = rejudge.split("Commit: ", 1)[1].split("\n", 1)[0].strip()
    assert syn not in (head, merge)
    parents = _git(staging, "rev-list", "--parents", "-n", "1",
                   syn).split()
    assert parents[1:] == [p1, _git(staging, "rev-parse",
                                    f"{merge}^2").strip()]
    assert _git(staging, "rev-parse", f"{syn}^{{tree}}").strip() == \
        _git(staging, "rev-parse", "HEAD^{tree}").strip()
    rec = st.data["integration_gate"][SID]
    assert rec["accepted"] is True and rec["outcome"] == "repaired"
    assert rec["repair_rounds"] == 1
    assert rec["residual_total"] == 0
    # Its own counter: the subtask's shared re-drive counter is untouched.
    assert SID not in (st.data.get("redrive_rounds") or {})
    assert SID not in (st.data.get("integration_defects") or {})


def test_intent_conflict_backs_out_without_a_repair(leerie, tmp_path,
                                                    monkeypatch):
    st, staging, p1, _merge = _setup(leerie, tmp_path)
    conflict = dict(DEFECT, kind="intent_conflict")
    w = _Workers(staging, [[conflict]])
    assert _run(leerie, st, staging, monkeypatch, w) is False
    assert w.kinds() == ["integration_judge"]
    assert _git(staging, "rev-parse", "HEAD").strip() == p1
    assert st.data["subtask_status"][SID] == "unconverged"
    assert st.data["unconverged_subtasks"][SID]["kind"] == \
        "integration_backed_out"
    rec = st.data["integration_gate"][SID]
    assert rec["outcome"] == "backed_out" and rec["reset_to"] == p1
    loss = st.data["risk_register"][SID]
    assert loss[-1]["kind"] == "integration_content_loss"


def test_exhausted_rounds_back_out(leerie, tmp_path, monkeypatch):
    st, staging, p1, _merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[DEFECT], [DEFECT], [DEFECT]])
    assert _run(leerie, st, staging, monkeypatch, w) is False
    assert w.kinds() == ["integration_judge", "integrator",
                         "integration_judge", "integrator",
                         "integration_judge"]
    assert _git(staging, "rev-parse", "HEAD").strip() == p1
    assert st.data["integration_gate"][SID]["repair_rounds"] == 2
    assert SID not in (st.data.get("redrive_rounds") or {})
    assert st.data["subtask_status"][SID] == "unconverged"


def test_spent_shared_counter_still_gets_its_repair_rounds(
        leerie, tmp_path, monkeypatch):
    """Review round 1 M1: a subtask whose shared re-drive rounds were spent
    earlier in the wave (here, both) is still repaired — integration repair
    has its own counter — instead of being backed out with zero rounds,
    which took all of its finished work out of the PR."""
    st, staging, p1, merge = _setup(leerie, tmp_path)
    st.data["redrive_rounds"] = {SID: 2}
    w = _Workers(staging, [[DEFECT], []])
    assert _run(leerie, st, staging, monkeypatch, w) is True
    assert w.kinds() == ["integration_judge", "integrator",
                         "integration_judge"]
    assert _git(staging, "rev-parse", "HEAD^").strip() == merge
    rec = st.data["integration_gate"][SID]
    assert rec["outcome"] == "repaired" and rec["repair_rounds"] == 1
    assert st.data["redrive_rounds"] == {SID: 2}
    assert st.data.get("subtask_status", {}).get(SID) != "unconverged"


def test_spent_run_redrive_time_backs_out_before_any_repair(
        leerie, tmp_path, monkeypatch):
    """The run-level wall-time budget still bounds integration repair."""
    st, staging, p1, _merge = _setup(leerie, tmp_path)
    st.data["redrive_spent_sec"] = float(
        leerie.DEFAULT_CAPS["redrive_budget_sec"])
    w = _Workers(staging, [[DEFECT]])
    assert _run(leerie, st, staging, monkeypatch, w) is False
    assert w.kinds() == ["integration_judge"]
    assert _git(staging, "rev-parse", "HEAD").strip() == p1
    assert st.data["integration_gate"][SID]["fallback_reason"] == \
        "not repaired within 0 round(s)"


def test_resumed_gate_continues_its_own_counter(leerie, tmp_path,
                                                 monkeypatch):
    """A round persisted before a crash is not handed back on resume."""
    st, staging, p1, merge = _setup(leerie, tmp_path)
    st.data["integration_gate"] = {SID: {"merge_commit_sha": merge,
                                         "repair_rounds": 2,
                                         "accepted": False}}
    w = _Workers(staging, [[DEFECT]])
    assert _run(leerie, st, staging, monkeypatch, w) is False
    assert w.kinds() == ["integration_judge"]


def test_design_conflict_during_repair_backs_out(leerie, tmp_path,
                                                 monkeypatch):
    st, staging, p1, _merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[DEFECT]], status="design-conflict")
    assert _run(leerie, st, staging, monkeypatch, w) is False
    assert "design-conflict" in \
        st.data["integration_gate"][SID]["fallback_reason"]
    assert _git(staging, "rev-parse", "HEAD").strip() == p1


def test_no_new_commit_is_a_spent_round_reset(leerie, tmp_path,
                                              monkeypatch):
    st, staging, _p1, merge = _setup(leerie, tmp_path)
    # Round 1 claims resolved but commits nothing (an uncommitted edit is
    # left behind); round 2 commits the fix.
    w = _Workers(staging, [[DEFECT], []])
    real = w.__call__
    n = {"i": 0}

    async def flaky(**kw):
        if kw["schema_key"] == "integrator":
            n["i"] += 1
            if n["i"] == 1:
                (staging / "app.py").write_text("garbage\n")
                w.calls.append(kw)
                return {"incoming_subtask": SID, "status": "resolved"}
        return await real(**kw)
    monkeypatch.setattr(leerie, "claude_p", flaky)
    monkeypatch.setattr(leerie, "_integration_test_failures", _no_tests())
    ok = asyncio.run(leerie._run_integration_judge_gate(
        SID, staging, {"intent": "i"}, [], dict(leerie.DEFAULT_CAPS), st,
        {"integrator": "sonnet"}, {"integrator": "low"}))
    assert ok is True
    rounds = st.data["integration_gate"][SID]["rounds"]
    assert rounds[0]["verdict"] == "mech_fail"
    assert rounds[0]["mech"]["new_commit"] is False
    assert rounds[0]["mech"]["clean"] is False
    assert rounds[1]["verdict"] == "pass"
    assert _git(staging, "rev-parse", "HEAD^").strip() == merge


def test_confirmed_test_failure_blocks_the_pass_and_is_routed(
        leerie, tmp_path, monkeypatch):
    st, staging, _p1, _merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[DEFECT], [], []])
    seq = [{"test_failure": ["tests/test_app.py::test_x"],
            "stale_by_other_side": [{"test_id": "tests/test_y.py::t",
                                     "home_side": "integrated",
                                     "other_side": "incoming"}],
            "scope": ["tests/test_app.py"], "skipped": None},
           {"test_failure": [], "stale_by_other_side": [], "scope": [],
            "skipped": None}]

    async def tests(*a, **kw):
        return seq.pop(0)
    assert _run(leerie, st, staging, monkeypatch, w, tests=tests) is True
    assert w.kinds().count("integrator") == 2
    second = w.calls[3]["user_prompt"]
    assert '"location": "tests/test_app.py::test_x"' in second
    assert '"kind": "test_failure"' in second
    assert '"test_id": "tests/test_y.py::t"' in second


def test_residual_loss_recorded_unless_superseded(leerie, tmp_path,
                                                  monkeypatch):
    st, staging, _p1, _merge = _setup(leerie, tmp_path)
    # The repair keeps the incoming function but renames the line; the
    # integrator marks the old line superseded by the new one.
    fix = OURS + "\ndef incoming_helper_fn():\n    return 'incoming'\n"
    sup = [{"file": "app.py", "line": "def incoming_helper_function():",
            "by_line": "def incoming_helper_fn():",
            "reason": "other_side_rewrite"}]
    w = _Workers(staging, [[DEFECT], []], fix=fix, superseded=sup)
    assert _run(leerie, st, staging, monkeypatch, w) is True
    rec = st.data["integration_gate"][SID]
    assert rec["superseded"] == sup
    # `return 'incoming'` is back; the def line is covered by superseded.
    assert rec["residual_total"] == 0
    # Without the superseded entry the same tree leaves one residual line.
    st2, staging2, _a, _b = _setup(leerie, tmp_path / "two")
    w2 = _Workers(staging2, [[DEFECT], []], fix=fix)
    assert _run(leerie, st2, staging2, monkeypatch, w2) is True
    rec2 = st2.data["integration_gate"][SID]
    assert rec2["residual_total"] == 1
    assert rec2["residual_lines"][0]["line"] == \
        "def incoming_helper_function():"
    assert st2.data["risk_register"][SID][-1]["kind"] == \
        "integration_content_loss"


class TestDroppedLines:
    def test_lists_the_drop_and_excludes_short_lines(self, leerie,
                                                     tmp_path):
        _st, staging, p1, merge = _setup(leerie, tmp_path)
        p2 = _git(staging, "rev-parse", f"{merge}^2").strip()
        out = asyncio.run(leerie._integration_dropped_lines(
            staging, p1, p2, merge))
        assert out["lines"] == [{"file": "app.py", "side": "incoming",
                                 "line": "def incoming_helper_function():"},
                                {"file": "app.py", "side": "incoming",
                                 "line": "return 'incoming'"}]
        assert out["excluded_rewritten"] == 0

    def test_line_the_other_side_rewrote_is_excluded(self, leerie,
                                                     tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", STAGING_BRANCH)
        _git(repo, "config", "user.email", "t@x")
        _git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("value = compute_total(items)\nkeep()\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "inc")
        # incoming rewrites the line ...
        (repo / "f.py").write_text("value = compute_total(items, tax)\n"
                                   "keep()\n")
        _git(repo, "commit", "-q", "-am", "inc")
        _git(repo, "checkout", "-q", STAGING_BRANCH)
        # ... and integrated deletes that same base line and adds another.
        (repo / "f.py").write_text("keep()\nextra_integrated_line()\n")
        _git(repo, "commit", "-q", "-am", "int")
        p1 = _git(repo, "rev-parse", "HEAD").strip()
        p2 = _git(repo, "rev-parse", "inc").strip()
        # HEAD drops the incoming rewrite.
        out = asyncio.run(leerie._integration_dropped_lines(
            repo, p1, p2, p1))
        # The incoming line is absent from HEAD; it is listed, because the
        # integrated side's own diff did not contain THAT exact line.
        assert {"file": "f.py", "side": "incoming",
                "line": "value = compute_total(items, tax)"} in out["lines"]
        # Reverse: on the incoming tree, the integrated-side addition is
        # absent and listed; the base line both deleted is not a side line.
        out2 = asyncio.run(leerie._integration_dropped_lines(
            repo, p1, p2, p2))
        assert out2["lines"] == [{"file": "f.py", "side": "integrated",
                                  "line": "extra_integrated_line()"}]

    def test_exact_rewrite_by_other_side_is_excluded(self, leerie,
                                                     tmp_path):
        repo = tmp_path / "r2"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", STAGING_BRANCH)
        _git(repo, "config", "user.email", "t@x")
        _git(repo, "config", "user.name", "t")
        (repo / "f.py").write_text("start()\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "inc")
        (repo / "f.py").write_text("start()\nshared_new_line_here()\n")
        _git(repo, "commit", "-q", "-am", "inc")
        _git(repo, "checkout", "-q", STAGING_BRANCH)
        (repo / "f.py").write_text("start()\nother_side_added_line()\n")
        _git(repo, "commit", "-q", "-am", "int")
        p1 = _git(repo, "rev-parse", "HEAD").strip()
        p2 = _git(repo, "rev-parse", "inc").strip()
        # A tree where the incoming line was rewritten by the integrated
        # side would show it in the integrated diff's '-' lines; here it
        # was not, so the drop is listed (S3 keeps conflict-hunk drops).
        out = asyncio.run(leerie._integration_dropped_lines(
            repo, p1, p2, p1))
        assert out["lines"] == [{"file": "f.py", "side": "incoming",
                                 "line": "shared_new_line_here()"}]


def test_line_the_other_side_deleted_is_excluded(leerie, tmp_path):
    """S3's one exclusion: a side line whose exact text is among the OTHER
    side's own '-' lines (here: incoming copies a base line into a new
    place, integrated deletes that line) is not put back on the list."""
    repo = tmp_path / "r3"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", STAGING_BRANCH)
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("legacy_setting = load()\nmain()\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "inc")
    (repo / "f.py").write_text("legacy_setting = load()\nmain()\n"
                               "def later():\n"
                               "    legacy_setting = load()\n")
    _git(repo, "commit", "-q", "-am", "inc")
    _git(repo, "checkout", "-q", STAGING_BRANCH)
    (repo / "f.py").write_text("main()\nintegrated_only_line()\n")
    _git(repo, "commit", "-q", "-am", "int")
    p1 = _git(repo, "rev-parse", "HEAD").strip()
    p2 = _git(repo, "rev-parse", "inc").strip()
    out = asyncio.run(leerie._integration_dropped_lines(repo, p1, p2, p1))
    assert out["excluded_rewritten"] == 1
    assert {"file": "f.py", "side": "incoming",
            "line": "legacy_setting = load()"} not in out["lines"]
    assert {"file": "f.py", "side": "incoming",
            "line": "def later():"} in out["lines"]


def test_incoming_view_reads_the_spec(leerie, tmp_path):
    st, _staging, _p1, _m = _setup(leerie, tmp_path)
    view = leerie._incoming_subtask_view(st.run_dir, SID,
                                         {"status": "complete"})
    assert view["intent"] == "add the incoming helper"
    assert view["criteria_results"] == ["helper exists"]
    kept = leerie._incoming_subtask_view(
        st.run_dir, SID, {"intent": "own", "criteria_results": ["c"]})
    assert kept["intent"] == "own" and kept["criteria_results"] == ["c"]


def test_wave_sids_integrated_before(leerie, tmp_path):
    _st, staging, _p1, _m = _setup(leerie, tmp_path)
    _git(staging, "branch", "leerie/subtasks/R/feat-001", "HEAD~1")
    _git(staging, "branch", "leerie/subtasks/R/feat-003", "incoming")
    _git(staging, "checkout", "-q", "-b", "later", "HEAD~1")
    (staging / "z.txt").write_text("z\n")
    _git(staging, "add", "-A")
    _git(staging, "commit", "-q", "-m", "later")
    _git(staging, "branch", "leerie/subtasks/R/feat-004", "later")
    _git(staging, "checkout", "-q", STAGING_BRANCH)
    got = asyncio.run(leerie._wave_sids_integrated_before(
        staging, "R", ["feat-001", "feat-003", "feat-004", "feat-009"],
        "HEAD"))
    assert got == ["feat-001", "feat-003"]


def test_judge_schema_and_prompt_carry_intent_conflict(leerie):
    kinds = (leerie.SCHEMAS["integration_judge"]["properties"]["defects"]
             ["items"]["properties"]["kind"]["enum"])
    assert "intent_conflict" in kinds
    prompt = (Path(leerie.__file__).resolve().parents[1] / "prompts" /
              "integration_judge.md").read_text()
    assert "`intent_conflict`" in prompt
    sup = leerie.SCHEMAS["integrator"]["properties"]["superseded"]
    assert sup["items"]["required"] == ["file", "line", "by_line", "reason"]
    assert "superseded" not in leerie.SCHEMAS["integrator"]["required"]


def test_integration_repair_rounds_cap(leerie):
    assert leerie.DEFAULT_CAPS["integration_repair_rounds"] == 2


@pytest.mark.parametrize("cap", [1])
def test_cap_one_allows_one_round(leerie, tmp_path, monkeypatch, cap):
    st, staging, p1, _merge = _setup(leerie, tmp_path)
    w = _Workers(staging, [[DEFECT], [DEFECT]])
    caps = dict(leerie.DEFAULT_CAPS, integration_repair_rounds=cap)
    assert _run(leerie, st, staging, monkeypatch, w, caps=caps) is False
    assert w.kinds().count("integrator") == 1


class TestConflictedAreaTests:
    """`_integration_test_failures`: a candidate is a test failing on HEAD
    and on no parent; it is confirmed under B* against the parent it passed
    on, then routed `stale_by_other_side` when the home parent with the
    other side's production files applied fails it too."""

    def _arrange(self, leerie, tmp_path, monkeypatch, failing: dict,
                 **cap_over):
        st, staging, p1, merge = _setup(leerie, tmp_path)
        p2 = _git(staging, "rev-parse", f"{merge}^2").strip()
        # The repair touched a test file and the module it imports.
        (staging / "tests").mkdir()
        (staging / "tests" / "test_app.py").write_text("import app\n")
        (staging / "tests" / "test_other.py").write_text("x = 1\n")
        _git(staging, "add", "-A")
        _git(staging, "commit", "-q", "-m", "repair")
        trees: dict[str, Path] = {"head": staging}

        async def fake_tree(st_, sid, name, rev, caps, log_path):
            t = tmp_path / f"tree-{name}"
            (t / "tests").mkdir(parents=True, exist_ok=True)
            for f in ("test_app.py", "test_other.py"):
                (t / "tests" / f).write_text("")
            trees[name] = t
            return t

        def which(tree: str) -> str:
            return next(k for k, v in trees.items() if str(v) == tree)

        async def fake_ids(tree, cmd, st_, caps, *, label, log_path,
                           timeout, serial=False, files=None):
            ids = failing[which(tree)]
            return {"ids": sorted(ids),
                    "files": {i: i.split("::")[0] for i in ids},
                    "kind": "junit"}

        async def no_overlay(tree, other, base, globs):
            return None
        monkeypatch.setattr(leerie, "_integration_tree", fake_tree)
        monkeypatch.setattr(leerie, "_integration_tree_remove",
                            lambda st_, wt: None)
        monkeypatch.setattr(leerie, "_capture_failing_test_ids", fake_ids)
        monkeypatch.setattr(leerie, "_overlay_other_side", no_overlay)
        monkeypatch.setattr(leerie, "resolve_blt",
                            lambda root: {"test": "pytest"})
        monkeypatch.setattr(leerie, "_acceptance_cmd",
                            lambda st_, files, tree=None: "pytest x")
        monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a: 0.0)
        caps = dict(leerie.DEFAULT_CAPS, confirm_pairs=1, **cap_over)
        return asyncio.run(leerie._integration_test_failures(
            st, SID, staging, p1, p2, merge, caps,
            log_path=tmp_path / "log", parent_cache={}))

    def test_stale_and_code_failures_are_routed(self, leerie, tmp_path,
                                                monkeypatch):
        a, o = "tests/test_app.py::t_a", "tests/test_other.py::t_o"
        pre = "tests/test_other.py::t_pre"
        out = self._arrange(leerie, tmp_path, monkeypatch, {
            "head": {a, o, pre},
            "integrated": {pre},            # pre fails on a parent
            "incoming": set(),
            "overlay-integrated": {a},      # a: the other side broke it
        })
        assert out["scope"] == ["tests/test_app.py", "tests/test_other.py"]
        assert out["stale_by_other_side"] == [
            {"test_id": a, "home_side": "integrated",
             "other_side": "incoming"}]
        assert out["test_failure"] == [o]
        assert pre not in out["test_failure"]

    def test_unconfirmed_candidate_is_not_a_failure(self, leerie, tmp_path,
                                                    monkeypatch):
        # The candidate fails on HEAD's first run but the B* rerun on the
        # base side fails it too: resource_affected, not a failure.
        a = "tests/test_app.py::t_a"
        state = {"n": 0}
        failing = {"head": {a}, "integrated": set(), "incoming": set()}

        class Flaky(dict):
            def __getitem__(self, k):
                if k == "integrated":
                    state["n"] += 1
                    return set() if state["n"] == 1 else {a}
                return dict.__getitem__(self, k)
        out = self._arrange(leerie, tmp_path, monkeypatch, Flaky(failing))
        assert out["test_failure"] == [] and out["stale_by_other_side"] == []

    def test_a_candidate_the_budget_left_unsettled_is_not_a_failure(
            self, leerie, tmp_path, monkeypatch):
        """Review round 6 H1: no base/final pair finished inside the
        confirm budget. The merged-tree failure seen once is unsettled —
        never sent to the integrator as a `test_failure` (which would fail
        the round and can back the merge out), never `stale`."""
        a = "tests/test_app.py::t_a"
        out = self._arrange(leerie, tmp_path, monkeypatch, {
            "head": {a}, "integrated": set(), "incoming": set()},
            confirm_budget_sec=0)
        assert out["test_failure"] == [] and out["stale_by_other_side"] == []
        assert out["budget_short_unsettled"] == [a]

    def test_scope_includes_one_hop_importers(self, leerie, tmp_path):
        repo = tmp_path / "s"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", STAGING_BRANCH)
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "pricing.py").write_text("x = 1\n")
        (repo / "tests" / "test_pricing.py").write_text(
            "from src.pricing import x\n")
        (repo / "tests" / "test_unrelated.py").write_text("import os\n")
        (repo / "web").mkdir()
        (repo / "web" / "cart.ts").write_text("export const a = 1\n")
        (repo / "web" / "cart.test.ts").write_text(
            "import { a } from './cart'\n")
        _git(repo, "add", "-A")
        got = asyncio.run(leerie._conflicted_area_tests(
            repo, ["src/pricing.py", "web/cart.ts", "README.md"], None))
        assert got == ["tests/test_pricing.py", "web/cart.test.ts"]


def test_phase_execute_counts_expected_after_integrating(leerie):
    """Structural pin, paired with the executed backed-out wave in
    tests/test_integrate_wave_judge_gate.py: `expected` is counted AFTER
    `integrate_wave`, so a subtask the gate backed out (now `unconverged`)
    is not read as a silent integration skip; and a backed-out gate record
    is never re-run as pending on resume."""
    import inspect
    src = inspect.getsource(leerie.phase_execute)
    assert src.index("await integrate_wave(") < src.index("expected = sum(")
    i = src.index("pending_gate_sids = [")
    assert '"backed_out"' in src[i:i + 300]


@pytest.mark.parametrize("marker", [True, False])
def test_a_crash_between_the_back_out_reset_and_its_save_finishes_it(
        leerie, tmp_path, monkeypatch, marker):
    """Review r1 suspicion: `_back_out` resets staging to the merge's first
    parent, then saves. A crash between the two leaves a record neither
    accepted nor backed out while the merge is gone from staging; the
    resumed gate re-judged a synthetic merge of the reset tree, and a clean
    verdict returned True — "the merge stands" — for a merge that is not on
    the branch, with the subtask still `complete`."""
    st, staging, p1, merge = _setup(leerie, tmp_path)
    conflict = dict(DEFECT, kind="intent_conflict")
    real_save = st.save
    reset_seen = {"n": 0}
    real_restore = leerie._restore_tree

    async def spy_restore(tree, rev, *, where, branch):
        if where.startswith("back-out of"):
            reset_seen["n"] += 1
        return await real_restore(tree, rev, where=where, branch=branch)

    class Crash(BaseException):
        pass

    def crashing_save():
        if reset_seen["n"]:
            raise Crash()
        real_save()
    monkeypatch.setattr(leerie, "_restore_tree", spy_restore)
    monkeypatch.setattr(st, "save", crashing_save)
    w = _Workers(staging, [[conflict]])
    with pytest.raises(Crash):
        _run(leerie, st, staging, monkeypatch, w)
    assert _git(staging, "rev-parse", "HEAD").strip() == p1
    # The resume: a fresh State read from disk, and a judge that now finds
    # nothing on the (reset) tree.
    st.release_lock()
    st2 = leerie.State(st.run_dir.parent.parent, st.run_id)
    assert st2.load()
    assert st2.data["integration_gate"][SID].get("backing_out")
    if not marker:
        # Review round 2 M1: with no marker, the merge's absence from
        # staging is never read as a back-out (an amended merge looks the
        # same). The recorded merge is restored and judged again, so the
        # tree and the record agree whatever the verdict.
        st2.data["integration_gate"][SID].pop("backing_out")
        w2 = _Workers(staging, [[]])
        assert _run(leerie, st2, staging, monkeypatch, w2) is True
        assert w2.kinds() == ["integration_judge"]
        assert _git(staging, "rev-parse", "HEAD").strip() == merge
        rec = st2.data["integration_gate"][SID]
        assert rec["accepted"] is True and rec.get("outcome") != "backed_out"
        assert st2.data["subtask_status"][SID] == "complete"
        return
    w2 = _Workers(staging, [[]])
    assert _run(leerie, st2, staging, monkeypatch, w2) is False
    assert w2.kinds() == []               # nothing re-judged
    rec = st2.data["integration_gate"][SID]
    assert rec["outcome"] == "backed_out" and rec["reset_to"] == p1
    assert st2.data["subtask_status"][SID] == "unconverged"
    assert _git(staging, "rev-parse", "HEAD").strip() == p1


class _Crash(BaseException):
    """A SIGTERM, OOM or resumable pause: nothing after it runs."""


class _AmendOnce(_Workers):
    """Round 1's integrator amends the merge itself (a mistake the round's
    `descends_from_merge` check catches and resets); later rounds repair
    with a new commit."""

    async def __call__(self, **kw):
        if kw["schema_key"] == "integrator" and not any(
                c["schema_key"] == "integrator" for c in self.calls):
            self.calls.append(kw)
            (self.staging / "app.py").write_text(THEIRS)
            _git(self.staging, "commit", "-q", "-a", "--amend", "--no-edit")
            return {"incoming_subtask": SID, "status": "resolved",
                    "diagnosis": None, "superseded": []}
        return await super().__call__(**kw)


class _TruthfulJudge(_Workers):
    """A judge that reads the tree it is shown: a finding exactly when the
    judged commit lacks the incoming side's function. A scripted verdict
    list cannot stand in on resume — which verdict comes first depends on
    where the crash fell."""

    async def __call__(self, **kw):
        if kw["schema_key"] != "integration_judge":
            return await super().__call__(**kw)
        self.calls.append(kw)
        sha = kw["user_prompt"].split("Commit: ", 1)[1].split("\n", 1)[0]
        text = _git(self.staging, "show", f"{sha.strip()}:app.py")
        ok = "def incoming_helper_function" in text
        return {"merge_reviewed": True, "defects": [] if ok else [DEFECT],
                "rationale": "r"}


def _crash_run_workers(kind: str, staging: Path) -> _Workers:
    if kind == "amend":
        # Judge: a finding; round 1 amends (reset, no re-judge); round 2
        # repairs and its re-judge passes.
        return _AmendOnce(staging, [[DEFECT], []])
    return _Workers(staging, [[DEFECT], []])


def _saves_in_a_full_run(leerie, tmp_path, monkeypatch, kind) -> int:
    st, staging, _p1, _merge = _setup(leerie, tmp_path / "count")
    n = {"saves": 0}
    real = st.save

    def counting():
        n["saves"] += 1
        real()
    monkeypatch.setattr(st, "save", counting)
    assert _run(leerie, st, staging, monkeypatch,
                _crash_run_workers(kind, staging)) is True
    monkeypatch.undo()
    return n["saves"]


def test_amend_then_crash_is_redone_not_backed_out(leerie, tmp_path,
                                                   monkeypatch):
    """Review round 2 M1, the reviewer's driver: the repair integrator
    amends the merge and the process dies mid-session. The resume read the
    merge's absence from staging as an interrupted back-out and backed the
    whole subtask out without a judge call."""
    st, staging, p1, merge = _setup(leerie, tmp_path)

    class AmendThenCrash(_Workers):
        async def __call__(self, **kw):
            self.calls.append(kw)
            if kw["schema_key"] == "integration_judge":
                return {"merge_reviewed": True,
                        "defects": self.verdicts.pop(0) if self.verdicts
                        else [], "rationale": "r"}
            (self.staging / "app.py").write_text(THEIRS)
            _git(self.staging, "commit", "-q", "-a", "--amend", "--no-edit")
            raise _Crash()

    with pytest.raises(_Crash):
        _run(leerie, st, staging, monkeypatch,
             AmendThenCrash(staging, [[DEFECT]]))
    # The amended merge is on staging, the recorded one is not.
    assert subprocess.run(["git", "merge-base", "--is-ancestor", merge,
                           "HEAD"], cwd=str(staging)).returncode == 1
    st.release_lock()
    st2 = leerie.State(st.run_dir.parent.parent, st.run_id)
    assert st2.load()
    rec = st2.data["integration_gate"][SID]
    assert rec["in_round"]["round"] == 1 and rec["in_round"]["pre"] == merge
    assert not rec.get("backing_out")
    w2 = _Workers(staging, [[]])          # a judge that would now pass
    assert _run(leerie, st2, staging, monkeypatch, w2) is True
    assert w2.kinds() == ["integration_judge"]
    rec = st2.data["integration_gate"][SID]
    assert rec.get("outcome") != "backed_out" and rec["accepted"] is True
    assert "in_round" not in rec
    # The interrupted round is handed back and marked, not counted spent.
    assert rec["repair_rounds"] == 0
    assert rec["rounds"][-1]["verdict"] == "interrupted"
    assert st2.data["subtask_status"][SID] == "complete"
    assert _git(staging, "rev-parse", "HEAD").strip() == merge


@pytest.mark.parametrize("kind", ["commit", "amend"])
def test_a_crash_at_every_save_point_never_backs_the_subtask_out(
        leerie, tmp_path, monkeypatch, kind):
    """Review round 2 M1: crash after each `st.save()` of a gate run that
    repairs its finding (with round 1 amending the merge, in `amend`), then
    resume the way `integrate_wave` does — skip a backed-out or accepted
    record, else re-enter the gate. At every point the merge survives on
    staging, repaired or clean, and the subtask stays `complete`."""
    total = _saves_in_a_full_run(leerie, tmp_path, monkeypatch, kind)
    assert total >= 4
    for k in range(1, total + 1):
        st, staging, _p1, merge = _setup(leerie, tmp_path / f"k{k}")
        real = st.save
        n = {"saves": 0}

        def crashing():
            real()
            n["saves"] += 1
            if n["saves"] == k:
                raise _Crash()
        monkeypatch.setattr(st, "save", crashing)
        try:
            _run(leerie, st, staging, monkeypatch,
                 _crash_run_workers(kind, staging))
        except _Crash:
            pass
        monkeypatch.undo()
        st.release_lock()
        st2 = leerie.State(st.run_dir.parent.parent, st.run_id)
        assert st2.load()
        rec = (st2.data.get("integration_gate") or {}).get(SID) or {}
        assert rec.get("outcome") != "backed_out", (k, rec)
        if not rec.get("accepted"):
            ok = _run(leerie, st2, staging, monkeypatch,
                      _TruthfulJudge(staging, []))
            assert ok is True, (k, st2.data["integration_gate"][SID])
            monkeypatch.undo()
        rec = st2.data["integration_gate"][SID]
        assert rec["accepted"] is True, (k, rec)
        assert rec.get("outcome") in ("clean", "repaired"), (k, rec)
        assert "in_round" not in rec and "backing_out" not in rec, (k, rec)
        assert st2.data["subtask_status"][SID] == "complete", k
        assert subprocess.run(
            ["git", "merge-base", "--is-ancestor", merge, "HEAD"],
            cwd=str(staging)).returncode == 0, k
        assert "def incoming_helper_function" in (
            staging / "app.py").read_text() or rec["outcome"] == "clean", k
        st2.release_lock()
