"""Review round 8, unproven suspicion, measured and fixed: CI parity's sized
confirm re-created its `ci-confirm-base` worktree on every pass, outside
`confirm_budget_sec` (DESIGN §8 *CI parity*).

`_ci_confirm_chosen` added the worktree, ran `_ci_prepare` (the dependency
install — its installed-once mark leaves with the worktree — the
throwaway database and every prerequisite) and tore it down, once per pass.
With round 8 L3's one-pair-first passes and their continuations, that is
up to two setups per red check. Now the confirm creates one base worktree,
prepares each check and each prerequisite once, and removes it at the
end."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_ci_parity import PY, _check, _run, env  # noqa: E402,F401

PRE = {"cwd": ".", "argv": [PY, "-c", "print('generate')"]}


def _drive(leerie, env, monkeypatch, tmp_path):
    made: list[str] = []
    torn: list[str] = []
    prepared: list[list[str]] = []
    pre_runs: list[str] = []

    async def fake_wt(st, name, sha):
        made.append(name)
        return tmp_path / name

    async def no_deps(*a, **k):
        return True

    async def fake_run(argv, *, tree, rec, st, caps, label):
        # A pass that reads 0.000 s is charged everything left, by design.
        await leerie.asyncio.sleep(0.01)
        if "-pre" in label:
            pre_runs.append(label)
            return {"rc": 0, "passed": True, "timed_out": False}
        bad = "confirm-base" not in label
        return {"rc": 1 if bad else 0, "passed": not bad, "timed_out": False}

    real_prepare = leerie._ci_prepare

    async def spy_prepare(tree, recs, *a, **k):
        prepared.append([r["id"] for r in recs])
        return await real_prepare(tree, recs, *a, **k)
    monkeypatch.setattr(leerie, "_ci_worktree", fake_wt)
    monkeypatch.setattr(leerie, "_ci_teardown",
                        lambda st, wt: torn.append(wt.name))
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", no_deps)
    monkeypatch.setattr(leerie, "_ci_run", fake_run)
    monkeypatch.setattr(leerie, "_ci_prepare", spy_prepare)
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    a, b = _check("a", [PY, "a.py"]), _check("b", [PY, "b.py"])
    a["prerequisites"] = b["prerequisites"] = [PRE]
    caps = {**env["caps"], "confirm_budget_sec": 3.0,
            "worker_timeout_sec": 60}
    # Recorded 1.0 s and 1.4 s: one pair of both (4.8 s) does not fit 3 s,
    # so `b` is sized out of the first pass; both rerun at once in fact.
    v = _run(leerie._ci_confirm_red(env["st"], caps, "deadbeef",
                                    tmp_path / "final", [a, b],
                                    durations={"a": 1.0, "b": 1.4}))
    return v, made, torn, prepared, pre_runs


def test_every_pass_shares_one_base_worktree(leerie, env, monkeypatch,
                                             tmp_path):
    v, made, torn, prepared, pre_runs = _drive(leerie, env, monkeypatch,
                                               tmp_path)
    # Four confirms ran (a, b, then each continued to `k`), one checkout.
    assert len(v["passes"]) == 2 and v["new"] == ["a", "b"]
    assert [p["pairs_run"] for p in v["passes"]] == [2, 2]
    assert not v["budget_short"]
    assert made == ["ci-confirm-base"]
    assert torn == ["ci-confirm-base"]
    # Each check prepared once, by the pass that first chose it; the shared
    # prerequisite run once.
    assert prepared == [["a"], ["b"]]
    assert len(pre_runs) == 1


def test_a_base_that_cannot_be_created_is_tried_once(leerie, env,
                                                     monkeypatch, tmp_path):
    made: list[str] = []

    async def no_wt(st, name, sha):
        made.append(name)
        return None
    monkeypatch.setattr(leerie, "_ci_worktree", no_wt)
    monkeypatch.setattr(leerie, "_ci_teardown",
                        lambda st, wt: made.append("teardown"))
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    caps = {**env["caps"], "confirm_budget_sec": 3.0}
    v = _run(leerie._ci_confirm_red(
        env["st"], caps, "deadbeef", tmp_path / "final",
        [_check("a", [PY, "a.py"]), _check("b", [PY, "b.py"])],
        durations={"a": 1.0, "b": 1.4}))
    assert made == ["ci-confirm-base"]
    assert v["new"] == []
