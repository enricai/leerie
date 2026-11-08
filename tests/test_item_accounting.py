"""Every required item is claimed before execution (DESIGN §5
*Required items are claimed too, and the plan accounts for every one*).

Motive, proven from barnacle run 4f9a4b4f's state and calls.ndjson: its
item 13 ("... The unfocused-probe 'treating as present' must not fire on
conditional steps.") WAS claimed — by `bugfix-003`, a subtask typed
`verification_only: true` with no files, whose verify commands tested other
items. It was settled by execution, nothing in the run planned the change,
and only the falsifier found the item unmet. 879de601's unmet item 8 has the
same shape. The same plan also claimed an item 14 of a 14-item list, and
2dcb4cce's only fix for item 13 claimed item 11: planners were handed a bare
list and counted positions.

Discipline: the rows and the check are executed on values; the wiring is
driven through the REAL `phase_plan` (planner loop, recursive decomposition)
in a real git repository and the REAL `phase_planning_coverage_gate`, with
only `claude_p` stubbed.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from tests.test_defect_scope_audit import EFFORTS, MODELS, _state


ITEMS_4F9A = [{"item": f"F{i}: requirement {i}", "span": f"R{i + 5}"}
              for i in range(13)] + [{
    "item": "Other observations: llm-rephrase click-and-type must clear the "
            "field first. The unfocused-probe 'treating as present' must not "
            "fire on conditional steps.",
    "span": "R19",
    "parts": ["Clear before typing", "No 'treating as present' for "
              "conditional steps"]}]


def _sub(sid: str, *items: tuple[int, str], change_kind: str = "fix",
         files=("src/flow_runner.py",), **extra) -> dict:
    s = {"id": sid, "title": sid, "intent": f"intent {sid}",
         "success_criteria_seed": "c", "files_likely_touched": list(files),
         "depends_on": [], "provides": [], "requires": [],
         "change_kind": change_kind, "change_shape": "point",
         "size": "small",
         "claims": {"sites": [], "items": [
             {"item_index": i, "kind": k} for i, k in items]}}
    s.update(extra)
    return s


def _plan(*subtasks, not_needed=(), domain="bug-fixing", status="ready"):
    return {"domain": domain, "status": status, "blocker_kind": "none",
            "subtasks": list(subtasks),
            "items_not_needed": [
                {"item_index": i, "reason_kind": k, "reason": r}
                for i, k, r in not_needed],
            "confidence": {"task_understanding": 9.5,
                           "decomposition_quality": 9.5, "basis": "stub"}}


def _verify_only(sid, *items):
    return _sub(sid, *items, change_kind="other", files=(),
                verification_only=True,
                verify=[{"criterion": "existing tests pass",
                         "argv": ["pytest", "tests/test_flow_runner.py"]}])


def _status(leerie, plans, items):
    return {r["item_index"]: r["status"]
            for r in leerie._item_accounting_rows(plans, items)}


# === the rows: set arithmetic on typed fields ==============================

def test_claimed_other_domain_and_uncovered(leerie):
    items = [{"item": f"i{n}"} for n in range(4)]
    plans = [
        _plan(_sub("bugfix-001", (0, "deliverable"), (1, "process")),
              not_needed=[(2, "other_domain", "tests own it")]),
    ]
    assert _status(leerie, plans, items) == {
        0: "claimed", 1: "claimed", 2: "other_domain", 3: "uncovered"}


def test_already_on_tree_is_no_exclusion(leerie):
    """The exclusion path is gone (DESIGN §5): no plan-time check of "the
    tree already carries it" is sound, so the item must be claimed."""
    assert leerie.ITEM_EXCLUSION_KINDS == ("other_domain",)
    plans = [_plan(not_needed=[(0, "already_on_tree", "x.py:4 does it")])]
    assert _status(leerie, plans, [{"item": "a"}]) == {0: "uncovered"}
    issues = leerie._check_item_claims(plans[0], [{"item": "a"}], 1)
    assert issues[0].startswith("ITEM_UNACCOUNTED: item 0 — ")


def test_a_verification_only_deliverable_claim_does_not_count(leerie):
    """The 4f9a4b4f/879de601 shape: a subtask that writes nothing cannot
    deliver a change, so its deliverable claim is no claim."""
    plans = [_plan(_sub("bugfix-001", (0, "deliverable")),
                   _verify_only("bugfix-003", (1, "deliverable"),
                                (2, "process")))]
    rows = leerie._item_accounting_rows(plans, [{"item": "a"}, {"item": "b"},
                                                {"item": "c"}])
    assert [(r["status"], r["claimed_by"], r["not_counted"]) for r in rows] \
        == [("claimed", ["bugfix-001"], []),
            ("uncovered", [], ["bugfix-003"]),
            ("claimed", ["bugfix-003"], [])]


def test_a_claim_wins_over_an_exclusion(leerie):
    items = [{"item": "a"}, {"item": "b"}]
    plans = [_plan(_sub("bugfix-001", (0, "deliverable"))),
             _plan(_sub("test-001", change_kind="test"), domain="testing",
                   not_needed=[(0, "other_domain", "bug-fixing has it"),
                               (1, "other_domain", "bug-fixing has it")])]
    rows = leerie._item_accounting_rows(plans, items)
    assert [r["status"] for r in rows] == ["claimed", "other_domain"]
    assert rows[1]["excluded"] == [
        {"domain": "test", "reason_kind": "other_domain",
         "reason": "bug-fixing has it"}]


@pytest.mark.parametrize("entry", [
    {"item_index": 0, "reason_kind": "other_domain", "reason": "  "},
    {"item_index": 0, "reason_kind": "out_of_scope", "reason": "r"},
    {"item_index": True, "reason_kind": "other_domain", "reason": "r"},
])
def test_an_untyped_or_empty_exclusion_is_no_exclusion(leerie, entry):
    plan = _plan()
    plan["items_not_needed"] = [entry]
    assert _status(leerie, [plan], [{"item": "a"}]) == {0: "uncovered"}


def test_split_children_keep_the_parents_item_claims(leerie):
    parent = _sub("bugfix-001", (0, "deliverable"))
    kids = [leerie._migration_child(parent, [f], f"bugfix-001-{n}", "t", "c")
            for n, f in enumerate(("a.py", "b.py"), 1)]
    rows = leerie._item_accounting_rows([_plan(*kids)], [{"item": "a"}])
    assert rows[0]["claimed_by"] == ["bugfix-001-1", "bugfix-001-2"]


@pytest.mark.parametrize("merge", ["overlap", "reconciler"])
def test_a_merge_keeps_both_subtasks_item_claims(leerie, merge):
    a = _sub("feat-001", (0, "deliverable"))
    b = _sub("feat-002", (1, "deliverable"), (0, "deliverable"))
    plans = [_plan(a, b, domain="feature-implementation")]
    if merge == "overlap":
        leerie._apply_overlap_merge(plans, "feat-001", "feat-002",
                                    "shared-artifact", "one spec")
    else:
        leerie._merge_subtask_core_fields(a, b, "feat-001", "feat-002")
        plans[0]["subtasks"] = [a]
    claims = plans[0]["subtasks"][0]["claims"]["items"]
    assert sorted(c["item_index"] for c in claims) == [0, 1]
    assert _status(leerie, plans, [{"item": "a"}, {"item": "b"}]) == {
        0: "claimed", 1: "claimed"}


# === the planner-loop check ================================================

def test_check_names_the_item_its_words_and_the_uncounted_claimer(leerie):
    issues = leerie._check_item_claims(
        _plan(_verify_only("bugfix-003", (13, "deliverable"),
                           (14, "deliverable")),
              *[_sub(f"bugfix-{i:03}", (i, "deliverable"))
                for i in range(13)]),
        ITEMS_4F9A, 1)
    assert issues[0].startswith("ITEM_INDEX_UNKNOWN: 14 — ")
    assert len(issues) == 2
    assert issues[1].startswith("ITEM_UNACCOUNTED: item 13 — ")
    assert "treating as present" in issues[1]
    assert "claimed only by bugfix-003, which is `verification_only`" \
        in issues[1]
    assert "settles as a no-op" in issues[1]
    assert "already_on_tree" not in issues[1]
    assert "other_domain" not in issues[1], \
        "a one-category run must not be offered other_domain"


@pytest.mark.parametrize("n_categories,expect", [(1, 1), (2, 0)])
def test_other_domain_counts_only_with_another_category(
        leerie, n_categories, expect):
    plan = _plan(not_needed=[(0, "other_domain", "the other planner")])
    assert len(leerie._check_item_claims(
        plan, [{"item": "a"}], n_categories)) == expect


@pytest.mark.parametrize("plan,items", [
    (_plan(status="blocked"), [{"item": "a"}]),
    (_plan(), []),
    (_plan(), None),
])
def test_check_is_silent_on_a_blocked_plan_or_no_items(leerie, plan, items):
    assert leerie._check_item_claims(plan, items, 1) == []


def test_planner_items_carry_the_code_assigned_index(leerie):
    out = leerie._planner_required_items(
        [{"item": "a", "span": "R1", "untouched_paths": []}, "junk",
         {"item": "c", "parts": ["p"]}])
    assert out == [{"item_index": 0, "item": "a", "span": "R1"},
                   {"item_index": 2, "item": "c", "parts": ["p"]}]


def test_planner_schema_carries_typed_exclusions(leerie):
    sch = leerie.SCHEMAS["planner"]
    assert sch["properties"]["items_not_needed"] == {
        "$ref": "#/$defs/items_not_needed"}
    entry = sch["$defs"]["items_not_needed"]["items"]
    assert sorted(entry["required"]) == ["item_index", "reason", "reason_kind"]
    assert entry["properties"]["reason_kind"]["enum"] == [
        "other_domain"]
    assert "items_not_needed" not in sch["required"]


def test_planner_prompt_documents_the_accounting(leerie):
    text = leerie._load_prompt("planner")
    assert "already_on_tree" not in text, "the exclusion path is gone"
    for name in ("items_not_needed", "other_domain",
                 "reason_kind", "item_index"):
        assert f"`{name}`" in text or f'"{name}"' in text, name
    assert "position in `required_items`" not in text, \
        "the planner copies the index; it never counts positions"
    assert "comparing `required_items` against your subtasks'" not in text, \
        "the prompt must not describe the deleted token floor"


# === the real phase_plan, in a real git repository =========================

@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "flow_runner.py").write_text("def run():\n    pass\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_flow_runner.py").write_text(
        "def test_run():\n    pass\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True,
                       env={**__import__("os").environ, **env})
    monkeypatch.chdir(root)
    return root


def _drive_plan(leerie, monkeypatch, st, replies):
    """phase_plan with only `claude_p` stubbed: the planner gets `replies`
    in turn; the fit judge scores every subtask a leaf."""
    planner_calls: list[dict] = []

    async def fake_claude_p(**kw):
        if kw["schema_key"] == "fit_judge":
            return {"score": 0.95, "rationale": "fits"}
        assert kw["schema_key"] == "planner", kw["schema_key"]
        planner_calls.append(kw)
        return json.loads(json.dumps(
            replies[min(len(planner_calls), len(replies)) - 1]))

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["planner_samples"] = 1
    plans = asyncio.run(leerie.phase_plan("fix the report", st, caps,
                                          MODELS, EFFORTS))
    return plans, planner_calls


def _4f9a_first_plan():
    """4f9a4b4f's final plan shape: fixes for some items, one
    verification-only subtask claiming the rest, item 14 included."""
    return _plan(_sub("bugfix-001", (0, "deliverable"), (1, "deliverable"),
                      (2, "deliverable"), (4, "deliverable"),
                      (8, "deliverable"), (0, "process")),
                 _verify_only("bugfix-003", *[(i, "deliverable") for i in
                                              (3, 5, 6, 7, 9, 10, 11, 12,
                                               13, 14)]))


def test_an_unaccounted_item_sends_the_plan_back_with_that_item(
        leerie, tmp_path, monkeypatch, repo):
    st = _state(leerie, tmp_path, required_items=ITEMS_4F9A)
    second = _plan(
        _sub("bugfix-001", (0, "deliverable"), (1, "deliverable"),
             (2, "deliverable"), (4, "deliverable"), (8, "deliverable"),
             (13, "deliverable")),
        # Items the planner believes already done are claimed too, by a
        # subtask that can change code; it settles as a no-op if so.
        _sub("bugfix-002", *[(i, "deliverable") for i in
                             (3, 5, 6, 7, 9, 10, 11, 12)]))
    plans, calls = _drive_plan(leerie, monkeypatch, st,
                               [_4f9a_first_plan(), second])

    assert len(calls) == 2, "the unaccounted items must re-invoke the planner"
    # The planner is handed the code-assigned index, not a bare list.
    assert '"item_index": 13' in calls[0]["user_prompt"]
    feedback = calls[1]["user_prompt"]
    assert "ITEM_INDEX_UNKNOWN: 14" in feedback
    lines = [ln for ln in feedback.splitlines() if "ITEM_UNACCOUNTED" in ln]
    assert sorted(int(ln.split("item ")[1].split(" ")[0]) for ln in lines) \
        == [3, 5, 6, 7, 9, 10, 11, 12, 13]
    assert "treating as present" in feedback
    # The accepted plan accounts for every item: 13 is now claimed by the
    # subtask that changes the tree.
    rows = leerie._item_accounting_rows(plans, ITEMS_4F9A)
    assert {r["status"] for r in rows} == {"claimed"}
    assert rows[13]["claimed_by"] == ["bugfix-001"]


def test_a_covered_plan_proceeds_without_another_round(
        leerie, tmp_path, monkeypatch, repo):
    st = _state(leerie, tmp_path, required_items=ITEMS_4F9A)
    covered = _plan(
        _sub("bugfix-001", *[(i, "deliverable") for i in range(13)]),
        _sub("bugfix-002", (13, "deliverable")))
    _, calls = _drive_plan(leerie, monkeypatch, st, [covered])
    assert len(calls) == 1


def test_skip_coverage_check_turns_the_planner_check_off(
        leerie, tmp_path, monkeypatch, repo):
    st = _state(leerie, tmp_path, required_items=ITEMS_4F9A,
                skip_coverage_check=True)
    _, calls = _drive_plan(leerie, monkeypatch, st, [_4f9a_first_plan()])
    assert len(calls) == 1


# === the real coverage gate: record, log, PR ===============================

def _gate(leerie, st, plans, caps=None):
    return asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "fix the report", st, caps or dict(leerie.DEFAULT_CAPS),
        MODELS, EFFORTS))


def _clean_judge(leerie, monkeypatch):
    calls: list[str] = []

    async def fake_claude_p(**kw):
        calls.append(kw["schema_key"])
        assert kw["schema_key"] == "task_coverage_judge", kw["schema_key"]
        return {"task_covered": True, "coverage_gaps": [], "rationale": "r"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


def test_a_residual_is_recorded_logged_and_listed_in_the_pr(
        leerie, tmp_path, monkeypatch, capsys):
    """The planner loop ran out with item 13 resting on a
    verification-only claim: the run proceeds, and says so before any
    implementation work — in state, in the log, in the PR."""
    st = _state(leerie, tmp_path, required_items=ITEMS_4F9A)
    calls = _clean_judge(leerie, monkeypatch)

    async def no_replan(*a, **k):
        pytest.fail("a single-domain residual already had the planner's "
                    "rounds; the gate must not re-plan it")
    monkeypatch.setattr(leerie, "phase_plan", no_replan)
    plans = [_4f9a_first_plan()]
    assert _gate(leerie, st, plans) is plans
    assert calls == ["task_coverage_judge"]

    rec = st.data["item_coverage"]
    assert rec["uncovered"] == [3, 5, 6, 7, 9, 10, 11, 12, 13]
    assert rec["items"][13]["not_counted"] == ["bugfix-003"]
    out = capsys.readouterr().out
    assert ("9 required item(s) no planned subtask that can change the "
            "tree claims") in out
    assert "item 13 (uncovered): Other observations" in out

    groups = leerie._pr_not_verified(st.data, lambda sid: True)
    lines = groups["Required items no planned subtask claimed"]
    assert len(lines) == 9
    assert lines[-1].startswith("- [ ] item 13: Other observations")


def test_a_fully_accounted_plan_is_recorded_clean(
        leerie, tmp_path, monkeypatch, capsys):
    st = _state(leerie, tmp_path, required_items=ITEMS_4F9A)
    _clean_judge(leerie, monkeypatch)
    plans = [_plan(_sub("bugfix-001", *[(i, "deliverable")
                                        for i in range(14)]))]
    _gate(leerie, st, plans)
    assert st.data["item_coverage"]["uncovered"] == []
    assert "all 14 required item(s) claimed by a subtask that can change" in \
        capsys.readouterr().out
    assert "Required items no planned subtask claimed" not in \
        leerie._pr_not_verified(st.data, lambda sid: True)


def _two_domain_gap():
    return [
        _plan(_sub("bugfix-001", (0, "deliverable")),
              not_needed=[(1, "other_domain", "the tests planner owns it")]),
        _plan(domain="testing",
              not_needed=[(1, "other_domain", "bug-fixing owns it")]),
    ]


def test_an_item_every_domain_points_elsewhere_replans_with_that_item(
        leerie, tmp_path, monkeypatch, repo):
    """The real gate driving the real phase_plan: the re-plan carries the
    item, is scoped to the domains that excluded it, and the plan that
    comes back claims it."""
    items = [{"item": "first requirement"},
             {"item": "second requirement, the one nobody claimed"}]
    st = _state(leerie, tmp_path, required_items=items,
                categories=["bug-fixing", "testing"])
    planner_prompts: list[str] = []

    async def fake_claude_p(**kw):
        key = kw["schema_key"]
        if key == "task_coverage_judge":
            return {"task_covered": True, "coverage_gaps": [],
                    "rationale": "r"}
        if key == "fit_judge":
            return {"score": 0.95, "rationale": "fits"}
        assert key == "planner", key
        planner_prompts.append(kw["user_prompt"])
        if "DOMAIN: testing" in kw["user_prompt"]:
            return _plan(domain="testing", not_needed=[
                (0, "other_domain", "bug-fixing owns it"),
                (1, "other_domain", "bug-fixing owns it")])
        return _plan(_sub("bugfix-001", (0, "deliverable"),
                          (1, "deliverable")))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    caps = dict(leerie.DEFAULT_CAPS)
    caps["planner_samples"] = 1
    plans = _gate(leerie, st, _two_domain_gap(), caps)
    assert len(planner_prompts) == 2, "both excluding domains re-plan"
    for p in planner_prompts:
        assert "ITEM_UNCLAIMED_BY_ANY_DOMAIN: item 1" in p
        assert "the one nobody claimed" in p
    assert st.data["item_coverage"]["uncovered"] == []
    rows = leerie._item_accounting_rows(plans, items)
    assert rows[1]["claimed_by"] == ["bugfix-001"]


def test_a_replan_that_cannot_be_afforded_spends_nothing(
        leerie, tmp_path, monkeypatch):
    items = [{"item": "a"}, {"item": "b"}]
    st = _state(leerie, tmp_path, required_items=items,
                categories=["bug-fixing", "testing"], worker_count=200)
    _clean_judge(leerie, monkeypatch)

    async def no_plan(*a, **k):
        pytest.fail("the preflight must stop the re-plan before it spends")
    monkeypatch.setattr(leerie, "phase_plan", no_plan)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_total_workers"] = 200
    with pytest.raises(SystemExit) as exc:
        _gate(leerie, st, _two_domain_gap(), caps)
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE


def test_a_cross_domain_residual_after_the_rounds_is_recorded(
        leerie, tmp_path, monkeypatch, capsys):
    items = [{"item": "a"}, {"item": "b"}]
    st = _state(leerie, tmp_path, required_items=items,
                categories=["bug-fixing", "testing"])
    _clean_judge(leerie, monkeypatch)
    replans: list[int] = []

    async def same_gap(task, st_, caps, models, efforts, replan_round=0,
                       domains=None):
        replans.append(replan_round)
        return _two_domain_gap()

    async def passthrough(plans, *a, **k):
        return plans
    monkeypatch.setattr(leerie, "phase_plan", same_gap)
    for name in ("phase_reconcile", "phase_overlap_judge",
                 "phase_adherence_gate"):
        monkeypatch.setattr(leerie, name, passthrough)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    _gate(leerie, st, _two_domain_gap(), caps)
    assert replans and replans == list(range(1, len(replans) + 1))
    assert len(replans) <= caps["judgment_check_rounds"] - 1
    assert st.data["item_coverage"]["uncovered"] == [1]
    assert st.data["item_coverage"]["items"][1]["status"] == "other_domain"
    assert "item 1 (other_domain): b" in capsys.readouterr().out
