"""What an existing test pins on the base holds on the tree that ships
(DESIGN §8 *What an existing test pins on the base holds on the tree that
ships*).

Run a79384a4 ended `done` with a PR that undid an earlier merged fix: its
acceptance repair round 2 changed the code and deleted the two existing cases
that pinned the fix. The test-ID diff and B* look for tests that newly fail,
and a deleted case never runs. So every test-case file the base had and a
tree modified or deleted is run in its BASE form against that tree, in place
of its final form (`tests/test_base_form_in_place.py` has what the
replacement itself must do).

Every test here runs the real check over a real git repository and a real
pytest, confined as a measurement is (the held-out sandbox through the
suite's emulator); only `claude_p` is stubbed, in the gate test.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import _make_sets, _st, _staging

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
LIB = ("def settle_rounds():\n    return 5\n\n"
       "def spend():\n    return 5\n\n"
       "def helper():\n    return 1\n")
CASES = ("from lib import spend\n\n"
         "def test_kept():\n    assert True\n\n"
         "def test_spends_the_whole_budget():\n"
         "    assert spend() == 5\n")
SPENT = LIB.replace("def spend():\n    return 5", "def spend():\n    return 2")
LOOSE = ("from lib import settle_rounds\n\n"
         "def test_rounds():\n    assert settle_rounds() == 5\n")
GONE = ("from lib import settle_rounds\n\n"
        "def test_gone_pins_rounds():\n    assert settle_rounds() == 5\n")
EXPORT = ("from lib import helper\n\n"
          "def test_helper():\n    assert helper() == 1\n")
APPEND = ("from lib import settle_rounds\n\n"
          "def test_a():\n    assert settle_rounds() > 0\n")
RED = "def test_always_red():\n    assert False\n"


@pytest.fixture(autouse=True)
def _quiet(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(worker_timeout_sec=120, confirm_pairs=2,
                confirm_intermittent_repeats=0, acceptance_sets=5)
    return caps


class World:
    """A base with `lib.py` and five existing tests, a run whose staging is
    on its own branch, `base_sha` recorded as `phase_execute` does."""

    def __init__(self, leerie, tmp_path):
        self.leerie = leerie
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@x")
        _git(repo, "config", "user.name", "t")
        (repo / "calc.py").write_text(BUGGY)
        (repo / "lib.py").write_text(LIB)
        (repo / "conftest.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
        (repo / ".leerie").mkdir()
        cfg = ('test = "python3 -m pytest -q -p no:cacheprovider tests"\n'
               'test_scoped = "python3 -m pytest -q -p no:cacheprovider'
               ' {test_files}"\n')
        (repo / ".leerie" / "config.toml").write_text(cfg)
        (repo / ".gitignore").write_text("__pycache__/\n.leerie/\n")
        t = repo / "tests"
        t.mkdir()
        for name, text in (("test_cases.py", CASES), ("test_loose.py", LOOSE),
                           ("test_gone.py", GONE), ("test_export.py", EXPORT),
                           ("test_append.py", APPEND),
                           ("test_red.py", RED)):
            (t / name).write_text(text)
        (t / "helpers.py").write_text("X = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        head = _git(repo, "rev-parse", "HEAD")
        self.base = head
        self.st = _st(leerie, tmp_path, repo, head, working_branch="main",
                      base_sha=head)
        self.staging = _staging(self.st, repo)
        self.caps = _caps(leerie)

    def commit(self, files: dict[str, str | None], msg: str = "round") -> str:
        for rel, text in files.items():
            p = self.staging / rel
            if text is None:
                p.unlink()
            else:
                p.write_text(text)
        _git(self.staging, "add", "-A")
        _git(self.staging, "commit", "-qm", msg)
        return _git(self.staging, "rev-parse", "HEAD")

    def check(self, before: str | None = None, **kw) -> dict:
        return asyncio.run(self.leerie._base_form_regressions(
            self.st, self.caps, self.staging, before or self.base,
            label="t", log_path=self.st.run_dir / "logs" / "t.log", **kw))


def _ids(rec: dict) -> list[str]:
    return sorted(r["id"] for r in rec["changed"])


# --- the four shapes of a79384a4, each alone ---------------------------------

def test_a_deleted_case_is_run_from_the_base_and_fails(leerie, tmp_path):
    """4d80a79: the code stops spending the budget and the case that pinned
    it is deleted. The final form passes; its base form does not."""
    w = World(leerie, tmp_path)
    w.commit({"lib.py": SPENT,
              "tests/test_cases.py": CASES.split("\n\ndef test_spends")[0]
              + "\n"})
    rec = w.check()
    assert rec["files"] == ["tests/test_cases.py"]
    (row,) = rec["changed"]
    assert row["file"] == "tests/test_cases.py"
    assert "test_spends_the_whole_budget" in row["id"]
    # Run under its own name: the ID is the file's own.
    assert row["id"].startswith("tests/test_cases.py") or (
        "tests.test_cases" in row["id"])
    assert "test_kept" not in str(rec["changed"])


def test_a_loosened_assertion_still_asserts_the_old_value(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"lib.py": LIB.replace("return 5", "return 2"),
              "tests/test_loose.py": LOOSE.replace("== 5", ">= 1")})
    rec = w.check()
    assert _ids(rec) and all("test_rounds" in i for i in _ids(rec))
    assert {r["file"] for r in rec["changed"]} == {"tests/test_loose.py"}


def test_a_deleted_file_is_run_from_its_base_blob(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"lib.py": LIB.replace("return 5", "return 2"),
              "tests/test_gone.py": None})
    rec = w.check()
    assert rec["files"] == ["tests/test_gone.py"]
    (row,) = rec["changed"]
    assert row["file"] == "tests/test_gone.py"
    assert "test_gone_pins_rounds" in row["id"]


def test_a_removed_export_fails_the_base_form_to_load(leerie, tmp_path):
    """The final form stopped importing what the code removed; the base form
    still imports it, does not load, and that is a failing case."""
    w = World(leerie, tmp_path)
    w.commit({"lib.py": "def settle_rounds():\n    return 5\n",
              "tests/test_export.py":
              "def test_helper():\n    assert True\n"})
    rec = w.check()
    (row,) = rec["changed"]
    assert row["file"] == "tests/test_export.py"
    assert "helper" in row["message"] or "test_export" in row["id"]


# --- what is not a change -------------------------------------------------------

def test_a_file_only_added_to_keeps_its_base_cases_passing(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"tests/test_append.py": APPEND
              + "\ndef test_b():\n    assert True\n",
              "tests/helpers.py": "X = 2\n"})
    rec = w.check()
    # A helper is no case file: reached through the cases, never run itself.
    assert rec["files"] == ["tests/test_append.py"]
    assert rec["changed"] == [] and rec["unmeasured"] == []


def test_a_case_the_base_already_failed_is_not_a_change(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"tests/test_red.py": RED.replace("assert False",
                                               "assert False  # still")})
    rec = w.check()
    assert rec["files"] == ["tests/test_red.py"]
    assert rec["changed"] == []
    assert rec["verdict"]["pre_existing"]


def test_a_round_is_judged_against_its_own_start(leerie, tmp_path):
    """An earlier change already broke the base form: the next round, which
    did not make it worse, is not blamed for it."""
    w = World(leerie, tmp_path)
    first = w.commit({"lib.py": LIB.replace("return 5", "return 2"),
                      "tests/test_loose.py": LOOSE.replace("== 5", ">= 1")})
    w.commit({"calc.py": FIXED})
    assert w.check(before=first, reach_of=first)["changed"] == []
    assert w.check()["changed"]           # but against the base it is


def test_a_round_whose_change_reaches_a_changed_test_runs_it(
        leerie, tmp_path):
    """An earlier round loosened a test and left the behaviour; the next
    round changes only the code it imports. Its reach is known (the import
    graph), so the narrowing keeps the test, and the round is caught."""
    w = World(leerie, tmp_path)
    first = w.commit({"tests/test_loose.py": LOOSE.replace("== 5", ">= 1")})
    w.commit({"lib.py": LIB.replace("return 5", "return 2", 1)})
    rec = w.check(before=first, reach_of=first)
    assert rec["files"] == ["tests/test_loose.py"]
    assert {r["file"] for r in rec["changed"]} == {"tests/test_loose.py"}


# --- the outcome and the PR -------------------------------------------------------

def test_a_changed_case_makes_the_run_unconverged_and_names_it(
        leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"lib.py": LIB.replace("return 5", "return 2"),
              "tests/test_gone.py": None})
    asyncio.run(leerie._check_existing_behaviour(w.st.run_dir, w.st, w.caps, {}, {}))
    rec = w.st.data["existing_behaviour"]
    assert rec["tree_sha"] == leerie._shipping_tree(w.st)
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    (why,) = [r for r in w.st.data["outcome_reasons"]
              if "existing_behaviour_changed" in r]
    assert "tests/test_gone.py" in why and "test_gone_pins_rounds" in why
    body = leerie._format_pr_end_state_section(w.st.data)
    assert "## Unconverged" in body
    assert "test_gone_pins_rounds" in body.split("## Unconverged", 1)[1]


def test_an_unchanged_behaviour_leaves_the_run_done(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"tests/test_append.py": APPEND
              + "\ndef test_b():\n    assert True\n"})
    asyncio.run(leerie._check_existing_behaviour(w.st.run_dir, w.st, w.caps, {}, {}))
    assert w.st.data["existing_behaviour"]["files"] == [
        "tests/test_append.py"]
    reasons = leerie._unmeasured_on_shipping_tree(w.st)
    assert not any("base form" in r for r in reasons)
    assert not any("existing_behaviour" in r for r in reasons)


def test_a_check_that_could_not_run_is_unmeasured_never_a_pass(
        leerie, tmp_path):
    """The file's path is a link in the tree: the base form is never written
    through it, and the file is unmeasured, not passing."""
    w = World(leerie, tmp_path)
    w.commit({"lib.py": LIB.replace("return 5", "return 2"),
              "tests/test_gone.py": None})
    (w.staging / "tests" / "test_gone.py").symlink_to("test_append.py")
    _git(w.staging, "add", "-A")
    _git(w.staging, "commit", "-qm", "a link where the test was")
    asyncio.run(leerie._check_existing_behaviour(w.st.run_dir, w.st, w.caps, {}, {}))
    rec = w.st.data["existing_behaviour"]
    assert rec["unmeasured"] == ["tests/test_gone.py"]
    assert rec["unmeasured_reason"] == ("the base form's path is a link or "
                                        "not a file")
    reasons = leerie._unmeasured_on_shipping_tree(w.st)
    assert any("gave no verdict in their base form" in r
               and "tests/test_gone.py" in r for r in reasons)
    nhv = str(w.st.data["needs_human_verification"])
    assert "base form" in nhv and "tests/test_gone.py" in nhv


def test_a_record_of_another_tree_is_not_the_shipping_trees(leerie, tmp_path):
    w = World(leerie, tmp_path)
    w.commit({"tests/test_append.py": APPEND + "\n"})
    asyncio.run(leerie._check_existing_behaviour(w.st.run_dir, w.st, w.caps, {}, {}))
    w.commit({"calc.py": FIXED})
    assert any("another tree" in r
               for r in leerie._unmeasured_on_shipping_tree(w.st))


# --- the acceptance repair round of a79384a4 -------------------------------------

class _Repair:
    """The stub conformer: each call runs the next fixer in staging."""

    def __init__(self, fixers):
        self.fixers = list(fixers)
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        assert kw["schema_key"] == "conformer"
        self.fixers.pop(0)(Path(kw["cwd"]))
        return {}


def test_a_repair_that_deletes_the_tests_it_disagrees_with_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    """Round 1 fixes the held-out case, changes the pinned behaviour and
    deletes the case that pinned it — every test passes in its new form, so
    nothing else rolls it back. Round 2 fixes the case alone and is kept."""
    w = World(leerie, tmp_path)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["acceptance"] = {"sets": _make_sets(leerie, w.st, 2)}

    async def unchanged(st, caps, models, efforts, staging, final, sha):
        return final
    monkeypatch.setattr(leerie, "_acceptance_audit_and_rewrite", unchanged)

    def fix_and_delete(p):
        (p / "calc.py").write_text(FIXED)
        (p / "lib.py").write_text(SPENT)
        (p / "tests" / "test_cases.py").write_text(
            CASES.split("\n\ndef test_spends")[0] + "\n")
        _git(p, "commit", "-qam", "conformer: bound the budget")

    def fix(p):
        (p / "calc.py").write_text(FIXED)
        _git(p, "commit", "-qam", "conformer: fix add")
    worker = _Repair([fix_and_delete, fix])
    monkeypatch.setattr(leerie, "claude_p", worker)
    asyncio.run(leerie._run_acceptance_gate(
        w.st.run_dir, w.st, w.caps, {"conformer": "sonnet"},
        {"conformer": "low"}))
    gate = w.st.data["acceptance"]["gate"]
    r1, r2 = gate["rounds"]
    assert r1["fixed_cases"] and not r1["broken_cases"]
    assert r1["ratchet"]["new_failures"] == []          # nothing newly fails
    assert any("test_spends_the_whole_budget" in i
               for i in r1["ratchet"]["base_form_changed"])
    assert r1["rollback_reason"].startswith(
        "changed what an existing test pinned on the base")
    assert r1["rolled_back"] is True
    (row,) = w.st.data["round_rollbacks"]
    assert "test_spends_the_whole_budget" in row["reason"]
    assert not r2.get("rollback_reason")
    assert r2["ratchet"]["base_form_changed"] == []
    assert _git(w.staging, "log", "--format=%s", f"{start}..HEAD") == \
        "conformer: fix add"
    assert (w.staging / "lib.py").read_text() == LIB


# --- paths -----------------------------------------------------------------------

@pytest.mark.parametrize("rel,case", [
    ("src/a.test.ts", True), ("a.spec.tsx", True),
    ("tests/test_a.py", True), ("pkg/a_test.py", True),
    ("pkg/a_test.go", True), ("spec/models/a_spec.rb", True),
    ("test/a_test.rb", True), ("tests/api.rs", True),
    ("src/a.test-helper.ts", False), ("tests/conftest.py", False),
    ("tests/helpers.py", False), ("src/__snapshots__/a.test.ts.snap", False),
    ("tests/fixtures/x.json", False), ("tests/common/mod.rs", False),
])
def test_what_is_a_test_case_file(leerie, rel, case):
    assert leerie._base_form_case(rel) is case


def test_a_runner_with_no_report_is_listed_not_run(leerie, tmp_path):
    """Go, Ruby and Rust cases run in base form; a JVM test, whose runner
    gives no report leerie reads, is listed."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    for rel in ("pkg/a_test.go", "src/a.test.ts", "tests/fixtures/x.json",
                "src/b.test.ts", "spec/a_spec.rb",
                "src/test/java/x/ATest.java"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "b")
    base = _git(repo, "rev-parse", "HEAD")
    for rel in ("pkg/a_test.go", "src/a.test.ts", "tests/fixtures/x.json",
                "spec/a_spec.rb", "src/test/java/x/ATest.java"):
        (repo / rel).write_text("2\n")
    (repo / "src" / "b.test.ts").unlink()
    (repo / "src" / "new.test.ts").write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "h")
    files, unsupported = leerie._base_form_files(repo, base, "HEAD")
    # Added files have no base; a fixture is no case.
    assert files == ["pkg/a_test.go", "spec/a_spec.rb", "src/a.test.ts",
                     "src/b.test.ts"]
    assert unsupported == ["src/test/java/x/ATest.java"]


# --- the delivery re-drive's group ------------------------------------------------

def test_a_redrive_group_that_changes_pinned_behaviour_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    """The re-drive's group goes through the same check (`_ratchet_exec(…,
    base_form=True)`); a group that fixed its case but changed what an
    existing test pinned is reset, and its reason names the case. The
    measurement is the real one above; here it answers for the World's
    JS-shaped tree, which its pytest cannot run."""
    from tests.test_delivery_redrive import World as RedriveWorld
    w = RedriveWorld(leerie, tmp_path)
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")
    seen: list[str] = []

    async def changed(st, caps, staging, before, **kw):
        seen.append(before)
        return {"changed": [{"id": "src/pin.test.ts :: pins the budget",
                             "file": "src/pin.test.ts"}]}
    monkeypatch.setattr(leerie, "_base_form_regressions", changed)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.caps["subtask_redrive_rounds"] = 1
    w.run()
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert ge["fixed_cases"] and not ge["worse"]
    assert seen == [start]                      # measured against its start
    assert ge["base_form_changed"] == ["src/pin.test.ts :: pins the budget"]
    assert ge["rolled_back"] is True
    assert "pins the budget" in ge["rollback_reason"]
    assert _git(w.staging, "rev-parse", "HEAD") == start
