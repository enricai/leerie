"""Stage 2b of the held-out acceptance sets (DESIGN §8 *A set that keeps
failing is audited, and rewritten once*, *The writer gets one more try when
nothing fails at the own start*, *Prisma repositories get a real
database*): the control-case validity rule, the writer's own-start retry,
the c+ set audit with code-verified quotes and a 3-sample union, the M1/M2
mechanical checks, the surrogate witness, the one rewrite with its guards,
the `broken` type, and the throwaway-database plumbing for Prisma repos.

Real git repos and a real pytest command throughout; only `claude_p` (and,
where named, a subprocess boundary) is stubbed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import types
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import (BUGGY, CONTROL_TEST, DEFECT_TEST,
                                        FIXED, NONDISCRIMINATING, _caps,
                                        _commit_fix, _repo, _st, _staging)

MODELS = {"acceptance_writer": "sonnet", "conformer": "sonnet",
          "set_auditor": "sonnet", "surrogate_fixer": "sonnet"}
EFFORTS = {"acceptance_writer": "medium", "conformer": "low",
           "set_auditor": "medium", "surrogate_fixer": "medium"}
# A defect case no correct fix passes: it pins an internal name.
OVERSPECIFIED = ("from calc import add\n\n"
                 "def test_add_sums():\n"
                 "    assert add(2, 3) == 5 and add.__name__ == 'plus_impl'\n")
OVERSPECIFIED_LINE = "assert add(2, 3) == 5 and add.__name__ == 'plus_impl'"


@pytest.fixture(autouse=True)
def _fast(leerie, monkeypatch):
    leerie._DEPS_INSTALLED.clear()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    yield
    leerie._DEPS_INSTALLED.clear()


def _set(st, k, defect_text, *, control=True):
    d = st.run_dir / "acceptance" / f"set-{k}"
    (d / "acc").mkdir(parents=True)
    (d / "acc" / f"test_defect_{k}.py").write_text(defect_text)
    s = {"index": k, "valid": True, "dir": str(d),
         "defect_files": [f"acc/test_defect_{k}.py"], "control_files": [],
         "support_files": [],
         "cases": {f"acc/test_defect_{k}.py": [f"case from set {k}"]},
         "modes": {}, "base_failing_cases": {}}
    if control:
        (d / "acc" / f"test_control_{k}.py").write_text(CONTROL_TEST)
        s["control_files"] = [f"acc/test_control_{k}.py"]
        s["cases"][f"acc/test_control_{k}.py"] = ["control"]
    return s


def _setup(leerie, tmp_path, defect_text):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    _commit_fix(staging)
    s1 = _set(st, 1, defect_text)
    st.data["acceptance"] = {"validity_base": head, "sets": [s1]}
    st.save()
    sha = _git(staging, "rev-parse", "HEAD")
    return repo, head, st, staging, sha


def _failing(idx=1):
    return [{"index": idx, "passed": False, "unmeasured": False,
             "failing_files": [f"acc/test_defect_{idx}.py"]}]


def _cases_in(prompt: str) -> list[str]:
    return re.findall(r'"case": "([^"]+)"', prompt)


class Workers:
    """A `claude_p` stand-in dispatching on schema_key; every call kept."""

    def __init__(self, *, audit=None, rewrite=None, surrogate=None,
                 conformer=None):
        self.calls: list[dict] = []
        self.audit = audit or (lambda kw, n: {"cases": []})
        self.rewrite = rewrite
        self.surrogate = surrogate
        self.conformer = conformer
        self.audit_n = 0

    async def __call__(self, **kw):
        kw = dict(kw)
        kw["_files_at_call"] = sorted(
            str(p.relative_to(kw["cwd"])) for p in Path(kw["cwd"]).rglob("*")
            if p.is_file() and ".git" not in p.parts
            and "__pycache__" not in p.parts)
        self.calls.append(kw)
        key = kw["schema_key"]
        if key == "set_auditor":
            self.audit_n += 1
            return self.audit(kw, self.audit_n)
        if key == "acceptance_writer":
            return self.rewrite(kw)
        if key == "surrogate_fixer":
            return self.surrogate(kw)
        if key == "conformer":
            if self.conformer:
                self.conformer(Path(kw["cwd"]))
            return {}
        raise AssertionError(key)

    def of(self, key):
        return [c for c in self.calls if c["schema_key"] == key]


def _typed(kw, *, quote=OVERSPECIFIED_LINE, file="acc/test_defect_1.py"):
    return {"cases": [{
        "case": c, "verdict": "set_defect", "defect_kind": "test_bug",
        "test_quote": {"file": file, "quote": quote},
        "prod_quote": {"file": "", "quote": ""}, "task_quote": "",
        "failure_quote": "", "reason": "pins an internal name"}
        for c in _cases_in(kw["user_prompt"]) if "defect" in c]}


def _good_rewrite(kw):
    wt = Path(kw["cwd"])
    (wt / "acc2").mkdir(exist_ok=True)
    (wt / "acc2" / "test_defect_r.py").write_text(DEFECT_TEST)
    (wt / "acc2" / "test_control_r.py").write_text(CONTROL_TEST)
    return {"files": [
        {"path": "acc2/test_defect_r.py", "kind": "defect",
         "cases": ["sums through add"], "failure_mode": "assertion"},
        {"path": "acc2/test_control_r.py", "kind": "control",
         "cases": ["zero"], "failure_mode": "assertion"}]}


def _run(leerie, st, staging, sha, workers, monkeypatch, final=None):
    monkeypatch.setattr(leerie, "claude_p", workers)
    return asyncio.run(leerie._acceptance_audit_and_rewrite(
        st, _caps(leerie, 1), MODELS, EFFORTS, str(staging),
        final or _failing(), sha))


# --- validity: a set needs a passing control ---------------------------------

def test_a_set_without_a_control_is_not_valid(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)

    async def writer(**kw):
        (Path(kw["cwd"]) / "acc").mkdir()
        (Path(kw["cwd"]) / "acc" / "test_defect_1.py").write_text(DEFECT_TEST)
        return {"files": [{"path": "acc/test_defect_1.py", "kind": "defect",
                           "cases": ["sums"], "failure_mode": "assertion"}]}
    monkeypatch.setattr(leerie, "claude_p", writer)
    res = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert res["valid"] is False
    assert res["reason"] == "no control file passes on the validity base"


# --- the writer's one own-start retry ----------------------------------------

def _two_round_writer(texts):
    calls = []

    async def writer(**kw):
        calls.append(kw)
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir(exist_ok=True)
        (wt / "acc" / "test_defect_1.py").write_text(texts[len(calls) - 1])
        (wt / "acc" / "test_control_1.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "acc/test_defect_1.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc/test_control_1.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}
    return writer, calls


def test_a_set_whose_defect_passes_at_own_start_gets_one_retry(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    writer, calls = _two_round_writer([NONDISCRIMINATING, DEFECT_TEST])
    monkeypatch.setattr(leerie, "claude_p", writer)
    res = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert len(calls) == 2
    assert calls[1]["sid"] == "acceptance-1-r1"
    note = calls[1]["user_prompt"]
    # The original payload, then the runner's per-case result.
    assert note.index("TASK:") < note.index("No defect case failed")
    assert "acc/test_defect_1.py: " in note and "passed" in note
    assert res["valid"] is True and res["own_start_rewrite"] is True
    assert res["defect_files"] == ["acc/test_defect_1.py"]


def test_the_retry_is_bounded_by_the_rewrite_cap(leerie, tmp_path,
                                                 monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    writer, calls = _two_round_writer([NONDISCRIMINATING, DEFECT_TEST])
    monkeypatch.setattr(leerie, "claude_p", writer)
    caps = _caps(leerie, 1)
    caps["acceptance_rewrite_rounds"] = 0
    res = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, caps, MODELS, EFFORTS, head))
    assert len(calls) == 1
    assert res["valid"] is False and "no_defect_fails" not in res


def test_a_still_passing_retry_discards_the_set(leerie, tmp_path,
                                                monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    writer, calls = _two_round_writer([NONDISCRIMINATING, NONDISCRIMINATING])
    monkeypatch.setattr(leerie, "claude_p", writer)
    res = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert len(calls) == 2 and res["valid"] is False


# --- quote verification ------------------------------------------------------

def test_quotes_are_checked_against_the_files_they_name(leerie, tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    set_texts = {"acc/test_defect_1.py": OVERSPECIFIED}
    v = leerie._verify_set_audit_case
    ok = {"case": "c", "defect_kind": "test_bug",
          "test_quote": {"file": "acc/test_defect_1.py",
                         "quote": "  assert add(2, 3) == 5 and\n add.__name__"}}
    assert v(ok, set_texts=set_texts, tree=tmp_path, task_text="",
             messages={}) == []
    # Not in the set file it names, too short, or naming another file.
    for tq in ({"file": "acc/test_defect_1.py", "quote": "assert add(9, 9)"},
               {"file": "acc/test_defect_1.py", "quote": "add(2, 3)"},
               {"file": "calc.py", "quote": "return a - b of add"}):
        assert v({**ok, "test_quote": tq}, set_texts=set_texts,
                 tree=tmp_path, task_text="", messages={}) == ["test_quote"]
    coupling = {**ok, "defect_kind": "coupling",
                "prod_quote": {"file": "calc.py", "quote": "return a - b"}}
    assert v(coupling, set_texts=set_texts, tree=tmp_path, task_text="",
             messages={}) == []
    # A production quote may not be taken from a set file.
    bad = {**coupling, "prod_quote": {"file": "acc/test_defect_1.py",
                                      "quote": OVERSPECIFIED_LINE}}
    assert v(bad, set_texts=set_texts, tree=tmp_path, task_text="",
             messages={}) == ["prod_quote"]
    env = {**ok, "defect_kind": "environment",
           "failure_quote": "connection refused to 127.0.0.1"}
    assert v(env, set_texts=set_texts, tree=tmp_path, task_text="",
             messages={"c": "E   connection refused to 127.0.0.1:5432"}) == []
    assert v(env, set_texts=set_texts, tree=tmp_path, task_text="",
             messages={"c": "AssertionError"}) == ["failure_quote"]


# --- the audit: three samples, union, quotes verified -------------------------

def test_the_audit_is_the_union_of_three_verified_samples(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)

    def audit(kw, n):
        # Sample 1 finds nothing, sample 2 types the case with a quote that
        # is not in the set, sample 3 types it correctly.
        if n == 1:
            return {"cases": []}
        if n == 2:
            return _typed(kw, quote="assert something_else() == 'x'")
        return _typed(kw)
    w = Workers(audit=audit)
    monkeypatch.setattr(leerie, "claude_p", w)
    res = asyncio.run(leerie._acceptance_audit_on_rev(
        st, _caps(leerie, 1), MODELS, EFFORTS, sha,
        st.data["acceptance"]["sets"][0], "a1"))
    assert len(w.of("set_auditor")) == 3
    assert res["samples"] == 3
    (f,) = res["findings"]
    assert f["sample"] == 3 and f["defect_kind"] == "test_bug"
    assert res["kinds"] == ["test_bug"]
    prompt = w.of("set_auditor")[0]["user_prompt"]
    # The payload, in order: the harness section, the task, the failing
    # case with the runner's failure message.
    assert prompt.index("REPOSITORY TEST HARNESS") < prompt.index("TASK:")
    assert "test_add_sums" in _cases_in(prompt)[0]
    assert '"file": "acc/test_defect_1.py"' in prompt
    assert "plus_impl" in prompt          # the failure message reached it
    call = w.of("set_auditor")[0]
    assert call["allowed_tools"] == leerie.SATISFIED_PROBE_TOOLS
    assert call["autonomous"] is False
    assert "acc/test_defect_1.py" in call["_files_at_call"]
    # Nothing of the set reached staging.
    assert not (staging / "acc").exists()


def test_the_sample_count_follows_the_cap(leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    w = Workers()
    monkeypatch.setattr(leerie, "claude_p", w)
    caps = _caps(leerie, 1)
    caps["set_audit_samples"] = 2
    asyncio.run(leerie._acceptance_audit_on_rev(
        st, caps, MODELS, EFFORTS, sha, st.data["acceptance"]["sets"][0],
        "a1"))
    assert len(w.of("set_auditor")) == 2


# --- the rewrite --------------------------------------------------------------

def test_an_audited_set_is_rewritten_once_at_own_start_and_judged_again(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    w = Workers(audit=lambda kw, n: _typed(kw), rewrite=_good_rewrite)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    (rw,) = w.of("acceptance_writer")
    # At the own start, with the original set's files back in place.
    assert "acc/test_defect_1.py" in rw["_files_at_call"]
    assert rw["sid"] == "acceptance-rewrite-1"
    note = rw["user_prompt"]
    assert note.index("TASK:") < note.index("NOTE FROM ORCHESTRATOR (typed)")
    assert OVERSPECIFIED_LINE in note
    assert leerie._REWRITE_TAIL_TYPED in note
    s = st.data["acceptance"]["sets"][0]
    assert s["rewrite"]["triggers"] == ["set_audit"]
    assert s["rewrite"]["outcome"] == "replaced"
    assert s["defect_files"] == ["acc2/test_defect_r.py"]
    assert s["rewrite"]["original"]["defect_files"] == ["acc/test_defect_1.py"]
    assert s["rewrite_rounds"] == 1 and not s.get("broken")
    assert final == [{"index": 1, "passed": True, "unmeasured": False,
                      "failing_files": []}]
    # No surrogate when the audit already fired.
    assert w.of("surrogate_fixer") == []


def test_an_empty_rewrite_is_asked_once_more_then_the_original_is_kept(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)

    def empty(kw):
        wt = Path(kw["cwd"])
        (wt / "acc2").mkdir(exist_ok=True)
        (wt / "acc2" / "test_control_r.py").write_text(CONTROL_TEST)
        return {"files": [{"path": "acc2/test_control_r.py",
                           "kind": "control", "cases": ["zero"],
                           "failure_mode": "assertion"}]}
    w = Workers(audit=lambda kw, n: _typed(kw), rewrite=empty)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    rws = w.of("acceptance_writer")
    assert [c["sid"] for c in rws] == ["acceptance-rewrite-1",
                                      "acceptance-rewrite-1-a2"]
    assert rws[0]["user_prompt"] == rws[1]["user_prompt"]   # identical
    s = st.data["acceptance"]["sets"][0]
    assert s["rewrite"]["outcome"] == "kept_original"
    assert s["rewrite"]["retried_empty"] is True
    assert s["defect_files"] == ["acc/test_defect_1.py"]    # never dropped
    # Typed broken by the audit, and its rewrite was not kept.
    assert s["broken"] is True
    assert final == _failing()


def test_a_rewrite_that_still_fails_and_is_retyped_is_broken(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)

    def still_bad(kw):
        wt = Path(kw["cwd"])
        (wt / "acc2").mkdir(exist_ok=True)
        (wt / "acc2" / "test_defect_r.py").write_text(OVERSPECIFIED)
        (wt / "acc2" / "test_control_r.py").write_text(CONTROL_TEST)
        return {"files": [
            {"path": "acc2/test_defect_r.py", "kind": "defect",
             "cases": ["sums"], "failure_mode": "assertion"},
            {"path": "acc2/test_control_r.py", "kind": "control",
             "cases": ["zero"], "failure_mode": "assertion"}]}

    def audit(kw, n):
        f = ("acc2/test_defect_r.py" if "acc2/test_defect_r.py"
             in kw["user_prompt"] else "acc/test_defect_1.py")
        return _typed(kw, file=f)
    w = Workers(audit=audit, rewrite=still_bad)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    s = st.data["acceptance"]["sets"][0]
    assert s["rewrite"]["outcome"] == "replaced"
    assert s["broken"] is True
    assert len(w.of("set_auditor")) == 6        # audit + re-audit, 3 each
    assert final[0]["passed"] is False
    assert len(w.of("acceptance_writer")) == 1  # never chased again


def test_a_set_at_the_rewrite_cap_is_left_alone(leerie, tmp_path,
                                                monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    st.data["acceptance"]["sets"][0]["rewrite_rounds"] = 1
    w = Workers()
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    assert w.calls == [] and final == _failing()


def test_a_resumed_rewrite_reuses_its_recorded_verdict(leerie, tmp_path,
                                                       monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    done = {"index": 1, "passed": True, "unmeasured": False,
            "failing_files": []}
    st.data["acceptance"]["sets"][0].update(
        rewrite_rounds=1, rewrite={"outcome": "replaced", "result": done})
    w = Workers()
    assert _run(leerie, st, staging, sha, w, monkeypatch) == [done]
    assert w.calls == []


# --- M1/M2 and the surrogate witness ------------------------------------------

def test_mechanical_findings_trigger_a_rewrite_without_a_surrogate(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    finding = {"check": "M1", "file": "acc/test_defect_1.py",
               "lines": ["3: fn.mock.calls[0]"]}
    seen = []

    def mech(repo_, rev, set_):
        seen.append(rev)
        return [finding]
    monkeypatch.setattr(leerie, "_acceptance_mechanical_findings", mech)
    w = Workers(rewrite=_good_rewrite)
    _run(leerie, st, staging, sha, w, monkeypatch)
    assert seen == [head]                     # read at the own start
    s = st.data["acceptance"]["sets"][0]
    assert s["rewrite"]["triggers"] == ["mechanical"]
    note = w.of("acceptance_writer")[0]["user_prompt"]
    assert "fn.mock.calls[0]" in note
    assert leerie._REWRITE_TAIL_MECHANICAL in note
    assert w.of("surrogate_fixer") == []
    assert not s.get("broken")                 # M1/M2 never type a set


def test_the_surrogate_witness_triggers_only_when_nothing_else_fired(
        leerie, tmp_path, monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)

    def fix(kw):
        (Path(kw["cwd"]) / "calc.py").write_text(FIXED)
        return {"summary": "fixed add", "files_changed": ["calc.py"]}
    # The audit's typing does not check (bad quote), so it fires nothing.
    w = Workers(audit=lambda kw, n: _typed(kw, quote="not in the set file!"),
                rewrite=_good_rewrite, surrogate=fix)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    (sur,) = w.of("surrogate_fixer")
    # Test-blind: no set file in its tree, none named in its prompt, and
    # its Read denies cover the sets.
    assert not any(f.startswith("acc/") for f in sur["_files_at_call"])
    assert "test_defect" not in sur["user_prompt"]
    assert "acceptance" in leerie._acceptance_read_denials(
        "surrogate_fixer", st.run_dir)
    assert sur["autonomous"] is True and sur["allowed_tools"] == leerie.ACT_TOOLS
    s = st.data["acceptance"]["sets"][0]
    assert s["rewrite"]["triggers"] == ["surrogate_witness"]
    note = w.of("acceptance_writer")[0]["user_prompt"]
    assert leerie._REWRITE_TAIL_SURROGATE in note
    assert "failing_on_an_independent_fix" in note
    assert final[0]["passed"] is True
    # The surrogate's clone is gone afterwards.
    assert not (st.run_dir / "worktrees" / "acceptance-surrogate").exists()


def test_a_set_passing_on_the_independent_fix_is_not_rewritten(
        leerie, tmp_path, monkeypatch):
    """A fair set — the integrated tree is what is still wrong."""
    repo, head, st, staging, sha = _setup(leerie, tmp_path, DEFECT_TEST)
    (staging / "calc.py").write_text(BUGGY)
    _git(staging, "commit", "-qam", "revert the fix")
    sha = _git(staging, "rev-parse", "HEAD")

    def fix(kw):
        (Path(kw["cwd"]) / "calc.py").write_text(FIXED)
        return {"summary": "fixed add", "files_changed": ["calc.py"]}
    w = Workers(surrogate=fix)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    assert len(w.of("surrogate_fixer")) == 1
    assert w.of("acceptance_writer") == []
    s = st.data["acceptance"]["sets"][0]
    assert "rewrite" not in s and not s.get("broken")
    assert final == _failing()


def test_a_surrogate_that_changes_nothing_abstains(leerie, tmp_path,
                                                   monkeypatch):
    repo, head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    w = Workers(surrogate=lambda kw: {"summary": "", "files_changed": []})
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    assert w.of("acceptance_writer") == [] and final == _failing()


# --- the gate runs it, and the outcome skips broken sets ----------------------

def test_the_gate_audits_a_set_still_failing_after_repair(
        leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"validity_base": head,
                             "sets": [_set(st, 1, OVERSPECIFIED)]}
    st.save()
    w = Workers(audit=lambda kw, n: _typed(kw), rewrite=_good_rewrite,
                conformer=_commit_fix)
    monkeypatch.setattr(leerie, "claude_p", w)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 1), MODELS, EFFORTS))
    keys = [c["schema_key"] for c in w.calls]
    # Repair rounds first, then the audit, then the one rewrite.
    assert keys.index("conformer") < keys.index("set_auditor") \
        < keys.index("acceptance_writer")
    gate = st.data["acceptance"]["gate"]
    assert gate["audited"] is True
    assert gate["final"][0]["passed"] is True and "residual" not in gate


def test_a_broken_set_is_not_unfinished_work(leerie, tmp_path):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    fail = {"index": 1, "passed": False, "unmeasured": False,
            "failing_files": ["a"]}
    st.data["acceptance"] = {
        "sets": [{"index": 1, "broken": True}],
        "gate": {"final": [fail], "residual": {
            "failing_sets": 1, "total_sets": 1, "sets": [1], "cases": ["c"],
            "cases_by_set": {"1": ["c"]}}}}
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    st.data["acceptance"]["sets"][0]["broken"] = False
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


# --- M1/M2 and the harness section on a real JS tree --------------------------

def _js_repo(tmp_path):
    repo = tmp_path / "js"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "tsconfig.json").write_text(
        '{\n  // aliases\n  "compilerOptions": {"paths": {"@/*": ["./src/*"]},}\n}\n')
    (repo / "src" / "probe.ts").write_text(
        "export function probe(x: number) { return helperA(x); }\n"
        "export function helperA(x: number) { return x; }\n"
        "export const publicB = 1;\n")
    (repo / "src" / "other.ts").write_text(
        'import { publicB } from "./probe";\nexport const o = publicB;\n')
    (repo / "test" / "fake-probe.test-helper.ts").write_text(
        "export function makeFakeProbe() { return {}; }\n"
        "export const PROBE_DEFAULTS = {};\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "src" / "probe.test.ts").write_text(
        'import { probe } from "@/probe";\n'
        'import { makeFakeProbe } from "../test/fake-probe.test-helper";\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "test")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_the_harness_section_names_the_repos_own_tests_and_helpers(
        leerie, tmp_path):
    repo, head = _js_repo(tmp_path)
    text = leerie._repo_test_harness(repo, head, ["src/probe.ts"])
    assert "- src/probe.test.ts  (uses test/fake-probe.test-helper.ts)" in text
    assert ("- test/fake-probe.test-helper.ts [1]: makeFakeProbe, "
            "PROBE_DEFAULTS") in text
    assert leerie._repo_test_harness(repo, head, ["src/nothing.ts"]) == \
        "REPOSITORY TEST HARNESS: (none found)"


def test_m1_and_m2_read_code_identifiers(leerie, tmp_path):
    repo, head = _js_repo(tmp_path)
    d = tmp_path / "set"
    (d / "src" / "acc").mkdir(parents=True)
    (d / "src" / "acc" / "defect.test.ts").write_text(
        'import { helperA, publicB } from "@/probe";\n'
        "const fn = vi.fn();\n"
        "expect(fn.mock.calls[0][0]).toBe(1);\n")
    (d / "src" / "acc" / "control.test.ts").write_text(
        'import { probe } from "../probe";\nexpect(probe(1)).toBe(1);\n')
    s = {"dir": str(d), "defect_files": ["src/acc/defect.test.ts"],
         "control_files": ["src/acc/control.test.ts"], "support_files": []}
    got = leerie._acceptance_mechanical_findings(repo, head, s)
    assert {"check": "M1", "file": "src/acc/defect.test.ts",
            "lines": ["3: expect(fn.mock.calls[0][0]).toBe(1);"]} in got
    m2 = [(f["symbol"], f["module"]) for f in got if f["check"] == "M2"]
    # helperA is used by no other production module; publicB and probe are.
    assert m2 == [("helperA", "src/probe.ts")]
    assert leerie._set_production_imports(
        leerie._js_source_graph(repo, head), s) == ["src/probe.ts"]


# --- Prisma: detection, deny + notice, the URL check, the push ----------------

def _prisma_repo(tmp_path, scripts=None, provider="postgresql"):
    repo = tmp_path / "pr"
    (repo / "prisma").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "prisma" / "schema.prisma").write_text(
        f'datasource db {{\n  provider = "{provider}"\n}}\n')
    (repo / "package.json").write_text(json.dumps({
        "dependencies": {"@prisma/client": "7"},
        "scripts": scripts or {}}))
    _git(repo, "add", "-A")
    return repo


def test_a_prisma_repo_is_detected_from_its_files(leerie, tmp_path):
    repo = _prisma_repo(tmp_path)
    assert leerie._prisma_project(repo) == {
        "dir": "", "schema": "prisma/schema.prisma", "provider": "postgresql"}
    (repo / "package.json").write_text("{}")
    assert leerie._prisma_project(repo) is None


def test_db_push_and_reset_commands_and_their_scripts_are_denied(
        leerie, tmp_path):
    repo = _prisma_repo(tmp_path, scripts={
        "db:push": "prisma db push", "db:reset": "rm -f x && prisma migrate reset",
        "test": "vitest run"})
    pats = leerie._db_reset_rule_patterns(repo)
    denied = lambda c: any(leerie._rule_matches(c, p) for p in pats)  # noqa: E731
    for cmd in ("npx prisma db push --accept-data-loss",
                "pnpm exec prisma migrate reset --force",
                "pnpm db:push", "npm run db:reset",
                "PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION=y pnpm test"):
        assert denied(cmd), cmd
    for cmd in ("pnpm exec prisma generate", "pnpm test",
                "npx vitest run src/a.test.ts", "prisma migrate diff"):
        assert not denied(cmd), cmd
    assert leerie._db_reset_rule_patterns(tmp_path) == []


def _spawn(leerie, monkeypatch, repo_root, allowed_tools, env_extra=None):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **kw):
        captured.update(cmd=list(cmd), stdin=stdin_data, kw=kw)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}
    st = types.SimpleNamespace(
        path=Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=Path("/tmp/leerie-test-nonexistent"), repo_root=repo_root,
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_append_system_prompt_file_supported",
                        lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key="implementer",
        cwd="/leerie-state/runs/r1/worktrees/w", allowed_tools=allowed_tools,
        max_turns=4, autonomous=True, caps=dict(leerie.DEFAULT_CAPS), st=st,
        model="sonnet", sid="t", env_extra=env_extra))
    argv = captured["cmd"]
    return argv[argv.index("--disallowedTools") + 1], captured


def test_every_bash_worker_gets_the_db_deny_with_its_notice(
        leerie, monkeypatch, tmp_path):
    repo = _prisma_repo(tmp_path)
    deny, cap = _spawn(leerie, monkeypatch, repo, leerie.ACT_TOOLS)
    assert "Bash(*prisma db push*)" in deny
    (line,) = [ln for ln in cap["stdin"].splitlines()
               if ln.startswith("NOTE FROM ORCHESTRATOR:") and "Prisma" in ln]
    assert "denied to you" in line and "throwaway test databases" in line
    # No notice where the worker holds no Bash; no deny outside Prisma.
    _deny, cap = _spawn(leerie, monkeypatch, repo, leerie.INSPECT_TOOLS)
    assert "Prisma" not in cap["stdin"]
    deny, cap = _spawn(leerie, monkeypatch, tmp_path, leerie.ACT_TOOLS)
    assert "prisma" not in deny and "Prisma" not in cap["stdin"]


def test_env_extra_reaches_the_worker_only_when_set(leerie, monkeypatch,
                                                    tmp_path):
    _d, cap = _spawn(leerie, monkeypatch, tmp_path, leerie.ACT_TOOLS,
                     env_extra={"DATABASE_URL": "u"})
    assert cap["kw"]["env_extra"] == {"DATABASE_URL": "u"}
    _d, cap = _spawn(leerie, monkeypatch, tmp_path, leerie.ACT_TOOLS)
    assert "env_extra" not in cap["kw"]


def test_a_worker_environment_never_carries_the_consent(leerie, monkeypatch,
                                                        tmp_path):
    seen = {}

    class Stop(Exception):
        pass

    async def fake_exec(*argv, env=None, **kw):
        seen["env"] = env
        raise Stop
    monkeypatch.setenv("PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION", "yes")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(Stop):
        asyncio.run(leerie._invoke(
            ["true"], str(tmp_path), 10, "t", tmp_path, "quiet",
            env_extra={"DATABASE_URL": "postgresql://x"}))
    assert seen["env"]["DATABASE_URL"] == "postgresql://x"
    assert not any(k.startswith("PRISMA_USER_CONSENT") for k in seen["env"])


@pytest.mark.parametrize("url,direct,port,why", [
    ("postgresql://u@127.0.0.1:5000/leerie_ab_test", None, 5000, None),
    ("postgres://u@localhost:5000/leerie_ab_test", None, 5000, None),
    ("postgresql://u@db.example.com:5000/leerie_ab_test", None, 5000, "host"),
    ("postgresql://u@127.0.0.1:5432/leerie_ab_test", None, 5000, "port"),
    ("postgresql://u@127.0.0.1:5000/app_test", None, 5000, "not named"),
    ("postgresql://u@127.0.0.1:5000/leerie_ab", None, 5000, "not named"),
    ("mysql://u@127.0.0.1:5000/leerie_ab_test", None, 5000, "scheme"),
    ("postgresql://u@127.0.0.1:5000/leerie_ab_test",
     "postgresql://u@127.0.0.1:5000/prod", 5000, "DIRECT_URL"),
])
def test_only_a_throwaway_database_on_our_own_port_passes(
        leerie, url, direct, port, why):
    got = leerie._test_db_url_refusal(url, direct or url, port)
    assert (got is None) if why is None else (why in got)


def _fake_cluster(leerie, monkeypatch, tree, *, name=None):
    calls = []
    monkeypatch.setitem(leerie._TEST_DB, "cluster", {
        "bin": "/pg/bin", "port": 5999, "data": "/nonexistent", "user": "u"})
    if name:
        monkeypatch.setattr(leerie, "_test_db_name", lambda key: name)
    real = subprocess.run

    def fake_run(argv, *a, **kw):
        if isinstance(argv, list) and (argv[0].startswith("/pg/bin")
                                       or argv[0].endswith("prisma")):
            calls.append((list(argv), kw.get("env")))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "run", fake_run)
    bin_ = tree / "node_modules" / ".bin"
    bin_.mkdir(parents=True)
    (bin_ / "prisma").write_text("")
    return calls


def test_the_push_carries_the_consent_and_only_the_push(leerie, tmp_path,
                                                        monkeypatch):
    repo = _prisma_repo(tmp_path)
    st = types.SimpleNamespace(repo_root=repo, run_id="r1")
    tree = tmp_path / "tree"
    calls = _fake_cluster(leerie, monkeypatch, tree)
    url = asyncio.run(leerie._create_test_db(st, tree, "w"))
    assert re.fullmatch(
        r"postgresql://u@127\.0\.0\.1:5999/leerie_[0-9a-f]{16}_test", url)
    tools = [os.path.basename(a[0]) for a, _e in calls]
    assert tools == ["dropdb", "createdb", "prisma"]
    (argv, env), = [(a, e) for a, e in calls if a[0].endswith("prisma")]
    assert argv[1:] == ["db", "push", "--accept-data-loss"]
    assert env["DATABASE_URL"] == env["DIRECT_URL"] == url
    assert env["PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION"]
    assert leerie._test_db_env(url) == {"DATABASE_URL": url,
                                       "DIRECT_URL": url}


def test_a_target_failing_the_check_is_never_pushed(leerie, tmp_path,
                                                    monkeypatch):
    repo = _prisma_repo(tmp_path)
    st = types.SimpleNamespace(repo_root=repo, run_id="r1")
    tree = tmp_path / "tree"
    calls = _fake_cluster(leerie, monkeypatch, tree, name="production")
    assert asyncio.run(leerie._create_test_db(st, tree, "w")) is None
    assert calls == []


def test_no_cluster_outside_a_postgres_prisma_repo(leerie, tmp_path):
    for repo in (tmp_path, _prisma_repo(tmp_path, provider="sqlite")):
        st = types.SimpleNamespace(repo_root=repo, run_id="r1")

        async def go():
            async with leerie._test_db_session(st) as c:
                return c
        assert asyncio.run(go()) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="postgres refuses root")
def test_a_real_cluster_starts_on_its_own_port_and_is_removed(leerie,
                                                              tmp_path):
    if leerie._pg_server_bin() is None:
        pytest.skip("no PostgreSQL server binaries on this host")
    st = types.SimpleNamespace(repo_root=tmp_path, run_id=f"t{os.getpid()}")
    c = leerie._start_test_db_cluster(st)
    assert c is not None
    try:
        ready = subprocess.run([os.path.join(c["bin"], "pg_isready"), "-h",
                                "127.0.0.1", "-p", str(c["port"])],
                               capture_output=True)
        assert ready.returncode == 0
    finally:
        leerie._stop_test_db_cluster(c)
    assert not Path(c["data"]).exists()
