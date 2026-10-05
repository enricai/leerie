"""The report's own example inputs (DESIGN §5 *The report's own example
inputs are captured too*).

Both repeat runs on v0.36.0 traced to a first run whose fix failed on the
report's own quoted input, because the tests paraphrased it and the
paraphrase dropped the triggering feature. The auditor now returns
`ground_truth.inline_examples`; the planner prompt turns them into test
requirements; their `site_tokens` back an advisory diff check.

Values, not key presence: the audit phase is executed with a stubbed
`claude_p`, the REAL `phase_plan` is driven to show the literal reaches the
planner, and the site-token check runs against a real git repository.
"""
from __future__ import annotations

import asyncio
import inspect

from tests.conftest import init_git_repo
from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_defect_scope_audit import (AUDIT, EFFORTS, MODELS, _caps,
                                           _drive_phase_plan, _patch_auditor,
                                           _state)

_EXAMPLE = {
    "literal": "Below the summary panel, click the 'Export' button",
    "site_identifying": False,
    "trigger_tokens": ["summary panel"],
    "site_tokens": [],
}
_SITE_EXAMPLE = {
    "literal": "https://www.acmeshop.example/list/#acmewidget,june",
    "site_identifying": True,
    "trigger_tokens": [],
    "site_tokens": ["AcmeShop", "acmewidget"],
}


def _audit_with(examples):
    return {**AUDIT, "ground_truth": {"data_dependent": False, "inputs": [],
                                      "repro_command": None,
                                      "inline_examples": examples}}


def test_audit_carries_inline_examples_into_the_scope(leerie, tmp_path,
                                                     monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch, _audit_with([_EXAMPLE, _SITE_EXAMPLE]))
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    ex = scope["ground_truth"]["inline_examples"]
    assert ex[0]["literal"] == _EXAMPLE["literal"]
    assert ex[0]["trigger_tokens"] == ["summary panel"]
    assert ex[1]["site_identifying"] is True
    assert ex[1]["site_tokens"] == ["AcmeShop", "acmewidget"]


def test_malformed_inline_examples_are_dropped(leerie):
    gt = leerie._check_ground_truth_inputs({
        "data_dependent": False, "inputs": [], "repro_command": None,
        "inline_examples": ["prose", {"site_identifying": True},
                            {"literal": "kept", "trigger_tokens": ["", "k"]}]})
    assert gt["inline_examples"] == [{
        "literal": "kept", "site_identifying": False,
        "trigger_tokens": ["k"], "site_tokens": []}]


def test_inline_example_literal_reaches_the_planner_prompt(leerie, tmp_path,
                                                          monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch, _audit_with([_EXAMPLE]))
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    st.data["defect_scope"] = scope
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "click the 'Export' button" in prompt
    system = calls[0].get("system_prompt") or ""
    assert "inline_examples" in system and "VERBATIM" in system


# --- the advisory site-token check ------------------------------------------

def _staged_run(tmp_path, base_text, added_files):
    """A run dir whose staging worktree is on a run branch that adds
    `added_files` on top of `main`, whose tree carries `base_text`."""
    leerie_dir = tmp_path / "run"
    staging = leerie_dir / "worktrees" / "staging"
    init_git_repo(staging)
    (staging / "base.txt").write_text(base_text)
    _git(staging, "add", ".")
    _git(staging, "commit", "-q", "-m", "base")
    _git(staging, "checkout", "-q", "-b", "leerie/runs/x")
    for name, text in added_files.items():
        (staging / name).parent.mkdir(parents=True, exist_ok=True)
        (staging / name).write_text(text)
    _git(staging, "add", ".")
    _git(staging, "commit", "-q", "-m", "run")
    return leerie_dir


def _scope(*examples):
    return {"applicable": True, "sites": [], "ground_truth": {
        "data_dependent": False, "inputs": [], "repro_command": None,
        "inline_examples": list(examples)}}


def test_site_token_added_by_the_run_is_reported(leerie, tmp_path, capsys):
    leerie_dir = _staged_run(tmp_path, "generic words only\n",
                             {"src/x.ts": "// e.g. an AcmeShop-style list\n"})
    st = _state(leerie, tmp_path, working_branch="main",
                defect_scope=_scope(_SITE_EXAMPLE))
    hits = leerie._warn_site_token_leaks(st, leerie_dir)
    assert hits == [{"token": "acmeshop", "file": "src/x.ts"}]
    assert st.data["site_token_warnings"] == hits
    assert "WARNING" in capsys.readouterr().out


def test_site_token_already_in_the_base_tree_is_skipped(leerie, tmp_path):
    """The measured false-positive source: a token the repo already uses
    (here present in `main`) flags nothing."""
    leerie_dir = _staged_run(tmp_path, "an acmewidget was here before\n",
                             {"src/y.ts": "const acmewidget = 1;\n"})
    st = _state(leerie, tmp_path, working_branch="main",
                defect_scope=_scope({**_SITE_EXAMPLE,
                                     "site_tokens": ["acmewidget"]}))
    assert leerie._warn_site_token_leaks(st, leerie_dir) == []


def test_partial_word_does_not_match(leerie, tmp_path):
    leerie_dir = _staged_run(tmp_path, "x\n",
                             {"src/z.ts": "const acmeshopping = 1;\n"})
    st = _state(leerie, tmp_path, working_branch="main",
                defect_scope=_scope(_SITE_EXAMPLE))
    assert leerie._warn_site_token_leaks(st, leerie_dir) == []


def test_no_examples_means_no_check(leerie, tmp_path):
    st = _state(leerie, tmp_path, working_branch="main",
                defect_scope=_scope(_EXAMPLE))
    assert leerie._warn_site_token_leaks(st, tmp_path / "nowhere") == []
    assert "site_token_warnings" not in st.data


def test_check_runs_after_execute_and_before_the_delivery_gate(leerie):
    """Order is the contract: after integration (there is a diff to read),
    before the gate. Advisory: wrapped so it can never abort the run."""
    src = inspect.getsource(leerie._run_phases)
    i_exec = src.index("await phase_execute(")
    i_warn = src.index("_warn_site_token_leaks(st, leerie_dir)")
    i_gate = src.index("await _run_delivery_prejudge(")
    assert i_exec < i_warn < i_gate
    window = src[i_exec:i_gate]
    assert "except Exception" in window
