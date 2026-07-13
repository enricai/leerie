"""Tests for the base-tree health baseline surface (DESIGN §9
*Base-tree health baseline*, findings F2 + F4):

  - `_format_baseline_section`  — conformer prompt BASELINE: line
  - `_base_health_payload`      — pr_writer payload base_health field
  - `_record_run_health`        — run.json.health (slowest worker +
                                  truncation count), merged with base_suite

These are pure/deterministic helpers; a lightweight State stub with
`run_dir` + `data` is enough.
"""
from __future__ import annotations

import inspect
import json
import types


def _st(tmp_path, conformance=None):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    return types.SimpleNamespace(
        run_dir=run_dir,
        data={"conformance": conformance} if conformance is not None else {},
    )


# --- _format_baseline_section -------------------------------------------

def test_baseline_section_none_when_absent(leerie):
    assert leerie._format_baseline_section(None) is None
    assert leerie._format_baseline_section({}) is None


def test_baseline_section_green(leerie):
    baseline = {"axes": {
        "build": {"ran": True, "measured": True, "passed": True},
        "lint": {"ran": False, "measured": False, "passed": None},
        "tests": {"ran": True, "measured": True, "passed": True},
    }}
    out = leerie._format_baseline_section(baseline)
    assert "BASELINE:" in out
    assert "GREEN" in out
    # A green base attributes any failure to the run.
    assert "introduced by" in out


def test_baseline_section_red_lists_axes(leerie):
    baseline = {"axes": {
        "build": {"ran": True, "measured": True, "passed": True},
        "lint": {"ran": True, "measured": True, "passed": False,
                 "summary": "eslint: 3 problems"},
        "tests": {"ran": True, "measured": True, "passed": False,
                  "summary": "2 failed, 100 passed"},
    }}
    out = leerie._format_baseline_section(baseline)
    assert "already RED" in out
    assert "lint" in out and "tests" in out
    assert "build" not in out.split("RED on")[1].split(".")[0]
    # pre-existing summaries surfaced so the conformer can match them
    assert "eslint: 3 problems" in out
    assert "2 failed, 100 passed" in out


# --- _runner_missing ----------------------------------------------------

def test_runner_missing_detects_command_not_found(leerie):
    assert leerie._runner_missing("bash: line 1: pytest: command not found")
    assert leerie._runner_missing("No such file or directory")
    assert not leerie._runner_missing("2 failed, 100 passed")
    assert not leerie._runner_missing("")


# --- _format_baseline_section: unmeasurable axis -------------------------

def test_baseline_section_unmeasurable_not_folded_into_green(leerie):
    """An axis whose runner was missing (measured=False) must NOT read as
    GREEN and must NOT read as RED — it is surfaced as 'could not measure'
    so the conformer attributes failures itself instead of re-deriving the
    base destructively."""
    baseline = {"axes": {
        "build": {"ran": False, "passed": None},
        "lint": {"ran": False, "passed": None},
        "tests": {"ran": True, "measured": False, "passed": None,
                  "summary": "bash: line 1: pytest: command not found"},
    }, "red_axes": []}
    out = leerie._format_baseline_section(baseline)
    assert "COULD NOT be measured" in out
    assert "tests" in out
    # It must not claim the base was RED on tests (no usable delta)...
    assert "already RED" not in out
    # ...must NOT falsely claim GREEN when nothing was actually measured
    # (the /code-review finding — a vacuous all-clear is the exact framing
    # this baseline exists to avoid)...
    assert "GREEN" not in out
    # ...and must steer the conformer away from the destructive re-derivation.
    assert "check out or reset" in out


def test_baseline_section_green_only_when_an_axis_actually_passed(leerie):
    """GREEN is claimed only for a genuine measured pass. A mix of one
    measured-green axis and one unmeasurable axis still says GREEN (for the
    measured one) plus the 'could not measure' caveat; but all-unmeasurable
    never says GREEN."""
    mixed = {"axes": {
        "build": {"ran": True, "measured": True, "passed": True},
        "tests": {"ran": True, "measured": False, "passed": None},
    }}
    out = leerie._format_baseline_section(mixed)
    assert "GREEN" in out and "COULD NOT be measured" in out

    all_unmeasured = {"axes": {
        "tests": {"ran": True, "measured": False, "passed": None},
    }}
    out2 = leerie._format_baseline_section(all_unmeasured)
    assert "GREEN" not in out2


def test_baseline_section_measured_is_mandatory_no_legacy_default(leerie):
    """`measured` is a mandatory field (no legacy support): an axis dict
    without it is NOT treated as a measured pass/fail. A `passed: False`
    axis missing `measured` is therefore not surfaced as RED — every real
    axis dict from capture_conformance_baseline always carries the field."""
    baseline = {"axes": {
        "tests": {"ran": True, "passed": False,
                  "summary": "2 failed, 100 passed"},
    }}
    out = leerie._format_baseline_section(baseline)
    assert "already RED" not in out


# --- _base_health_payload -----------------------------------------------

def test_base_health_payload_none_without_baseline(leerie, tmp_path):
    st = _st(tmp_path)
    assert leerie._base_health_payload(st) is None


def test_base_health_payload_green(leerie, tmp_path):
    st = _st(tmp_path, conformance={"_baseline": {"axes": {
        "build": {"ran": True, "measured": True, "passed": True},
        "lint": {"ran": True, "measured": True, "passed": True},
        "tests": {"ran": True, "measured": True, "passed": True},
    }}})
    out = leerie._base_health_payload(st)
    assert out["base_status"] == "green"
    assert out["base_red_axes"] == []


def test_base_health_payload_red(leerie, tmp_path):
    st = _st(tmp_path, conformance={"_baseline": {"axes": {
        "build": {"ran": True, "measured": True, "passed": True},
        "lint": {"ran": True, "measured": True, "passed": True},
        "tests": {"ran": True, "measured": True, "passed": False},
    }}})
    out = leerie._base_health_payload(st)
    assert out["base_status"] == "red"
    assert out["base_red_axes"] == ["tests"]
    assert out["axes"]["tests"]["passed"] is False


def test_base_health_payload_unmeasurable_axis_not_red(leerie, tmp_path):
    """Regression: an unmeasurable axis (runner missing) must NOT colour
    base_status red — it carries no verdict. Mirrors the same measured-aware
    rule in capture_conformance_baseline.red_axes and
    _format_baseline_section, so the PR body doesn't show a false-RED base."""
    st = _st(tmp_path, conformance={"_baseline": {"axes": {
        "build": {"ran": False, "measured": False, "passed": None},
        "lint": {"ran": False, "measured": False, "passed": None},
        "tests": {"ran": True, "measured": False, "passed": None,
                  "summary": "bash: line 1: pytest: command not found"},
    }}})
    out = leerie._base_health_payload(st)
    assert out["base_status"] == "green"
    assert out["base_red_axes"] == []
    assert out["axes"]["tests"]["measured"] is False


# --- _record_run_health -------------------------------------------------

def _write_result_log(logs_dir, sid, duration_ms, terminal="completed"):
    rec = {"type": "result", "subtype": "success",
           "duration_ms": duration_ms, "terminal_reason": terminal}
    (logs_dir / f"{sid}.log").write_text(json.dumps(rec) + "\n")


def test_record_run_health_picks_slowest_and_counts_truncation(
        leerie, tmp_path):
    st = _st(tmp_path)
    logs = st.run_dir / "logs"
    _write_result_log(logs, "feat-001", 60000)              # 1 min
    _write_result_log(logs, "feat-002", 600000)             # 10 min slowest
    _write_result_log(logs, "test-003", 120000, "max_turns")  # truncated
    leerie._record_run_health(st)
    health = json.loads((st.run_dir / "run.json").read_text())["health"]
    assert health["slowest_worker_sid"] == "feat-002"
    assert health["slowest_worker_min"] == 10.0
    assert health["truncated_worker_count"] == 1


def test_record_run_health_preserves_base_suite(leerie, tmp_path):
    st = _st(tmp_path)
    # baseline wrote base_suite first
    (st.run_dir / "run.json").write_text(json.dumps(
        {"health": {"base_suite": {"status": "red", "red_axes": ["tests"]}}}))
    _write_result_log(st.run_dir / "logs", "feat-001", 30000)
    leerie._record_run_health(st)
    health = json.loads((st.run_dir / "run.json").read_text())["health"]
    assert health["base_suite"] == {"status": "red", "red_axes": ["tests"]}
    assert health["slowest_worker_sid"] == "feat-001"


def test_record_run_health_no_logs_dir_is_noop(leerie, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    st = types.SimpleNamespace(run_dir=run_dir, data={})
    leerie._record_run_health(st)  # must not raise
    assert not (run_dir / "run.json").exists()


# --- wiring seams (source-coupling; the fix is inert without them) -------

def test_phase_execute_calls_baseline_gated_on_skip(leerie):
    """phase_execute must call capture_conformance_baseline, gated on
    skip_base_baseline. Silent removal disables F2 entirely."""
    src = inspect.getsource(leerie.phase_execute)
    assert "capture_conformance_baseline(" in src, (
        "phase_execute must invoke capture_conformance_baseline() — the "
        "base-tree health baseline (DESIGN §9) stops firing without it.")
    assert 'skip_base_baseline' in src, (
        "the baseline call must be gated on st.data['skip_base_baseline'] "
        "so --skip-base-baseline actually skips it.")
    # The call must be inside a non-fatal guard (advisory phase).
    call_pos = src.index("capture_conformance_baseline(")
    try_pos = src.rindex("try:", 0, call_pos)
    except_pos = src.index("except Exception", call_pos)
    assert try_pos < call_pos < except_pos, (
        "capture_conformance_baseline() must sit inside a try/except "
        "Exception guard — a baseline glue error must never block the run.")


def test_both_conformers_inject_baseline_section(leerie):
    """run_conformer and run_final_conformance must both append the
    BASELINE: section so the conformer scopes residuals to the delta."""
    for fn in (leerie.run_conformer, leerie.run_final_conformance):
        src = inspect.getsource(fn)
        assert "_format_baseline_section(" in src, (
            f"{fn.__name__} must inject _format_baseline_section() into the "
            "conformer prompt — without it the BASELINE: context is lost "
            "and the conformer falls back to self-judging pre-existing.")


def test_phase_finalize_records_run_health(leerie):
    """phase_finalize must call _record_run_health (F4). Silent removal
    drops run.json.health."""
    src = inspect.getsource(leerie.phase_finalize)
    assert "_record_run_health(" in src, (
        "phase_finalize must invoke _record_run_health() — run.json.health "
        "(slowest worker + truncation count) stops populating without it.")


def test_baseline_maps_tests_axis_to_resolve_blt_test_key(leerie):
    """Regression: resolve_blt keys the test command "test" (singular),
    but the baseline stores/reports the axis as "tests" (plural, matching
    the conformer result shape). capture_conformance_baseline must map
    "tests" -> blt["test"], else the tests axis silently never runs.

    Pins the mapping in source so a future refactor can't reintroduce the
    `blt.get("tests")` (always-None) bug."""
    src = inspect.getsource(leerie.capture_conformance_baseline)
    # The command lookup must go through the axis->cmd-key map, not a bare
    # blt.get(axis) which would return None for the "tests" axis.
    assert "_AXIS_CMD_KEY" in src, (
        "capture_conformance_baseline must map the 'tests' axis to "
        "resolve_blt's 'test' key — a bare blt.get('tests') is always None "
        "and silently skips the test suite.")
    assert '"tests": "test"' in src, (
        "the axis-name->command-key map must send 'tests' to 'test'.")
