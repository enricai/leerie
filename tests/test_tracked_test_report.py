"""Existing tests the run changed are listed in the PR (DESIGN §8 *Existing
tests the run changed are listed in the PR*): report-only.

Every test runs the real consumer on a real git repository: a base commit
holding tracked tests, a run branch in the run's own repository with real
commits on it, `phase_finalize` measuring base to final tree, and the PR
text rendered from what it stored. Assertions are on values and on order,
not on key presence.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

JS_BASE = """\
describe("pay", () => {
  it("totals the cart", () => {
    expect(total(cart)).toBe(10);
    expect(tax(cart)).toBe(2);
  });
  it("rounds", () => {
    expect(round(1.5)).toBe(2);
  });
});
"""
PY_BASE = """\
def test_parse():
    assert parse("a") == 1
    assert parse("b") == 2
"""


def _git(cwd, *cmd):
    r = subprocess.run(["git", *cmd], cwd=cwd, capture_output=True,
                       text=True)
    assert r.returncode == 0, f"git {cmd} in {cwd}: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "tests" / "pay.test.ts").write_text(JS_BASE)
    (root / "tests" / "test_parse.py").write_text(PY_BASE)
    (root / "src" / "pay.ts").write_text("export const total = 1;\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def st(leerie, repo, tmp_path):
    root = tmp_path / ".leerie"
    (root / "runs" / "r1").mkdir(parents=True)
    s = leerie.State(root, "r1", repo_root=repo)
    yield s
    s.release_lock()


@pytest.fixture(autouse=True)
def _real_git_stub_scripts(leerie, monkeypatch, repo):
    async def _script(name, *args, **_kw):
        return subprocess.CompletedProcess(["bash", name], 0, "", "")

    async def _compose(*a, **k):
        return None

    async def _capture(*a, **k):
        return None

    monkeypatch.chdir(repo)
    monkeypatch.setattr(leerie, "_run_script", _script)
    monkeypatch.setattr(leerie, "_compose_pr_via_llm", _compose)
    monkeypatch.setattr(leerie, "capture_repo_deps", _capture)


class Run:
    """A run branch in the run's own repository, committed to in a worktree."""

    def __init__(self, leerie, st):
        st.data["working_branch"] = "main"
        self.repo = leerie._ensure_run_repo(st)
        self.wt = st.run_dir / "worktrees" / "staging"
        _git(self.repo, "worktree", "add", "-q", "-b", "leerie/runs/r1",
             str(self.wt), "main")
        _git(self.wt, "config", "user.email", "t@t")
        _git(self.wt, "config", "user.name", "t")
        st.data["base_sha"] = _git(self.repo, "rev-parse", "main")
        st.data.update({"waves": [["feat-001"]], "completed_waves": 1,
                        "worker_count": 1,
                        "subtask_status": {"feat-001": "complete"}})
        self.st = st

    def commit(self, **files):
        for rel, text in files.items():
            p = self.wt / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "work")

    def remove(self, rel):
        _git(self.wt, "rm", "-q", rel)
        _git(self.wt, "commit", "-qm", "rm")

    def finalize(self, leerie):
        asyncio.run(leerie.phase_finalize(
            self.st.leerie_root, self.st, no_push=True, no_verify=False,
            caps=dict(leerie.DEFAULT_CAPS), models={}, efforts={}))
        return leerie._format_pr_end_state_section(self.st.data)


HEADING = "## Existing tests changed by this run"


def test_changed_expected_value_lists_old_then_new(leerie, st):
    run = Run(leerie, st)
    run.commit(**{"tests/pay.test.ts": JS_BASE.replace(
        "toBe(10)", "toBe(12)")})
    sec = run.finalize(leerie)
    assert HEADING in sec
    old, new = "expect(total(cart)).toBe(10)", "expect(total(cart)).toBe(12)"
    assert sec.index("`tests/pay.test.ts`") < sec.index(old) < sec.index(new)
    assert f"- removed assertion: `{old}`" in sec
    assert f"- added assertion: `{new}`" in sec
    assert "Nothing verified" in sec
    # The untouched assertions are not listed.
    assert "tax(cart)" not in sec
    # The run.json sidecar the launcher's fallback appends carries it.
    sidecar = json.loads((st.run_dir / "run.json").read_text())
    assert new in sidecar["pr_end_state"]


def test_python_assertion_and_dropped_case(leerie, st):
    run = Run(leerie, st)
    run.commit(**{
        "tests/test_parse.py": 'def test_parse():\n    assert parse("a") == 1\n',
        "tests/pay.test.ts": JS_BASE.replace(
            '  it("rounds", () => {\n    expect(round(1.5)).toBe(2);\n  });\n',
            "")})
    sec = run.finalize(leerie)
    assert '- removed assertion: `assert parse("b")==2`' in sec
    assert "- removed case:" in sec and "rounds" in sec
    assert "- removed assertion: `expect(round(1.5)).toBe(2)`" in sec


def test_flagging_changes_no_outcome(leerie, st, tmp_path, repo):
    run = Run(leerie, st)
    run.commit(**{"src/pay.ts": "export const total = 5;\n"})
    quiet = run.finalize(leerie)
    assert quiet == ""
    assert st.data["outcome"] == "done"
    clean = (st.data["outcome"], list(st.data.get("outcome_reasons") or []))

    run.commit(**{"tests/pay.test.ts": JS_BASE.replace("toBe(10)",
                                                       "toBe(12)")})
    st.data.pop("finished_at", None)
    sec = run.finalize(leerie)
    assert HEADING in sec
    assert (st.data["outcome"],
            list(st.data.get("outcome_reasons") or [])) == clean
    assert st.data["subtask_status"] == {"feat-001": "complete"}


def test_a_change_reverted_within_the_run_is_not_listed(leerie, st):
    run = Run(leerie, st)
    run.commit(**{"tests/pay.test.ts": JS_BASE.replace("toBe(10)",
                                                       "toBe(99)")})
    run.commit(**{"tests/pay.test.ts": JS_BASE})
    assert run.finalize(leerie) == ""
    assert st.data["tracked_test_report"] == {"files": [],
                                             "files_omitted": 0}


def test_a_deleted_test_file_is_listed(leerie, st):
    run = Run(leerie, st)
    run.remove("tests/test_parse.py")
    sec = run.finalize(leerie)
    assert HEADING in sec
    assert sec.index("`tests/test_parse.py`") < sec.index("- file deleted")


@pytest.mark.parametrize("path,text,marker", [
    ("tests/pay.test.ts", JS_BASE.replace('it("rounds"', 'it.skip("rounds"'),
     "- skip markers added: 1"),
    ("tests/test_parse.py",
     "import pytest\n\n@pytest.mark.skip\n" + PY_BASE,
     "- skip markers added: 1"),
])
def test_a_skip_marker_is_listed(leerie, st, path, text, marker):
    run = Run(leerie, st)
    run.commit(**{path: text})
    sec = run.finalize(leerie)
    assert HEADING in sec
    assert marker in sec


def test_only_additions_and_new_files_list_nothing(leerie, st):
    run = Run(leerie, st)
    run.commit(**{
        "tests/pay.test.ts": JS_BASE.replace(
            "});\n});", "});\n  it(\"new\", () => { expect(1).toBe(1); });\n});"),
        "tests/new.test.ts": "it('x', () => { expect(1).toBe(2); });\n",
        "src/pay.ts": "export const total = 2;\n"})
    assert run.finalize(leerie) == ""
    assert HEADING not in json.dumps(st.data.get("tracked_test_report"))


def test_no_test_change_renders_no_section(leerie, st):
    run = Run(leerie, st)
    run.commit(**{"src/pay.ts": "export const total = 3;\n"})
    assert HEADING not in run.finalize(leerie)


def test_a_draft_pr_carries_the_section(leerie, st):
    report = {"files": [{"file": "tests/pay.test.ts",
                         "removed": ["expect(a).toBe(1)"],
                         "added": ["expect(a).toBe(2)"]}], "files_omitted": 0}
    state = {"outcome": "unconverged", "outcome_reasons": ["1 unconverged"],
             "tracked_test_report": report}
    sec = leerie._format_pr_end_state_section(state)
    assert sec.index("## Unconverged") < sec.index(HEADING)
    assert "expect(a).toBe(1)" in sec and "expect(a).toBe(2)" in sec
    assert HEADING in leerie.compose_pr_body(state, "r1")


def test_the_listing_is_bounded_and_counts_the_rest(leerie):
    n = leerie._PR_TRACKED_TEST_SHOWN
    report = {"files": [{"file": "tests/a.test.ts",
                         "removed": [f"expect(x{i}).toBe(1)"
                                     for i in range(n + 10)]}],
              "files_omitted": 3}
    sec = leerie._format_pr_end_state_section({"tracked_test_report": report})
    assert sec.count("- removed assertion:") == n
    assert "expect(x0)" in sec and f"expect(x{n})" not in sec
    assert "(13 more changed assertions, cases or files" in sec


def test_the_stored_report_is_bounded(leerie, repo):
    k = leerie._TRACKED_TEST_ITEMS_MAX
    body = "".join(f"expect(v{i}).toBe({i});\n" for i in range(k + 5))
    (repo / "tests" / "big.test.ts").write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "big")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "big.test.ts").write_text("")
    _git(repo, "commit", "-qam", "gut")
    rec = leerie._tracked_test_report(
        repo, base, _git(repo, "rev-parse", "HEAD"))["files"][0]
    assert len(rec["removed"]) == k
    assert rec["removed"][0] == "expect(v0).toBe(0)"
    assert rec["omitted"] == 5


def test_a_failing_measurement_does_not_stop_finalize(leerie, st,
                                                      monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("git gone")

    monkeypatch.setattr(leerie, "_tracked_test_report", boom)
    run = Run(leerie, st)
    run.commit(**{"src/pay.ts": "export const total = 4;\n"})
    run.finalize(leerie)
    assert st.data["current_phase"] == "phase 6: finalize"
    assert "tracked_test_report" not in st.data


def test_worker_copy_of_the_heading_is_stripped(leerie):
    body = f"## Summary\n\nwork\n\n{HEADING}\n\n- invented\n\n## Other\n\nx"
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert stripped and "invented" not in out and "## Other" in out


def test_escalation_gate_b_is_unchanged_in_what_it_counts(leerie, repo):
    """Gate (b) stays a count check: a changed expected value, same number
    of call sites, is invisible to it; the report is a separate path."""
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "pay.test.ts").write_text(
        JS_BASE.replace("toBe(10)", "toBe(12)"))
    _git(repo, "commit", "-qam", "edit")
    head = _git(repo, "rev-parse", "HEAD")
    assert leerie._redrive_weakened_tests(repo, base, head) == []
    rep = leerie._tracked_test_report(repo, base, head)
    assert rep["files"][0]["removed"] == ["expect(total(cart)).toBe(10)"]


SETUP_BASE = """\
function drive() {
  const io = {
    settle: async (): Promise<void> => {
      const pre = await snapshot();
      await settleAfterClick(
        page,
        { n: 0 },
        0,
        true,
        read,
        pre.visibleTextSignature,
        idleQuiescence
      );
    },
    snapshot,
  };
  return io;
}
describe("report contract", () => {
  it("is credited", async () => {
    const r = await readAdvance(drive());
    expect(r.pageAdvanced).toBe(true);
  });
});
"""
SETUP_HEAD = """\
function drive() {
  const io = {
    settle: async (...args: unknown[]): Promise<void> => {
      const baseline = args[5] as string;
      await settleAfterClick(page, { n: 0 }, 0, true, read, baseline, idleQuiescence);
    },
    snapshot,
  };
  return io;
}
describe("report contract", () => {
  it("is credited", async () => {
    const r = await readAdvance(drive());
    expect(r.pageAdvanced).toBe(true);
    expect(r.post.networkCount).toBe(0);
  });
});
"""


def _setup_repo(repo):
    (repo / "tests" / "settle.test.ts").write_text(SETUP_BASE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "settle test")


def test_a_rewritten_setup_with_no_assertion_change_is_listed(leerie, st,
                                                              repo):
    _setup_repo(repo)
    run = Run(leerie, st)
    run.commit(**{"tests/settle.test.ts": SETUP_HEAD})
    sec = run.finalize(leerie)
    assert "**`tests/settle.test.ts`**" in sec
    # 11 base lines are gone (the `settle:` header, `const pre` and the
    # nine lines of the call); 4 are new (the header, `const baseline`, the
    # one-line call and the added assertion). No assertion string went.
    assert "- setup or other lines changed: 11 removed, 4 added" in sec, sec
    assert "- removed assertion" not in sec
    assert "- added assertion: `expect(r.post.networkCount).toBe(0)`" in sec
    assert sec.index("setup or other") > sec.index("**`tests/settle")


def test_blank_comment_reindent_and_moved_lines_count_nothing(leerie, repo):
    _setup_repo(repo)
    base = _git(repo, "rev-parse", "HEAD")
    text = SETUP_BASE.replace("    snapshot,\n", "\n    // note\n    snapshot,\n")
    text = text.replace("  return io;", "      return   io;")
    (repo / "tests" / "settle.test.ts").write_text(text)
    _git(repo, "commit", "-qam", "noise")
    rep = leerie._tracked_test_report(repo, base, _git(repo, "rev-parse",
                                                       "HEAD"))
    assert rep["files"] == []
    # Deleting a blank line and a comment from a file that has them.
    (repo / "tests" / "settle.test.ts").write_text(text)
    base2 = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "settle.test.ts").write_text(SETUP_BASE)
    _git(repo, "commit", "-qam", "denoise")
    assert leerie._tracked_test_report(
        repo, base2, _git(repo, "rev-parse", "HEAD"))["files"] == []


def test_a_removed_assertion_alone_does_not_add_a_line_count(leerie, repo):
    _setup_repo(repo)
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "settle.test.ts").write_text(SETUP_BASE.replace(
        "    expect(r.pageAdvanced).toBe(true);\n", ""))
    _git(repo, "commit", "-qam", "drop")
    rec = leerie._tracked_test_report(
        repo, base, _git(repo, "rev-parse", "HEAD"))["files"][0]
    assert rec["removed"] == ["expect(r.pageAdvanced).toBe(true)"]
    assert "lines_removed" not in rec
