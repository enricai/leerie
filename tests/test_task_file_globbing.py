"""Markdown prose is not a glob pattern (S5).

`_glob_task_references` classifies any whitespace token containing a
`_GLOB_CHARS` member (`*?[{`) as a file pattern, and `_format_task_file_references`
then tells the planner "Read each one before decomposing". A markdown task file
is prose: `*` and `**` are emphasis, not globs -- and `glob("*")` matches EVERY
file in the repo root.

Measured on a real 185 KB markdown task carrying 134 bare `*` and 217 `**`: the
planner was handed 18 files / 1.86 MB as required reading, including LICENSE,
CODE_OF_CONDUCT.md, `.claude.json`, and a prior run's 847 KB log.

Note this defect did NOT cause the incident that surfaced it -- the failing
run's very first request was already over the ceiling, before the planner read
anything. It is fixed on its own merits.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    """A repo root with a representative spread of files."""
    (tmp_path / "LICENSE").write_text("MIT")
    (tmp_path / "README.md").write_text("readme")
    (tmp_path / "spec.md").write_text("spec")
    (tmp_path / "notes.log").write_text("log")
    (tmp_path / ".hidden.json").write_text("{}")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN.md").write_text("design")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("a")
    (tmp_path / "tests" / "test_b.py").write_text("b")
    return tmp_path


def names(paths):
    return sorted(p.name for p in paths)


class TestMarkdownEmphasisIsNotAGlob:
    def test_bare_asterisk_matches_nothing(self, repo):
        assert leerie._glob_task_references("fix the * thing", repo) == []

    def test_double_asterisk_matches_nothing(self, repo):
        assert leerie._glob_task_references("this is ** important", repo) == []

    def test_bold_word_matches_nothing(self, repo):
        assert leerie._glob_task_references("**Root** cause **log**", repo) == []

    def test_realistic_markdown_prose_matches_nothing(self, repo):
        task = ("## **Severity: HIGH**\n\n"
                "The *planner* fails because **every** worker is affected.\n"
                "- **Root cause**: unclear\n- *Solution*: unknown\n")
        assert leerie._glob_task_references(task, repo) == []

    def test_underscore_and_backtick_emphasis_stripped(self, repo):
        assert leerie._glob_task_references("_emphasis_ and `code`", repo) == []


class TestGenuineReferencesStillResolve:
    def test_plain_filename_with_extension(self, repo):
        assert names(leerie._glob_task_references("implement spec.md", repo)) == ["spec.md"]

    def test_glob_pattern_with_extension(self, repo):
        got = names(leerie._glob_task_references("update tests/*.py", repo))
        assert got == ["test_a.py", "test_b.py"]

    def test_path_with_separator(self, repo):
        assert names(leerie._glob_task_references("read docs/DESIGN.md", repo)) == ["DESIGN.md"]

    def test_reference_survives_surrounding_bold(self, repo):
        # The emphasis strip must not damage the path inside it.
        assert names(leerie._glob_task_references("see **spec.md**", repo)) == ["spec.md"]

    def test_brace_group_still_expands(self, repo):
        got = names(leerie._glob_task_references("touch spec.{md,txt}", repo))
        assert got == ["spec.md"]


class TestPathEscapeIsRefused:
    """`repo_root / "/etc/passwd"` discards the root -- pathlib lets an
    absolute right-hand side win. Admitting separator-bearing tokens without
    this guard reached outside the repo entirely (a task mentioning
    `/bin/bash` matched a 1.4 MB binary)."""

    def test_absolute_path_matches_nothing(self, repo):
        assert leerie._glob_task_references("run /bin/sh and /etc/hosts", repo) == []

    def test_parent_traversal_matches_nothing(self, repo, tmp_path):
        (tmp_path.parent / "outside.md").write_text("x")
        assert leerie._glob_task_references("see ../outside.md", repo) == []

    def test_containment_holds_for_a_glob_that_escapes(self, repo, tmp_path):
        (tmp_path.parent / "escaped.md").write_text("x")
        assert leerie._glob_task_references("see ../*.md", repo) == []


class TestTaskFileDoesNotListItself:
    """The planner already has the task verbatim; listing the file again asks
    it to re-read content it holds -- 51K tokens of duplication on the
    measured incident."""

    def test_self_reference_excluded(self, repo):
        task = "Work through everything in spec.md and report back."
        (repo / "spec.md").write_text(task)
        assert leerie._glob_task_references(task, repo) == []

    def test_self_reference_excluded_despite_whitespace(self, repo):
        task = "Work through spec.md."
        (repo / "spec.md").write_text("\n  " + task + "  \n")
        assert leerie._glob_task_references(task, repo) == []

    def test_a_different_file_with_the_same_name_is_kept(self, repo):
        # Only content identity excludes; a same-named file with other
        # contents is a genuine reference.
        (repo / "spec.md").write_text("entirely different contents here")
        task = "Work through everything in spec.md and report back."
        assert names(leerie._glob_task_references(task, repo)) == ["spec.md"]


class TestRegressionOnTheMeasuredShape:
    def test_repo_root_is_not_swept_by_prose(self, repo):
        """The headline regression: emphasis must not enumerate the root."""
        task = "**Findings**\n\n* one\n* two\n\n**Severity: HIGH** — see *below*."
        got = leerie._glob_task_references(task, repo)
        assert got == [], f"prose swept in {names(got)}"

    def test_prose_plus_one_real_reference_yields_only_that_reference(self, repo):
        task = ("**N1** — *critical*. See docs/DESIGN.md for the rationale.\n"
                "* bullet one\n* bullet two\n**Root cause**: unknown\n")
        assert names(leerie._glob_task_references(task, repo)) == ["DESIGN.md"]


class TestUnreachableTaskReferences:
    """`_glob_task_references` silently drops a path outside the repo --
    correct for prose, but a `~`-prefixed or absolute path that the planner
    can genuinely never read (e.g. a plan file left in `~/.claude/plans/`)
    should not vanish without a trace.

    `repo_root` is required (not defaulted to cwd): the `../` shape added by
    N33 has to resolve against the repo, and a cwd default would silently
    resolve against whatever directory the container happened to start in.
    These cases are all `/`- or `~`-shaped, so the root only has to exist."""

    @pytest.fixture
    def repo(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        return root

    def test_home_relative_plan_path_warns(self, repo):
        got = leerie._unreachable_task_references(
            "Implement the plan in ~/.claude/plans/redesign.md", repo)
        assert got == ["~/.claude/plans/redesign.md"]

    def test_ordinary_prose_does_not_warn(self, repo):
        assert leerie._unreachable_task_references(
            "e.g. do this, i.e. not that", repo) == []
        assert leerie._unreachable_task_references("this is * important", repo) == []
        assert leerie._unreachable_task_references("**Root** cause **log**", repo) == []

    def test_genuine_in_repo_reference_does_not_warn(self, repo, tmp_path, monkeypatch):
        (tmp_path / "spec.md").write_text("spec")
        monkeypatch.chdir(tmp_path)
        assert leerie._unreachable_task_references(
            "see spec.md for details", repo) == []

    def test_absolute_path_outside_repo_still_warns(self, repo, tmp_path):
        missing = tmp_path / "nowhere" / "plan.md"
        got = leerie._unreachable_task_references(f"see {missing} for details", repo)
        assert got == [str(missing)]

    def test_existing_home_relative_path_does_not_warn(self, repo, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "plan.md").write_text("plan")
        monkeypatch.setenv("HOME", str(home))
        assert leerie._unreachable_task_references(
            "see ~/plan.md for details", repo) == []

    # --- the reference must come back CLEAN (N33) ------------------------
    #
    # It is rendered straight into an operator warning, so trailing sentence
    # punctuation makes them go looking for a path that was never written.
    # Same class as `_parse_touched_file_line`'s `None.` defect: punctuation
    # the tokenizer never stripped.

    def test_reference_ending_a_sentence_has_no_trailing_period(self, repo):
        assert leerie._unreachable_task_references(
            "See /etc/nonexistent-leerie-thing.conf.", repo) == [
                "/etc/nonexistent-leerie-thing.conf"]

    def test_backticked_reference_keeps_no_backtick_or_period(self, repo):
        """The incident's exact shape: a backticked path ending a sentence.
        The two pre-existing strips run in a fixed order, so the backtick is
        unreachable behind the period unless it is rstripped."""
        assert leerie._unreachable_task_references(
            "Specs live in `~/.claude/plans/so-we-have-to-giggly-gray.md`.",
            repo) == ["~/.claude/plans/so-we-have-to-giggly-gray.md"]

    def test_parenthesised_reference_is_clean(self, repo):
        assert leerie._unreachable_task_references(
            "(see ~/.claude/plans/nope-not-here.md)",
            repo) == ["~/.claude/plans/nope-not-here.md"]

    def test_a_leading_dot_is_never_stripped(self, repo, tmp_path, monkeypatch):
        """rstrip, never strip. `.env`, `.github/...`, `./x` and `../x` are
        all real paths whose LEADING dot is meaningful -- a two-sided strip
        mangles every one of them, measured on the sibling defect.

        Driven through an absolute path so the token actually reaches the
        collector (only `/`- and `~`-prefixed tokens do), which is what makes
        this observable rather than vacuous.
        """
        assert leerie._unreachable_task_references(
            "check /nonexistent-leerie-root/.github/workflows/ci.yml.", repo) == [
                "/nonexistent-leerie-root/.github/workflows/ci.yml"]


# ---------------------------------------------------------------------------
# N33: parent-relative (`../`) references. The shape most likely to mislead,
# because the file usually DOES exist on the host -- it is simply outside the
# repo, so `_glob_task_references`' containment re-check drops it and the
# planner never sees it. The operator has no reason to suspect that.
# ---------------------------------------------------------------------------

def test_parent_relative_reference_outside_the_repo_is_flagged(leerie, tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    outside = tmp_path / "notes.md"
    outside.write_text("the real instructions\n")

    refs = leerie._unreachable_task_references(
        "Follow the plan in ../notes.md exactly.", repo)

    assert refs == ["../notes.md"], (
        "a ../ reference resolving outside the repo must be flagged even "
        "though the file exists -- the planner cannot read it")


def test_parent_relative_reference_back_inside_the_repo_is_not_flagged(leerie, tmp_path):
    """`../` that lands back inside the root is reachable; warning on it
    would be the false positive that teaches operators to ignore this."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "spec.md").write_text("real spec\n")

    refs = leerie._unreachable_task_references(
        "See src/../spec.md for the contract.", repo)

    assert refs == []


def test_parent_relative_reference_to_nothing_is_flagged(leerie, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    refs = leerie._unreachable_task_references(
        "Implement ../gone/spec.md please.", repo)
    assert refs == ["../gone/spec.md"]


def test_parent_relative_warning_does_not_relax_the_containment_guard(leerie, tmp_path):
    """The warning sits BESIDE the containment guard, never replaces it.

    `_glob_task_references` must still refuse to hand the planner a file
    outside the repo -- that guard exists because admitting separator-bearing
    tokens once reached /bin/bash. Flagging the same token for the operator
    must not start admitting it.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    outside = tmp_path / "notes.md"
    outside.write_text("outside content\n")

    task = "Follow ../notes.md exactly."
    assert leerie._unreachable_task_references(task, repo) == ["../notes.md"]
    assert leerie._glob_task_references(task, repo) == [], (
        "the containment guard was relaxed -- a file outside the repo is now "
        "being handed to the planner")


def test_prose_with_dot_dot_slash_is_not_flagged(leerie, tmp_path):
    """False-positive control: the token filters still apply, so ordinary
    prose and bare emphasis never reach the warning."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for prose in ("Compare **before** and **after** states.",
                  "Use e.g. the second approach.",
                  "Ratio is 3/4 of the total."):
        assert leerie._unreachable_task_references(prose, repo) == [], prose


def test_absolute_and_tilde_shapes_still_flagged_with_a_repo_root(leerie, tmp_path):
    """Regression control: adding the ../ branch must not disturb the two
    shapes that already worked, now that the function takes a root."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    refs = leerie._unreachable_task_references(
        "Read ~/.claude/plans/nope-xyz.md and /tmp/nope-xyz-abs.md first.", repo)
    assert refs == ["~/.claude/plans/nope-xyz.md", "/tmp/nope-xyz-abs.md"]
