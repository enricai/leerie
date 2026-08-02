"""Tests for migration-surface completeness checks (DESIGN §5).

Two layers:
1. _check_migration_surface (intra-domain, CRITIC-enforced via
   check_planner_output) — greps for old-pattern call sites.
2. warn_layer_gaps (cross-domain, advisory) — schema-without-seed
   and env-provider-without-template heuristics.
"""
from __future__ import annotations


def _conf(**axes: float) -> dict:
    return {**axes, "basis": "test", "falsifiers_tested": [],
            "contradictions_reconciled": [], "gap_to_close": {}}


# --- _check_migration_surface --------------------------------------------- #

class TestCheckMigrationSurface:
    """The check greps patterns the planner DECLARED in `migration_targets`.

    It used to regex `intent` + `investigation_notes` for phrases like
    "replaces direct X" and grep whatever token followed the verb. That
    mined ordinary English out of any sentence describing replacement:
    measured on run 19a70d96, all 27 extractions were stopwords — 'with'
    (grepping to 332 files), 'both' (178), 'task' (168) — and every
    resulting issue was false. The tests below that used to drive prose
    now declare the target instead; `TestProseSignalAbsent` pins that the
    prose path cannot return.
    """

    def _sub(self, sid="refactor-001", *, targets=None, files=(), intent="x"):
        s = {
            "id": sid, "title": "t", "intent": intent,
            "investigation_notes": "",
            "files_likely_touched": list(files),
            "depends_on": [], "size": "small",
            "success_criteria_seed": "check",
        }
        if targets is not None:
            s["migration_targets"] = targets
        return s

    def test_declared_target_with_uncovered_files_is_flagged(
            self, leerie, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text(
                "const id = legacy.session.tenantKey;\n")
        (src / "seam.ts").write_text("export function resolveTenantKey() {}")

        subtasks = [self._sub(
            targets=[{"old_pattern": "legacy.session.tenantKey",
                      "replacement": "resolveTenantKey()",
                      "is_real_identifier": True}],
            files=["src/seam.ts", "src/route0.ts"])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)
        assert "legacy.session.tenantKey" in issues[0]

    def test_covered_migration_clean(self, leerie, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(10):
            (src / f"route{i}.ts").write_text(
                "const id = legacy.session.tenantKey;\n")

        subtasks = [self._sub(
            targets=[{"old_pattern": "legacy.session.tenantKey",
                      "replacement": "resolveTenantKey()",
                      "is_real_identifier": True}],
            files=[f"src/route{i}.ts" for i in range(10)])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert not any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)

    def test_small_uncovered_ignored(self, leerie, tmp_path):
        """Under the threshold, an uncovered remainder is not worth a retry."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(3):
            (src / f"route{i}.ts").write_text(
                "const id = legacy.session.tenantKey;\n")

        subtasks = [self._sub(
            targets=[{"old_pattern": "legacy.session.tenantKey",
                      "replacement": "resolveTenantKey()",
                      "is_real_identifier": True}])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert not any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)

    def test_no_declared_target_is_clean(self, leerie, tmp_path):
        """The common case: most subtasks replace nothing and omit the
        field entirely."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text("const x = 1;\n")

        subtasks = [self._sub("feat-001", files=["src/route0.ts"],
                              intent="adds a new login endpoint")]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert not any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)

    def test_several_targets_each_checked(self, leerie, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"a{i}.ts").write_text("use(oldHelper);\n")
            (src / f"b{i}.ts").write_text("use(legacyToast);\n")

        subtasks = [self._sub(targets=[
            {"old_pattern": "oldHelper", "replacement": "newHelper",
             "is_real_identifier": True},
            {"old_pattern": "legacyToast", "replacement": "useToast",
             "is_real_identifier": True},
        ])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        flagged = [i for i in issues if "UNCOVERED_MIGRATION_SURFACE" in i]
        assert len(flagged) == 2
        assert any("oldHelper" in i for i in flagged)
        assert any("legacyToast" in i for i in flagged)

    def test_blank_and_malformed_targets_are_skipped(self, leerie, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"f{i}.ts").write_text("use(oldHelper);\n")

        subtasks = [self._sub(targets=[
            {"old_pattern": "   ", "replacement": "x",
             "is_real_identifier": True},
            "not-a-dict",
            {"replacement": "missing old_pattern",
             "is_real_identifier": True},
        ])]
        assert leerie._check_migration_surface(subtasks, tmp_path) == []

    def test_wired_through_check_planner_output(self, leerie, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text(
                "const id = legacy.session.tenantKey;\n")

        result = {
            "subtasks": [self._sub(
                targets=[{"old_pattern": "legacy.session.tenantKey",
                          "replacement": "resolveTenantKey()",
                          "is_real_identifier": True}],
                files=["src/route0.ts"])],
            "status": "ready",
            "confidence": _conf(task_understanding=9.5,
                                decomposition_quality=9.5),
        }
        issues = leerie.check_planner_output(result, tmp_path, "refactoring")
        assert any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)

    def test_is_real_identifier_false_does_not_fire(
            self, leerie, tmp_path):
        """The planner's own attestation gates the check now — never a
        shape Python infers. `is_real_identifier: false` (the planner
        declaring it is NOT confident `old_pattern` is a real symbol)
        must keep the check silent, the same way the retired
        bare-lowercase-word shape guard used to for the historical
        stopword shapes."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text("const x = 1; with y;\n")

        for stopword in ("with", "both", "task", "when", "that", "only"):
            subtasks = [self._sub(
                sid=f"refactor-{stopword}",
                targets=[{"old_pattern": stopword, "replacement": "y",
                          "is_real_identifier": False}],
                files=["src/route0.ts"])]
            issues = leerie._check_migration_surface(subtasks, tmp_path)
            assert issues == [], (
                f"{stopword!r} incorrectly fired UNCOVERED_MIGRATION_SURFACE "
                "with is_real_identifier: false")

    def test_missing_is_real_identifier_does_not_fire(self, leerie, tmp_path):
        """A malformed/absent attestation is treated as `false`, not as
        implicit consent — the check must not trust an unset field."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text(
                "const id = legacy.session.tenantKey;\n")

        subtasks = [self._sub(
            targets=[{"old_pattern": "legacy.session.tenantKey",
                      "replacement": "resolveTenantKey()"}],
            files=["src/route0.ts"])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert issues == []

    def test_is_real_identifier_true_fires_even_for_stopword_shape(
            self, leerie, tmp_path):
        """The check trusts the planner's attestation rather than
        second-guessing it with a shape check — if the planner explicitly
        attests `is_real_identifier: true` for a bare-lowercase-word
        pattern, the check still fires. This is deliberate: judging
        `old_pattern`'s shape is the planner's job now, not Python's."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text("const x = 1; with y;\n")

        subtasks = [self._sub(
            targets=[{"old_pattern": "with", "replacement": "y",
                      "is_real_identifier": True}],
            files=["src/route0.ts"])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)

    def test_real_identifier_shape_still_fires_when_uncovered(
            self, leerie, tmp_path):
        """A genuine identifier (camelCase, snake_case, dotted, etc.)
        attested `is_real_identifier: true` still fires normally when
        uncovered."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"route{i}.ts").write_text(
                "const id = getUserData();\n")

        subtasks = [self._sub(
            targets=[{"old_pattern": "getUserData",
                      "replacement": "fetchUser()",
                      "is_real_identifier": True}],
            files=["src/route0.ts"])]
        issues = leerie._check_migration_surface(subtasks, tmp_path)
        assert any("UNCOVERED_MIGRATION_SURFACE" in i for i in issues)


class TestProseSignalAbsent:
    """The prose-regex path can never silently return.

    Mirrors `TestRegexPathAbsent` in tests/test_capture_deps.py, the
    precedent CLAUDE.md names for migrations off hand-parsing onto
    LLM-structured output.
    """

    def test_regex_symbol_is_gone(self, leerie):
        assert not hasattr(leerie, "_MIGRATION_SIGNAL_RE")

    def test_bare_lowercase_word_shape_regex_is_gone(self, leerie):
        """The shape-guard regex that used to classify the LLM-authored
        `old_pattern` field is retired in favor of the planner's own
        `is_real_identifier` attestation — CLAUDE.md *Language-to-JSON*
        forbids Python from regex-classifying an LLM's response, and a
        shape check on a worker-populated field is exactly that, just
        relocated from `intent`/`investigation_notes` to `old_pattern`."""
        assert not hasattr(leerie, "_BARE_LOWERCASE_WORD_RE")

    def test_check_reads_neither_intent_nor_notes(self, leerie):
        import inspect
        src = inspect.getsource(leerie._check_migration_surface)
        body = src[src.index("issues: list[str] = []"):]
        for field in ('"intent"', '"investigation_notes"'):
            assert field not in body, (
                f"_check_migration_surface reads {field} again — that is "
                "the prose path this migration removed")
        assert '"migration_targets"' in body

    def test_prose_alone_no_longer_triggers_the_check(self, leerie, tmp_path):
        """The incident shape: an intent whose sentence describes a
        replacement, with no declared target. It must be silent."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(30):
            (src / f"f{i}.ts").write_text("with(x);\n")

        subtasks = [{
            "id": "refactor-001", "title": "t",
            "intent": "Replace both regex sites with an LLM worker, "
                      "replacing the prose-derived detection entirely",
            "investigation_notes": "extracting oldHelper as the new seam",
            "files_likely_touched": [], "depends_on": [], "size": "small",
            "success_criteria_seed": "check",
        }]
        assert leerie._check_migration_surface(subtasks, tmp_path) == []

    def test_planner_schema_declares_migration_targets(self, leerie):
        item = (leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
                ["properties"]["migration_targets"])
        assert item["type"] == "array"
        assert item["items"]["required"] == [
            "old_pattern", "replacement", "is_real_identifier"]
        assert item["items"]["properties"]["old_pattern"]["minLength"] == 3

    def test_is_real_identifier_field_is_boolean_and_required(self, leerie):
        item = (leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
                ["properties"]["migration_targets"]["items"])
        assert item["properties"]["is_real_identifier"]["type"] == "boolean"
        assert "is_real_identifier" in item["required"]

    def test_planner_prompt_asks_for_the_field(self):
        """The field is inert unless the prompt populates it — and an
        absent field silently disables the check."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent
                / "prompts" / "planner.md").read_text()
        assert "migration_targets" in text
        assert "old_pattern" in text
        assert "is_real_identifier" in text, (
            "the prompt must ask the planner to self-attest old_pattern's "
            "identifier shape, since Python no longer regex-classifies it")
        assert "Never an English word" in text, (
            "the prompt must ban the stopword shape that produced 27 false "
            "issues on run 19a70d96")


# --- warn_layer_gaps ------------------------------------------------------ #

class TestWarnLayerGaps:
    def _make_plan(self, subtasks: list[dict]) -> dict:
        return {"domain": "feature-implementation",
                "subtasks": subtasks}

    def test_schema_without_seed_warns(self, leerie, capsys):
        plans = [self._make_plan([{
            "id": "feat-001", "title": "Add model",
            "files_likely_touched": ["prisma/schema.prisma"],
            "provides": [],
        }])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" in captured.err or "LAYER_GAP" in captured.out

    def test_schema_with_seed_clean(self, leerie, capsys):
        plans = [self._make_plan([
            {"id": "feat-001", "title": "Add model",
             "files_likely_touched": ["prisma/schema.prisma"],
             "provides": []},
            {"id": "config-001", "title": "Seed data",
             "files_likely_touched": ["prisma/seed.ts"],
             "provides": []},
        ])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" not in captured.err
        assert "LAYER_GAP" not in captured.out

    def test_schema_with_migration_clean(self, leerie, capsys):
        plans = [self._make_plan([
            {"id": "feat-001", "title": "Add model",
             "files_likely_touched": ["prisma/schema.prisma"],
             "provides": []},
            {"id": "feat-002", "title": "Migration",
             "files_likely_touched": [
                 "prisma/migrations/001_add_model/migration.sql"],
             "provides": []},
        ])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" not in captured.err
        assert "LAYER_GAP" not in captured.out

    def test_env_provider_without_template(self, leerie, capsys):
        plans = [self._make_plan([{
            "id": "feat-001", "title": "Add bootstrap",
            "files_likely_touched": ["src/lib/platform/bootstrap.ts"],
            "provides": ["superadmin-bootstrap-env-contract"],
        }])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" in captured.err or "LAYER_GAP" in captured.out

    def test_env_provider_with_template_clean(self, leerie, capsys):
        plans = [self._make_plan([
            {"id": "feat-001", "title": "Add bootstrap",
             "files_likely_touched": ["src/lib/platform/bootstrap.ts"],
             "provides": ["superadmin-bootstrap-env-contract"]},
            {"id": "config-001", "title": "Update env docs",
             "files_likely_touched": [".env.example"],
             "provides": []},
        ])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" not in captured.err
        assert "LAYER_GAP" not in captured.out

    def test_non_env_provides_clean(self, leerie, capsys):
        plans = [self._make_plan([{
            "id": "feat-001", "title": "Add CRUD",
            "files_likely_touched": ["src/api/users.ts"],
            "provides": ["user-crud-api"],
        }])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" not in captured.err
        assert "LAYER_GAP" not in captured.out

    def test_secret_keyword_triggers(self, leerie, capsys):
        plans = [self._make_plan([{
            "id": "infra-001", "title": "Add secrets bundle",
            "files_likely_touched": ["infra/lib/app-stack.ts"],
            "provides": ["platform-secret-bundle-provisioned"],
        }])]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" in captured.err or "LAYER_GAP" in captured.out

    def test_no_subtasks_no_crash(self, leerie, capsys):
        plans = [{"domain": "testing", "subtasks": []}]
        leerie.warn_layer_gaps(plans)
        captured = capsys.readouterr()
        assert "LAYER_GAP" not in captured.err
        assert "LAYER_GAP" not in captured.out


# --- _check_migration_targets_declared ------------------------------------ #

class TestCheckMigrationTargetsDeclared:
    """MIGRATION_TARGETS_MISSING: same-worker contradiction check.

    `performs_replacement: true` with empty/absent `migration_targets`
    closes the "planner forgot to declare the target" case that
    UNCOVERED_MIGRATION_SURFACE alone cannot see (an omitted field
    produces no issue, not a wrong one). It is not an independent
    witness: both fields come from the same planner call.
    """

    def _sub(self, sid="refactor-001", *, performs_replacement=None,
              targets=None):
        s = {
            "id": sid, "title": "t", "intent": "x",
            "investigation_notes": "",
            "files_likely_touched": [],
            "depends_on": [], "size": "small",
            "success_criteria_seed": "check",
        }
        if performs_replacement is not None:
            s["performs_replacement"] = performs_replacement
        if targets is not None:
            s["migration_targets"] = targets
        return s

    def test_true_with_no_targets_flags(self, leerie):
        subtasks = [self._sub(performs_replacement=True)]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert any("MIGRATION_TARGETS_MISSING" in i for i in issues)
        assert "refactor-001" in issues[0]

    def test_true_with_empty_targets_list_flags(self, leerie):
        subtasks = [self._sub(performs_replacement=True, targets=[])]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert any("MIGRATION_TARGETS_MISSING" in i for i in issues)

    def test_true_with_targets_present_is_silent(self, leerie):
        subtasks = [self._sub(
            performs_replacement=True,
            targets=[{"old_pattern": "legacyFn", "replacement": "newFn"}])]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert issues == []

    def test_false_with_no_targets_is_silent(self, leerie):
        subtasks = [self._sub(performs_replacement=False)]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert issues == []

    def test_absent_field_with_no_targets_is_silent(self, leerie):
        """The common case: most subtasks omit performs_replacement
        entirely, matching migration_targets' own omit-by-default
        convention."""
        subtasks = [self._sub()]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert issues == []

    def test_absent_field_with_targets_present_is_silent(self, leerie):
        """A planner that declares targets without setting the boolean
        must not be penalized — the boolean is a convenience signal, not
        a second required declaration."""
        subtasks = [self._sub(
            targets=[{"old_pattern": "legacyFn", "replacement": "newFn"}])]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert issues == []

    def test_multiple_subtasks_each_checked_independently(self, leerie):
        subtasks = [
            self._sub(sid="refactor-001", performs_replacement=True),
            self._sub(sid="refactor-002", performs_replacement=True,
                      targets=[{"old_pattern": "legacyFn",
                                "replacement": "newFn"}]),
            self._sub(sid="refactor-003"),
        ]
        issues = leerie._check_migration_targets_declared(subtasks)
        assert len(issues) == 1
        assert "refactor-001" in issues[0]

    def test_no_subtasks_no_crash(self, leerie):
        assert leerie._check_migration_targets_declared([]) == []


class TestMigrationTargetsMissingWiredIntoCheckPlannerOutput:
    """check_planner_output must actually call the new check — the
    schema/function existing in isolation is inert without this wiring."""

    def test_check_planner_output_source_calls_it(self, leerie):
        import inspect
        src = inspect.getsource(leerie.check_planner_output)
        assert "_check_migration_targets_declared(subtasks)" in src

    def test_check_planner_output_flags_the_contradiction(
            self, leerie, tmp_path):
        result = {
            "subtasks": [{
                "id": "refactor-001", "title": "t", "intent": "x",
                "investigation_notes": "", "files_likely_touched": [],
                "depends_on": [], "size": "small",
                "success_criteria_seed": "check",
                "performs_replacement": True,
            }],
        }
        issues = leerie.check_planner_output(result, tmp_path, "refactoring")
        assert any("MIGRATION_TARGETS_MISSING" in i for i in issues)


class TestPerformsReplacementSchema:
    """SCHEMAS["planner"]'s subtask performs_replacement field shape."""

    def _subtask_schema(self, leerie):
        return leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]

    def test_field_exists(self, leerie):
        assert "performs_replacement" in self._subtask_schema(leerie)["properties"]

    def test_field_is_boolean(self, leerie):
        prop = self._subtask_schema(leerie)["properties"]["performs_replacement"]
        assert prop["type"] == "boolean"

    def test_field_not_required(self, leerie):
        """Optional — most subtasks replace nothing."""
        assert "performs_replacement" not in self._subtask_schema(leerie)["required"]

    def test_field_is_sibling_not_nested_in_migration_targets(self, leerie):
        """migration_targets items forbid additionalProperties, so
        performs_replacement must live on the subtask object itself."""
        subtask_props = self._subtask_schema(leerie)["properties"]
        mt_item_props = subtask_props["migration_targets"]["items"]["properties"]
        assert "performs_replacement" not in mt_item_props
