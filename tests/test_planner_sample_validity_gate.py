"""The validity gate in `_select_best_planner_sample` (DESIGN §5).

`check_planner_output` inspects subtasks, so a plan with none to inspect
returns `[]` — a perfect score on the selection's PRIMARY key. An empty plan
is therefore *unfalsifiable*, and it beat every sibling that had real content
to critique. Measured across the two 2026-08-03 runs: **2 of 21 selections
(9.5%) chose a 0-subtask sample**, silently discarding the whole domain, with
every winner logging "0 issues".

Demoting the empty sample via the existing tiebreak cannot fix this — it wins
on the primary key, so no tiebreak is ever consulted. Hence a gate that runs
*before* scoring.

The gate is **relative, never absolute**: an empty `ready` plan is also the
planner's legitimate way to say "this domain has no work", which
`_detect_no_work` routes a run on. It is a defect only when a sibling sample
found work. The all-empty case must therefore pass through untouched — that
is what `test_all_empty_samples_are_all_kept` protects, and it is the one
behaviour a careless "just reject empty plans" fix would break.
"""
from __future__ import annotations


def _plan(n_subtasks: int = 1, status: str = "ready", **overrides) -> dict:
    """A plan dict shaped like real planner output.

    Subtasks carry `files_likely_touched`, which matters: against a `tmp_path`
    repo those paths do not exist, so `check_planner_output` raises
    `PHANTOM_PATH` for each. That is not fixture noise — it reproduces the
    incident's defining asymmetry, in which **any plan with real content
    accrues issues while an empty plan accrues none**, so the empty one wins
    the primary sort key. A fixture whose substantive plans scored a clean 0
    would let the gate's tests pass vacuously, since the larger sample would
    win on the existing tiebreak with or without the gate.
    """
    base = {
        "status": status,
        "subtasks": [
            {
                "id": f"feat-{i:03d}",
                "title": f"subtask {i}",
                "intent": "do the thing",
                "scope_note": "one change",
                "files_likely_touched": [f"src/module_{i}.ts"],
                "depends_on": [],
                "requires": [],
                "provides": [],
                "success_criteria_seed": "done",
                "size": "small",
                "investigation_notes": "",
            }
            for i in range(1, n_subtasks + 1)
        ],
    }
    base.update(overrides)
    return base


# ----- _planner_sample_is_empty_ready --------------------------------------

def test_empty_ready_plan_is_flagged(leerie):
    assert leerie._planner_sample_is_empty_ready(_plan(0)) is True


def test_non_empty_ready_plan_is_not_flagged(leerie):
    assert leerie._planner_sample_is_empty_ready(_plan(1)) is False


def test_empty_blocked_plan_is_not_flagged(leerie):
    """Scoped deliberately to `ready`. A `blocked` plan is a planner verdict
    the run must still see — `_schedule()` owns the all-blocked die(). Widening
    the gate to swallow it would convert a diagnosable stop into a silent
    one."""
    assert leerie._planner_sample_is_empty_ready(_plan(0, status="blocked")) \
        is False


def test_missing_keys_do_not_raise(leerie):
    assert leerie._planner_sample_is_empty_ready({}) is False
    assert leerie._planner_sample_is_empty_ready({"status": "ready"}) is True
    assert leerie._planner_sample_is_empty_ready({"subtasks": []}) is False


# ----- the gate, through the real selector ---------------------------------

def test_the_reported_failure_empty_no_longer_beats_substantive(
        leerie, tmp_path):
    """The measured incident shape: an empty sample scores 0 issues and wins
    the primary key over siblings with real content. After the gate it cannot
    be selected at all."""
    empty, small, large = _plan(0), _plan(13), _plan(16)
    best = leerie._select_best_planner_sample(
        [empty, small, large], tmp_path, "feature-implementation")
    assert best is not empty
    assert best["subtasks"], "a domain with work must not select an empty plan"


def test_falsifier_empty_sample_would_win_without_the_gate(leerie, tmp_path):
    """Anti-vacuity control. Proves the test above exercises the GATE rather
    than passing because the scorer happened to prefer the larger sample
    anyway: with the gate bypassed, the empty sample wins outright."""
    empty, large = _plan(0), _plan(16)
    scored = []
    for i, s in enumerate([empty, large]):
        issues = leerie.check_planner_output(
            s, tmp_path, "feature-implementation")
        scored.append((len(issues), -len(s.get("subtasks", [])), i, s))
    scored.sort()
    assert scored[0][3] is empty, (
        "if this fails the incident no longer reproduces and the gate's "
        "justification needs re-measuring")


def test_all_empty_samples_are_all_kept(leerie, tmp_path):
    """The load-bearing negative. Every sample empty means the domain really
    has no work, so the set must pass through and `_detect_no_work` must still
    be able to route on the result."""
    samples = [_plan(0), _plan(0), _plan(0)]
    best = leerie._select_best_planner_sample(
        samples, tmp_path, "feature-implementation")
    assert best is not None
    assert best["status"] == "ready"
    assert best["subtasks"] == []


def test_single_empty_sample_is_kept(leerie, tmp_path):
    """Single-sample mode has no sibling to compare against, so the empty
    plan is the domain's answer and must survive."""
    only = _plan(0)
    assert leerie._select_best_planner_sample(
        [only], tmp_path, "bug-fixing") is only


def test_all_valid_samples_are_unaffected(leerie, tmp_path):
    """Regression: with no empty samples the gate is a no-op and the existing
    criteria decide. `src/` is created so the paths resolve and every sample
    ties at 0 issues — which is what puts the subtask-count tiebreak in play
    at all."""
    (tmp_path / "src").mkdir()
    samples = [_plan(2), _plan(5), _plan(3)]
    best = leerie._select_best_planner_sample(
        samples, tmp_path, "feature-implementation")
    assert best in samples
    assert len(best["subtasks"]) == 5, "most subtasks still wins the tiebreak"


def test_selection_no_longer_penalises_plan_size(leerie, tmp_path):
    """The general case of the same defect the validity gate fixes at its
    extreme.

    Per-subtask ADVISORY findings (`PHANTOM_PATH` here, one per subtask) used
    to make issue count grow ~1:1 with plan size, so a larger plan lost on the
    primary key to a smaller one — a large plan had to be flawless to beat a
    small plan with one flaw. Ranking on gating findings only removes the
    coupling, and the subtask-count tiebreak then decides as intended.

    Note `src/` is deliberately NOT created here, so the paths are phantom and
    the advisory findings really do fire — otherwise this would pass with no
    advisory findings present at all and prove nothing."""
    small, large = _plan(2), _plan(5)
    assert leerie.check_planner_output(
        large, tmp_path, "feature-implementation"), (
        "fixture must actually produce findings or this test is vacuous")
    best = leerie._select_best_planner_sample(
        [small, large], tmp_path, "feature-implementation")
    assert best is large, (
        "advisory per-subtask findings must not count toward selection")


def test_gating_findings_still_decide_selection(leerie, tmp_path):
    """The control for the test above: a GATING finding must still lose a
    sample the ranking, or the severity split would have disarmed selection
    rather than de-biased it. `EMPTY_CRITERIA` is gating."""
    clean, broken = _plan(2), _plan(2)
    broken["subtasks"][0]["success_criteria_seed"] = ""
    best = leerie._select_best_planner_sample(
        [broken, clean], tmp_path, "feature-implementation")
    assert best is clean


def test_blocked_sample_still_selectable_against_an_empty_ready(
        leerie, tmp_path):
    """The gate drops the empty `ready`; the `blocked` sample is untouched and
    remains available for `_schedule()` to act on."""
    empty_ready, blocked = _plan(0), _plan(0, status="blocked")
    best = leerie._select_best_planner_sample(
        [empty_ready, blocked], tmp_path, "bug-fixing")
    assert best is blocked


# ----- per-sample score logging (P0.2) -------------------------------------

def test_roster_logs_every_ranked_sample(leerie, tmp_path, monkeypatch):
    """The winner line alone hid why it won. The roster must name each ranked
    sample's issue and subtask counts."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))
    leerie._select_best_planner_sample(
        [_plan(2), _plan(5)], tmp_path, "feature-implementation")
    roster = [ln for ln in lines if "ranked:" in ln]
    assert roster, "no roster line emitted"
    assert "#0(" in roster[0] and "#1(" in roster[0]
    assert "5s" in roster[0] and "2s" in roster[0]


def test_logged_indices_track_original_positions_after_a_drop(
        leerie, tmp_path, monkeypatch):
    """The gate can remove entries, and the surviving samples must keep their
    ORIGINAL indices — the logged number is cross-referenced against the
    per-sample worker sids (`planner-<domain>-s<N>`), so renumbering would
    point a reader at the wrong worker log."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))
    # index 0 is dropped by the gate; the survivors are original 1 and 2.
    leerie._select_best_planner_sample(
        [_plan(0), _plan(4), _plan(7)], tmp_path, "feature-implementation")
    roster = [ln for ln in lines if "ranked:" in ln]
    assert roster
    assert "#1(" in roster[0] and "#2(" in roster[0]
    assert "#0(" not in roster[0], "the dropped sample must not be ranked"


def test_drop_is_announced(leerie, tmp_path, monkeypatch):
    """A silent drop would look identical to a planner that simply produced
    fewer samples."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))
    leerie._select_best_planner_sample(
        [_plan(0), _plan(4)], tmp_path, "feature-implementation")
    assert any("dropped 1 empty sample" in ln for ln in lines)


def test_no_drop_message_when_nothing_was_dropped(leerie, tmp_path,
                                                  monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))
    leerie._select_best_planner_sample(
        [_plan(3), _plan(4)], tmp_path, "feature-implementation")
    assert not any("dropped" in ln for ln in lines)


def test_no_drop_message_when_every_sample_is_empty(leerie, tmp_path,
                                                    monkeypatch):
    """All-empty is a no-work verdict, not a drop — announcing a drop here
    would misdescribe what happened."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: lines.append(m))
    leerie._select_best_planner_sample(
        [_plan(0), _plan(0)], tmp_path, "feature-implementation")
    assert not any("dropped" in ln for ln in lines)
