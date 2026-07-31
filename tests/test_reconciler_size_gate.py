"""Tests for the phase 2½ size gate, mirroring the cycle-gate test
corpus shape (`test_reconciler_cycle_gate.py`).

Covers:
  - `_find_oversized_added_subtasks` helper (detects size='large' on
    reconciler-added subtasks, ignores planner-authored subtasks even
    if they happen to be `size: large`).
  - `_build_size_retry_prompt` builder (names offenders, surfaces
    provides/requires/depends_on, includes the decomposition rule,
    re-appends the original user prompt).
  - `validate_plan` error wording switches between "planner" and
    "reconciler" based on the `_added_by_reconciler` flag.

Integration testing of the async retry loop in `phase_reconcile` is
out of scope here — the corpus convention (see
test_reconciler_cycle_gate.py) is to test building blocks unit-style;
end-to-end retry exercise is the manual re-run on the original
failing task.
"""
from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Helpers (match test_reconciler_cycle_gate.py's shape)
# ---------------------------------------------------------------------------

def _req(tag: str, extent: str = "in_plan") -> dict:
    return {"tag": tag, "extent": extent}


def _subtask(sid: str, *, provides=(), requires=(), depends_on=(),
             files=(), scs: str = "", size: str = "small") -> dict:
    return {
        "id": sid,
        "title": f"Subtask {sid}",
        "intent": f"intent for {sid}",
        "provides": list(provides),
        "requires": [_req(r) if isinstance(r, str) else r for r in requires],
        "depends_on": list(depends_on),
        "files_likely_touched": list(files),
        "success_criteria_seed": scs or f"{sid} succeeds",
        "size": size,
    }


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


# ---------------------------------------------------------------------------
# _find_oversized_added_subtasks
# ---------------------------------------------------------------------------

def test_find_oversized_empty_when_no_subtasks(leerie):
    assert leerie._find_oversized_added_subtasks([]) == []


def test_find_oversized_ignores_planner_authored_large(leerie):
    """A planner-authored `size: large` subtask is NOT caught by the
    size gate — the size gate only runs against reconciler-added
    subtasks. The planner case is the responsibility of `validate_plan`
    (the post-merge backstop). This boundary is load-bearing: the size
    gate's revert mechanism only knows how to revert the reconciler's
    mutations, not planner output."""
    s = _subtask("feat-001", size="large")
    plans = [_plan("feature-implementation", s)]
    assert leerie._find_oversized_added_subtasks(plans) == []


def test_find_oversized_catches_reconciler_large(leerie):
    """The captured-failure shape: reconciler-added subtask carrying
    `_added_by_reconciler: true` with `size: large`. Mirrors
    summarizer run feat-011's authoring."""
    s = _subtask("feat-011",
                 provides=["app-env-config-ready",
                           "db-client-and-dal-ready",
                           "object-storage-s3-ready"],
                 size="large")
    s["_added_by_reconciler"] = True
    plans = [_plan("_reconciler", s)]
    oversized = leerie._find_oversized_added_subtasks(plans)
    assert len(oversized) == 1
    assert oversized[0]["id"] == "feat-011"


def test_find_oversized_ignores_reconciler_added_small_medium(leerie):
    """`size: small` and `size: medium` are legal on reconciler-added
    subtasks. Only `size: large` is the trigger."""
    s_small = _subtask("feat-100", size="small")
    s_small["_added_by_reconciler"] = True
    s_med = _subtask("feat-101", size="medium")
    s_med["_added_by_reconciler"] = True
    plans = [_plan("_reconciler", s_small, s_med)]
    assert leerie._find_oversized_added_subtasks(plans) == []


def test_find_oversized_is_exact_match_only(leerie):
    """`size` is now a schema enum (`small`/`medium`/`large`), so a
    non-canonical value like `LARGE` can never reach this function on a
    schema-valid response — case-insensitivity is enforced at the schema
    layer (`claude_p()`'s `--json-schema` validation), not here. This
    function does an exact-match comparison and does not re-fold case."""
    s = _subtask("feat-001", size="LARGE")
    s["_added_by_reconciler"] = True
    plans = [_plan("_reconciler", s)]
    assert leerie._find_oversized_added_subtasks(plans) == []


def test_find_oversized_returns_all_offenders(leerie):
    """When the reconciler emits multiple oversized subtasks in one
    output, all are returned (not just the first)."""
    a = _subtask("feat-101", size="large")
    a["_added_by_reconciler"] = True
    b = _subtask("feat-102", size="large")
    b["_added_by_reconciler"] = True
    c = _subtask("feat-103", size="small")
    c["_added_by_reconciler"] = True
    plans = [_plan("_reconciler", a, b, c)]
    offenders = leerie._find_oversized_added_subtasks(plans)
    ids = sorted(s["id"] for s in offenders)
    assert ids == ["feat-101", "feat-102"]


# ---------------------------------------------------------------------------
# _build_size_retry_prompt
# ---------------------------------------------------------------------------

def test_size_retry_prompt_names_offender_and_provides(leerie):
    """The retry prompt must name each oversized subtask by sid and
    list the `provides` tags it bundled — the partition signal."""
    s = _subtask(
        "feat-011",
        provides=["app-env-config-ready", "db-client-and-dal-ready",
                  "object-storage-s3-ready"],
        requires=["bullmq-dependency-available"],
        size="large",
    )
    s["_added_by_reconciler"] = True

    prompt = leerie._build_size_retry_prompt([s], "ORIGINAL USER PROMPT")

    assert "feat-011" in prompt
    assert "app-env-config-ready" in prompt
    assert "db-client-and-dal-ready" in prompt
    assert "object-storage-s3-ready" in prompt
    assert "bullmq-dependency-available" in prompt
    assert "size: large" in prompt
    # The decomposition rule must be in the prompt (so the model knows
    # what to do, not just what was wrong).
    assert "one subtask per `provides` tag" in prompt
    # The original prompt is re-appended so the worker has full input.
    assert "ORIGINAL USER PROMPT" in prompt
    assert "ORIGINAL INPUT" in prompt


def test_size_retry_prompt_handles_multiple_offenders(leerie):
    """The prompt enumerates each offender with a section header."""
    a = _subtask("feat-101", provides=["cap-a"], size="large")
    a["_added_by_reconciler"] = True
    b = _subtask("feat-102", provides=["cap-b"], size="large")
    b["_added_by_reconciler"] = True

    prompt = leerie._build_size_retry_prompt([a, b], "ORIGINAL")

    assert "OVERSIZED 1:" in prompt
    assert "OVERSIZED 2:" in prompt
    assert "feat-101" in prompt
    assert "feat-102" in prompt
    assert "2 `added_subtask`(s)" in prompt


def test_size_retry_prompt_marks_no_provides_as_smell(leerie):
    """A reconciler-added subtask with no `provides` is itself a smell
    (the whole point of an added subtask is to *produce* a needed
    capability). The prompt surfaces that explicitly so the model
    notices the secondary issue when it splits."""
    s = _subtask("feat-001", provides=[], size="large")
    s["_added_by_reconciler"] = True

    prompt = leerie._build_size_retry_prompt([s], "ORIGINAL")

    assert "(none — this is itself a smell)" in prompt


# ---------------------------------------------------------------------------
# validate_plan error-message wording (the "D" fix)
# ---------------------------------------------------------------------------

def _good_subtask(sid="feat-001", **overrides):
    """Mirror of test_validate_plan.py's helper — kept independent so
    this file is self-contained."""
    base = {
        "id": sid,
        "title": "a subtask",
        "intent": "do the thing",
        "scope_note": "one verifiable change",
        "files_likely_touched": ["src/foo.py"],
        "depends_on": [],
        "requires": [],
        "provides": [],
        "success_criteria_seed": "the thing is done",
        "size": "small",
        "investigation_notes": "",
    }
    base.update(overrides)
    return base


def test_validate_plan_large_planner_wording(leerie, capsys):
    """A `size: large` subtask without `_added_by_reconciler` is
    planner-authored; the message blames the planner (existing
    behavior, regression guard)."""
    plan = {"feat-001": _good_subtask("feat-001", size="large")}
    with pytest.raises(SystemExit):
        leerie.validate_plan(plan)
    err = capsys.readouterr().err
    assert "feat-001: size='large'" in err
    assert "planner must split it further" in err
    # The reconciler wording must NOT appear for planner-authored.
    assert "reconciler must split" not in err


def test_validate_plan_large_reconciler_wording(leerie, capsys):
    """A `size: large` subtask with `_added_by_reconciler: true` means
    the phase 2½ size gate's retry exhausted; the message must blame
    the reconciler so the user knows which prompt misbehaved."""
    s = _good_subtask("feat-011", size="large")
    s["_added_by_reconciler"] = True
    plan = {"feat-011": s}
    with pytest.raises(SystemExit):
        leerie.validate_plan(plan)
    err = capsys.readouterr().err
    assert "feat-011: size='large'" in err
    assert "reconciler must split it further" in err
    assert "size-retry exhausted" in err
    # Must NOT misleadingly say "planner".
    assert "planner must split" not in err


# ---------------------------------------------------------------------------
# _apply_reconciler_output stamps `_added_by_reconciler` mechanically
# ---------------------------------------------------------------------------

def test_apply_reconciler_output_stamps_added_by_reconciler(leerie):
    """The `_added_by_reconciler` flag MUST be stamped by leerie, not
    trusted from the model's response. Otherwise a defective or hostile
    model could emit `_added_by_reconciler: false` on a `size: large`
    added subtask and bypass the size gate. CLAUDE.md's central
    principle: any guarantee that matters and can be checked
    mechanically lives in code, not in a worker prompt."""
    plans: list[dict] = [{
        "domain": "feature-implementation",
        "status": "ready",
        "subtasks": [{
            "id": "feat-001",
            "title": "existing planner subtask",
            "provides": [], "requires": [], "depends_on": [],
            "files_likely_touched": [],
            "success_criteria_seed": "exists",
            "size": "small",
        }],
    }]
    # Model emits an added_subtask WITHOUT the flag (or with false —
    # both must be overridden by leerie).
    output = {
        "renames": [], "added_provides": [],
        "added_subtasks": [
            {"id": "feat-100", "title": "new connector",
             "success_criteria_seed": "produces foo",
             "provides": ["foo"], "requires": [], "depends_on": [],
             "size": "small"},
            {"id": "feat-101", "title": "another connector",
             "success_criteria_seed": "produces bar",
             "provides": ["bar"], "requires": [], "depends_on": [],
             "size": "small",
             "_added_by_reconciler": False},  # model lies — leerie overrides
        ],
        "dropped_requires": [], "dependency_edges": [],
        "merged_subtasks": [], "unresolvable": [],
    }
    leerie._apply_reconciler_output(plans, output)

    # Find the appended `_reconciler` plan.
    recon_plan = next(p for p in plans if p["domain"] == "_reconciler")
    by_id = {s["id"]: s for s in recon_plan["subtasks"]}
    assert by_id["feat-100"].get("_added_by_reconciler") is True, (
        "apply step must stamp the flag on added_subtasks regardless "
        "of whether the model set it")
    assert by_id["feat-101"].get("_added_by_reconciler") is True, (
        "apply step must override a model-emitted `false` flag — "
        "otherwise a hostile model could bypass the size gate")


# ---------------------------------------------------------------------------
# Size retry's work must survive a subsequent cycle retry's revert
# ---------------------------------------------------------------------------

def _minimal_state_for_retry(leerie, tmp_path):
    """Stub State with just enough plumbing for phase_reconcile +
    _spawn_reconciler to call bump_workers + st.save without crashing.
    Pattern duplicated from test_reconciler_cycle_gate.py — kept inline
    to preserve test-file independence."""
    leerie_root = tmp_path / ".leerie"
    run_id = "test-size-then-cycle-bbb222"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.save()
    return st


def test_size_retry_then_cycle_retry_preserves_split(leerie, monkeypatch, tmp_path):
    """When size retry succeeds and then the cycle retry fires, the
    size retry's split MUST survive — the cycle retry's revert to the
    snapshot must restore the POST-size-retry state, not the original
    pre-mutation state. Without the snapshot refresh, the cycle
    retry's revert would undo the size split and the oversized
    subtask would return."""
    # Pre-reconcile fixture: two planner subtasks with mutually-requiring
    # tags so the reconciler's renames will close a cycle on top of
    # whatever it adds.
    plans = [
        {"domain": "feature-implementation", "status": "ready",
         "subtasks": [{
             "id": "feat-001",
             "title": "Backend service",
             "intent": "implement backend",
             "provides": ["backend-ready"],
             "requires": [{"tag": "node-runtime-libs", "extent": "in_plan"}],
             "depends_on": [],
             "files_likely_touched": ["src/server.ts"],
             "success_criteria_seed": "server boots",
             "size": "small"}]},
        {"domain": "configuration-build", "status": "ready",
         "subtasks": [{
             "id": "config-001",
             "title": "Runtime config",
             "intent": "config the runtime",
             "provides": ["app-runtime-deps"],
             "requires": [{"tag": "backend-framework", "extent": "in_plan"}],
             "depends_on": [],
             "files_likely_touched": ["package.json"],
             "success_criteria_seed": "config valid",
             "size": "small"}]},
    ]

    # Attempt 1: emit an oversized added_subtask `feat-X` bundling 3
    # provides + cycle-closing renames between feat-001 and config-001.
    attempt_1_output = {
        "renames": [
            {"sid": "feat-001", "from": "node-runtime-libs",
             "to": "app-runtime-deps"},
            {"sid": "config-001", "from": "backend-framework",
             "to": "backend-ready"},
        ],
        "added_provides": [],
        "added_subtasks": [{
            "id": "feat-100",
            "title": "Bundled foundation",
            "intent": "bundle 3 capabilities",
            "success_criteria_seed": "all three caps work",
            "provides": ["cap-a", "cap-b", "cap-c"],
            "requires": [], "depends_on": [],
            "size": "large"}],  # OVERSIZED → size gate fires
        "dropped_requires": [], "dependency_edges": [],
        "merged_subtasks": [], "unresolvable": [],
    }
    # Attempt 2 (size retry): splits the oversized subtask but keeps
    # the cycle-closing renames so the cycle gate still fires.
    attempt_2_output = {
        "renames": [
            {"sid": "feat-001", "from": "node-runtime-libs",
             "to": "app-runtime-deps"},
            {"sid": "config-001", "from": "backend-framework",
             "to": "backend-ready"},
        ],
        "added_provides": [],
        "added_subtasks": [
            {"id": "feat-100a", "title": "split-a",
             "success_criteria_seed": "cap-a works",
             "provides": ["cap-a"], "requires": [], "depends_on": [],
             "size": "small"},
            {"id": "feat-100b", "title": "split-b",
             "success_criteria_seed": "cap-b works",
             "provides": ["cap-b"], "requires": [], "depends_on": [],
             "size": "small"},
            {"id": "feat-100c", "title": "split-c",
             "success_criteria_seed": "cap-c works",
             "provides": ["cap-c"], "requires": [], "depends_on": [],
             "size": "small"},
        ],
        "dropped_requires": [], "dependency_edges": [],
        "merged_subtasks": [], "unresolvable": [],
    }
    # Attempt 3 (cycle retry): the cycle retry's prompt asked the
    # model to break the cycle. With the snapshot refresh in place,
    # the cycle retry's revert restores the POST-size-retry state
    # (which includes attempt 2's renames + the split). Attempt 3
    # only needs to drop one of the cycle-closing entries. The
    # consumer's requires entry holds the ORIGINAL pre-rename tag
    # because apply-on-pre-snapshot would re-apply the renames; leerie's
    # cycle-retry recommendation logic uses the original tag. Here,
    # attempt 3 drops config-001's original `backend-framework`
    # requires (the entry the rename rewrote to `backend-ready`).
    attempt_3_output = {
        "renames": [
            {"sid": "feat-001", "from": "node-runtime-libs",
             "to": "app-runtime-deps"},
            {"sid": "config-001", "from": "backend-framework",
             "to": "backend-ready"},
        ],
        "added_provides": [],
        "added_subtasks": [],
        "dropped_requires": [
            # Drop the ORIGINAL pre-rename tag (matches the apply
            # step's lookup after the revert restores the snapshot
            # and the rename re-applies — actually: revert puts back
            # the post-rename state; then attempt-3's renames are
            # no-ops since the entries are already renamed; then the
            # drop targets the POST-rename tag because that's what's
            # in the entry now).
            {"sid": "config-001", "tag": "backend-ready",
             "reason": "break the cycle"},
        ],
        "dependency_edges": [], "merged_subtasks": [], "unresolvable": [],
    }

    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return attempt_1_output
        if len(calls) == 2:
            return attempt_2_output
        return attempt_3_output

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    st = _minimal_state_for_retry(leerie, tmp_path)
    caps = dict(leerie.DEFAULT_CAPS)
    models = {"reconciler": "opus"}
    efforts = {"reconciler": "high"}

    result = asyncio.run(leerie.phase_reconcile(
        plans, "feature task", st, caps, models, efforts))

    # The size split MUST survive — the three split subtasks (feat-100a,
    # feat-100b, feat-100c) must be in the final plans; the original
    # oversized feat-100 must NOT.
    all_sids = {s["id"] for plan in result for s in plan.get("subtasks", [])}
    assert "feat-100a" in all_sids, (
        "size split's feat-100a must survive the cycle retry — "
        "without the snapshot refresh, the cycle retry's revert "
        "would undo the size split")
    assert "feat-100b" in all_sids
    assert "feat-100c" in all_sids
    assert "feat-100" not in all_sids, (
        "the original oversized feat-100 must NOT be in the final plans")
    # Sanity check on the call count.
    assert len(calls) == 3, (
        f"expected 3 claude_p calls (initial + size-retry + cycle-retry); "
        f"got {len(calls)}")
