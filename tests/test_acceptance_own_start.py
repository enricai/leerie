"""Stage 2a of the held-out acceptance sets (DESIGN §8 *Held-out acceptance
tests*): own-start writing and per-case validity (A1), repairing every
failing set with B*-confirmed verdicts and the broken-set residual hook (A2),
scoped runners for pytest/rspec/minitest/go/cargo (A4), and writing sets
whenever the defect-scope audit ran with the 3/3 applicability rule (N1-L).

Real git repos and a real pytest command throughout; only `claude_p` is
stubbed. Runner reports for the runners this host lacks are the real C9
bench reports (`tests/fixtures/runner_reports/`).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git

FIXTURES = Path(__file__).parent / "fixtures" / "runner_reports"

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
DEFECT_TEST = ("from calc import add\n\n"
               "def test_add_sums():\n    assert add(2, 3) == 5\n")
MIXED_DEFECT_TEST = ("from calc import add\n\n"
                     "def test_add_sums():\n    assert add(2, 3) == 5\n\n"
                     "def test_add_zero():\n    assert add(0, 0) == 0\n")
CONTROL_TEST = ("from calc import add\n\n"
                "def test_add_zero():\n    assert add(0, 0) == 0\n")
MODELS = {"acceptance_writer": "sonnet", "conformer": "sonnet",
          "defect_scope_auditor": "sonnet"}
EFFORTS = {"acceptance_writer": "medium", "conformer": "low",
           "defect_scope_auditor": "medium"}


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    # B*'s gate reads the host's PSI file; pin it quiet so a loaded host
    # cannot stretch these tests by the gate's wait.
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _repo(tmp_path: Path, config: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        config if config is not None else
        'test = "python3 -m pytest -q -p no:cacheprovider"\n')
    (repo / "report.md").write_text("add(2, 3) returns -1; it must be 5\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _st(leerie, tmp_path, repo, head, **data):
    root = tmp_path / "state"
    run_dir = root / "runs" / "run-a"
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    st = leerie.State(root, "run-a", repo_root=repo)
    st.data = {"task": "fix the bug in report.md", "verbosity": "quiet",
               "categories": ["bug-fixing"],
               "repo_state_before_planning": {"head": head},
               "defect_scope": {"applicable": True, "sites": [],
                                "defect_shape": "add subtracts"},
               "worker_count": 0}
    st.data.update(data)
    st.save()
    return st


def _caps(leerie, n=1):
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(acceptance_sets=n, worker_timeout_sec=120,
                confirm_pairs=2, confirm_pairs_escalated=2)
    return caps


def _writer(leerie, monkeypatch, files, probe=None):
    seen = []

    async def fake(**kw):
        assert kw["schema_key"] == "acceptance_writer"
        seen.append(kw)
        if probe:
            probe(Path(kw["cwd"]))
        decl = []
        for rel, (kind, text, cases) in files.items():
            p = Path(kw["cwd"]) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            decl.append({"path": rel, "kind": kind, "cases": cases,
                         "failure_mode": "assertion"})
        if not any(d["kind"] == "control" for d in decl):
            # A valid set needs a passing control (DESIGN §8).
            p = Path(kw["cwd"]) / "acc" / "test_control_auto.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(CONTROL_TEST)
            decl.append({"path": "acc/test_control_auto.py",
                         "kind": "control", "cases": ["zero"],
                         "failure_mode": "assertion"})
        return {"files": decl}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return seen


# --- A4: scoped runners ------------------------------------------------------

@pytest.mark.parametrize("canonical,template,runner", [
    ("pytest", "pytest {test_files}", "pytest"),
    ("python3 -m pytest -q", "python3 -m pytest -q {test_files}", "pytest"),
    ("uv run pytest -x", "uv run pytest -x {test_files}", "pytest"),
    ("bundle exec rspec", "bundle exec rspec {test_files}", "rspec"),
    ("bin/rails test", "bin/rails test {test_files}", "minitest"),
    ("go test -count=1 ./...", "go test -count=1 {test_packages}", "go"),
    ("cargo test", "cargo test --no-fail-fast {test_targets}", "cargo"),
    ("cargo test --no-fail-fast",
     "cargo test --no-fail-fast {test_targets}", "cargo"),
    # r1 L2: wrapped and argumented pytest. The path positionals are
    # replaced by the set's files; the options and the wrapper are kept.
    ("poetry run pytest", "poetry run pytest {test_files}", "pytest"),
    ("pytest -q tests", "pytest -q {test_files}", "pytest"),
    ("python -m pytest tests/", "python -m pytest {test_files}", "pytest"),
    ("uv run --frozen pytest -q tests",
     "uv run --frozen pytest -q {test_files}", "pytest"),
    ("uv run --python 3.12 pytest", "uv run --python 3.12 pytest "
     "{test_files}", "pytest"),
    ("poetry run python -m pytest tests",
     "poetry run python -m pytest {test_files}", "pytest"),
    ("pipenv run pytest", "pipenv run pytest {test_files}", "pytest"),
    ("pdm run pytest tests/unit", "pdm run pytest {test_files}", "pytest"),
    ("python3.12 -m pytest -x tests",
     "python3.12 -m pytest -x {test_files}", "pytest"),
    ("pytest -n auto --log-level DEBUG tests",
     "pytest -n auto --log-level DEBUG {test_files}", "pytest"),
    ("pytest --strict-markers tests", "pytest --strict-markers {test_files}",
     "pytest"),
])
def test_a_bare_runner_command_infers_a_scoped_template(leerie, canonical,
                                                         template, runner):
    assert leerie._infer_scoped_test_template(canonical) == (template, runner)


def test_rake_test_goes_through_the_ruby_loader(leerie):
    template, runner = leerie._infer_scoped_test_template(
        "bundle exec rake test")
    assert runner == "minitest"
    assert template.startswith("bundle exec ruby -Itest -Ilib -e ")
    assert template.endswith(" {test_files}")


@pytest.mark.parametrize("canonical,why", [
    ("npm test", "no_scoped_runner:npm"),
    ("make test", "no_scoped_runner:make"),
    ("pytest -k smoke tests/", "no_scoped_runner:not a bare pytest command"),
    ("pytest -mslow", "no_scoped_runner:not a bare pytest command"),
    ("poetry run pytest --lf", "no_scoped_runner:not a bare pytest command"),
    ("pytest tests/test_a.py::test_one",
     "no_scoped_runner:not a bare pytest command"),
    # A word after an unknown long option may be its value: not dropped.
    ("pytest --some-plugin-opt v tests",
     "no_scoped_runner:not a bare pytest command"),
    ("poetry install", "no_scoped_runner:poetry"),
    ("bundle exec rspec spec/models",
     "no_scoped_runner:not a bare rspec command"),
    ("cd sub && pytest", "no_scoped_runner:cd"),
    ("cargo test -- --nocapture",
     "no_scoped_runner:cargo test passes runner flags"),
    ("", "no_scoped_runner:no test command"),
])
def test_anything_else_is_a_typed_no_scoped_runner(leerie, canonical, why):
    assert leerie._infer_scoped_test_template(canonical) == (None, why)


@pytest.mark.parametrize("canonical", [
    "python3 -m pytest -q -p no:cacheprovider tests",
    "python3 -m pytest -p no:cacheprovider tests/",
])
def test_an_argumented_pytest_template_runs_only_the_given_file(
        leerie, tmp_path, canonical):
    """Mechanically, on real pytest: the canonical command names the whole
    `tests` root, where one file fails; the inferred template over the
    passing file alone runs only that file (the root is not run beside
    it)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n")
    (tmp_path / "tests" / "test_bad.py").write_text(
        "def test_bad():\n    assert False\n")
    whole = subprocess.run(canonical, shell=True, cwd=str(tmp_path),
                           capture_output=True, text=True)
    assert whole.returncode == 1, whole.stdout
    template, runner = leerie._infer_scoped_test_template(canonical)
    assert runner == "pytest"
    cmd = template.replace("{test_files}", "tests/test_ok.py")
    one = subprocess.run(cmd, shell=True, cwd=str(tmp_path),
                         capture_output=True, text=True)
    assert one.returncode == 0, one.stdout
    assert "1 passed" in one.stdout


def test_package_and_target_placeholders_render_only_what_selects(leerie):
    files = ["calc/add_test.go", "calc/add.go", "main_test.go",
             "tests/defect.rs", "src/lib.rs", "docs/x.md"]
    assert (leerie._render_scoped("go test {test_packages}", files, "HEAD")
            == "go test . ./calc")
    assert (leerie._render_scoped("cargo test {test_targets}", files, "HEAD")
            == "cargo test --test defect")
    # Absence rule: nothing selectable renders nothing (never a bare runner).
    assert leerie._render_scoped("go test {test_packages}", ["a.go"],
                                 "HEAD") is None
    assert leerie._render_scoped("cargo test {test_targets}", ["src/x.rs"],
                                 "HEAD") is None


def test_resolve_blt_scoped_now_infers_pytest_over_test_files(leerie,
                                                              tmp_path):
    repo, _ = _repo(tmp_path)
    scoped = leerie.resolve_blt_scoped(repo)
    assert scoped["test"] == (
        "python3 -m pytest -q -p no:cacheprovider {test_files}")
    # A diff with no test file still defers (renders nothing).
    assert leerie._render_scoped(scoped["test"], ["calc.py"], "HEAD") is None


def test_go_acceptance_command_selects_the_sets_own_functions(leerie,
                                                             tmp_path):
    repo, head = _repo(tmp_path, 'test = "go test ./..."\n')
    st = _st(leerie, tmp_path, repo, head)
    tree = tmp_path / "tree"
    (tree / "calc").mkdir(parents=True)
    (tree / "calc" / "defect_test.go").write_text(
        "package calc\n\nfunc TestMain(m *testing.M) {}\n"
        "func TestAddSmall(t *testing.T) {}\nfunc TestAddZero(t *testing.T) {}\n"
        "func helper() {}\n")
    cmd = leerie._acceptance_cmd(st, ["calc/defect_test.go"], str(tree))
    assert cmd == "go test ./calc -run '^(TestAddSmall|TestAddZero)$'"
    # The report request appends -json and captures stdout.
    run_cmd, env, kind = leerie._acceptance_report_spec(
        cmd, tmp_path / "r.json", ["calc/defect_test.go"])
    assert kind == "go-json"
    assert run_cmd == cmd + " -json >" + str(tmp_path / "r.json")
    # Without a tree to read the functions from there is no command.
    assert leerie._acceptance_cmd(st, ["calc/defect_test.go"]) is None


def test_cargo_report_request_unlocks_libtest_json(leerie, tmp_path):
    run_cmd, env, kind = leerie._acceptance_report_spec(
        "cargo test --no-fail-fast --test defect", tmp_path / "r.json",
        ["tests/defect.rs"])
    assert kind == "libtest-json"
    assert run_cmd == ("cargo test --no-fail-fast --test defect -- -Z "
                       "unstable-options --format json >"
                       + str(tmp_path / "r.json"))
    assert env["RUSTC_BOOTSTRAP"] == "1"


def test_rspec_and_minitest_report_requests(leerie, tmp_path):
    run_cmd, env, kind = leerie._acceptance_report_spec(
        "bundle exec rspec spec/defect_spec.rb", tmp_path / "r.json",
        ["spec/defect_spec.rb"])
    assert kind == "rspec-json" and env is None
    assert run_cmd.endswith(f"--format progress --format json --out "
                            f"{tmp_path / 'r.json'}")
    run_cmd, env, kind = leerie._acceptance_report_spec(
        "bin/rails test test/models/defect_test.rb", tmp_path / "m.json",
        ["test/models/defect_test.rb"])
    assert kind == "minitest-json"
    assert run_cmd == "bin/rails test test/models/defect_test.rb"
    plugin_dir = Path(env["RUBYLIB"].split(":")[0])
    assert env["LEERIE_MT_REPORT"] == str(tmp_path / "m.json")
    plugin = plugin_dir / "minitest" / "leeriereport_plugin.rb"
    assert "plugin_leeriereport_init" in plugin.read_text()


@pytest.mark.parametrize("kind,fixture,failed,passed", [
    ("rspec-json", "rspec.json", ["spec/defect_spec.rb :: add adds small"],
     3),
    ("minitest-json", "minitest.json", ["DefectTest#test_add_small"], 3),
    ("go-json", "go.json", ["example.com/c9/calc :: TestAddSmall"], 3),
    ("libtest-json", "libtest.json", ["add_small"], 3),
    ("junit", "junit.xml", ["test_add_small"], 3),
])
def test_real_runner_reports_give_per_case_statuses(leerie, kind, fixture,
                                                    failed, passed):
    """The C9 bench's real reports (one failing case of four each)."""
    parsed = leerie._parse_runner_report(kind, FIXTURES / fixture)
    statuses = parsed["cases"]
    got_failed = [c for c, s in statuses.items() if s == "failed"]
    assert len(got_failed) == 1 and failed[0] in got_failed[0]
    assert sum(1 for s in statuses.values() if s == "passed") == passed
    assert parsed["executed"] == 4 and parsed["collection_error"] is False


def test_a_go_package_that_did_not_build_is_a_load_error(leerie, tmp_path):
    rep = tmp_path / "go.json"
    rep.write_text("\n".join(json.dumps(e) for e in [
        {"Action": "start", "Package": "m/calc"},
        {"Action": "output", "Package": "m/calc", "Output": "build failed\n"},
        {"Action": "fail", "Package": "m/calc", "Elapsed": 0}]) + "\nnoise\n")
    parsed = leerie._parse_runner_report("go-json", rep)
    assert parsed == {"executed": 0, "collection_error": True,
                      "cases": {"m/calc :: (package failed to build)":
                                "load_error"}}


def test_failing_ids_are_unchanged_for_junit(leerie, tmp_path):
    """B*'s persisted IDs keep their shape after the per-case refactor."""
    ids = leerie._runner_report_failing_ids("junit", FIXTURES / "junit.xml",
                                            str(tmp_path))
    assert list(ids) == [
        "tests.test_defect :: tests.test_defect.test_add_small"]


# --- A1: own start, per case ------------------------------------------------

def test_sets_are_written_and_validated_at_the_runs_own_start(
        leerie, tmp_path, monkeypatch):
    """An earlier same-task run started on an older tree: the sets are still
    validated at THIS run's own start, and the older base is recorded only
    as `report_reproduced`."""
    repo, old = _repo(tmp_path)
    (repo / "notes.txt").write_text("later\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "later")
    own = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, own)
    sib = st.run_dir.parent / "run-old"
    sib.mkdir()
    (sib / "state.json").write_text(json.dumps({
        "task": st.data["task"], "started_at": "2026-01-01",
        "repo_state_before_planning": {"head": old}}))
    seen_heads = []
    _writer(leerie, monkeypatch, {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sum"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"])},
        probe=lambda wt: seen_heads.append(_git(wt, "rev-parse", "HEAD")))
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    assert seen_heads == [own]
    assert acc["validity_base"] == own and acc["report_base"] == old
    (s,) = acc["sets"]
    assert s["report_reproduced"] is True      # still buggy at the old base


def test_report_reproduced_is_false_where_the_old_base_passes(
        leerie, tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path)
    (repo / "calc.py").write_text(FIXED)
    _git(repo, "commit", "-qam", "fixed")
    fixed = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text(BUGGY)
    _git(repo, "commit", "-qam", "regressed")
    own = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, own)
    sib = st.run_dir.parent / "run-old"
    sib.mkdir()
    (sib / "state.json").write_text(json.dumps({
        "task": st.data["task"], "started_at": "2026-01-01",
        "repo_state_before_planning": {"head": fixed}}))
    _writer(leerie, monkeypatch, {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sum"])})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    (s,) = acc["sets"]                          # valid: it is what own says
    assert s["report_reproduced"] is False


def test_the_writer_reaches_no_ref_and_no_later_commit(leerie, tmp_path,
                                                       monkeypatch):
    repo, own = _repo(tmp_path)
    _git(repo, "checkout", "-qb", "leerie/runs/earlier")
    (repo / "calc.py").write_text(FIXED)
    _git(repo, "commit", "-qam", "an earlier run's fix")
    fix = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "tag", "v1")
    st = _st(leerie, tmp_path, repo, own)
    seen = {}

    def probe(wt: Path) -> None:
        seen["refs"] = _git(wt, "for-each-ref")
        seen["remotes"] = _git(wt, "remote")
        seen["head"] = _git(wt, "rev-parse", "HEAD")
        seen["all"] = _git(wt, "log", "--all", "--format=%H").split()
    _writer(leerie, monkeypatch, {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sum"])},
        probe=probe)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    assert seen["refs"] == "" and seen["remotes"] == ""
    assert seen["head"] == own and fix not in seen["all"]
    assert len(acc["sets"]) == 1
    assert not leerie._helper_worktree_path(st, "acceptance-1").exists()
    # The user's checkout and its refs are untouched.
    assert _git(repo, "rev-parse", "leerie/runs/earlier") == fix
    assert _git(repo, "status", "--porcelain") == ""


def test_a_failed_clone_falls_back_to_a_worktree(leerie, tmp_path,
                                                 monkeypatch, capsys):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    real = subprocess.run

    def run(argv, *a, **k):
        if argv[:2] == ["git", "clone"]:
            return subprocess.CompletedProcess(argv, 128, "", "no clone")
        return real(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", run)
    wt = leerie._helper_worktree_path(st, "acceptance-9")
    assert leerie._acceptance_writer_checkout(st, wt, own) == "worktree"
    assert "detached worktree" in capsys.readouterr().out
    assert _git(wt, "rev-parse", "HEAD") == own
    leerie._remove_acceptance_checkout(st, wt)
    assert not wt.exists()


def test_a_defect_file_counts_per_case(leerie, tmp_path, monkeypatch):
    """One failing case among passing ones is a discriminating defect file;
    its failing case IDs are recorded."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    _writer(leerie, monkeypatch, {
        "acc/test_defect_mixed.py": ("defect", MIXED_DEFECT_TEST, ["sum"])})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    (s,) = acc["sets"]
    (failing,) = s["base_failing_cases"]["acc/test_defect_mixed.py"]
    assert "test_add_sums" in failing and "test_add_zero" not in failing


def test_a_red_exit_with_every_case_passing_is_a_passing_control(
        leerie, tmp_path, monkeypatch):
    """A session hook that forces exit 1 (a coverage-threshold stand-in)
    used to discard the set by exit code; per case the control passed."""
    repo, own = _repo(tmp_path)
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n\n"
        "def pytest_sessionfinish(session, exitstatus):\n"
        "    if exitstatus == 0:\n        session.exitstatus = 1\n")
    _git(repo, "commit", "-qam", "threshold")
    own = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, own)
    _writer(leerie, monkeypatch, {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sum"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"])})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    (s,) = acc["sets"]
    assert s["control_files"] == ["acc/test_control_zero.py"]


# --- N1-L: written whenever the audit ran -----------------------------------

def test_sets_are_written_when_the_audit_said_not_applicable(
        leerie, tmp_path, monkeypatch):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own,
             defect_scope={"applicable": False})
    seen = _writer(leerie, monkeypatch, {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sum"])})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    assert len(acc["sets"]) == 1
    assert "DEFECT CONTRACT (from the defect-scope audit): (none)" in (
        seen[0]["user_prompt"])


def test_a_run_with_no_audit_writes_no_sets(leerie, tmp_path, monkeypatch):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own, categories=["feature"])
    seen = _writer(leerie, monkeypatch, {})
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    assert "not a defect-fix run" in acc["skipped"] and seen == []


def test_no_scoped_runner_is_a_loud_typed_skip(leerie, tmp_path, capsys):
    repo, own = _repo(tmp_path, 'test = "npm test"\n')
    st = _st(leerie, tmp_path, repo, own)
    acc = asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps(leerie), MODELS, EFFORTS))
    assert acc == {"skipped": "no scoped test command",
                   "skip_kind": "no_scoped_runner",
                   "reason": "no_scoped_runner:npm",
                   "test_command": "npm test"}
    assert "WARNING: acceptance: no scoped test runner" in (
        capsys.readouterr().out)


def _auditor(leerie, monkeypatch, answers):
    calls = []

    async def fake(**kw):
        assert kw["schema_key"] == "defect_scope_auditor"
        calls.append(kw)
        a = answers[len(calls) - 1]
        if isinstance(a, BaseException):
            raise a
        return a
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


SITE = {"file": "calc.py", "symbol": "add", "role": "decision_site"}


@pytest.mark.parametrize("answers,applicable,n", [
    ([{"applicable": True, "sites": [SITE], "defect_shape": "x"}], True, 1),
    ([{"applicable": False}, {"applicable": False},
      {"applicable": True, "sites": [SITE], "defect_shape": "x"}], True, 3),
    ([{"applicable": False}] * 3, False, 3),
])
def test_not_applicable_needs_three_agreeing_samples(
        leerie, tmp_path, monkeypatch, answers, applicable, n):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    st.data.pop("defect_scope")
    calls = _auditor(leerie, monkeypatch, answers)
    monkeypatch.setattr(leerie, "_judgment_cwd", lambda _st: str(repo))
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "t", st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert scope["applicable"] is applicable and len(calls) == n
    if applicable:
        # The audit stamps a code-assigned site_id (DESIGN §5 *Every `fix`
        # site is claimed*).
        assert scope["sites"] == [{**SITE, "site_id": "D1"}]


def test_a_crashed_sample_is_no_vote(leerie, tmp_path, monkeypatch):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    caps = dict(leerie.DEFAULT_CAPS)
    calls = _auditor(leerie, monkeypatch, [
        {"applicable": False}, leerie.WorkerError("boom"),
        {"applicable": False}, leerie.WorkerError("boom"),
        {"applicable": False}])
    monkeypatch.setattr(leerie, "_judgment_cwd", lambda _st: str(repo))
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "t", st, caps, MODELS, EFFORTS))
    assert scope == {"applicable": False} and len(calls) == 5


# --- A2: every failing set, B*-confirmed ------------------------------------

def test_a_minority_of_failing_sets_still_fails(leerie):
    res = [{"index": 1, "passed": True, "unmeasured": False},
           {"index": 2, "passed": True, "unmeasured": False},
           {"index": 3, "passed": False, "unmeasured": False}]
    assert leerie._acceptance_any_fails(res) is True
    assert leerie._acceptance_any_fails(res[:2]) is False
    assert leerie._acceptance_any_fails(
        [{"index": 1, "passed": False, "unmeasured": True}]) is False
    assert not hasattr(leerie, "_acceptance_majority_fails")


def _one_set(st, text):
    d = st.run_dir / "acceptance" / "set-1"
    (d / "acc").mkdir(parents=True)
    (d / "acc" / "test_defect_1.py").write_text(text)
    return [{"index": 1, "valid": True, "dir": str(d),
             "defect_files": ["acc/test_defect_1.py"], "control_files": [],
             "cases": {"acc/test_defect_1.py": ["c"]}}]


def test_a_flaky_set_failure_is_resource_affected_not_a_failure(
        leerie, tmp_path):
    """Fails once (the first run), passes on the B* reruns: no evidence,
    never a repair."""
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    marker = tmp_path / "ran-once"
    flaky = ("import pathlib\n\ndef test_flaky():\n"
             f"    m = pathlib.Path({str(marker)!r})\n"
             "    first = not m.exists()\n    m.write_text('x')\n"
             "    assert not first\n")
    sets = _one_set(st, flaky)
    (res,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie), str(repo), sets, "t"))
    assert res["unmeasured"] is True and res["passed"] is False
    assert res["failing_files"] == []
    assert res["resource_affected_files"] == ["acc/test_defect_1.py"]
    assert leerie._acceptance_any_fails([res]) is False


def test_a_consistent_set_failure_is_confirmed(leerie, tmp_path):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    sets = _one_set(st, DEFECT_TEST)
    (res,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie), str(repo), sets, "t"))
    assert res["failing_files"] == ["acc/test_defect_1.py"]
    assert res["unmeasured"] is False and "resource_affected_files" not in res
    # Unconfirmed (confirm=False) reads the single run as it is.
    (raw,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie), str(repo), sets, "t2", confirm=False))
    assert raw["failing_files"] == ["acc/test_defect_1.py"]


def test_still_failing_needs_every_rerun(leerie):
    caps = {"confirm_pairs": 3, "confirm_pairs_escalated": 5,
            "confirm_budget_sec": 100, "confirm_psi_cpu_max": 40,
            "confirm_gate_wait_sec": 1}
    runs = iter([{"a", "b"}, {"a"}, {"a", "b"}])

    async def rerun(_t):
        return next(runs)
    out = asyncio.run(leerie._confirm_still_failing(
        ["a", "b"], rerun, caps, label="t", read_psi=lambda: 0.0))
    assert out["confirmed"] == ["a"] and out["resource_affected"] == ["b"]

    async def none(_t):
        return None
    out = asyncio.run(leerie._confirm_still_failing(
        ["a"], none, caps, label="t", read_psi=lambda: 0.0))
    assert out["confirmed"] == [] and out["resource_affected"] == ["a"]
    # A gate timeout escalates the reruns.
    n = {"i": 0}

    async def count(_t):
        n["i"] += 1
        return {"a"}

    async def nosleep(_s):
        return None
    out = asyncio.run(leerie._confirm_still_failing(
        ["a"], count, caps, label="t", read_psi=lambda: None, sleep=nosleep))
    assert out["reruns_required"] == 5 and n["i"] == 5


def test_head_at_the_validity_base_is_known_without_running(
        leerie, tmp_path, monkeypatch):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    st.data["acceptance"] = {"validity_base": own, "sets": [
        {"index": 1, "defect_files": ["a/test_d.py"]}]}

    async def noop(_st):
        return None

    async def boom(*a, **k):
        raise AssertionError("must not run the sets")
    monkeypatch.setattr(leerie, "_ensure_planning_worktree", noop)
    monkeypatch.setattr(leerie, "_judgment_cwd", lambda _st: repo)
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", boom)
    res = asyncio.run(leerie._acceptance_results_on_head(st, {}))
    assert res == [{"index": 1, "passed": False, "unmeasured": False,
                    "failing_files": ["a/test_d.py"]}]


# --- A2: the broken-set hook ------------------------------------------------

def _completed_sibling(st, name, data):
    d = st.run_dir.parent / name
    d.mkdir()
    (d / "state.json").write_text(json.dumps(data))
    (d / "orchestrator.exit_code").write_text("0")
    return d


@pytest.mark.parametrize("broken,want", [
    (set(), {"failing_sets": 2, "cases": ["one", "two"]}),
    ({2}, {"failing_sets": 1, "cases": ["one"]}),
    ({1, 2}, None),
])
def test_a_set_typed_broken_is_not_handed_on(leerie, tmp_path, broken, want):
    repo, own = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, own)
    _completed_sibling(st, "run-prev", {
        "task": st.data["task"],
        "acceptance": {
            "sets": [{"index": i, "broken": i in broken} for i in (1, 2, 3)],
            "gate": {"residual": {
                "failing_sets": 2, "total_sets": 3, "cases": ["one", "two"],
                "sets": [1, 2],
                "cases_by_set": {"1": ["one"], "2": ["two"]}}}}})
    got = (leerie._prior_delivery_residual(st) or {}).get("acceptance_unmet")
    if want is None:
        assert got is None
    else:
        assert got["failing_sets"] == want["failing_sets"]
        assert got["cases"] == want["cases"]


def test_a_legacy_residual_without_set_indices_is_kept(leerie):
    legacy = {"failing_sets": 1, "total_sets": 2, "cases": ["x"]}
    assert leerie._acceptance_residual_unbroken(
        legacy, [{"index": 1, "broken": True}]) is legacy
