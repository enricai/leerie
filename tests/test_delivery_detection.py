"""Delivery detection, F+ k=3 (DESIGN §8 *Delivery detection*).

An item is unmet when the cited delivery judge confirms it unmet OR any of
`falsifier_samples` falsifier samples is confirmed by code. Covered here:

- the judge's typed per-part citations, verified against HEAD by code
  (`_verify_delivery_cite`, `_delivery_screen_sample`) — a fabricated quote
  cannot buy a met, an `ABSENT` cite is re-run as `git grep -E`;
- the orchestrator's own results in the record (`_orchestrator_results_digest`);
- the falsifier's code gates (`_falsifier_gates`), run for real on a pytest
  probe in a pristine checkout — new file, item quote, B* failure on the
  final tree, assertion inside the case — with only the TS AST facts
  stubbed where the probe is Python; the AST facts script itself and the
  coupling policy are tested on their own;
- the one re-ask, decided from the sample's own transcript, resumed in the
  same session (`resume_session` → `--resume`);
- `_run_delivery_detection` end to end: judge reuse, external / untouched
  items never falsified, the any-of-k verdict, `unmet_final` and the
  outcome / next-run residual reading it.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import types
from pathlib import Path
from unittest import mock

import pytest

from tests.stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,
                                _delivery_tree, _judge_verdict)

MODELS = {"delivery_judge": "sonnet", "falsifier": "sonnet"}
EFFORTS = {"delivery_judge": "medium", "falsifier": "medium"}

BUGGY = "def add(a, b):\n    return a - b\n"
ITEM = "add(2, 3) returns 5, the sum of its arguments"
PROBE_REL = "falsify_probe/test_add_probe.py"
PROBE_SRC = ("from calc import add\n\n\n"
             "def test_add_returns_the_sum():\n"
             "    assert add(2, 3) == 5\n")
PASSING_PROBE_SRC = ("from calc import add\n\n\n"
                     "def test_add_returns_the_sum():\n"
                     "    assert add(3, 3) == 0\n")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    # B*'s gate reads the host's PSI; pin it quiet so a loaded host cannot
    # stretch these tests by the gate's wait.
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / "data").mkdir()
    (repo / "data" / "a.csv").write_text("x\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider '
        '{test_files}"\n'
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _st(leerie, tmp_path: Path, repo: Path, head: str, **data):
    root = tmp_path / "state"
    run_dir = root / "runs" / "run-d"
    (run_dir / "logs").mkdir(parents=True)
    st = leerie.State(root, "run-d", repo_root=repo)
    st.data = {"task": "make add return the sum", "verbosity": "quiet",
               "working_branch": "main",
               "repo_state_before_planning": {"head": head},
               "worker_count": 0}
    st.data.update(data)
    st.save()
    return st


def _staging(st, repo: Path, sha: str) -> Path:
    staging = st.run_dir / "worktrees" / "staging"
    staging.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "--detach", str(staging), sha)
    return staging


# === the judge's citations, checked by code ================================

class TestVerifyDeliveryCite:
    def test_statuses(self, leerie, tmp_path):
        tree = _delivery_tree(tmp_path / "t")
        v = leerie._verify_delivery_cite

        def c(path, quote, ls=1, le=1):
            return {"path": path, "line_start": ls, "line_end": le,
                    "quote": quote}
        assert v(c(DELIVERY_CITED_FILE, DELIVERY_CITED_LINE), tree, "") == "ok"
        # Real text at the wrong lines is still in the file at HEAD.
        assert v(c(DELIVERY_CITED_FILE, DELIVERY_CITED_LINE, 9, 9),
                 tree, "") == "drift"
        # Whitespace differences are normalised away.
        assert v(c(DELIVERY_CITED_FILE,
                   "def   delivered_feature(value):\n"), tree, "") == "ok"
        # A fabricated quote is not in the file.
        assert v(c(DELIVERY_CITED_FILE, "def invented_function(x):"),
                 tree, "") == "absent"
        assert v(c("src/missing.py", DELIVERY_CITED_LINE), tree,
                 "") == "no_file"
        assert v(c(DELIVERY_CITED_FILE, "def"), tree, "") == "short"
        assert v(c("../outside.py", DELIVERY_CITED_LINE), tree,
                 "") == "no_file"
        # The tree's own absolute prefix is stripped.
        assert v(c(f"{tree}/{DELIVERY_CITED_FILE}", DELIVERY_CITED_LINE),
                 tree, "") == "ok"

    def test_record_and_absent(self, leerie, tmp_path):
        tree = _delivery_tree(tmp_path / "t")
        v = leerie._verify_delivery_cite
        record = "[orchestrator final-tree tests] $ pytest -q\n    result: passed"
        assert v({"path": "RECORD", "quote": "result: passed"}, tree,
                 record) == "record_ok"
        assert v({"path": "RECORD", "quote": "result: failed"}, tree,
                 record) == "record_absent"
        # ABSENT: re-run as git grep -E over HEAD.
        assert v({"path": "ABSENT", "quote": "forbidden_term"}, tree,
                 "") == "absent_ok"
        assert v({"path": "ABSENT", "quote": "delivered_feat[a-z]+"}, tree,
                 "") == "absent_found"
        # Scoped by a pathspec: the term exists, but not under docs/.
        assert v({"path": "ABSENT:docs/", "quote": "delivered_feature"},
                 tree, "") == "absent_ok"
        assert v({"path": "ABSENT:src/", "quote": "delivered_feature"},
                 tree, "") == "absent_found"
        assert v({"path": "ABSENT", "quote": "("}, tree, "") == "bad_regex"
        assert v({"path": "ABSENT", "quote": ""}, tree, "") == "bad_regex"


class TestJudgeScreen:
    def test_a_fabricated_quote_cannot_buy_a_met(self, leerie, tmp_path):
        tree = _delivery_tree(tmp_path / "t")
        fake = _judge_verdict(0, True)
        fake["parts"][0]["cites"][0]["quote"] = "def invented_function(x):"
        no_parts = {"item_index": 1, "met": True, "parts": [],
                    "evidence": "e"}
        half = _judge_verdict(2, True)
        half["parts"].append({"requirement": "r", "kind": "static",
                              "status": "not_inspected", "cites": []})
        good = _judge_verdict(3, True)
        unmet = _judge_verdict(4, False)
        out = leerie._delivery_screen_sample(
            {"verdicts": [fake, no_parts, half, good, unmet]}, tree, "")
        got = {v["item_index"]: (v["met"], v.get("rejected"))
               for v in out["verdicts"]}
        assert got == {0: (False, "part_uncited"), 1: (False, "no_parts"),
                       2: (False, "part_not_met"), 3: (True, None),
                       4: (False, None)}

    def _judge(self, leerie, tmp_path, monkeypatch, samples):
        tree = _delivery_tree(tmp_path / "t")
        st = leerie.State(tmp_path / "s", "r")
        st.data = {"task": "t", "worker_count": 0}
        calls = []

        async def fake_claude_p(**kw):
            calls.append(kw)
            return samples.pop(0)
        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        details: dict = {}
        unmet, n, _ = asyncio.run(leerie._delivery_judge_unmet(
            "t", [{"item": "the feature exists"}], tree, st,
            dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS, phase_label="pre",
            item_details=details))
        return unmet, n, details, calls

    def test_fabricated_met_escalates_and_is_outvoted_by_verified_mets(
            self, leerie, tmp_path, monkeypatch):
        fake = _judge_verdict(0, True)
        fake["parts"][0]["cites"][0]["quote"] = "def invented_function(x):"
        unmet, n, details, _ = self._judge(
            leerie, tmp_path, monkeypatch,
            [{"verdicts": [fake]}, {"verdicts": [_judge_verdict(0, True)]},
             {"verdicts": [_judge_verdict(0, True)]}])
        # The rejected met is an unmet vote: it buys the dispute samples
        # (3, not 1) but 1 of 3 confirms nothing.
        assert n == 3 and unmet == []
        assert details[0]["unmet_votes"] == 1 and details[0]["rejected"] == 1

    def test_fabricated_mets_confirm_unmet_and_parts_keep_only_verified(
            self, leerie, tmp_path, monkeypatch):
        fake = _judge_verdict(0, True)
        fake["parts"][0]["cites"][0]["quote"] = "def invented_function(x):"
        real_unmet = _judge_verdict(0, False)
        real_unmet["parts"][0]["requirement"] = "the edge case is handled"
        invented = {"requirement": "an invented gap", "kind": "behavioral",
                    "status": "unmet",
                    "cites": [{"path": DELIVERY_CITED_FILE, "line_start": 1,
                               "line_end": 1,
                               "quote": "raise NotImplementedError()"}]}
        real_unmet["parts"].append(invented)
        unmet, n, details, _ = self._judge(
            leerie, tmp_path, monkeypatch,
            [{"verdicts": [fake]}, {"verdicts": [real_unmet]},
             {"verdicts": [dict(fake)]}])
        assert [u["item_index"] for u in unmet] == [0] and n == 3
        parts = details[0]["unmet_parts"]
        # Both unmet parts are hypotheses; only the verified citation stays.
        assert [p["requirement"] for p in parts] == [
            "the edge case is handled", "an invented gap"]
        assert parts[0]["cites"][0]["quote"] == DELIVERY_CITED_LINE
        assert parts[1]["cites"] == []


# === the orchestrator's own results reach the record ======================

def _head(tree):
    out = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD",
                          "HEAD^{tree}"], capture_output=True, text=True,
                         check=True).stdout.split()
    return out[0], out[1]


def _digest_state(leerie, tmp_path, commit, tree):
    import uuid
    st = leerie.State(tmp_path / "s", uuid.uuid4().hex[:8])
    st.data = {
        "conformance": {"_final": {"tree_sha": tree, "result": {
            "build": {"command": "make build", "measured": True,
                      "passed": True, "summary": "ok"},
            "tests": {"command": "pytest -q", "measured": True,
                      "passed": False, "summary": "1 failed"}}}},
        "test_id_diff": {"candidates": ["a"], "new": ["t.py :: test_a"],
                         "tree_sha": tree},
        "acceptance": {"gate": {
            "final_sha": commit,
            "final": [{"index": 1, "passed": True},
                      {"index": 2, "passed": False,
                       "failing_files": ["acc/test_d.py"]},
                      {"index": 3, "passed": False, "unmeasured": True}],
            "residual": {"cases_by_set": {"2": ["acc/test_d.py :: test_x"]}}}},
    }
    return st


def test_orchestrator_results_digest_values_and_order(leerie, tmp_path):
    staging = _delivery_tree(tmp_path / "staging")
    st = _digest_state(leerie, tmp_path, *_head(staging))
    d = leerie._orchestrator_results_digest(st, staging)
    lines = [ln for ln in d.splitlines() if ln.startswith("[")]
    assert lines == [
        "[orchestrator final-tree build] $ make build",
        "[orchestrator final-tree tests] $ pytest -q",
        "[orchestrator B* failing-test diff] tests newly failing on the "
        "final tree, confirmed against the base: t.py :: test_a",
        "[orchestrator held-out acceptance set 1] result on the final "
        "tree: passed",
        "[orchestrator held-out acceptance set 2] result on the final "
        "tree: failed: acc/test_d.py (cases: acc/test_d.py :: test_x)",
        "[orchestrator held-out acceptance set 3] result on the final "
        "tree: not measured"]
    assert "result: passed — ok" in d and "result: failed — 1 failed" in d
    st.data = {}
    assert leerie._orchestrator_results_digest(st, staging) == ""


def test_a_result_of_another_tree_is_not_on_the_record(leerie, tmp_path):
    """Review round 10 claims M1: a RECORD cite can make a verdict met, and
    the final-tree axes were measured on staging's working tree — a
    worker's uncommitted file included. Each result is on the record only
    while it describes HEAD: the final axes and the B* diff by the
    committed tree they measured (None: not a committed tree), the held-out
    sets by their commit."""
    staging = _delivery_tree(tmp_path / "staging")
    commit, tree = _head(staging)
    for c, t in ((commit, None), ("0" * 40, "f" * 40)):
        st = _digest_state(leerie, tmp_path, c, t)
        lines = leerie._orchestrator_results_digest(st, staging).splitlines()
        assert not any(ln.startswith(("[orchestrator final-tree",
                                      "[orchestrator B*")) for ln in lines)
        assert any("held-out" in ln for ln in lines) == (c == commit)
    st = _digest_state(leerie, tmp_path, commit, tree)
    assert leerie._orchestrator_results_digest(st, None) == ""
    # The reviewer's driver: with the axis measured off HEAD, the cite of
    # its line no longer verifies, so it buys no met.
    st = _digest_state(leerie, tmp_path, commit, None)
    rec = leerie._orchestrator_results_digest(st, staging)
    v = {"met": True, "parts": [{"status": "met", "cites": [
        {"path": "RECORD",
         "quote": "[orchestrator final-tree tests] $ pytest -q"}]}]}
    assert leerie._delivery_verdict_check(v, staging, rec) == (
        "unverified", "part_uncited")
    st = _digest_state(leerie, tmp_path, commit, tree)
    rec = leerie._orchestrator_results_digest(st, staging)
    assert leerie._delivery_verdict_check(v, staging, rec) == ("met", "")


def test_the_judge_payload_carries_the_orchestrator_results(
        leerie, tmp_path, monkeypatch):
    run_dir = tmp_path / ".leerie" / "runs" / "r"
    _delivery_tree(run_dir / "worktrees" / "staging")
    st = leerie.State(tmp_path / ".leerie", "r")
    st.data = {"task": "t", "worker_count": 0, "working_branch": "main",
               "required_items": [{"item": "the suite passes"}],
               "conformance": {"_final": {
                   "tree_sha": _head(run_dir / "worktrees" / "staging")[1],
                   "result": {"tests": {
                       "command": "pytest -q", "measured": True,
                       "passed": True}}}}}
    st.save()
    calls = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        v = _judge_verdict(0, True)
        v["parts"][0]["cites"] = [{"path": "RECORD", "line_start": 0,
                                   "line_end": 0,
                                   "quote": "result: passed"}]
        return {"verdicts": [v]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    # The RECORD citation verified against the orchestrator's own line, so
    # the clean first pass cost one sample.
    assert unmet == [] and len(calls) == 1
    assert ("[orchestrator final-tree tests] $ pytest -q"
            in calls[0]["user_prompt"])
    gate = st.data["delivery_gate"]
    assert gate["judged_before"]["sha"] == _git(
        run_dir / "worktrees" / "staging", "rev-parse", "HEAD")
    assert gate["judged_before"]["details"][0]["unmet_votes"] == 0


# === falsifier helpers =====================================================

def test_case_id_matching(leerie):
    ids = ["probe/x.test.ts :: math add returns the sum",
           "probe/x.test.ts :: (file failed to load)",
           "p.py :: p.test_other"]
    f = leerie._falsifier_case_id
    assert f(ids, "add returns the sum") == ids[0]
    assert f(ids, "math > add returns the sum") == ids[0]
    # A word inside the name is not a suffix match.
    assert f(ids, "returns") is None
    assert f(ids, "subtracts") is None
    # A JUnit `class.name` id (pytest) ends in the function name.
    assert f(ids, "test_other") == ids[2]
    assert f(ids, "") is None
    assert f(["probe/y.ts :: (file failed to load)"],
             "(file failed to load)") is None


def _write_log(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _tool(name: str, inp: dict, session: str = "sess-1") -> dict:
    return {"type": "assistant", "session_id": session,
            "message": {"content": [{"type": "tool_use", "id": name + "1",
                                     "name": name, "input": inp}]}}


class TestReask:
    def test_activity_and_reask_predicate(self, leerie, tmp_path):
        repo, _ = _repo(tmp_path)
        lane = tmp_path / "lane"
        log = tmp_path / "f.log"
        cnd = {"verdict": "could_not_demonstrate"}

        def act(events):
            _write_log(log, events)
            return leerie._falsifier_probe_activity(log, lane, repo)
        # Wrote a test-shaped probe AND ran it: no re-ask.
        a = act([_tool("Write", {"file_path": f"{lane}/{PROBE_REL}"}),
                 _tool("Bash", {"command": "cd x && python3 -m pytest -q "
                                f"{PROBE_REL}"})])
        assert a["created"] and a["ran"]
        assert leerie._falsifier_needs_reask(cnd, a) is False
        # Wrote it, never ran it: re-ask.
        a = act([_tool("Write", {"file_path": f"{lane}/{PROBE_REL}"}),
                 _tool("Bash", {"command": f"cat {PROBE_REL}"})])
        assert a["created"] and not a["ran"]
        assert leerie._falsifier_needs_reask(cnd, a) is True
        # Ran the suite on a lead copy only: the lead is not a probe.
        a = act([_tool("Write", {"file_path":
                                 f"{lane}/.leerie-falsifier/test_x.py"}),
                 _tool("Bash", {"command": "pytest .leerie-falsifier/test_x.py"})])
        assert not a["created"]
        assert leerie._falsifier_needs_reask(cnd, a) is True
        # Never re-asked after a demonstration claim.
        assert leerie._falsifier_needs_reask(
            {"verdict": "unmet_demonstrated"}, a) is False

    def test_session_id_is_read_from_the_log(self, leerie, tmp_path):
        log = tmp_path / "f.log"
        _write_log(log, [{"type": "system", "session_id": "s-a"},
                         _tool("Bash", {"command": "ls"}, session="s-b")])
        assert leerie._log_session_id(log) == "s-b"
        assert leerie._log_session_id(tmp_path / "none.log") is None

    def test_claude_p_resume_session_adds_the_flag(self, leerie):
        captured: list[list[str]] = []

        async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                              stdin_data=None, **_kw):
            captured.append(list(cmd))
            return {"type": "result", "subtype": "success", "is_error": False,
                    "result": "{}", "structured_output": {
                        "verdict": "could_not_demonstrate", "probe_path": "",
                        "case_name": "", "assertion_quote": "",
                        "item_span_quote": "", "entry_point": "",
                        "reason": "r"}}
        st = types.SimpleNamespace(
            path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
            run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
            repo_root=pathlib.Path("/leerie-test-user-repo"),
            data={"verbosity": "quiet"}, run_id="r1",
            bump_workers=lambda *a, **k: None,
            add_telemetry=lambda *a, **k: None)
        with mock.patch.object(leerie, "_invoke", fake_invoke), \
                mock.patch.object(leerie, "_capture_call",
                                  lambda *a, **k: None):
            for resume in (None, "sess-42"):
                asyncio.run(leerie.claude_p(
                    user_prompt="u", system_prompt="s",
                    schema_key="falsifier", cwd="/work/lane",
                    allowed_tools=leerie.ACT_TOOLS, max_turns=80,
                    autonomous=True, caps=dict(leerie.DEFAULT_CAPS), st=st,
                    model="sonnet", sid="falsifier-i0-s0",
                    resume_session=resume))
        assert "--resume" not in captured[0]
        i = captured[1].index("--resume")
        assert captured[1][i + 1] == "sess-42"


# === the AST coupling check ================================================

def _ts_dir() -> str | None:
    """A `typescript` package the facts script can load: the env override,
    else whatever `node` resolves from the repo root. None → skip."""
    env = os.environ.get("LEERIE_TEST_TYPESCRIPT_DIR")
    if env:
        # Set means required (CI sets it): returned even when unusable — no
        # node, no package — so a broken install fails the tests instead of
        # skipping them.
        return env
    if shutil.which("node") is None:
        return None
    r = subprocess.run(["node", "-e", "console.log(require.resolve("
                        "'typescript/package.json'))"],
                       capture_output=True, text=True, check=False)
    return str(Path(r.stdout.strip()).parent) if r.returncode == 0 else None


def test_ci_provisions_the_typescript_the_ast_tests_need():
    """On CI the AST-script tests must run, never skip: the workflow sets
    LEERIE_TEST_TYPESCRIPT_DIR to an installed package node can load."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        pytest.skip("CI-only guard")
    ts = os.environ.get("LEERIE_TEST_TYPESCRIPT_DIR")
    assert ts and Path(ts, "package.json").is_file(), ts
    r = subprocess.run(["node", "-e", f"require({json.dumps(ts)})"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


class TestCouplingPolicy:
    def _repo(self, tmp_path):
        repo = tmp_path / "r"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "lib.ts").write_text(
            "export function publicFn() {}\n"
            "export function internalHelper() {}\n")
        (repo / "src" / "app.ts").write_text(
            "import { publicFn } from './lib';\npublicFn();\n")
        _git(repo.parent, "init", "-q", str(repo))
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit",
             "-qm", "b")
        return repo, _git(repo, "rev-parse", "HEAD")

    def _facts(self, **kw):
        base = {"reach_src": [], "reads": [], "execs": [], "evals": [],
                "imports": [], "vm_import": False}
        base.update(kw)
        return base

    def test_each_rule(self, leerie, tmp_path):
        repo, sha = self._repo(tmp_path)
        cp = leerie._falsifier_coupling
        rel = "probe/x.test.ts"

        def imp(spec, name):
            return {"spec": spec, "imported": name, "typeOnly": False}
        clean = cp(self._facts(imports=[imp("../src/lib", "publicFn"),
                                        imp("node:fs", "readFileSync"),
                                        imp("vitest", "it")]),
                   rel, repo, sha, "item")
        assert clean == {"coupled": False, "reasons": []}
        r = cp(self._facts(imports=[imp("../src/lib", "internalHelper")]),
               rel, repo, sha, "the item text")
        assert r["coupled"] and r["reasons"] == [
            "R3 internalHelper from src/lib.ts"]
        # The item itself names the symbol: no longer coupling.
        assert cp(self._facts(imports=[imp("../src/lib", "internalHelper")]),
                  rel, repo, sha, "call internalHelper twice")[
            "coupled"] is False
        assert cp(self._facts(imports=[imp("pkg/dist/inner", "x")]), rel,
                  repo, sha, "")["reasons"] == ["R3 deep-package pkg/dist/inner"]
        assert cp(self._facts(reads=[{"fn": "readFileSync",
                                      "lits": ["src/lib.ts"]}]), rel, repo,
                  sha, "")["reasons"][0].startswith("R1 readFileSync")
        assert cp(self._facts(reads=[{"fn": "readFileSync",
                                      "lits": ["fixtures/a.json"]}]), rel,
                  repo, sha, "")["coupled"] is False
        assert cp(self._facts(execs=[{"fn": "execSync",
                                      "lits": ["grep -r foo src"]}]), rel,
                  repo, sha, "")["coupled"] is True
        assert cp(self._facts(evals=["eval"]), rel, repo, sha,
                  "")["reasons"] == ["R2 eval"]
        assert cp(self._facts(vm_import=True), rel, repo, sha,
                  "")["reasons"] == ["R2 vm"]
        assert cp({"error": "case_not_found"}, rel, repo, sha,
                  "")["coupled"] is None

    @pytest.mark.skipif(_ts_dir() is None,
                        reason="node or the typescript package unavailable")
    def test_facts_script_reads_the_reachable_case(self, leerie, tmp_path,
                                                   monkeypatch):
        tree = tmp_path / "tree"
        (tree / "node_modules").mkdir(parents=True)
        os.symlink(_ts_dir(), tree / "node_modules" / "typescript")
        probe = tree / "probe" / "x.test.ts"
        probe.parent.mkdir()
        probe.write_text(
            "import { readFileSync } from 'fs';\n"
            "import { publicFn } from '../src/lib';\n"
            "const helper = () => readFileSync('src/lib.ts', 'utf8');\n"
            "const unused = () => eval('1');\n"
            "describe('math', () => {\n"
            "  it('add returns the sum', () => {\n"
            "    expect(helper()).toContain('x');\n"
            "    publicFn();\n"
            "  });\n"
            "  it('other', () => { unused(); });\n"
            "});\n")
        facts = leerie._probe_case_facts(tree, probe,
                                         "math add returns the sum")
        assert facts["title"] == "add returns the sum"
        reach = " ".join(facts["reach_src"])
        assert "expect(helper()).toContain('x');" in reach
        assert "readFileSync('src/lib.ts'" in reach
        assert "eval('1')" not in reach
        assert facts["evals"] == []
        # The read's path argument only (its first argument's literals).
        assert [r["lits"] for r in facts["reads"]] == [["src/lib.ts"]]
        assert {i["imported"] for i in facts["imports"]} == {"readFileSync",
                                                            "publicFn"}
        assert leerie._ts_resolvable(tree) is True
        (tmp_path / "probe-less").mkdir()
        monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT",
                            tmp_path / "no-bundle")
        assert leerie._ts_resolvable(tmp_path / "probe-less") is False

    def test_facts_failure_is_an_error_not_a_crash(self, leerie, tmp_path):
        assert "error" in leerie._probe_case_facts(
            tmp_path, tmp_path / "missing.test.ts", "x")


# === the bundled typescript fallback =======================================

def _ts7_tree(root: Path) -> Path:
    """An installed tree shaped like a TypeScript 7 (native port) repo: its
    `typescript` package resolves, and its export is a version string only —
    no compiler API (`ts.createSourceFile` undefined)."""
    pkg = root / "node_modules" / "typescript"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({
        "name": "typescript", "version": "7.0.2",
        "exports": {".": "./lib/version.cjs",
                    "./package.json": "./package.json"}}))
    (pkg / "lib" / "version.cjs").write_text(
        "module.exports = { version: '7.0.2' };\n")
    probe = root / "probe" / "x.test.ts"
    probe.parent.mkdir()
    probe.write_text("describe('m', () => {\n"
                     "  it('keeps the sum', () => {\n"
                     "    expect(add(2, 3)).toBe(5);\n  });\n});\n")
    return root


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_ts7_tree_without_a_bundle_is_unavailable(leerie, tmp_path,
                                                  monkeypatch):
    """The package resolves but has no compiler API: not available — the
    check that used to pass on a bare `require.resolve`."""
    tree = _ts7_tree(tmp_path / "tree")
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", tmp_path / "none")
    assert subprocess.run(["node", "-e", "require.resolve('typescript')"],
                          cwd=tree, check=False).returncode == 0
    assert leerie._ts_resolvable(tree) is False
    facts = leerie._probe_case_facts(tree, tree / "probe" / "x.test.ts",
                                     "m keeps the sum")
    assert facts["error"] == "facts_failed"


@pytest.mark.skipif(_ts_dir() is None,
                    reason="node or the typescript package unavailable")
def test_ts7_tree_falls_back_to_the_bundled_typescript(leerie, tmp_path,
                                                       monkeypatch):
    """The tree's TypeScript 7 lacks the compiler API, so the scripts load
    the bundled copy and the facts come out as from any JS-API tree."""
    tree = _ts7_tree(tmp_path / "tree")
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", Path(_ts_dir()))
    assert leerie._ts_resolvable(tree) is True
    r = subprocess.run(["node", str(leerie.TS_LOADER)], cwd=tree,
                       env=leerie._ts_script_env(), capture_output=True,
                       text=True, check=False)
    assert r.stdout.strip() == "bundled", r.stderr
    facts = leerie._probe_case_facts(tree, tree / "probe" / "x.test.ts",
                                     "m keeps the sum")
    assert "error" not in facts, facts
    assert facts["title"] == "keeps the sum"
    assert "expect(add(2, 3)).toBe(5);" in " ".join(facts["reach_src"])


@pytest.mark.skipif(_ts_dir() is None,
                    reason="node or the typescript package unavailable")
def test_a_tree_with_the_compiler_api_uses_its_own(leerie, tmp_path,
                                                   monkeypatch):
    tree = tmp_path / "tree"
    (tree / "node_modules").mkdir(parents=True)
    os.symlink(_ts_dir(), tree / "node_modules" / "typescript")
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", tmp_path / "none")
    r = subprocess.run(["node", str(leerie.TS_LOADER)], cwd=tree,
                       env=leerie._ts_script_env(), capture_output=True,
                       text=True, check=False)
    assert r.stdout.strip() == "tree", r.stderr


def test_the_image_ships_the_bundled_typescript_where_leerie_looks(leerie):
    """The Dockerfile extracts the pinned package exactly at
    BUNDLED_TYPESCRIPT, outside the /opt/leerie-image bind mount, pinned by
    version and sha512, and checks it has the compiler API."""
    df = (Path(leerie.ROOT) / "Dockerfile").read_text()
    assert f"dst={leerie.BUNDLED_TYPESCRIPT};" in df
    assert not str(leerie.BUNDLED_TYPESCRIPT).startswith("/opt/leerie-image")
    assert "ARG BUNDLED_TYPESCRIPT_VERSION=5." in df
    assert "sha512sum -c" in df
    assert "typeof ts.createSourceFile!=='function'" in df


# === code gates, run for real on a pytest probe ============================

def _facts_for(path_text: str) -> dict:
    """What the TS facts script would report for a clean probe: its whole
    text reachable, nothing read or evaluated, no in-repo import."""
    return {"reach_src": [path_text], "reads": [], "execs": [], "evals": [],
            "imports": [], "vm_import": False, "title": "t"}


@pytest.fixture
def gate_env(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    lane = leerie._falsifier_checkout(st, "falsifier-lane0", head)
    confirm = leerie._falsifier_checkout(st, "falsifier-confirm", head)
    seen_facts = []

    def fake_facts(tree, probe, case):
        seen_facts.append((str(tree), str(probe), case))
        return _facts_for(Path(probe).read_text())
    monkeypatch.setattr(leerie, "_probe_case_facts", fake_facts)
    return types.SimpleNamespace(repo=repo, head=head, st=st, lane=lane,
                                 confirm=confirm, facts=seen_facts)


def _claim(**over) -> dict:
    res = {"verdict": "unmet_demonstrated", "probe_path": PROBE_REL,
           "case_name": "test_add_returns_the_sum",
           "assertion_quote": "assert add(2, 3) == 5",
           "item_span_quote": "add(2, 3) returns 5", "entry_point": "add",
           "reason": "add subtracts"}
    res.update(over)
    return res


def _gates(leerie, env, res, probe_src=PROBE_SRC):
    p = env.lane / PROBE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(probe_src)
    return asyncio.run(leerie._falsifier_gates(
        env.st, dict(leerie.DEFAULT_CAPS), res=res, lane=env.lane,
        confirm_tree=env.confirm, sha=env.head, item_text=ITEM,
        acc_paths={"acc/test_d.py"}, label="t"))


class TestFalsifierGates:
    def test_a_real_demonstration_is_confirmed(self, leerie, gate_env):
        out = _gates(leerie, gate_env, _claim())
        assert out["confirmed"] is True, out
        assert out["gates"] == {
            "new_file": True, "not_a_lead": True, "runnable": True,
            "item_quote": True, "case_fails": True, "fails_under_bstar": True,
            "facts_read": True, "assertion_in_case": True,
            "not_coupled": True}
        assert out["case_id"].endswith("test_add_returns_the_sum")
        # The probe ran in the pristine checkout and was removed after.
        assert gate_env.facts[0][0] == str(gate_env.confirm)
        assert not (gate_env.confirm / PROBE_REL).exists()
        assert not (gate_env.confirm / "falsify_probe").exists()

    def test_a_probe_that_passes_on_the_final_tree_confirms_nothing(
            self, leerie, gate_env):
        out = _gates(leerie, gate_env, _claim(), PASSING_PROBE_SRC)
        assert out["confirmed"] is False
        assert out["gates"]["case_fails"] is False

    def test_a_probe_that_fails_only_once_is_not_confirmed_under_bstar(
            self, leerie, gate_env):
        """The first run fails, every rerun passes — a flake, which B*
        must never confirm."""
        flaky = ("import pathlib\n\n\n"
                 "def test_add_returns_the_sum():\n"
                 "    m = pathlib.Path(__file__).resolve().parent.parent"
                 " / '.flaky_marker'\n"
                 "    seen = m.exists()\n"
                 "    m.write_text('x')\n"
                 "    assert seen\n")
        out = _gates(leerie, gate_env, _claim(assertion_quote="assert seen"),
                     flaky)
        assert out["gates"]["case_fails"] is True
        assert out["gates"]["fails_under_bstar"] is False
        assert out["confirmed"] is False

    def test_a_fabricated_item_quote_is_refused_before_running(
            self, leerie, gate_env):
        out = _gates(leerie, gate_env,
                     _claim(item_span_quote="add(2, 3) returns 6"))
        assert out["confirmed"] is False
        assert out["gates"]["item_quote"] is False
        assert "case_fails" not in out["gates"]   # never ran

    def test_an_assertion_outside_the_case_is_refused(self, leerie,
                                                      gate_env):
        out = _gates(leerie, gate_env,
                     _claim(assertion_quote="assert add(1, 1) == 2"))
        assert out["gates"]["fails_under_bstar"] is True
        assert out["gates"]["assertion_in_case"] is False
        assert out["confirmed"] is False

    def test_a_coupled_probe_is_refused(self, leerie, gate_env,
                                        monkeypatch):
        monkeypatch.setattr(
            leerie, "_probe_case_facts",
            lambda tree, probe, case: {**_facts_for(Path(probe).read_text()),
                                       "evals": ["eval"]})
        out = _gates(leerie, gate_env, _claim())
        assert out["gates"]["not_coupled"] is False
        assert out["coupling"]["reasons"] == ["R2 eval"]
        assert out["confirmed"] is False

    def test_a_tracked_file_or_a_lead_is_not_a_probe(self, leerie, gate_env):
        out = asyncio.run(leerie._falsifier_gates(
            gate_env.st, dict(leerie.DEFAULT_CAPS),
            res=_claim(probe_path="conftest.py"), lane=gate_env.lane,
            confirm_tree=gate_env.confirm, sha=gate_env.head,
            item_text=ITEM, acc_paths=set(), label="t"))
        assert out["gates"]["new_file"] is False and not out["confirmed"]
        lead = ".leerie-falsifier/acceptance/set-1/acc/test_d.py"
        p = gate_env.lane / lead
        p.parent.mkdir(parents=True)
        p.write_text(PROBE_SRC)
        out = asyncio.run(leerie._falsifier_gates(
            gate_env.st, dict(leerie.DEFAULT_CAPS),
            res=_claim(probe_path=lead), lane=gate_env.lane,
            confirm_tree=gate_env.confirm, sha=gate_env.head,
            item_text=ITEM, acc_paths=set(), label="t"))
        assert out["gates"]["not_a_lead"] is False and not out["confirmed"]

    def test_a_facts_script_failure_is_its_own_gate_not_a_rejection(
            self, leerie, gate_env, monkeypatch):
        """The AST facts script could not run (TypeScript 7's compiler-less
        package, a crash): an infrastructure miss, recorded as
        `facts_read: false` — never as a failed assertion or coupling gate
        the falsifier could be blamed for."""
        monkeypatch.setattr(
            leerie, "_probe_case_facts",
            lambda tree, probe, case: {"error": "facts_failed",
                                       "stderr": "TypeError: Latest"})
        out = _gates(leerie, gate_env, _claim())
        assert out["confirmed"] is False
        assert out["gates"] == {
            "new_file": True, "not_a_lead": True, "runnable": True,
            "item_quote": True, "case_fails": True, "fails_under_bstar": True,
            "facts_read": False}
        assert out["facts_error"] == {"error": "facts_failed",
                                      "stderr": "TypeError: Latest"}
        assert "coupling" not in out
        # The probe is still removed from the pristine checkout.
        assert not (gate_env.confirm / PROBE_REL).exists()

    def test_a_case_the_script_cannot_find_is_still_a_rejection(
            self, leerie, gate_env, monkeypatch):
        """`case_not_found` is a fact about the probe, not a script
        failure: the facts were read, and the case's gates fail."""
        monkeypatch.setattr(
            leerie, "_probe_case_facts",
            lambda tree, probe, case: {"error": "case_not_found",
                                       "titles": []})
        out = _gates(leerie, gate_env, _claim())
        assert out["gates"]["facts_read"] is True
        assert out["gates"]["assertion_in_case"] is False
        assert out["gates"]["not_coupled"] is False
        assert "facts_error" not in out and out["confirmed"] is False

    def test_could_not_demonstrate_runs_no_gate(self, leerie, gate_env):
        out = _gates(leerie, gate_env,
                     _claim(verdict="could_not_demonstrate"))
        assert out == {"gates": {}, "confirmed": False}


# === _run_delivery_detection end to end ====================================

ITEMS = [
    {"item": ITEM, "span": "S1", "verifiability": "tree"},
    {"item": "say so in the PR description", "span": "S2",
     "verifiability": "external"},
    {"item": "leave data/ unchanged", "span": "S3", "verifiability": "tree",
     "untouched_paths": ["data/"]},
    {"item": "the module keeps its name", "span": "S4",
     "verifiability": "tree"},
]


def _detection_env(leerie, tmp_path, monkeypatch, *, judged=True,
                   ts=True, k=None):
    repo, head = _repo(tmp_path)
    acc_dir = tmp_path / "accset"
    (acc_dir / "acc").mkdir(parents=True)
    (acc_dir / "acc" / "test_d.py").write_text("def test_d():\n    pass\n")
    st = _st(leerie, tmp_path, repo, head, required_items=ITEMS,
             acceptance={"sets": [{"index": 1, "valid": True,
                                   "dir": str(acc_dir),
                                   "defect_files": ["acc/test_d.py"],
                                   "control_files": [],
                                   "support_files": []}],
                         "gate": {"final": [{"index": 1, "passed": False,
                                             "failing_files": [
                                                 "acc/test_d.py"]}]}})
    _staging(st, repo, head)
    if judged:
        st.data["delivery_gate"] = {
            "unmet_after": [], "judged_after": {
                "sha": head, "unmet": [],
                "details": [{"item_index": 0, "samples": 3,
                             "unmet_votes": 1, "rejected": 0,
                             "unmet_parts": [{
                                 "requirement": "add of two positives",
                                 "kind": "behavioral", "cites": []}]}]}}
        st.save()
    monkeypatch.setattr(leerie, "_ts_resolvable", lambda tree: ts)
    monkeypatch.setattr(
        leerie, "_probe_case_facts",
        lambda tree, probe, case: _facts_for(Path(probe).read_text()))
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_parallel"] = 1
    if k is not None:
        caps["falsifier_samples"] = k
    return repo, head, st, caps


def _falsifier_stub(leerie, monkeypatch, st, behave):
    """Stub claude_p: a falsifier call runs `behave(kw, n)` (n = how many
    falsifier calls so far) in its own cwd and returns its result; a judge
    call is recorded and answered all-met."""
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        if kw["schema_key"] == "delivery_judge":
            return {"verdicts": []}
        assert kw["schema_key"] == "falsifier"
        n = sum(1 for c in calls if c["schema_key"] == "falsifier") - 1
        return behave(kw, n)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


def _demonstrate(kw, n, src=PROBE_SRC, log_dir=None):
    cwd = Path(kw["cwd"])
    p = cwd / PROBE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)
    return _claim()


class TestRunDeliveryDetection:
    def test_falsifier_confirms_and_unmet_final_is_written(
            self, leerie, tmp_path, monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch)
        seen: list[dict] = []

        def behave(kw, n):
            cwd = Path(kw["cwd"])
            lead = cwd / ".leerie-falsifier/acceptance/set-1/acc/test_d.py"
            seen.append({
                "lead": lead.is_file(), "lead_writable": os.access(lead,
                                                                   os.W_OK),
                "status": _git(cwd, "status", "--porcelain"),
                "head": _git(cwd, "rev-parse", "HEAD")})
            if "add(2, 3)" in kw["user_prompt"]:
                return _demonstrate(kw, n)
            return _claim(verdict="could_not_demonstrate", probe_path="")
        calls = _falsifier_stub(leerie, monkeypatch, st, behave)
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        gate = st.data["delivery_gate"]
        # The recheck judged this very commit: no judge call at all.
        assert not [c for c in calls if c["schema_key"] == "delivery_judge"]
        assert gate["final_judge"] == {"source": "judged_after"}
        f_sids = [c["sid"] for c in calls if c["schema_key"] == "falsifier"]
        # Item 0 confirmed on its first sample (stops there); item 3 tried 3
        # times; the external item 1 and the untouched item 2 never.
        assert f_sids == ["falsifier-i0-s0", "falsifier-i3-s0",
                          "falsifier-i3-s1", "falsifier-i3-s2"]
        assert [u["item_index"] for u in gate["unmet_final"]] == [0]
        assert gate["unmet_final"][0]["by"] == ["falsifier"]
        assert "fails under B*" in gate["unmet_final"][0]["evidence"]
        verdicts = [(r["item_index"], r["verdict"]) for r in gate["items"]]
        assert verdicts == [(0, "unmet"), (1, "external"), (2, "met"),
                            (3, "met")]
        assert gate["items"][0]["falsifier"]["samples"][0]["confirmed"]
        # The confirmed probe is kept for the delivery re-drive's re-check;
        # an unconfirmed sample's is not.
        saved = gate["items"][0]["falsifier"]["samples"][0]["probe_saved"]
        assert Path(saved["dir"]) == st.run_dir / "acceptance/probes/i0"
        assert Path(saved["dir"], saved["file"]).read_text() == PROBE_SRC
        assert not any(s_.get("probe_saved") for r in gate["items"][1:]
                       for s_ in ((r.get("falsifier") or {}).get("samples")
                                  or []))
        assert gate["falsifier"]["enabled"] is True
        assert "final_progress" not in gate and gate["final_sha"] == head
        # Leads: present, read-only, git-excluded; the tree is the final
        # commit.
        assert all(s["lead"] and not s["lead_writable"] for s in seen)
        assert all(s["status"] == "" and s["head"] == head for s in seen)
        # The payload carries the judge's open part, and — the benched F+
        # payload, DESIGN §8 *What ships is what was measured* — not the
        # executed-commands record the judge gets.
        p0 = next(c for c in calls if c["sid"] == "falsifier-i0-s0")
        assert "add of two positives" in p0["user_prompt"]
        assert ("[orchestrator held-out acceptance set 1]"
                not in p0["user_prompt"])
        assert "EXECUTED COMMANDS RECORD" not in p0["user_prompt"]
        assert p0["allowed_tools"] == leerie.ACT_TOOLS and p0["autonomous"]
        # Every checkout is gone.
        assert not list((st.run_dir / "worktrees").glob("falsifier-*"))
        # The outcome reads the final verdict.
        assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
        assert "1 required item(s) still unmet" in st.data["outcome_reasons"]

    def test_nothing_confirmed_means_met(self, leerie, tmp_path,
                                         monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              k=2)
        calls = _falsifier_stub(
            leerie, monkeypatch, st,
            lambda kw, n: _demonstrate(kw, n, src=PASSING_PROBE_SRC))
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        gate = st.data["delivery_gate"]
        assert gate["unmet_final"] == []
        # k=2: two samples per falsified item, none confirmed.
        assert len([c for c in calls if c["schema_key"] == "falsifier"]) == 4
        leerie._decide_outcome(st, any_commits=True)
        # (The fixture's failing held-out set is its own reason.)
        assert not any("required item" in r
                       for r in st.data["outcome_reasons"])

    def test_judge_runs_again_on_a_moved_tree_and_its_unmet_counts(
            self, leerie, tmp_path, monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              ts=False)
        st.data["delivery_gate"]["judged_after"]["sha"] = "0" * 40
        _delivery_tree(st.run_dir / "worktrees" / "staging")
        calls: list[dict] = []

        async def fake_claude_p(**kw):
            calls.append(kw)
            assert kw["schema_key"] == "delivery_judge"
            vs = [_judge_verdict(0, True), _judge_verdict(3, False, "renamed")]
            return {"verdicts": vs}
        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        gate = st.data["delivery_gate"]
        assert [c["sid"] for c in calls] == [
            "delivery_judge-final-s0", "delivery_judge-final-s1",
            "delivery_judge-final-s2"]
        assert gate["final_judge"]["source"] == "final"
        assert [(u["item_index"], u["by"]) for u in gate["unmet_final"]] == [
            (3, ["judge"])]
        # No typescript resolvable: the falsifier is off, never spawned.
        assert gate["falsifier"]["enabled"] is False
        assert "typescript" in gate["falsifier"]["skipped"]

    def test_reask_resumes_the_same_session_once(self, leerie, tmp_path,
                                                 monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              k=1)
        st.data["required_items"] = [ITEMS[0]]
        st.save()

        def behave(kw, n):
            sid = kw["sid"]
            if not kw.get("resume_session"):
                # The first answer: gave up without writing or running.
                _write_log(st.run_dir / "logs" / f"{sid}.log",
                           [_tool("Bash", {"command": "ls"},
                                  session="sess-77")])
                return _claim(verdict="could_not_demonstrate",
                              probe_path="")
            return _demonstrate(kw, n)
        calls = _falsifier_stub(leerie, monkeypatch, st, behave)
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        fc = [c for c in calls if c["schema_key"] == "falsifier"]
        assert [c["sid"] for c in fc] == ["falsifier-i0-s0",
                                          "falsifier-i0-s0-reask"]
        assert fc[1]["resume_session"] == "sess-77"
        assert fc[1]["user_prompt"] == leerie.FALSIFIER_REASK_NOTE
        assert fc[1]["cwd"] == fc[0]["cwd"]
        smp = st.data["delivery_gate"]["items"][0]["falsifier"]["samples"][0]
        assert smp["reasked"] is True and smp["confirmed"] is True

    def test_a_sample_that_wrote_and_ran_a_probe_is_not_reasked(
            self, leerie, tmp_path, monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              k=1)
        st.data["required_items"] = [ITEMS[0]]
        st.save()

        def behave(kw, n):
            lane = kw["cwd"]
            _write_log(st.run_dir / "logs" / f"{kw['sid']}.log",
                       [_tool("Write", {"file_path": f"{lane}/{PROBE_REL}"}),
                        _tool("Bash", {"command": f"pytest -q {PROBE_REL}"})])
            return _claim(verdict="could_not_demonstrate", probe_path="")
        calls = _falsifier_stub(leerie, monkeypatch, st, behave)
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        assert [c["sid"] for c in calls] == ["falsifier-i0-s0"]
        assert st.data["delivery_gate"]["unmet_final"] == []

    def test_resume_skips_a_finished_detection(self, leerie, tmp_path,
                                               monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch)
        st.data["delivery_gate"].update(unmet_final=[], final_sha=head)
        calls = _falsifier_stub(leerie, monkeypatch, st,
                                lambda kw, n: _demonstrate(kw, n))
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        assert calls == []
        assert st.data["delivery_gate"]["unmet_final"] == []

    def test_a_finished_detection_of_another_commit_runs_again(
            self, leerie, tmp_path, monkeypatch):
        # DESIGN §8 *A finished verdict is reused only on the commit it was
        # measured on*: a recorded "all met" of a tree that no longer ships
        # (a re-drive group moved HEAD before a crash) is not this tree's.
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              k=1)
        st.data["delivery_gate"].update(
            unmet_final=[], final_sha="0" * 40, items=[{"item_index": 0,
                                                        "verdict": "met"}])
        calls = _falsifier_stub(leerie, monkeypatch, st,
                                lambda kw, n: _demonstrate(kw, n))
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        gate = st.data["delivery_gate"]
        assert [c["sid"] for c in calls if c["schema_key"] == "falsifier"
                ] == ["falsifier-i0-s0", "falsifier-i3-s0"]
        assert gate["final_sha"] == head
        assert [u["item_index"] for u in gate["unmet_final"]] == [0]
        assert [(r["item_index"], r["verdict"]) for r in gate["items"]] == [
            (0, "unmet"), (1, "external"), (2, "met"), (3, "met")]
        assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
        assert "1 required item(s) still unmet" in st.data["outcome_reasons"]

    def test_a_stale_verdict_is_dropped_before_the_rerun(
            self, leerie, tmp_path, monkeypatch):
        # A re-run that fails leaves no verdict, never the other tree's.
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch)
        st.data["delivery_gate"].update(
            unmet_final=[], final_sha="0" * 40, items=[],
            final_judge={"source": "final"}, falsifier={"enabled": True})
        st.save()

        def boom(*a, **kw):
            raise RuntimeError("record unreadable")
        monkeypatch.setattr(leerie, "_delivery_record", boom)
        with pytest.raises(RuntimeError):
            asyncio.run(leerie._run_delivery_detection(
                st.run_dir, st, caps, MODELS, EFFORTS))
        on_disk = json.loads((st.run_dir / "state.json").read_text())
        for gate in (st.data["delivery_gate"], on_disk["delivery_gate"]):
            assert not {"unmet_final", "items", "final_sha", "final_judge",
                        "falsifier"} & set(gate)
            # The phase records of earlier trees stay.
            assert gate["unmet_after"] == []

    def test_resume_reuses_finished_items_on_the_same_commit(
            self, leerie, tmp_path, monkeypatch):
        repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                              k=1)
        st.data["delivery_gate"]["final_progress"] = {
            "sha": head, "items": {"0": {"item_index": 0, "confirmed": True,
                                         "samples": [{"confirmed": True}]}}}
        calls = _falsifier_stub(
            leerie, monkeypatch, st,
            lambda kw, n: _claim(verdict="could_not_demonstrate",
                                 probe_path=""))
        asyncio.run(leerie._run_delivery_detection(
            st.run_dir, st, caps, MODELS, EFFORTS))
        # Item 0 was finished before the interruption; only item 3 runs.
        assert [c["sid"] for c in calls if c["schema_key"] == "falsifier"
                and not c.get("resume_session")] == ["falsifier-i3-s0"]
        assert [u["item_index"] for u in
                st.data["delivery_gate"]["unmet_final"]] == [0]


# A database-backed probe: it can only show the defect against a database
# (DESIGN §8 *Prisma repositories get a real database*) — without one it
# passes, as a repository's DB-backed test skips.
DB_PROBE_SRC = ("import os\n\nfrom calc import add\n\n\n"
                "def test_add_returns_the_sum():\n"
                "    if not os.environ.get('DATABASE_URL', '').endswith("
                "'_test'):\n"
                "        return\n"
                "    assert add(2, 3) == 5\n")


def test_falsifier_samples_and_confirm_runs_get_a_fresh_test_database(
        leerie, tmp_path, monkeypatch):
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          k=2)
    made: list[tuple[str, str]] = []
    dropped: list[str] = []
    sessions: list[str] = []

    async def fake_create(st_, tree, key):
        url = f"postgresql://u@127.0.0.1:5999/leerie_{len(made)}_test"
        made.append((Path(tree).name, url))
        return url
    monkeypatch.setattr(leerie, "_create_test_db", fake_create)
    monkeypatch.setattr(leerie, "_drop_test_db",
                        lambda url: url and dropped.append(url))
    real_session = leerie._test_db_session

    def session(st_):
        sessions.append("enter")
        return real_session(st_)
    monkeypatch.setattr(leerie, "_test_db_session", session)

    def behave(kw, n):
        if "add(2, 3)" in kw["user_prompt"]:
            return _demonstrate(kw, n, src=DB_PROBE_SRC)
        return _claim(verdict="could_not_demonstrate", probe_path="")
    calls = _falsifier_stub(leerie, monkeypatch, st, behave)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    assert sessions == ["enter"]
    fc = [c for c in calls if c["schema_key"] == "falsifier"]
    # Every sample's worker got its lane's database, made fresh for that
    # sample (item 0 confirmed at s0; item 3 ran two samples).
    by_url = dict((u, t) for t, u in made)
    # Checkouts are run-id-prefixed (`_helper_worktree_path`).
    lane0 = leerie._helper_worktree_path(st, "falsifier-lane0").name
    assert [by_url[c["env_extra"]["DATABASE_URL"]] for c in fc] == [
        lane0] * 3
    assert len({c["env_extra"]["DATABASE_URL"] for c in fc}) == 3
    assert all(c["env_extra"]["DIRECT_URL"] == c["env_extra"]["DATABASE_URL"]
               and not any(k.startswith("PRISMA_USER_CONSENT")
                           for k in c["env_extra"]) for c in fc)
    assert [t for t, _u in made][0] == leerie._helper_worktree_path(
        st, "falsifier-confirm").name
    # The probe only fails against a database: confirmed means the confirm
    # checkout's re-runs carried one.
    gate = st.data["delivery_gate"]
    assert [u["item_index"] for u in gate["unmet_final"]] == [0]
    assert sorted(dropped) == sorted(u for _t, u in made)


def test_next_run_residual_reads_unmet_final(leerie, tmp_path):
    root = tmp_path / ".leerie"
    cur = root / "runs" / "current"
    cur.mkdir(parents=True)
    prev = root / "runs" / "prev"
    prev.mkdir()
    (prev / "state.json").write_text(json.dumps({
        "task": "T", "delivery_gate": {
            "unmet_after": [],
            "unmet_final": [{"item_index": 0, "item": "the sum",
                             "evidence": "probe fails", "by": ["falsifier"]}]
        }}))
    (prev / "orchestrator.exit_code").write_text("0")
    st = leerie.State(root, "current")
    st.data = {"task": "T"}
    r = leerie._prior_delivery_residual(st)
    assert r["unmet_after"] == [{"item": "the sum",
                                 "evidence": "probe fails"}]


def test_outcome_prefers_unmet_final_over_unmet_after(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"delivery_gate": {"unmet_after": [{"item": "x"}],
                                 "unmet_final": []}}
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    st.data = {"delivery_gate": {"unmet_after": [], "unmet_final": [
        {"item": "x", "by": ["falsifier"]}]}}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def test_a_test_outcome_item_is_settled_in_code_never_re_driven(
        leerie, tmp_path, monkeypatch):
    """DESIGN §8 *A test outcome is the failing-test-ID diff's to settle*
    (replay barnacle 45fa4253 pass 1): an item typed `execution` /
    `check: test_id_diff` gets its row from the diff, is never judged or
    falsified, never reaches `unmet_final` (the delivery re-drive's only
    source of item notes), and an unknown verdict goes to the PR."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=0)
    item = {"item": "No regressions: the full suite shows no new failures",
            "span": "S9", "verifiability": "execution",
            "check": "test_id_diff"}
    st.data["required_items"] = [item]
    st.save()
    calls = _falsifier_stub(leerie, monkeypatch, st, lambda kw, n: {})
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    gate = st.data["delivery_gate"]
    assert calls == []
    assert gate["items"][0]["verdict"] == "unknown"
    assert gate["items"][0]["by"] == ["test_id_diff"]
    assert gate["unmet_final"] == []
    assert [e["source"] for e in st.data["needs_human_verification"]] == [
        "test_outcome"]
    # With B* confirming a new failure the row is unmet — still not a
    # re-drive note; the run is unconverged on the diff itself.
    st.data["test_id_diff"] = {"new": ["t.py::test_x"], "tree_sha": "z"}
    st.data.pop("delivery_gate")
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    gate = st.data["delivery_gate"]
    assert gate["items"][0]["verdict"] == "unmet"
    assert gate["unmet_final"] == []
    assert not [e for e in st.data["needs_human_verification"]
                if e["source"] == "test_outcome"]
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert any("newly failing" in r for r in st.data["outcome_reasons"])


def test_an_unsettled_candidate_is_unknown_and_reaches_the_pr(
        leerie, tmp_path, monkeypatch):
    """Review round 4 M2: a "no regressions" item settled `met` while B*
    left a candidate new failure without a verdict (the base checkout could
    not be created). Driven from the real `_confirm_new_failures` with the
    base worktree's `_none` runner, through `_run_delivery_detection`, the
    PR's end-state section and `_decide_outcome`."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=0)
    item = {"item": "No regressions: the full suite shows no new failures",
            "span": "S9", "verifiability": "execution",
            "check": "test_id_diff"}
    st.data["required_items"] = [item]
    # Only the test outcome is under test: no failing held-out set.
    st.data.pop("acceptance")
    _falsifier_stub(leerie, monkeypatch, st, lambda kw, n: {})
    tree = asyncio.run(leerie._worktree_tree_sha(
        str(st.run_dir / "worktrees" / "staging")))
    cand = "tests/a.test.ts > newly broken"

    async def gate(*a, **k):
        return {"timed_out": False, "waited_sec": 0}
    monkeypatch.setattr(leerie, "_confirm_gate", gate)

    async def none(_t):
        return None
    v = asyncio.run(leerie._confirm_new_failures(
        [cand], none, none, dict(caps), label="final"))
    assert v["new"] == [] and v["no_verdict"] == [cand]
    v.update(tree_sha=tree, base_unavailable=True)
    st.data["test_id_diff"] = v
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unknown"
    assert cand in row["reason"] and "base checkout" in row["reason"]
    gap = [e for e in st.data["needs_human_verification"]
           if e["source"] == "test_outcome"]
    assert len(gap) == 1
    assert cand in gap[0]["detail"] and item["item"] in gap[0]["detail"]
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    pr = leerie._format_pr_end_state_section(st.data)
    section = pr.split("## Needs human verification", 1)[1]
    assert cand in section and "no rerun produced a verdict" in section
    # The same candidate failing on every base rerun is pre_existing:
    # confirmed not this run's, so met, and the gap is cleared.
    async def red(_t):
        return {cand}
    v = asyncio.run(leerie._confirm_new_failures(
        [cand], red, red, dict(caps), label="final"))
    assert v["pre_existing"] == [cand]
    v["tree_sha"] = tree
    st.data["test_id_diff"] = v
    st.data.pop("delivery_gate")
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    assert st.data["delivery_gate"]["items"][0]["verdict"] == "met"
    assert not [e for e in st.data["needs_human_verification"]
                if e["source"] == "test_outcome"]


def test_a_compound_items_unknown_test_part_is_never_read_met(
        leerie, tmp_path, monkeypatch):
    """Review round 5 L3: an item with a "no regressions" part beside other
    parts read `met` in its row while that part was unknown — the gap was
    written, the row said met. Through `_run_delivery_detection`: the judge
    holds the other part met, B* left the candidate without a verdict, and
    the row reads `unknown` with "met except: no regressions, unknown —
    <why>"; the ratchet still protects the met part; a re-drive recheck
    that rewrites the row keeps the part's verdict; and once B* settles the
    part met, the row is plain met."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=0)
    part = "no regressions in the existing suite"
    item = {"item": "Login times out after 30 s, with no regressions in the "
                    "existing suite",
            "span": "S9",
            "parts": ["login times out after 30 s", part],
            "part_checks": [{"part": part, "check": "test_id_diff",
                             "verifiability": "execution"}]}
    st.data["required_items"] = [item]
    st.data.pop("acceptance")
    calls = _falsifier_stub(leerie, monkeypatch, st, lambda kw, n: {})
    tree = asyncio.run(leerie._worktree_tree_sha(
        str(st.run_dir / "worktrees" / "staging")))
    cand = "tests/a.test.ts > newly broken"
    st.data["test_id_diff"] = {"candidates": [cand], "new": [],
                               "no_verdict": [cand], "tree_sha": tree,
                               "base_unavailable": True}
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    assert [c["schema_key"] for c in calls] == ["delivery_judge"]
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unknown" and row["by"] == ["test_id_diff"]
    assert row["reason"].startswith(f"met except: {part}, unknown — ")
    assert cand in row["reason"] and "base checkout" in row["reason"]
    assert row["other_parts"] == "met"
    assert st.data["delivery_gate"]["unmet_final"] == []
    gap = [e for e in st.data["needs_human_verification"]
           if e["source"] == "test_outcome"]
    assert len(gap) == 1 and item["item"] in gap[0]["detail"]
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    # Its met part is still protected by the delivery ratchet.
    assert 0 in leerie._ratchet_prior_met(st)
    # A re-drive recheck that holds the other part met keeps the part's
    # verdict instead of writing a plain met over it.
    leerie._redrive_apply_item_verdicts(
        st, {0: {"unmet": False, "by": [], "evidence": ""}}, head)
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unknown"
    assert row["reason"].startswith(f"met except: {part}, unknown")
    # B* confirming a new failure: the row is unmet, still not a re-drive
    # note (the run is unconverged on the diff itself).
    st.data["test_id_diff"] = {"candidates": [cand], "new": [cand],
                               "tree_sha": tree}
    st.data.pop("delivery_gate")
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "unmet" and row["by"] == ["test_id_diff"]
    assert row["reason"] == (f"met except: {part}, unmet — new failing "
                             f"test(s) confirmed against the base: {cand}")
    assert st.data["delivery_gate"]["unmet_final"] == []
    # Settled met: the row is plain met, with no part record.
    st.data["test_id_diff"] = {"candidates": [], "new": [],
                               "tree_sha": tree}
    st.data.pop("delivery_gate")
    st.save()
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    row = st.data["delivery_gate"]["items"][0]
    assert row["verdict"] == "met" and "test_outcome_part" not in row
    assert "reason" not in row
