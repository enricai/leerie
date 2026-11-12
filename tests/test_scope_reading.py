"""A task that reads narrow or broad takes the broad reading (DESIGN §8 *A
sweep's instances are items*).

Navegando 4134b78c: "make sure that all of the translations in the messages
directory … are complete … for the entire site". Lane SW2 measured that a
typed sweep makes a half-done sweep visible as unmet items, and that the
samples which failed were the defect-scope auditor taking the narrow reading
(the JSON files) and never listing the components. Which reading the user
meant is not the run's to guess, so the auditor reports both readings as a
typed field — the broad one a swept pattern — and the run does both: it
takes the broad reading, asks nobody, and says so in the PR.

Real git repositories, real tree-sitter, the real `phase_defect_scope_audit`,
`_settle_scope_reading`, `_run_phases` and PR renderer; only `claude_p`, the
terminal and `input()` are stubbed."""
from __future__ import annotations

import asyncio
import builtins
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from conftest import HAS_TREESITTER  # noqa: E402
from test_resume_planning_reentry import run_dirs  # noqa: E402,F401
from test_sweep_instances import SITE_SWEEP, _repo  # noqa: E402

needs_ts = pytest.mark.skipif(not HAS_TREESITTER,
                              reason="tree-sitter stack unavailable")

NARROW = "the message files' keys are complete in both languages"
BROAD = ("no hard-coded user-facing text remains in components or server "
         "messages")


def _reply(kind="narrow_or_broad", broad_only=True, narrow=NARROW,
           broad=BROAD):
    """An audit of the translations task: the message files (both readings)
    and the components' hard-coded text (the broad reading only)."""
    sites = [
        {"file": "messages/en.json", "symbol": "values",
         "role": "decision_site", "disposition": "fix",
         "extent": "one_place", "resolution_kind": "recompute",
         "resolution": "complete the keys", "reading": "both"},
        {"file": "src/components", "symbol": "hard-coded UI text",
         "role": "bypass", "disposition": "fix", "extent": "pattern",
         "resolution_kind": "relink",
         "resolution": "route the text through the messages",
         "sweep": SITE_SWEEP,
         **({"reading": "broad_only"} if broad_only else {})}]
    return {"applicable": True, "defect_shape": "text bypasses messages",
            "sites": sites, "chokepoint": {"exists": False},
            "reading": {"kind": kind,
                        "narrow": narrow if kind != "one_reading" else "",
                        "broad": broad if kind != "one_reading" else ""}}


def _state(leerie, tmp_path, name="scope-reading", **data):
    st = leerie.State(tmp_path / ".leerie", name)
    st.data = {"task": "t", "worker_count": 0, "categories": ["bug-fixing"],
               "required_items": [{"span": "S1", "item": "translations are "
                                   "complete", "verifiability": "tree"}],
               "planning_worktree": str(_repo(tmp_path / "plan")), **data}
    st.save()
    return st


def _audit(leerie, st, replies, monkeypatch):
    prompts: list[str] = []

    async def fake(**kw):
        assert kw["schema_key"] == "defect_scope_auditor"
        prompts.append(kw["user_prompt"])
        return replies[min(len(prompts), len(replies)) - 1]
    monkeypatch.setattr(leerie, "claude_p", fake)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    st.data["defect_scope"] = asyncio.run(leerie.phase_defect_scope_audit(
        "t", st, caps, {"defect_scope_auditor": "sonnet"},
        {"defect_scope_auditor": "medium"}))
    st.save()
    return prompts


class _Terminal:
    def isatty(self) -> bool:
        return True


class _NoTerminal:
    def isatty(self) -> bool:
        return False


def _never_asked(monkeypatch, terminal: bool = True) -> None:
    """A person at the terminal (or not) who must never be asked."""
    def no_input(prompt=""):
        raise AssertionError(f"asked: {prompt!r}")
    monkeypatch.setattr(sys, "stdin", _Terminal() if terminal
                        else _NoTerminal())
    monkeypatch.setattr(builtins, "input", no_input)


def _derived_files(st) -> list[str]:
    return [it["sweep_of"]["file"] for it in st.data["required_items"]
            if isinstance(it.get("sweep_of"), dict)]


# === the auditor reports the readings, checked in its loop ===================

@pytest.mark.parametrize("reply,issue", [
    (_reply(), None),
    (_reply(kind="one_reading", broad_only=False), None),
    # Two readings whose broad one adds no site: the question would offer a
    # choice that changes nothing.
    (_reply(broad_only=False), "READING_BROAD_ADDS_NOTHING"),
    (_reply(broad=" "), "READING_UNDESCRIBED"),
    # A broad-only site under one reading is a reading not declared.
    (_reply(kind="one_reading"), "READING_UNDECLARED: sites[1]"),
    # Not applicable: nothing to check.
    ({"applicable": False, "sites": [],
      "reading": {"kind": "narrow_or_broad", "narrow": "", "broad": ""}},
     None),
])
def test_the_reading_is_checked_where_it_is_written(leerie, reply, issue):
    got = leerie._reading_issues(reply)
    if issue is None:
        assert got == []
    else:
        assert len(got) == 1 and got[0].startswith(issue)


@pytest.mark.parametrize("change", [
    {"disposition": "ruled_out"},
    # Single files are not a broad reading: the pattern they belong to must
    # be named and swept (SW3 attempt 5: three component files, no sweep).
    {"extent": "one_place", "sweep": None,
     "file": "src/components/order-card.tsx"},
    # A pattern the auditor did not give a sweep enumerates nothing.
    {"sweep": None},
])
def test_a_broad_reading_counts_only_by_a_swept_pattern(leerie, change):
    reply = _reply()
    site = reply["sites"][1]
    site.update(change)
    for k in [k for k, v in site.items() if v is None]:
        del site[k]
    got = leerie._reading_issues(reply)
    assert len(got) == 1 and got[0].startswith("READING_BROAD_ADDS_NOTHING")
    assert "`extent: pattern`" in got[0]


@needs_ts
def test_the_audit_sends_back_a_broad_reading_that_adds_nothing(
        leerie, tmp_path, monkeypatch):
    """The real check loop: a two-reading answer with no broad-only site
    goes back to the auditor; the corrected one is kept, readings and all,
    and the audit derives nothing itself — the reading is not settled yet."""
    st = _state(leerie, tmp_path)
    prompts = _audit(leerie, st, [_reply(broad_only=False), _reply()],
                     monkeypatch)
    assert len(prompts) == 2
    assert "READING_BROAD_ADDS_NOTHING" in prompts[1]
    scope = st.data["defect_scope"]
    assert scope["reading"] == {"kind": "narrow_or_broad", "narrow": NARROW,
                                "broad": BROAD}
    assert [s.get("reading") for s in scope["sites"]] == ["both",
                                                          "broad_only"]
    assert _derived_files(st) == []


def test_auditor_schema_requires_the_reading(leerie):
    schema = leerie.SCHEMAS["defect_scope_auditor"]
    assert "reading" in schema["required"]
    r = schema["properties"]["reading"]
    assert r["required"] == ["kind", "narrow", "broad"]
    assert r["properties"]["kind"]["enum"] == ["one_reading",
                                               "narrow_or_broad"]
    site = schema["properties"]["sites"]["items"]["properties"]
    assert site["reading"]["enum"] == ["both", "broad_only"]
    prompt = leerie._load_prompt("defect_scope_auditor")
    assert '`"broad_only"`' in prompt and '`kind: "narrow_or_broad"`' in prompt


# === the run takes the broad reading and asks nobody ========================

@needs_ts
@pytest.mark.parametrize("terminal", [True, False])
@pytest.mark.parametrize("clarify", [True, False])
def test_two_readings_take_the_broad_one_without_asking(
        leerie, tmp_path, monkeypatch, terminal, clarify, capsys):
    """Terminal or not, `--clarify` or not: nothing is asked, every site is
    kept, and the broad-only site's sweep derives its items."""
    st = _state(leerie, tmp_path, clarify=clarify)
    _audit(leerie, st, [_reply()], monkeypatch)
    _never_asked(monkeypatch, terminal)
    leerie._settle_scope_reading(st)
    assert st.data["scope_reading"] == {
        "kind": "narrow_or_broad", "narrow": NARROW, "broad": BROAD,
        "chosen": "broad"}
    assert [s["site_id"] for s in st.data["defect_scope"]["sites"]] == [
        "D1", "D2"]
    assert _derived_files(st) == ["src/components/order-card.tsx"]
    assert [r["site_id"] for r in st.data["sweeps"]] == ["D2"]
    assert "?" not in capsys.readouterr().out
    assert "scope_reading" not in (st.data.get("answers") or {})


@needs_ts
def test_one_reading_records_no_choice_and_sweeps_every_site(
        leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    _audit(leerie, st, [_reply(kind="one_reading", broad_only=False)],
           monkeypatch)
    _never_asked(monkeypatch)
    leerie._settle_scope_reading(st)
    assert st.data["scope_reading"] == {"kind": "one_reading"}
    assert _derived_files(st) == ["src/components/order-card.tsx"]


# === the PR says which reading the run took ==================================

def test_the_pr_says_the_run_did_both(leerie):
    state = {"scope_reading": {"kind": "narrow_or_broad", "narrow": NARROW,
                               "broad": BROAD, "chosen": "broad"}}
    body = leerie._format_pr_end_state_section(state)
    section = body.split("## Scope reading", 1)[1]
    assert "This run did both: it took the **broad** reading" in section
    assert "Nobody was asked." in section
    assert f"- Narrow: {NARROW}" in section and f"- Broad: {BROAD}" in section
    # Every composition path carries it: the deterministic fallback too.
    assert "## Scope reading" in leerie.compose_pr_body(
        {"task": "t", **state}, "r")


@pytest.mark.parametrize("rec", [{"kind": "one_reading"}, {"kind": "none"},
                                 None])
def test_one_reading_adds_no_pr_section(leerie, rec):
    assert "Scope reading" not in leerie._format_pr_end_state_section(
        {"scope_reading": rec})


# === once, after the audit: the step's own checkpoint ======================

@needs_ts
def test_run_phases_settles_the_reading_after_the_audit_once(
        leerie, tmp_path, monkeypatch, run_dirs):
    """The real `_run_phases` (its phases stubbed as the resume tests stub
    them, the audit returning the two-reading scope), on a terminal: nobody
    is asked, the broad reading's derived items reach the planner, and a
    resume that finds `scope_reading` derives nothing again."""
    from test_resume_planning_reentry import (
        _args, _caps, _drive, _make_state, _plan, _stub_common, _subtask)
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    plan_tree = str(_repo(tmp_path / "plan"))

    async def _keep_tree(st, *a, **kw):
        st.data["planning_worktree"] = plan_tree
        return plan_tree
    monkeypatch.setattr(leerie, "_ensure_planning_worktree", _keep_tree)
    planned: list[list] = []

    async def _audit_stub(task, st, caps, models, efforts):
        return {**_reply(), "sites": leerie._scope_sites(_reply())}
    monkeypatch.setattr(leerie, "phase_defect_scope_audit", _audit_stub)

    async def _plan_stub(task, st, caps, models, efforts):
        planned.append(json.loads(json.dumps(st.data["required_items"])))
        return [_plan("bug-fixing", _subtask("bugfix-001"))]
    monkeypatch.setattr(leerie, "phase_plan", _plan_stub)
    st = _make_state(leerie, run_dirs, {
        "task": "t", "worker_count": 0, "categories": ["bug-fixing"],
        "plans_after_classify": [], "clarify": False,
        "required_items": [{"span": "S1", "item": "translations are "
                            "complete", "verifiability": "tree"}],
        "planning_worktree": plan_tree})
    _never_asked(monkeypatch)
    _drive(leerie, _args(clarify=False), _caps(leerie), run_dirs, st)
    assert st.data["scope_reading"]["chosen"] == "broad"
    assert planned and [it["sweep_of"]["file"] for it in planned[0]
                        if it.get("sweep_of")] == [
        "src/components/order-card.tsx"]
    items = json.loads(json.dumps(st.data["required_items"]))
    st.data.pop("plans_after_plan", None)
    st.save()
    _drive(leerie, _args(clarify=False), _caps(leerie), run_dirs, st)
    assert st.data["required_items"] == items
