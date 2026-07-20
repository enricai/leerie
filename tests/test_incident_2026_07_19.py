"""Incident reproduction harness for 2026-07-19
(``argv-e2big-and-coverage-freeze``): the single test module the
incident note's "Reproducing" section points to.

Builds the ~150KB, ~114-subtask payload from
``tests/fixtures/incident_2026_07_19/{shape.json,generate.py}`` — synthetic
and shape-matched to the measured per-field byte distribution, since the
real task file (an internal product audit) is deliberately not committed
— and pins that **both** root causes reproduce against pre-fix behavior
and are closed by the shipped fixes:

  - Root cause A (coverage freeze): ``check_task_file_coverage`` no
    longer fires a blocking ``LOW_COVERAGE`` issue on the harvested
    CLAUDE.md-shaped backtick+MUST headings (fixed by
    ``_is_uncoverable_convention_item`` /
    ``_dedup_frozen_coverage_issues``, bugfix-003).
  - Root cause B (argv E2BIG): no argv element ``claude_p``'s ``build()``
    constructs for the reconstructed reconciler payload exceeds
    ``MAX_ARG_STRLEN`` (131,071 bytes) — the prompt travels over stdin
    instead (bugfix-001/002).

Mechanism-level coverage for each half already exists in
``tests/test_task_file_coverage_freeze.py`` (root cause A, hand-crafted
headings) and ``tests/test_argv_stdin_transport.py`` /
``test_prompt_over_stdin.py`` / ``test_append_system_prompt_file.py``
(root cause B, uniform-filler payload). This module is the single
end-to-end pin the incident note itself references: it drives the same
two fixes through the *generated, shape-matched* incident payload rather
than hand-rolled fixtures, and reverting either fix turns the
corresponding assertion in this file red. No live ``claude`` binary
required.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys
import types
import unittest.mock as mock

import pytest

# Linux's per-argument ceiling (PAGE_SIZE * 32 on a 4KB-page kernel) —
# the external fact the fix routes around, not a leerie constant.
MAX_ARG_STRLEN = 131_071

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "incident_2026_07_19"


def _load_generate():
    """Imports tests/fixtures/incident_2026_07_19/generate.py directly
    (it is not on sys.path and is not a test_*.py module, so pytest never
    collects it — see pytest.ini's python_files)."""
    spec = importlib.util.spec_from_file_location(
        "incident_2026_07_19_generate", _FIXTURES_DIR / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generate():
    return _load_generate()


@pytest.fixture(scope="module")
def shape(generate):
    return generate.load_shape()


def _make_state():
    return types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )


def _run_claude_p_capturing(leerie, user_prompt: str, system_prompt: str):
    """Drives claude_p's real build() closure through a stubbed _invoke,
    capturing the constructed argv and stdin payload — mirrors
    test_argv_stdin_transport.py's / test_no_result_event_retry.py's
    stubbed-_invoke pattern (no live `claude` binary needed)."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin_data"] = stdin_data
        if "--append-system-prompt-file" in cmd:
            idx = cmd.index("--append-system-prompt-file") + 1
            captured["system_prompt_file_contents"] = (
                pathlib.Path(cmd[idx]).read_text())
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {
                    "dropped_requires": [], "dependency_edges": [],
                    "merged_subtasks": [], "renamed_requires": [],
                    "renamed_provides": [], "recommendation": "",
                    "unresolved": [], "notes": ""}}

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None), \
         mock.patch.object(
             leerie, "_append_system_prompt_file_supported", lambda: True):
        asyncio.run(leerie.claude_p(
            user_prompt, system_prompt,
            schema_key="reconciler", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=_make_state(), model="opus",
            sid="incident-2026-07-19",
        ))
    return captured


# ---------------------------------------------------------------------------
# Root cause B — argv E2BIG on the generated, shape-matched payload
# ---------------------------------------------------------------------------

class TestRootCauseB_ArgvE2BIG:
    def test_generated_payload_matches_measured_shape(self, generate, shape):
        """Sanity: generate.py actually reproduces the incident's
        measured component sizes and subtask count before any assertion
        about the fix is meaningful."""
        rp = shape["reconciler_payload"]
        task = generate.build_task(shape)
        assert len(task.encode()) == rp["task_bytes"]

        views = generate.build_subtask_views(shape)
        assert len(views) == rp["subtask_count"]
        assert (len(json.dumps(views, indent=rp["json_indent"]).encode())
                == rp["subtask_views_bytes"])

    def test_pre_fix_payload_would_exceed_max_arg_strlen(self, generate):
        """The load-bearing pre-fix fact: the reconstructed reconciler
        user_prompt, taken as a single string, is larger than
        MAX_ARG_STRLEN — exactly the shape that raised a raw execve
        E2BIG when it traveled as a positional argv element pre-fix.
        This assertion is about the payload itself (not the fix) and
        must hold regardless of which transport claude_p uses today —
        it is what makes the fix necessary in the first place."""
        user_prompt = generate.build_user_prompt()
        assert len(user_prompt.encode()) > MAX_ARG_STRLEN, (
            "the generated incident payload no longer exceeds "
            "MAX_ARG_STRLEN — shape.json's measured sizes must be "
            "restored, or this fixture stops reproducing root cause B")

    def test_no_argv_element_exceeds_max_arg_strlen_post_fix(
            self, leerie, generate):
        """The fix: with the full incident-shaped payload driven through
        claude_p's real build() closure, no argv element it constructs
        exceeds MAX_ARG_STRLEN — the oversized user_prompt travels over
        stdin instead. Reverting the stdin-transport fix (putting the
        prompt back as a positional argv element) turns this red."""
        user_prompt = generate.build_user_prompt()
        system_prompt = generate.build_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)

        for i, elem in enumerate(captured["cmd"]):
            assert len(elem.encode()) <= MAX_ARG_STRLEN, (
                f"argv element {i} ({elem[:80]!r}...) is "
                f"{len(elem.encode())} bytes, exceeding MAX_ARG_STRLEN "
                f"({MAX_ARG_STRLEN}) on the generated incident payload")

    def test_no_positional_prompt_on_argv(self, leerie, generate):
        user_prompt = generate.build_user_prompt()
        system_prompt = generate.build_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        cmd = captured["cmd"]

        assert cmd[0] == "claude"
        assert cmd[1] == "-p"
        assert cmd[2].startswith("--"), (
            f"expected a flag immediately after -p, got positional "
            f"{cmd[2]!r} — a positional prompt silently wins over stdin "
            "with no error (incident-verified)")
        assert not any(user_prompt in elem for elem in cmd)

    def test_prompt_reaches_child_via_stdin(self, leerie, generate):
        user_prompt = generate.build_user_prompt()
        system_prompt = generate.build_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        assert captured["stdin_data"] == user_prompt

    def test_append_system_prompt_routed_via_file(self, leerie, generate):
        user_prompt = generate.build_user_prompt()
        system_prompt = generate.build_system_prompt()

        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        assert captured["system_prompt_file_contents"] == system_prompt


# ---------------------------------------------------------------------------
# Root cause A — coverage-gate freeze on the generated CLAUDE.md-shaped
# headings
# ---------------------------------------------------------------------------

class TestRootCauseA_CoverageFreeze:
    def test_generated_headings_match_measured_item_count(
            self, leerie, generate, shape):
        claude_md_text = generate.build_claude_md_text(shape)
        extracted = [
            f"CLAUDE.md: {m.group(1).strip()}"
            for m in leerie.re.finditer(
                r'^#{3,6}\s+(.+)', claude_md_text, leerie.re.MULTILINE)
        ]
        assert len(extracted) == shape["coverage_gate"]["harvested_item_count"]

    def test_extraction_reproduces_the_incident_harvest(
            self, leerie, generate, shape, tmp_path):
        """End-to-end through the real extraction path: a task that
        incidentally mentions CLAUDE.md once (root cause A's trigger)
        against a repo containing a CLAUDE.md file shaped like the
        incident's harvested headings."""
        (tmp_path / "CLAUDE.md").write_text(generate.build_claude_md_text(shape))
        task = generate.build_task(shape)

        extracted = leerie.extract_task_file_structure(task, tmp_path)
        assert extracted is not None
        assert len(extracted) == shape["coverage_gate"]["harvested_item_count"]

    def test_pre_fix_shape_would_freeze_every_round(self, generate, shape):
        """The load-bearing pre-fix fact about the fixture itself: the
        harvested backtick+MUST headings cannot substring-match the
        non-matching plan text, at the same 15/15-style ratio the
        incident measured across 33/33 feedback rounds. This is a fact
        about the fixture shape, independent of the fix."""
        gate = shape["coverage_gate"]
        extracted = [f"CLAUDE.md: {h}" for h in
                     gate["uncoverable_backtick_must_headings"]]
        plan_text = generate.build_non_matching_plan_text()
        uncovered = [item for item in extracted
                     if item.split(": ", 1)[1].lower() not in plan_text]
        assert len(uncovered) == len(extracted), (
            "the generated backtick+MUST headings no longer reproduce "
            "an all-uncovered ratio against the non-matching plan text")

    def test_coverage_gate_excludes_the_uncoverable_subset_post_fix(
            self, leerie, generate, shape):
        """The fix's first guard: check_task_file_coverage no longer
        emits a blocking LOW_COVERAGE issue on the 3 backtick+MUST
        convention headings alone, even though every one is textually
        uncovered. Reverting bugfix-003's _is_uncoverable_convention_item
        turns this red."""
        gate = shape["coverage_gate"]
        extracted = [f"CLAUDE.md: {h}" for h in
                     gate["uncoverable_backtick_must_headings"]]

        plan_text = generate.build_non_matching_plan_text()
        subtasks = [{"title": "unrelated subtask", "intent": plan_text,
                     "investigation_notes": ""}]

        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert issues == [], (
            "the 3 backtick+MUST convention headings produced a "
            f"blocking LOW_COVERAGE issue post-fix: {issues}")

    def test_coverage_gate_does_not_freeze_across_repeated_rounds(
            self, leerie, generate, shape):
        """The incident's defining symptom, reproduced via the full
        harvested 15-item set (3 uncoverable + 12 other) exactly as
        phase_plan's per-category feedback loop would see it: the fix's
        second guard, _dedup_frozen_coverage_issues, stops re-emitting a
        LOW_COVERAGE issue once its ratio has repeated — even though the
        12 non-convention headings remain genuinely uncovered and the
        first round's issue still fires once. Reverting
        _dedup_frozen_coverage_issues turns this red (every round would
        re-emit the same frozen ratio, as measured: 33/33 in the
        incident run)."""
        gate = shape["coverage_gate"]
        extracted = [f"CLAUDE.md: {h}" for h in
                     (gate["uncoverable_backtick_must_headings"] +
                      gate["other_headings"])]
        plan_text = generate.build_non_matching_plan_text()
        subtasks = [{"title": "unrelated subtask", "intent": plan_text,
                     "investigation_notes": ""}]

        seen_ratios: set[str] = set()
        fired_rounds = 0
        for _ in range(gate["feedback_rounds_measured"] // 10):
            issues = leerie.check_task_file_coverage(extracted, subtasks)
            deduped = leerie._dedup_frozen_coverage_issues(
                issues, seen_ratios)
            if deduped:
                fired_rounds += 1

        assert fired_rounds <= 1, (
            f"the frozen ratio re-fired across {fired_rounds} rounds "
            "post-fix — _dedup_frozen_coverage_issues should suppress "
            "every repeat after the first")

    def test_genuinely_uncovered_item_still_gates(
            self, leerie, generate, shape):
        """Control: the fix must narrow the gate, not disable it — a
        genuinely uncovered, coverable item mixed into the generated
        heading set still produces LOW_COVERAGE signal."""
        gate = shape["coverage_gate"]
        extracted = ([f"CLAUDE.md: {h}" for h in
                      gate["uncoverable_backtick_must_headings"]] +
                     ["CLAUDE.md: Real spec item that should be covered",
                      "CLAUDE.md: Another real spec item, also uncovered"])
        plan_text = generate.build_non_matching_plan_text()
        subtasks = [{"title": "unrelated subtask", "intent": plan_text,
                     "investigation_notes": ""}]

        issues = leerie.check_task_file_coverage(extracted, subtasks)
        assert any("LOW_COVERAGE" in i for i in issues), (
            f"a genuinely uncovered coverable item must still gate, got: "
            f"{issues}")


# ---------------------------------------------------------------------------
# Both root causes together, on the single generated payload
# ---------------------------------------------------------------------------

class TestBothRootCausesComposeOnOnePayload:
    def test_both_fixes_hold_simultaneously(self, leerie, generate, shape,
                                            tmp_path):
        """The incident's own claim: the two fixes compose on one
        realistic payload. Runs both halves against the same generated
        task/shape.json-derived fixtures in a single test."""
        (tmp_path / "CLAUDE.md").write_text(generate.build_claude_md_text(shape))
        task = generate.build_task(shape)

        # Root cause A: coverage extraction + gate. The uncoverable
        # backtick+MUST subset alone (the harvest's most acute freeze
        # trigger) must not gate at all; the dedup guard then prevents
        # the full 15-item harvest from re-firing an unchanged ratio
        # across repeated rounds — see TestRootCauseA_CoverageFreeze for
        # the two guards pinned individually.
        extracted = leerie.extract_task_file_structure(task, tmp_path)
        assert extracted is not None
        gate = shape["coverage_gate"]
        uncoverable_only = [
            f"CLAUDE.md: {h}"
            for h in gate["uncoverable_backtick_must_headings"]]
        plan_text = generate.build_non_matching_plan_text()
        subtasks = [{"title": "unrelated subtask", "intent": plan_text,
                     "investigation_notes": ""}]
        issues = leerie.check_task_file_coverage(uncoverable_only, subtasks)
        assert issues == []

        # Root cause B: transport.
        user_prompt = generate.build_user_prompt(shape)
        system_prompt = generate.build_system_prompt(shape)
        assert len(user_prompt.encode()) > MAX_ARG_STRLEN
        captured = _run_claude_p_capturing(leerie, user_prompt, system_prompt)
        for elem in captured["cmd"]:
            assert len(elem.encode()) <= MAX_ARG_STRLEN
        assert captured["stdin_data"] == user_prompt
