"""`integration_judge` must not kill a run over a dropped *duplicate*.

DESIGN §8 *Location is not coverage*. A merge that drops a duplicate is
textually identical to one that drops the only copy — in both, content present
in a parent is absent from the result — so the parent diffs alone cannot tell
them apart.

Measured incident: the gate killed a run at wave 3 with 28 subtasks complete
and two waves integrated, over a dropped `describe` block whose assertions were
a strict *subset* of a separate test file written 4h45m earlier. The textual
claim was true; the behavioral claim was false. `dropped_change` is 10 of the
14 findings this gate has ever emitted, and it has no bypass flag.

The fix asks the judge to cite equivalent coverage in the merged tree and
downgrades a *cited* `dropped_change` to advisory. Two properties are
load-bearing and are pinned hardest here:

1. **The citation field is asked for, never `required`.** Requiring a judge
   field has three times in this codebase produced a worker emitting no
   schema-valid output at all (`confidence` on 10 schemas; `artifact_paths`
   dropping `plan_overlap_judge` to 40.9% validity; `severity` invalidating
   9/66 `wiring_judge` runs). A gate that never runs catches nothing.
2. **Absence gates.** The downgrade is the narrow exception, not the default,
   because this is the last defence against a silently-lossy merge.
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap

import pytest
from jsonschema import Draft7Validator, validate


def _defect(**over) -> dict:
    d = {
        "kind": "dropped_change",
        "concrete_scenario": "feat-005's assertions are absent from the result.",
        "location": "tests/export_route_test.py",
        "why_broken": "Those assertions no longer run from this file.",
    }
    d.update(over)
    return d


class TestSchemaShape:
    """The field must exist and must NOT be required — see the module
    docstring for the three measured regressions behind that split."""

    def test_coverage_elsewhere_is_a_property(self, leerie):
        item = leerie.SCHEMAS["integration_judge"]["properties"]["defects"]["items"]
        assert "coverage_elsewhere" in item["properties"]

    def test_coverage_elsewhere_is_NOT_required(self, leerie):
        item = leerie.SCHEMAS["integration_judge"]["properties"]["defects"]["items"]
        assert "coverage_elsewhere" not in item["required"], (
            "requiring a judge field has three times produced a worker "
            "emitting no schema-valid output at all; a gate that never runs "
            "catches nothing (DESIGN §8)")

    def test_preexisting_required_fields_are_unchanged(self, leerie):
        item = leerie.SCHEMAS["integration_judge"]["properties"]["defects"]["items"]
        assert set(item["required"]) == {
            "kind", "concrete_scenario", "location", "why_broken"}

    def test_schema_is_valid_and_serializable(self, leerie):
        s = leerie.SCHEMAS["integration_judge"]
        Draft7Validator.check_schema(s)
        json.loads(json.dumps(s))

    def test_defect_without_the_field_still_validates(self, leerie):
        """The whole point of not requiring it."""
        validate({"merge_reviewed": True, "defects": [_defect()],
                  "rationale": "x"}, leerie.SCHEMAS["integration_judge"])

    def test_defect_with_a_full_citation_validates(self, leerie):
        validate({"merge_reviewed": True,
                  "defects": [_defect(coverage_elsewhere={
                      "searched": True, "file": "tests/sibling_test.py",
                      "assertion": "test_header_row"})],
                  "rationale": "x"}, leerie.SCHEMAS["integration_judge"])

    @pytest.mark.parametrize("bad", [
        {"searched": True, "file": "", "assertion": "test_x"},
        {"searched": True, "file": "tests/sibling_test.py", "assertion": ""},
    ])
    def test_blank_citation_still_parses_but_is_rejected_by_code(
            self, leerie, bad, tmp_path):
        """Blankness is enforced in `_coverage_citation_clears`, NOT by the
        schema — see the `minLength` test below for why the schema must not
        do it. The schema accepts a blank citation; the gate ignores it."""
        validate({"merge_reviewed": True,
                  "defects": [_defect(coverage_elsewhere=bad)],
                  "rationale": "x"}, leerie.SCHEMAS["integration_judge"])
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere=bad), tmp_path) is None

    def test_citation_strings_carry_no_minLength(self, leerie):
        """A `minLength` on an OPTIONAL property is a trap:
        `--dangerously-force-strict-output` forces every property into
        `required`, and the grammar could then emit `""` — a value the CLI's
        own validator rejects, trading a compile error for a validation
        error. Pinned by `tests/test_strict_output_proxy.py`'s
        forcing-a-field-never-makes-a-trivial-value-illegal invariant, which
        this schema violated on first draft."""
        item = leerie.SCHEMAS["integration_judge"]["properties"]["defects"]["items"]
        cite = item["properties"]["coverage_elsewhere"]["properties"]
        for k in ("file", "assertion"):
            assert "minLength" not in cite[k], (
                f"{k} carries minLength on an optional field — breaks the "
                "strict-output grammar invariant")


class TestCitationClears:
    """`_coverage_citation_clears` — the mechanical half. A citation buys a
    downgrade only when it names a file that really exists in the merged
    tree."""

    @pytest.fixture
    def tree(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "sibling_test.py").write_text("def test_x(): ...")
        return tmp_path

    def test_real_citation_clears(self, leerie, tree):
        got = leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_header_row"}), tree)
        assert got == "tests/sibling_test.py: test_header_row"

    def test_leading_dot_slash_is_normalized(self, leerie, tree):
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "file": "./tests/sibling_test.py", "assertion": "test_x"}),
            tree) == "tests/sibling_test.py: test_x"

    def test_no_citation_gates(self, leerie, tree):
        assert leerie._coverage_citation_clears(_defect(), tree) is None

    def test_hallucinated_path_gates(self, leerie, tree):
        """The load-bearing anti-gaming property: a judge cannot talk its way
        out of a finding by naming a file that is not there."""
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "searched": True, "file": "tests/does_not_exist.py",
                "assertion": "test_x"}), tree) is None

    def test_searched_true_alone_gates(self, leerie, tree):
        """"I looked and found nothing" is the gating answer, not a bypass."""
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={"searched": True}), tree) is None

    @pytest.mark.parametrize("cite", [
        {"file": "", "assertion": "test_x"},
        {"file": "tests/sibling_test.py", "assertion": ""},
        {"file": "tests/sibling_test.py", "assertion": "   "},
        {"file": "   ", "assertion": "test_x"},
    ])
    def test_blank_halves_gate(self, leerie, tree, cite):
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere=cite), tree) is None

    def test_non_dict_citation_gates(self, leerie, tree):
        for junk in ("tests/sibling_test.py", ["a"], 3, None):
            assert leerie._coverage_citation_clears(
                _defect(coverage_elsewhere=junk), tree) is None

    def test_directory_is_not_a_file(self, leerie, tree):
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "file": "tests", "assertion": "test_x"}), tree) is None

    def test_traversal_outside_the_tree_gates(self, leerie, tree, tmp_path):
        """`_normalize_artifact_path` strips only leading `./` and `/`, so a
        `..` segment survives it. A citation must not reach outside the
        merged tree to find its evidence."""
        outside = tmp_path.parent / "outside_test.py"
        outside.write_text("x")
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "file": f"../{outside.name}", "assertion": "test_x"}),
            tree) is None

    @pytest.mark.parametrize("kind", [
        "reintroduced_conflict", "call_site_mismatch",
        "semantic_regression", "incomplete_resolution"])
    def test_only_dropped_change_is_eligible(self, leerie, tree, kind):
        """The other four kinds describe breakage the merge INTRODUCED;
        coverage living elsewhere cannot excuse them."""
        assert leerie._coverage_citation_clears(
            _defect(kind=kind, coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_x"}), tree) is None


class TestIncidentReplay:
    """The measured shape, end to end through the helper."""

    def test_dropped_duplicate_is_downgraded(self, leerie, tmp_path):
        (tmp_path / "tests").mkdir()
        sibling = tmp_path / "tests" / "export_route_shape_test.py"
        sibling.write_text(
            "def test_export_header_row_and_rejects_unauthenticated(): ...")
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "searched": True,
                "file": "tests/export_route_shape_test.py",
                "assertion":
                    "test_export_header_row_and_rejects_unauthenticated"}),
            tmp_path)

    def test_genuinely_lossy_merge_still_gates(self, leerie, tmp_path):
        """ANTI-VACUITY. If this ever returns a citation, the fix has traded a
        false positive for a false negative on the one gate with no bypass."""
        (tmp_path / "tests").mkdir()
        assert leerie._coverage_citation_clears(
            _defect(coverage_elsewhere={
                "searched": True, "file": "tests/export_route_shape_test.py",
                "assertion": "test_header_row"}), tmp_path) is None


class TestWiring:
    """The helper is inert unless `_check_integration` consults it. That
    function is nested inside `integrate_wave`, so this is source-coupled."""

    def test_gate_consults_the_helper(self, leerie):
        """The citation check lives in `_partition_integration_defects`, which
        `integrate_wave` must reach on both its call paths."""
        part = inspect.getsource(leerie._partition_integration_defects)
        assert "_coverage_citation_clears(" in part, (
            "the downgrade is a no-op unless the partition calls it")
        wave = inspect.getsource(leerie.integrate_wave)
        assert wave.count("_partition_integration_defects(") >= 2, (
            "both the `check=` adapter and the post-loop site must use it, or "
            "the two paths can disagree")

    def test_downgrade_short_circuits_before_the_issue_is_appended(self, leerie):
        """A cleared defect must `continue`, not fall through and gate
        anyway — the fix would be inert but look present."""
        src = inspect.getsource(leerie._partition_integration_defects)
        i = src.index("_coverage_citation_clears(")
        window = src[i:i + 500]
        assert "continue" in window
        assert window.index("continue") < window.index("gating.append")

    def test_helper_is_module_level_not_nested(self, leerie):
        """Module-level so it is directly testable; `test_no_dead_functions`
        also requires it to have a real caller, which the guard above pins."""
        assert callable(getattr(leerie, "_coverage_citation_clears", None))

    def test_anti_gaming_scenario_location_filter_survives(self, leerie):
        """The pre-existing filter must not have been displaced by the new
        clause when the logic moved into the partition helper."""
        src = inspect.getsource(leerie._partition_integration_defects)
        assert "if not scenario or not location:" in src

    def test_gate_still_dies_on_remaining_defects(self, leerie):
        """The downgrade narrows the gate; it must not have removed it."""
        src = inspect.getsource(leerie.integrate_wave)
        assert "integration gate found behavioral defect(s)" in src


class TestPromptAsksForIt:
    """§12: the prompt is advisory, the code enforces. But a field nothing
    asks for is never filled, so the ask must exist."""

    def test_prompt_requests_the_citation(self):
        import pathlib
        p = (pathlib.Path(__file__).resolve().parent.parent
             / "prompts" / "integration_judge.md").read_text()
        assert "coverage_elsewhere" in p
        assert "merged tree" in p

    def test_prompt_states_that_absence_gates(self):
        import pathlib
        p = (pathlib.Path(__file__).resolve().parent.parent
             / "prompts" / "integration_judge.md").read_text().lower()
        assert "gates" in p and "does not exist" in p


class TestGateExecutes:
    """RUN the real gate function, don't just read its source.

    Every other test in this file either exercises `_coverage_citation_clears`
    in isolation or asserts on `integrate_wave`'s *source text*. Neither
    executes `_check_integration`, which is where the two are joined — and
    `integrate_wave` is a large async function no unit test drives, so the
    branch would otherwise ship unexecuted by anything.

    That is exactly how the 0.10.0 coverage-gate bug shipped: every test
    stubbed the layer above, so a `claude_p` call that raised `TypeError` on
    EVERY invocation survived a whole release while the gate's own broad
    `except Exception` logged it as a clean advisory degrade. Source-coupling
    cannot see a runtime error; only running the code can.

    So: lift `_check_integration` out of its enclosing function by AST and
    execute it against a synthetic closure scope.
    """

    @staticmethod
    def _extract(leerie, repo_root, sid="feat-014"):
        fn = ast.parse(
            textwrap.dedent(inspect.getsource(leerie.integrate_wave))).body[0]
        found = [n for n in ast.walk(fn)
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "_check_integration"]
        assert found, (
            "ANTI-VACUITY: _check_integration not found inside integrate_wave "
            "— the harness would silently test nothing")
        mod = ast.Module(body=[found[0]], type_ignores=[])
        ast.fix_missing_locations(mod)
        logs: list[str] = []
        ns = {"log": logs.append,
              "_coverage_citation_clears": leerie._coverage_citation_clears,
              "_partition_integration_defects":
                  leerie._partition_integration_defects,
              "repo_root": repo_root, "sid": sid}
        exec(compile(mod, "<extracted>", "exec"), ns)
        return ns["_check_integration"], logs

    @pytest.fixture
    def gate(self, leerie, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "sibling_test.py").write_text(
            "def test_header_row(): ...")
        check, logs = self._extract(leerie, tmp_path)
        return check, logs

    def test_uncited_dropped_change_gates(self, gate):
        check, logs = gate
        assert len(check({"defects": [_defect()]})) == 1

    def test_cited_and_present_is_downgraded(self, gate):
        check, _ = gate
        assert check({"defects": [_defect(coverage_elsewhere={
            "searched": True, "file": "tests/sibling_test.py",
            "assertion": "test_header_row"})]}) == []

    def test_the_adapter_is_pure(self, gate):
        """REGRESSION PIN. `_check_integration` is invoked TWICE per judge
        result — once by `_run_checked_loop` as its `check=`, once again
        post-loop to decide the `die()` — and up to `judgment_check_rounds`+1
        times when a WorkerError retry re-invokes. A `log()` inside it emitted
        every advisory 2-4 times; the first version of this file shipped that
        bug, and the test that should have caught it asserted `len(logs) == 1`
        after a SINGLE call, which is precisely the shape that hides a
        double-call.

        Call it repeatedly, as production does, and assert nothing is
        emitted."""
        check, logs = gate
        payload = {"defects": [
            _defect(),
            _defect(location="b.py", coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_header_row"}),
        ]}
        first = check(payload)
        for _ in range(4):
            assert check(payload) == first, "the adapter must be deterministic"
        assert logs == [], (
            "_check_integration must be side-effect free — it runs 2-4 times "
            "per judge result, so a log() here duplicates every advisory")

    def test_cited_but_absent_still_gates(self, gate):
        """The anti-gaming property, exercised through the real gate rather
        than the helper alone. If this ever passes, a hallucinated citation
        can silence the only merge check with no operator bypass."""
        check, _ = gate
        assert len(check({"defects": [_defect(coverage_elsewhere={
            "searched": True, "file": "tests/NOPE.py",
            "assertion": "test_header_row"})]})) == 1

    def test_citation_on_a_non_dropped_change_still_gates(self, gate):
        check, _ = gate
        assert len(check({"defects": [_defect(
            kind="call_site_mismatch", coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_header_row"})]})) == 1

    def test_preexisting_anti_gaming_filter_still_runs(self, gate):
        """The new clause must not have displaced the scenario/location
        filter that ran before it."""
        check, _ = gate
        assert check({"defects": [_defect(concrete_scenario="")]}) == []
        assert check({"defects": [_defect(location="  ")]}) == []

    def test_mixed_batch_keeps_only_the_uncleared(self, gate):
        """The realistic shape: one real defect plus one dropped duplicate."""
        check, _ = gate
        issues = check({"defects": [
            _defect(location="a.py"),
            _defect(location="b.py", coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_header_row"}),
        ]})
        assert len(issues) == 1 and "a.py" in issues[0]


class TestPartition:
    """`_partition_integration_defects` — where both filters actually live,
    and the only place the advisory text is produced."""

    @pytest.fixture
    def tree(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "sibling_test.py").write_text("def t(): ...")
        return tmp_path

    def test_returns_both_lists_in_one_pass(self, leerie, tree):
        gating, advisory = leerie._partition_integration_defects({"defects": [
            _defect(location="a.py"),
            _defect(location="b.py", coverage_elsewhere={
                "searched": True, "file": "tests/sibling_test.py",
                "assertion": "test_header_row"}),
        ]}, tree)
        assert len(gating) == 1 and "a.py" in gating[0]
        assert len(advisory) == 1 and "b.py" in advisory[0]
        assert "tests/sibling_test.py" in advisory[0], (
            "the advisory must name where the coverage was found, or it is "
            "unreviewable")

    def test_advisory_is_empty_when_nothing_is_cited(self, leerie, tree):
        gating, advisory = leerie._partition_integration_defects(
            {"defects": [_defect()]}, tree)
        assert len(gating) == 1 and advisory == []

    def test_anti_gaming_drops_from_both_lists(self, leerie, tree):
        """A defect with no concrete scenario is neither gating nor advisory —
        it is not a finding at all."""
        gating, advisory = leerie._partition_integration_defects(
            {"defects": [_defect(concrete_scenario="")]}, tree)
        assert gating == [] and advisory == []

    def test_empty_and_malformed_input(self, leerie, tree):
        assert leerie._partition_integration_defects({}, tree) == ([], [])
        assert leerie._partition_integration_defects(
            {"defects": None}, tree) == ([], [])
        assert leerie._partition_integration_defects(
            {"defects": ["not-a-dict", 7]}, tree) == ([], [])

    def test_gating_text_keeps_the_INTEGRATION_DEFECT_prefix(self, leerie, tree):
        """The die() message and any log scraping depend on this prefix."""
        gating, _ = leerie._partition_integration_defects(
            {"defects": [_defect()]}, tree)
        assert gating[0].startswith("INTEGRATION_DEFECT (dropped_change)")


class TestAdvisoryIsLoggedExactlyOnce:
    """The other half of the purity fix: the caller must still surface
    advisories, or the downgrade becomes silent."""

    def test_post_loop_site_logs_the_advisories(self, leerie):
        src = inspect.getsource(leerie.integrate_wave)
        i = src.index("remaining_defects, advisories = ")
        window = src[i:i + 400]
        assert "for a in advisories:" in window and "log(" in window, (
            "advisories must be logged at the single post-loop site")

    def test_adapter_body_contains_no_log_call(self, leerie):
        """Source-coupled twin of `test_the_adapter_is_pure`: catches a `log()`
        reintroduced on a branch the behavioural test happens not to hit."""
        fn = ast.parse(textwrap.dedent(
            inspect.getsource(leerie.integrate_wave))).body[0]
        ci = [n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef)
              and n.name == "_check_integration"][0]
        calls = [n.func.id for n in ast.walk(ci)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert "log" not in calls, (
            "_check_integration runs 2-4 times per judge result; a log() here "
            "duplicates every advisory line")

    def test_partition_helper_itself_never_logs(self, leerie):
        calls = [n.func.id for n in ast.walk(ast.parse(textwrap.dedent(
                    inspect.getsource(leerie._partition_integration_defects))))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert "log" not in calls and "print" not in calls
