"""CI parity (DESIGN §9 *The repository's own merge checks run too*, plan
row N6).

The `ci_checks` worker only types; code checks provenance, selects, and
decides by execution what gates. The pure helpers are tested on values; the
two halves (`_capture_ci_parity_baseline`, `_run_ci_parity_final`) are
driven for real against a git repo with real child processes — only the
worker (`claude_p`), the broker (`_cgroup_create` → None, so commands run
uncontained) and the host-pressure gate are stubbed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import _run

WORKFLOW = """\
name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: python3 lint.py src
      - run: python3 lint.py src --strict
      - run: python3 build.py
      - run: python3 flaky.py
      - run: python3 deploy.py
      - run: python3 -m pytest
      - run: python3 db.py
        env:
          DATABASE_URL: postgresql://ci@localhost:5432/ci
      - run: sh -c prisma db push
"""

# Green only against the database this run handed it: the check stands for
# a database-backed CI step (DESIGN §8 *Prisma repositories get a real
# database*).
DBCHECK = (
    "import os, sys\n"
    "sys.exit(0 if os.environ.get('DATABASE_URL', '').endswith('_test')"
    " and os.environ.get('DIRECT_URL') == os.environ['DATABASE_URL']"
    " else 1)\n")

# A toy linter with fixed output: an error line per `ERROR` marker (exit 1),
# and, under --strict, a warning line per `WARN` marker (exit 1 when any).
LINT = r'''
import pathlib, sys
strict = "--strict" in sys.argv
stricter = pathlib.Path("STRICTER").exists()
rc = 0
for p in sorted(pathlib.Path(sys.argv[1]).rglob("*.txt")):
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if "ERROR" in line:
            print(f"{p}:{n}:1 lint/error something is broken"); rc = 1
        if strict and ("WARN" in line or (stricter and "fine" in line)):
            print(f"{p}:{n}:3 lint/style {line.strip()}"); rc = 1
sys.exit(rc)
'''
BUILD = "import sys, pathlib; sys.exit(1 if pathlib.Path('BROKEN').exists() else 0)\n"
# Fails once per marker file, then passes: a flaky check.
FLAKY = r'''
import pathlib, sys
m = pathlib.Path(sys.argv[0]).resolve().parent / ".flaky_ran"
if pathlib.Path("FLAKE").exists() and not m.exists():
    m.write_text("x"); sys.exit(1)
sys.exit(0)
'''


def _check(cid, argv, kind="lint", trigger=("pull_request",), requires=(),
           strict=(), cov=None, env=()):
    return {"id": cid, "kind": kind, "argv": list(argv), "cwd": ".",
            "env": [{"name": n, "value": v} for n, v in env],
            "prerequisites": [], "trigger": list(trigger),
            "path_filters": [], "requires": list(requires),
            "strict_argv": list(strict),
            "source": {"file": ".github/workflows/ci.yml", "job": "checks",
                       "step": cid},
            "coverage": cov or {"summary_path": "", "config_file": "",
                                "thresholds": []},
            "notes": ""}


def _sh(argv, cwd):
    subprocess.run(argv, cwd=str(cwd), capture_output=True, check=True)


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def env(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh(["git", "init", "-q", "-b", "main"], cwd=repo)
    _sh(["git", "config", "user.email", "t@t"], cwd=repo)
    _sh(["git", "config", "user.name", "t"], cwd=repo)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    (repo / "lint.py").write_text(LINT)
    (repo / "build.py").write_text(BUILD)
    (repo / "flaky.py").write_text(FLAKY)
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("fine\n")
    (repo / "src" / "old.txt").write_text("WARN legacy\n")
    (repo / ".gitignore").write_text(".leerie/\n.flaky_ran\n")
    _sh(["git", "add", "-A"], cwd=repo)
    _sh(["git", "commit", "-q", "-m", "base"], cwd=repo)
    run_id = "ci-001-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _sh(["git", "worktree", "add", "-q", "-b", f"leerie/runs/{run_id}",
          str(staging), "main"], cwd=repo)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x"}
    st.save()
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update({"confirm_pairs": 2, "confirm_pairs_escalated": 2,
                 "confirm_budget_sec": 120, "worker_timeout_sec": 60})
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)

    async def _gate(caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 1.0,
                "readable": True}
    monkeypatch.setattr(leerie, "_confirm_gate", _gate)
    calls: list[dict] = []
    checks: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        return {"checks": checks, "excluded": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return {"leerie": leerie, "repo": repo, "run_dir": run_dir,
            "staging": staging, "st": st, "caps": caps, "calls": calls,
            "checks": checks}


def _baseline(env):
    lr = env["leerie"]
    _run(lr._capture_ci_parity_baseline(
        env["run_dir"], env["st"], env["caps"],
        {"ci_checks": "sonnet"}, {"ci_checks": "medium"}))
    return {c["id"]: c for c in env["st"].data["ci_parity"]["checks"]}


def _commit(env, files: dict[str, str]):
    for rel, text in files.items():
        p = env["staging"] / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(env["staging"], "add", "-A")
    _git(env["staging"], "commit", "-q", "-m", "work")


def _final(env):
    return _run(env["leerie"]._run_ci_parity_final(
        env["run_dir"], env["st"], env["caps"]))


PY = "python3"


# ---- selection and provenance, in code ------------------------------------

def test_selection_is_code(leerie, env):
    env["checks"][:] = [
        _check("lint", [PY, "lint.py", "src"]),
        _check("tests", [PY, "-m", "pytest"], kind="test"),
        _check("deploy", [PY, "deploy.py"], kind="other",
               trigger=("push_default_branch",)),
        _check("e2e", [PY, "build.py"], kind="other",
               requires=("browser",)),
        _check("db", [PY, "build.py"], kind="build", requires=("database",)),
        _check("invented", [PY, "lint.py", "--fix"]),
        _check("matrix", [PY, "${{ matrix.v }}"]),
        _check("hook", [PY, "build.py"], kind="build",
               trigger=("pre_push_hook",)),
    ]
    recs = {r["id"]: r for r in leerie._ci_select(env["checks"],
                                                    env["staging"])}
    assert recs["lint"]["selected"] and recs["hook"]["selected"]
    # `database` is not an external resource (bench n6 scored it eligible).
    assert recs["db"]["selected"]
    assert recs["tests"]["reason"] == "left_to_test_axis"
    assert recs["deploy"]["reason"] == "not_merge_gating"
    assert recs["e2e"]["reason"] == "external_resource"
    assert recs["invented"]["reason"] == "provenance"
    assert any("'--fix'" in p for p in recs["invented"]["problems"])
    assert recs["matrix"]["reason"] == "provenance"
    assert any("placeholder" in p for p in recs["matrix"]["problems"])


def test_provenance_checks_env_cwd_strict_and_source(leerie, env):
    tree = env["staging"]
    ok = _check("lint", [PY, "lint.py", "src"], strict=[PY, "lint.py",
                                                        "--max-warnings=0"])
    assert leerie._ci_check_problems(ok, tree) == []
    # A strict NON-flag token from nowhere is refused; flags are free.
    bad_strict = dict(ok, strict_argv=[PY, "other_tool.py", "--strict"])
    assert leerie._ci_check_problems(bad_strict, tree)
    assert leerie._ci_check_problems(dict(ok, env=[{"name": "SECRET",
                                                    "value": "x"}]), tree)
    assert leerie._ci_check_problems(dict(ok, cwd="../.."), tree)
    assert leerie._ci_check_problems(dict(ok, cwd="nope"), tree)
    assert leerie._ci_check_problems(
        dict(ok, source={"file": "/etc/passwd", "job": "", "step": ""}), tree)
    assert leerie._ci_check_problems(
        dict(ok, source={"file": "missing.yml", "job": "", "step": ""}), tree)


def test_coverage_threshold_provenance(leerie, env):
    tree = env["staging"]
    (tree / "vitest.config.ts").write_text(
        "thresholds: { 'src/lib/billing/**/*.ts': { branches: 95 } }\n")
    cov = {"summary_path": "coverage/coverage-summary.json",
           "config_file": "vitest.config.ts",
           "thresholds": [{"glob": "src/lib/billing/**/*.ts",
                           "metric": "branches", "min": 95}]}
    c = _check("cov", [PY, "build.py"], kind="coverage", cov=cov)
    assert leerie._ci_check_problems(c, tree) == []
    wrong_min = json.loads(json.dumps(cov))
    wrong_min["thresholds"][0]["min"] = 90
    assert leerie._ci_check_problems(dict(c, coverage=wrong_min), tree)
    no_ths = dict(cov, thresholds=[])
    assert leerie._ci_check_problems(dict(c, coverage=no_ths), tree)


# ---- the diagnostic key and the coverage ratchet, on values ---------------

def test_diagnostic_keys_drop_positions_and_unknown_files(leerie, env):
    tree = env["staging"]
    out = (f"{tree}/src/a.txt:3:1 lint/style useOptionalChain ━━━\n"
           "src/a.txt:9:7 lint/style useOptionalChain\n"
           "  > 3 │ code frame line\n"
           "src/nope.txt:1:1 lint/style ghost\n"
           "src/old.txt\n"
           "  12:5  warning  'x' is unused  no-unused-vars\n"
           "\x1b[31msrc/a.txt:1:1 lint/x red\x1b[0m\n")
    keys = leerie._ci_diagnostic_keys(out, tree, ".")
    assert keys == {"src/a.txt\tlint/style useOptionalChain": 2,
                    "src/old.txt\twarning 'x' is unused no-unused-vars": 1,
                    "src/a.txt\tlint/x red": 1}


@pytest.mark.parametrize("final,touched,fires", [
    # The benched regression: billing branches 91.62 -> 91.53, min 95.
    (91.53, ["src/lib/billing/stripe.ts"], True),
    # Not touched by the run: never this run's regression.
    (91.53, ["src/lib/tax/x.ts"], False),
    # Inside epsilon (rounding), or not below the base.
    (91.615, ["src/lib/billing/stripe.ts"], False),
    (91.70, ["src/lib/billing/stripe.ts"], False),
    # Above the minimum: the threshold holds whatever the drop.
    (96.0, ["src/lib/billing/stripe.ts"], False),
])
def test_coverage_ratchet(leerie, final, touched, fires):
    rec = {"cwd": ".", "coverage": {"thresholds": [
        {"glob": "src/lib/billing/**/*.ts", "metric": "branches",
         "min": 95}]},
        "base": {"coverage": {"src/lib/billing/**/*.ts|branches": 91.62}}}
    got = leerie._ci_coverage_fired(
        rec, {"src/lib/billing/**/*.ts|branches": final}, touched,
        leerie.DEFAULT_CAPS["ci_coverage_epsilon_pct"])
    assert bool(got) is fires
    if fires:
        assert got[0]["base"] == 91.62 and got[0]["final"] == 91.53


def test_coverage_pcts_from_covered_and_total(leerie, tmp_path):
    tree = tmp_path
    summ = tree / "s.json"
    summ.write_text(json.dumps({
        "total": {"branches": {"covered": 0, "total": 1, "pct": 0}},
        f"{tree}/src/lib/billing/a.ts": {"branches": {"covered": 3,
                                                      "total": 4}},
        "src/lib/billing/deep/b.ts": {"branches": {"covered": 1,
                                                   "total": 4}},
        "src/lib/tax/c.ts": {"branches": {"covered": 0, "total": 10}}}))
    got = leerie._ci_coverage_pcts(summ, tree, ".", [
        {"glob": "src/lib/billing/**/*.ts", "metric": "branches"},
        {"glob": "src/none/**", "metric": "branches"}])
    assert got == {"src/lib/billing/**/*.ts|branches": 50.0,
                   "src/none/**|branches": None}


# ---- the two halves, executed ---------------------------------------------

def test_no_ci_file_spawns_no_worker(leerie, env):
    _git(env["staging"], "rm", "-q", ".github/workflows/ci.yml")
    _git(env["staging"], "commit", "-q", "-m", "drop ci")
    _baseline(env)
    assert env["calls"] == []
    assert env["st"].data["ci_parity"]["skipped"].startswith("no CI")


def test_baseline_decides_gates_by_execution(leerie, env):
    env["checks"][:] = [
        _check("lint", [PY, "lint.py", "src"],
               strict=[PY, "lint.py", "src", "--strict"]),
        _check("build", [PY, "build.py"], kind="build"),
    ]
    (env["staging"] / "src" / "old.txt").write_text("fine\n")
    _git(env["staging"], "commit", "-qam", "clean base")
    recs = _baseline(env)
    assert env["calls"][0]["schema_key"] == "ci_checks"
    assert env["calls"][0]["autonomous"] is False
    # Strict green at the baseline: the strict form is the gate.
    assert recs["lint"]["gate"] == "strict"
    assert recs["build"]["gate"] == "plain"
    assert not (env["run_dir"] / "worktrees" / "ci-base").exists()


def test_red_final_gate_is_unmet_and_drives_the_outcome(leerie, env):
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build"),
                        _check("lint", [PY, "lint.py", "src"])]
    _baseline(env)
    _commit(env, {"BROKEN": "x\n"})
    unmet = _final(env)
    assert [(u["id"], u["why"]) for u in unmet] == [("build", "red")]
    st = env["st"]
    assert leerie._ci_parity_unmet(st) == unmet
    assert st.data["ci_parity"]["final"]["b_star"]["new"] == ["build"]
    out = leerie._decide_outcome(st, any_commits=True)
    assert out == "unconverged"
    assert any("merge check" in r and "build" in r
               for r in st.data["outcome_reasons"])
    # Idempotent per final commit: no rerun on the same sha.
    st.data["ci_parity"]["final"]["unmet"] = [{"id": "sentinel"}]
    assert _final(env) == [{"id": "sentinel"}]


def test_clean_final_is_done(leerie, env):
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build")]
    _baseline(env)
    _commit(env, {"src/new.txt": "fine\n"})
    assert _final(env) == []
    assert leerie._decide_outcome(env["st"], any_commits=True) == "done"


def test_red_at_baseline_is_not_a_gate(leerie, env):
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build")]
    (env["staging"] / "BROKEN").write_text("x\n")
    _git(env["staging"], "add", "-A")
    _git(env["staging"], "commit", "-qm", "red base")
    recs = _baseline(env)
    assert recs["build"]["reason"] == "red_at_baseline"
    assert not recs["build"].get("gate")
    assert _final(env) == []


def test_flaky_red_is_resource_affected_not_unmet(leerie, env):
    env["checks"][:] = [_check("flaky", [PY, "flaky.py"], kind="other")]
    _baseline(env)
    _commit(env, {"FLAKE": "x\n"})
    assert _final(env) == []
    fin = env["st"].data["ci_parity"]["final"]
    assert fin["resource_affected"] == ["flaky"]


def test_strict_red_at_baseline_ratchets_per_diagnostic(leerie, env):
    env["checks"][:] = [_check(
        "lint", [PY, "lint.py", "src"],
        strict=[PY, "lint.py", "src", "--strict"])]
    recs = _baseline(env)
    # The base already warns (src/old.txt): plain gates, strict ratchets.
    assert recs["lint"]["gate"] == "plain"
    assert recs["lint"]["ratchet"] == "diagnostic"
    assert recs["lint"]["base"]["diagnostics"] == {
        "src/old.txt\tlint/style WARN legacy": 1}
    # The old warning moves (positions dropped: same key), and a new one
    # lands in a file the run changed: only the new one is unmet.
    _commit(env, {"src/old.txt": "\n\nWARN legacy\n",
                  "src/a.txt": "fine\nWARN fresh\n"})
    unmet = _final(env)
    assert [(u["id"], u["why"]) for u in unmet] == [("lint",
                                                     "new_diagnostics")]
    assert unmet[0]["diagnostics"] == ["src/a.txt\tlint/style WARN fresh"]


def test_new_diagnostic_in_an_untouched_file_is_not_this_runs(leerie, env):
    env["checks"][:] = [_check(
        "lint", [PY, "lint.py", "src"],
        strict=[PY, "lint.py", "src", "--strict"])]
    _baseline(env)
    # A config change makes the linter warn on src/a.txt, which the run
    # never changed: out of the touched-file scope, so not unmet (the
    # benched scope; the run's own files are what it answers for).
    _commit(env, {"STRICTER": "x\n"})
    assert _final(env) == []
    res = env["st"].data["ci_parity"]["final"]["results"]["lint"]
    assert res["strict_rc"] == 1


def test_check_that_writes_tracked_files_is_dropped(leerie, env):
    (env["staging"] / "fmt.py").write_text(
        "import pathlib; pathlib.Path('src/a.txt').write_text('changed')\n")
    wf = env["staging"] / ".github" / "workflows" / "ci.yml"
    wf.write_text(wf.read_text() + "      - run: python3 fmt.py\n")
    _git(env["staging"], "add", "-A")
    _git(env["staging"], "commit", "-qm", "fmt")
    env["checks"][:] = [_check("fmt", [PY, "fmt.py"], kind="format")]
    recs = _baseline(env)
    assert recs["fmt"]["reason"] == "writes_tracked_files"
    assert not recs["fmt"].get("gate")


def test_resume_does_not_rediscover(leerie, env):
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build")]
    _baseline(env)
    _baseline(env)
    assert len(env["calls"]) == 1


def test_prior_residual_hands_red_checks_to_the_next_run(leerie, env,
                                                          tmp_path):
    st = env["st"]
    runs = st.run_dir.parent
    prev = runs / "prev-run-0000"
    prev.mkdir()
    (prev / "state.json").write_text(json.dumps({
        "task": "x", "ci_parity": {"final": {"unmet": [
            {"id": "lint", "why": "new_diagnostics",
             "argv": ["pnpm", "exec", "tool", "--strict"], "cwd": ".",
             "diagnostics": ["src/a.ts\trule"]}]}}}))
    (prev / "orchestrator.exit_code").write_text("0\n")
    res = leerie._prior_delivery_residual(st)
    assert res["ci_unmet"] == [{
        "id": "lint", "why": "new_diagnostics",
        "argv": ["pnpm", "exec", "tool", "--strict"], "cwd": ".",
        "diagnostics": ["src/a.ts\trule"], "thresholds": []}]
    assert "ci_unmet" in leerie._load_prompt("planner")


def test_ci_checks_is_a_registered_judgment_worker(leerie):
    assert "ci_checks" in leerie.WORKER_TYPES
    assert "ci_checks" in leerie.PLANNING_WORKER_TYPES
    assert leerie.EFFORT_DEFAULT_PER_WORKER["ci_checks"] == "medium"
    assert "ci_checks" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert "ci_checks" in leerie.SCHEMAS
    assert len(json.dumps(leerie.SCHEMAS["ci_checks"])) < 6100
    assert Path(leerie.__file__).parent.parent.joinpath(
        "prompts", "ci_checks.md").is_file()


# ---- database-backed checks run against the throwaway test database --------

def _fake_test_db(leerie, monkeypatch):
    made: list[tuple[str, str]] = []
    dropped: list[str] = []

    async def fake_create(st, tree, key):
        url = f"postgresql://u@127.0.0.1:5999/leerie_{len(made)}_test"
        made.append((str(tree), url))
        return url
    monkeypatch.setattr(leerie, "_create_test_db", fake_create)
    monkeypatch.setattr(leerie, "_drop_test_db",
                        lambda url: url and dropped.append(url))
    return made, dropped


def test_a_database_check_runs_against_its_own_test_database(
        leerie, env, monkeypatch):
    made, dropped = _fake_test_db(leerie, monkeypatch)
    (env["staging"] / "db.py").write_text(DBCHECK)
    _git(env["staging"], "add", "-A")
    _git(env["staging"], "commit", "-qm", "db check")
    env["checks"][:] = [
        _check("db", [PY, "db.py"], kind="build", requires=("database",),
               env=[("DATABASE_URL", "postgresql://ci@localhost:5432/ci")]),
        _check("build", [PY, "build.py"], kind="build")]
    recs = _baseline(env)
    # Green only with the run's database laid over the CI file's own URL.
    assert recs["db"]["gate"] == "plain", recs["db"]
    assert recs["build"]["gate"] == "plain"
    # One database for the base tree, dropped with it.
    assert [t.endswith("ci-base") for t, _u in made] == [True]
    assert dropped == [made[0][1]]
    _commit(env, {"src/new.txt": "fine\n"})
    assert _final(env) == []
    # Run-id-prefixed helper worktrees (`_helper_worktree_path`).
    prefix = env["st"].run_id[:12] + "-"
    assert [Path(t).name for t, _u in made] == [prefix + "ci-base",
                                               prefix + "ci-final"]
    assert sorted(dropped) == sorted(u for _t, u in made)


def test_no_database_is_made_when_no_check_needs_one(leerie, env,
                                                     monkeypatch):
    made, _dropped = _fake_test_db(leerie, monkeypatch)
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build")]
    _baseline(env)
    assert made == []


def test_a_db_reset_check_or_prerequisite_never_runs(leerie, env, tmp_path):
    repo = env["staging"]
    (repo / "package.json").write_text(json.dumps(
        {"dependencies": {"@prisma/client": "7"}}))
    (repo / "prisma").mkdir()
    (repo / "prisma" / "schema.prisma").write_text(
        'datasource db {\n  provider = "postgresql"\n}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "prisma")
    pushes = _check("push", ["sh", "-c", "prisma db push"], kind="other")
    recs = {r["id"]: r for r in leerie._ci_select([pushes], repo)}
    assert recs["push"]["reason"] == "db_reset"
    st = env["st"]
    st.repo_root = repo
    marker = tmp_path / "RAN"
    out = _run(leerie._ci_run(
        ["sh", "-c", f"touch {marker}; prisma db push"], tree=repo,
        rec={"cwd": "."}, st=st, caps=env["caps"], label="t"))
    assert out["skipped"] == "db_reset" and out["rc"] is None
    assert not marker.exists()
    # An ordinary command in the same repo still runs.
    out = _run(leerie._ci_run(["touch", str(marker)], tree=repo,
                              rec={"cwd": "."}, st=st, caps=env["caps"],
                              label="t2"))
    assert out["rc"] == 0 and marker.exists()


# ---- B* reruns stay inside confirm_budget_sec -----------------------------

def test_a_slow_red_gate_is_confirmed_inside_the_budget(leerie, env,
                                                        monkeypatch):
    """`_ci_confirm_red`'s reruns share `confirm_budget_sec` (DESIGN §9
    *The repository's own merge checks run too*, step 5): a gate that takes
    longer than the budget is cut at the budget, not run to its own
    `worker_timeout_sec`, and the cut never reads as the red going away."""
    env["checks"][:] = [_check("build", [PY, "build.py"], kind="build")]
    _baseline(env)
    _commit(env, {"build.py": "import sys, time\ntime.sleep(6)\nsys.exit(1)\n"})
    env["caps"]["confirm_budget_sec"] = 1.5
    real = leerie._confirm_new_failures
    took: list[float] = []

    async def timed(*a, **k):
        t0 = leerie.time.monotonic()
        try:
            return await real(*a, **k)
        finally:
            took.append(leerie.time.monotonic() - t0)
    monkeypatch.setattr(leerie, "_confirm_new_failures", timed)
    unmet = _final(env)
    # Before the fix the final rerun ran its full 6 s.
    assert len(took) == 1 and took[0] < 1.5 + 2.0, took
    b = env["st"].data["ci_parity"]["final"]["b_star"]
    assert b["budget_exhausted"] is True
    assert b["new"] == ["build"] and b["budget_short"] == ["build"]
    assert [(u["id"], u["why"]) for u in unmet] == [("build", "red")]
    assert leerie._decide_outcome(env["st"], any_commits=True) == "unconverged"


def _fake_ci(leerie, monkeypatch, tmp_path, durations, fails):
    """`_ci_run` replaced by a timed fake: check `id` takes
    `durations[(side, id)]` seconds and fails on the final side when in
    `fails`. Records the timeout each run was handed."""
    seen: list[tuple[str, str, float]] = []

    async def fake_run(argv, *, tree, rec, st, caps, label):
        side = "base" if "confirm-base" in label else "final"
        t = float(caps["worker_timeout_sec"])
        seen.append((side, rec["id"], t))
        d = durations.get((side, rec["id"]), 0.0)
        await leerie.asyncio.sleep(min(d, t))
        if d > t:
            return {"rc": None, "passed": False, "timed_out": True}
        bad = side == "final" and rec["id"] in fails
        return {"rc": 1 if bad else 0, "passed": not bad,
                "timed_out": False}

    async def fake_wt(st, name, sha):
        return tmp_path / name

    async def fake_prepare(*a, **k):
        return None
    monkeypatch.setattr(leerie, "_ci_run", fake_run)
    monkeypatch.setattr(leerie, "_ci_worktree", fake_wt)
    monkeypatch.setattr(leerie, "_ci_prepare", fake_prepare)
    monkeypatch.setattr(leerie, "_ci_teardown", lambda st, wt: None)
    return seen


def test_each_check_of_a_ci_rerun_gets_only_the_budget_left(
        leerie, env, monkeypatch, tmp_path):
    """Check `a` finished passing on the cut final rerun: that refutes it
    (never `new`); `b`, which the budget alone kept short, is
    `new` and `budget_short`. Every check's timeout is what was left."""
    caps = {**env["caps"], "confirm_pairs": 2, "confirm_budget_sec": 1.2,
            "worker_timeout_sec": 60}
    seen = _fake_ci(leerie, monkeypatch, tmp_path,
                    {("final", "a"): 0.2, ("final", "b"): 5.0},
                    fails={"b"})
    recs = [_check("a", [PY, "a.py"]), _check("b", [PY, "b.py"])]
    v = _run(leerie._ci_confirm_red(env["st"], caps, "deadbeef",
                                    tmp_path / "final", recs))
    assert all(t <= 1.2 for _s, _i, t in seen), seen
    # b's final run was handed less than a's: the rerun shares one budget.
    t_a = [t for s, i, t in seen if s == "final" and i == "a"][0]
    t_b = [t for s, i, t in seen if s == "final" and i == "b"][0]
    assert t_b < t_a
    assert v["budget_exhausted"] is True
    assert v["new"] == ["b"] and v["budget_short"] == ["b"]
    assert v["no_verdict"] == ["a"]


def test_a_check_timing_out_with_budget_left_is_no_verdict_not_a_cut(
        leerie, env, monkeypatch, tmp_path):
    """A check's own `worker_timeout_sec`, shorter than the budget left,
    is a real timeout: no verdict, never `new` on the strength of a cut."""
    caps = {**env["caps"], "confirm_pairs": 2, "confirm_budget_sec": 60,
            "worker_timeout_sec": 0.3}
    _fake_ci(leerie, monkeypatch, tmp_path, {("final", "b"): 5.0},
             fails={"b"})
    v = _run(leerie._ci_confirm_red(env["st"], caps, "deadbeef",
                                    tmp_path / "final",
                                    [_check("b", [PY, "b.py"])]))
    assert v["budget_exhausted"] is False
    assert v["new"] == [] and v["budget_short"] == []
    assert v["resource_affected"] == ["b"]
