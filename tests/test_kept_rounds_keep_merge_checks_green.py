"""A kept repair keeps the repository's merge checks green (DESIGN §8 *A
kept repair keeps the repository's merge checks green*; barnacle 87580f62).

Re-drive round 1 of 87580f62 committed `5c0f667`, whose
`parseInt(mapped[1], 16)` is TS2345 under `noUncheckedIndexedAccess`; build,
typecheck, prepush-typecheck and smoke-load-dist went red, nothing measured
them before the loop ended, and the round was kept. Here the same shape runs
for real: a real git repo, the real re-drive and acceptance gate, real merge
checks (a script standing for `tsc`, a build writing `dist/`, a smoke test
loading it), real B* reruns. Only `claude_p` (the workers), the broker
cgroup and the host-pressure gate are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import (FIXED, _caps, _make_sets, _repo,
                                        _st, _staging)
from tests.test_delivery_redrive import World

# One script for the four checks barnacle's CI runs, in its order. The
# typecheck flags an unguarded `mapped[1]` the way tsc does under
# `noUncheckedIndexedAccess`; the build fails on the same and otherwise
# writes `dist/server.js`; the smoke test loads what the build wrote, so it
# goes red as a cascade, as 87580f62's did.
CHECKS = r'''
import pathlib, sys
mode, src = sys.argv[1], pathlib.Path(sys.argv[2])
text = src.read_text()
bad = "mapped[1], 16" in text
msg = (f"{src}(536,25): error TS2345: Argument of type 'string | undefined' "
       "is not assignable to parameter of type 'string'.")
if mode in ("typecheck", "build"):
    if bad:
        print("> tsc --noEmit")
        print(msg)
        print("  Type 'undefined' is not assignable to type 'string'.")
        sys.exit(1 if mode == "typecheck" else 2)
    if mode == "build":
        pathlib.Path("dist").mkdir(exist_ok=True)
        pathlib.Path("dist/server.js").write_text("ok\n")
    sys.exit(0)
if mode == "smoke":
    if not pathlib.Path("dist/server.js").exists():
        print("node:internal/modules/cjs/loader:1386")
        print("Error: Cannot find module '@/api/plugins/auth'")
        sys.exit(1)
    sys.exit(0)
'''

# 5c0f667's hunk, in the shape that matters.
RED = ("export function isLinkLocal(h: string): boolean {\n"
       "  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(h);\n"
       "  if (mapped) { const hi = parseInt(mapped[1], 16); "
       "if (hi === 0xa9fe) return true; }\n"
       "  return false;\n}\n")
# The one-line narrowing the audit names.
GREEN = RED.replace("parseInt(mapped[1], 16)", 'parseInt(mapped[1] ?? "", 16)')


def _ci_recs(script: Path, src: str) -> list[dict]:
    def rec(cid: str, kind: str, mode: str) -> dict:
        return {"id": cid, "kind": kind, "cwd": ".", "env": [],
                "prerequisites": [], "requires": [], "strict_argv": [],
                "argv": [sys.executable, str(script), mode, src],
                "selected": True, "gate": "plain", "base": {"rc": 0}}
    return [rec("typecheck", "typecheck", "typecheck"),
            rec("prepush-typecheck", "typecheck", "typecheck"),
            rec("build", "build", "build"),
            rec("smoke-load-dist", "other", "smoke")]


def _quiet_ci(leerie, monkeypatch, caps: dict) -> None:
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)

    async def _gate(caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 1.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", _gate)
    caps.update({"confirm_pairs": 2, "confirm_pairs_escalated": 2,
                 "confirm_budget_sec": 120, "worker_timeout_sec": 60})


# --- the re-drive: the 87580f62 replay -------------------------------------

ITEMS = [{"item": "IPv4-mapped link-local hosts are refused", "span": "S1",
          "verifiability": "tree"}]
UNMET = [{"item_index": 0, "item": ITEMS[0]["item"], "evidence": "missing",
          "by": ["judge"]}]


def _redrive_world(leerie, tmp_path, monkeypatch, edits) -> World:
    real_final = leerie._run_ci_parity_final
    w = World(leerie, tmp_path, n_sets=0, items=ITEMS, unmet=UNMET,
              claims={"s1": [{"item_index": 0, "kind": "deliverable"}]})
    w.install(monkeypatch)
    # The real final pass, not World's stub: the re-drive's own merge-check
    # runs are what this file is about.
    monkeypatch.setattr(leerie, "_run_ci_parity_final", real_final)
    _quiet_ci(leerie, monkeypatch, w.caps)
    script = tmp_path / "checks.py"
    script.write_text(CHECKS)
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["ci_parity"] = {"baseline_done": True, "base_sha": w.base,
                              "checks": _ci_recs(script, "src/a.ts")}
    w.st.save()
    # The final pass on the tree the re-drive starts from: every gate green,
    # as 87580f62's was at 04:35.
    assert asyncio.run(leerie._run_ci_parity_final(
        w.dir, w.st, w.caps)) == []
    assert w.st.data["ci_parity"]["final"]["sha"] == head
    w.edits = list(edits)

    async def met(task, its, staging, st, caps, models, efforts,
                  phase_label, **kw):
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", met)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))

    async def no_falsifier(st, caps, models, efforts, staging, out):
        return out
    monkeypatch.setattr(leerie, "_redrive_falsify_met", no_falsifier)
    return w


def test_a_round_that_turns_the_merge_checks_red_is_rolled_back_named(
        leerie, tmp_path, monkeypatch):
    w = _redrive_world(leerie, tmp_path, monkeypatch, [
        lambda w: w.write(RED, "fix: link-local mapped hosts"),
        lambda w: w.write(GREEN, "fix: link-local mapped hosts, narrowed")])
    start = _git(w.staging, "rev-parse", "HEAD")
    w.run()
    rec = w.st.data["delivery_redrive"]
    g1 = rec["rounds"][0]["groups"][0]
    # Round 1's commit is rolled back, every check it broke named, with
    # the compiler's first error line.
    assert g1["rolled_back"] is True
    # (smoke-load-dist reads build's `dist/`; B*'s reruns go shortest
    # first, so on the start's reruns it can run before build and read as
    # mixed — resource_affected, never the round's. The three that do not
    # depend on another are enough to roll the round back.)
    assert {"typecheck", "prepush-typecheck", "build"} <= set(
        g1["merge_checks_broke"])
    assert "turned the repository's merge check(s) red" in \
        g1["rollback_reason"]
    assert "error TS2345" in g1["rollback_reason"]
    broke = {b["id"]: b for b in g1["ratchet"]["merge_checks_broke"]}
    assert any("error TS2345" in ln for ln in broke["typecheck"]["lines"])
    # Confirmed under B* against the round's start, not taken from one run.
    mc = g1["ratchet"]["merge_checks"]
    assert mc["pre_sha"] == start
    assert sorted(mc["b_star"]["new"]) == sorted(broke)
    # The next group is told which check, its command and its first lines.
    assert len(w.calls) == 2
    p2 = w.calls[1]["prompt"]
    assert '"kind": "control_regression"' in p2
    assert "merge check `typecheck`" in p2 and "error TS2345" in p2
    assert "checks.py typecheck src/a.ts" in p2
    # Round 2's narrowing is kept; round 1's commit is not in what ships.
    g2 = rec["rounds"][1]["groups"][0]
    assert not g2.get("rolled_back")
    assert g2["ratchet"]["merge_checks"]["broke"] == []
    final = _git(w.staging, "rev-parse", "HEAD")
    assert _git(w.staging, "log", "--format=%s", f"{start}..{final}") == \
        "fix: link-local mapped hosts, narrowed"
    # The final pass reads round 2's measurement: green, not run again.
    fin = w.st.data["ci_parity"]["final"]
    assert fin["sha"] == final and fin["unmet"] == []
    assert fin["reused"] == "repair redrive-r2g0"


def test_a_check_already_red_at_the_rounds_start_is_not_the_rounds(
        leerie, tmp_path, monkeypatch):
    # The tree the round starts from is red (an earlier commit broke it):
    # the round that leaves it red is not rolled back for it.
    w = _redrive_world(leerie, tmp_path, monkeypatch, [])
    w.write(RED, "an earlier commit")
    pre = _git(w.staging, "rev-parse", "HEAD")
    (w.staging / "src" / "b.ts").write_text("export const b = 3;\n")
    _git(w.staging, "commit", "-qam", "round")
    post = _git(w.staging, "rev-parse", "HEAD")
    mc = asyncio.run(leerie._ci_round_regressions(
        w.st, w.caps, w.staging, pre, post, label="redrive-r1g0"))
    assert mc["broke"] == []
    assert sorted(mc["pre_existing"]) == sorted(
        ["typecheck", "prepush-typecheck", "build", "smoke-load-dist"])
    rat = asyncio.run(leerie._delivery_ratchet(
        w.st, w.caps, {}, {}, w.staging, pre, post, exec_result=None,
        label="redrive-r1g0b"))
    assert leerie._ratchet_rollback_reason(rat) is None


# --- the acceptance gate: the same rule, its other caller --------------------

BREAKS = FIXED + "\n# BREAKS_BUILD\n"
BUILD = ("import pathlib, sys\n"
         "sys.exit(1 if 'BREAKS_BUILD' in pathlib.Path('calc.py').read_text()"
         " else 0)\n")


def test_an_acceptance_round_that_breaks_the_build_is_reset(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 1)}
    script = tmp_path / "build.py"
    script.write_text(BUILD)
    st.data["ci_parity"] = {"baseline_done": True, "base_sha": head,
                            "checks": [{
                                "id": "build", "kind": "build", "cwd": ".",
                                "env": [], "prerequisites": [],
                                "requires": [], "strict_argv": [],
                                "argv": [sys.executable, str(script)],
                                "selected": True, "gate": "plain",
                                "base": {"rc": 0}}]}
    st.save()
    caps = _caps(leerie, 1)
    _quiet_ci(leerie, monkeypatch, caps)

    async def unchanged(st, caps, models, efforts, staging, final, sha):
        return final
    monkeypatch.setattr(leerie, "_acceptance_audit_and_rewrite", unchanged)
    fixers = [lambda wt: ((wt / "calc.py").write_text(BREAKS),
                          _git(wt, "commit", "-qam", "conformer: fix, break")),
              lambda wt: ((wt / "calc.py").write_text(FIXED),
                          _git(wt, "commit", "-qam", "conformer: fix add"))]

    async def workers(**kw):
        assert kw["schema_key"] == "conformer"
        fixers.pop(0)(Path(kw["cwd"]))
        return {}
    monkeypatch.setattr(leerie, "claude_p", workers)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, caps, {"conformer": "sonnet"}, {"conformer": "low"}))
    r1, r2 = st.data["acceptance"]["gate"]["rounds"]
    # Round 1 fixed the failing case (so it was not "fixed nothing") and
    # broke the build: reset, the check named.
    assert r1["fixed_cases"]
    assert r1["rollback_reason"].startswith(
        "turned the repository's merge check(s) red: build")
    assert r1["rolled_back"] is True
    assert [b["id"] for b in r1["ratchet"]["merge_checks_broke"]] == ["build"]
    assert not r2.get("rollback_reason")
    assert _git(staging, "log", "--format=%s", f"{start}..HEAD") == \
        "conformer: fix add"
    assert "BREAKS_BUILD" not in (staging / "calc.py").read_text()


# --- pure ---------------------------------------------------------------------

def test_first_error_lines_skip_the_header_and_banners(leerie):
    log = ("=== ci final-typecheck: pnpm run typecheck (cwd=/x) ===\n\n"
           "> @enricai/barnacle@1.21.3 typecheck /x\n> tsc --noEmit\n\n"
           "src/scripts/recon-http.ts(536,25): error TS2345: Argument of "
           "type 'string | undefined' is not assignable to parameter of "
           "type 'string'.\n"
           "  Type 'undefined' is not assignable to type 'string'.\n"
           " ELIFECYCLE  Command failed with exit code 1.\n")
    assert leerie._ci_first_error_lines(log) == [
        "src/scripts/recon-http.ts(536,25): error TS2345: Argument of type "
        "'string | undefined' is not assignable to parameter of type "
        "'string'."]
    # No line names an error: the last lines, never the header.
    assert leerie._ci_first_error_lines(
        "=== ci x: y ===\na\nb\n") == ["a", "b"]


def test_no_gating_check_measures_nothing(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r")
    st.data = {}
    assert asyncio.run(leerie._ci_round_regressions(
        st, {}, tmp_path, "a" * 40, "b" * 40, label="x")) == {}


@pytest.mark.parametrize("keep", [1, 4])
def test_measured_commits_are_bounded(leerie, tmp_path, keep, monkeypatch):
    monkeypatch.setattr(leerie, "_CI_MEASURED_KEEP", keep)
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"ci_parity": {"checks": []}}
    for i in range(6):
        leerie._ci_remember(st, f"sha{i}", {"c": {"passed": True,
                                                  "log": "/l"}}, f"m{i}")
    memo = st.data["ci_parity"]["measured"]
    assert list(memo) == [f"sha{i}" for i in range(6 - keep, 6)]
    assert memo["sha5"]["results"] == {"c": {"passed": True}}
    json.dumps(memo)
