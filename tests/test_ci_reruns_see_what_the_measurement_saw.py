"""A B* rerun of a merge check sees what the measurement saw (DESIGN §9 *The
repository's own merge checks run too*, *A merge check's rerun sees what its
measurement saw*).

A measurement runs the gating checks once each, in recorded order, in one
tree: barnacle's `smoke-load-dist` loads the `dist/` its `build` wrote. B*
then reruns only the red checks, sized shortest first, on a fresh worktree
of the reference commit. Two shapes broke there, both run for real here (a
real git repo, real check scripts, the real `_ci_round_regressions` and
`_run_ci_parity_final`, real B* reruns; only the workers, the broker cgroup
and the host-pressure gate are stubbed):

- every check red: the smoke check, the shortest, ran before build on the
  base's first rerun and failed there (no `dist/` yet), then passed on the
  second once build had written it — mixed, `resource_affected`, never the
  round's;
- build green, the smoke check red on what build wrote: only the smoke check
  is rerun, and on the base worktree build never runs at all, so the smoke
  check fails every base rerun — `pre_existing`, and the regression ships.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_delivery_redrive import World
from tests.test_kept_rounds_keep_merge_checks_green import _quiet_ci

# barnacle's four gates, in its CI's order. The build is the slowest, so
# shortest-first sizing puts the smoke check ahead of it.
CHECKS = r'''
import pathlib, sys, time
mode, src = sys.argv[1], pathlib.Path(sys.argv[2])
text = src.read_text()
if mode == "typecheck":
    if "BAD_TYPE" in text:
        print(f"{src}(1,1): error TS2345: bad")
        sys.exit(1)
    sys.exit(0)
if mode == "build":
    time.sleep(0.6)
    if "BAD_TYPE" in text:
        print(f"{src}(1,1): error TS2345: bad")
        sys.exit(2)
    pathlib.Path("dist").mkdir(exist_ok=True)
    pathlib.Path("dist/server.js").write_text(
        "broken\n" if "BAD_DIST" in text else "ok\n")
    sys.exit(0)
if mode == "smoke":
    p = pathlib.Path("dist/server.js")
    if not p.exists():
        print("Error: Cannot find module './dist/server.js'")
        sys.exit(1)
    if p.read_text() != "ok\n":
        print("Error: dist/server.js failed to load")
        sys.exit(1)
    sys.exit(0)
'''


def _recs(script: Path) -> list[dict]:
    def rec(cid: str, kind: str, mode: str) -> dict:
        return {"id": cid, "kind": kind, "cwd": ".", "env": [],
                "prerequisites": [], "requires": [], "strict_argv": [],
                "argv": [sys.executable, str(script), mode, "src/a.ts"],
                "selected": True, "gate": "plain", "base": {"rc": 0}}
    return [rec("typecheck", "typecheck", "typecheck"),
            rec("prepush-typecheck", "typecheck", "typecheck"),
            rec("build", "build", "build"),
            rec("smoke-load-dist", "other", "smoke")]


def _world(leerie, tmp_path, monkeypatch) -> World:
    real_final = leerie._run_ci_parity_final
    w = World(leerie, tmp_path, n_sets=0)
    w.install(monkeypatch)
    # The real final pass, not World's stub.
    monkeypatch.setattr(leerie, "_run_ci_parity_final", real_final)
    _quiet_ci(leerie, monkeypatch, w.caps)
    script = tmp_path / "checks.py"
    script.write_text(CHECKS)
    w.st.data["ci_parity"] = {"baseline_done": True, "base_sha": w.base,
                              "checks": _recs(script)}
    w.st.save()
    return w


def _round(leerie, w: World, text: str) -> tuple[str, str, dict]:
    pre = _git(w.staging, "rev-parse", "HEAD")
    w.write(text, "round")
    post = _git(w.staging, "rev-parse", "HEAD")
    mc = asyncio.run(leerie._ci_round_regressions(
        w.st, w.caps, w.staging, pre, post, label="redrive-r1g0"))
    return pre, post, mc


def test_every_check_red_the_dependent_one_is_the_rounds_too(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    _pre, _post, mc = _round(leerie, w, "export const a = 1; // BAD_TYPE\n")
    durs = {i: r["duration_sec"] for i, r in
            w.st.data["ci_parity"]["measured"][_post]["results"].items()}
    # The shape that broke: shortest first puts the smoke check ahead of the
    # build it reads.
    assert durs["smoke-load-dist"] < durs["build"]
    v = mc["b_star"]
    assert v["resource_affected"] == [] and v["pre_existing"] == []
    assert sorted(b["id"] for b in mc["broke"]) == sorted(
        ["typecheck", "prepush-typecheck", "build", "smoke-load-dist"])
    assert v["per_id"]["smoke-load-dist"]["base_failed"] == 0


def test_a_green_producer_still_runs_before_its_reader_on_the_base(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    _pre, _post, mc = _round(leerie, w, "export const a = 1; // BAD_DIST\n")
    # Only the smoke check is red, so only it is rerun; the build it reads
    # is green and is not a candidate.
    assert mc["b_star"]["candidates"] == ["smoke-load-dist"]
    assert mc["pre_existing"] == []
    assert [b["id"] for b in mc["broke"]] == ["smoke-load-dist"]
    assert mc["b_star"]["per_id"]["smoke-load-dist"]["base_failed"] == 0


def test_the_final_pass_confirms_the_dependent_check_against_the_base(
        leerie, tmp_path, monkeypatch):
    # The same rule at the final pass, which shares the confirm: the base of
    # the run, not a round's start.
    w = _world(leerie, tmp_path, monkeypatch)
    w.write("export const a = 1; // BAD_DIST\n", "the run's change")
    unmet = asyncio.run(leerie._run_ci_parity_final(w.dir, w.st, w.caps))
    fin = w.st.data["ci_parity"]["final"]
    assert [u["id"] for u in unmet] == ["smoke-load-dist"]
    assert fin["pre_existing"] == [] and fin["resource_affected"] == []
