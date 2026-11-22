"""CI parity's helper worktrees that a run uses more than once — `ci-round`,
`ci-round-pre`, `ci-final` and `ci-confirm-base` — are kept for the run and
moved to the next commit with their install reused when the install key there
is unchanged; `ci-base` and `ci-browser` (one use per process) stay disposable
(DESIGN §9 *The merge checks' trees are kept too*).

Driven through the real `_capture_ci_parity_baseline`, `_ci_round_regressions`
and `_run_ci_parity_final` over the real run repository and real child
processes, with `tests/test_ci_parity.py`'s stubs only (the `ci_checks`
worker, the broker, the host-pressure gate). The install is real and
incremental, as a package manager's is: it counts its runs under its own
output. The merge check is a function of the tree it runs in — green only when
the install is in place (dep == manifest) — and it reports whether anything an
earlier run of it left (an ignored `stale/` report, a tool cache under
`node_modules`) is there, leaving both behind. So each test pins the verdict
(outcome-identity), the saving (the same install, its count incremental), and
that the first check run in every use of a kept tree sees none of what the
previous use left.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_ci_parity import PY, _check, _commit, _git, _run, env  # noqa: E402,F401

pytestmark = pytest.mark.real_planning_worktree

INSTALL = (
    "import pathlib\n"
    "c = pathlib.Path('node_modules/.count')\n"
    "c.parent.mkdir(exist_ok=True)\n"
    "c.write_text(str(int(c.read_text()) + 1 if c.exists() else 1))\n"
    "want = open('package.json').read()\n"
    "p = pathlib.Path('node_modules/dep/index.js')\n"
    "if not p.exists() or p.read_text() != want:\n"
    "    p.parent.mkdir(parents=True, exist_ok=True)\n"
    "    p.write_text(want)\n")

# Green only with its install in place; `src/BROKEN` (tracked, beside no
# file the install reads, so its install key is unchanged) turns it red. It
# prints what it found — `leak=1` when a report or tool cache an earlier run
# left is there — and leaves both behind, as a real build or linter does: the
# first run in each use of a tree must print `leak=0`.
CHECK = r'''
import pathlib, sys
t = pathlib.Path(".")
dep = t / "node_modules" / "dep" / "index.js"
ok = dep.exists() and dep.read_text() == (t / "package.json").read_text()
leak = (t / "stale").exists() or (t / "node_modules" / ".cache").exists()
print(f"OBS ok={int(ok)} leak={int(leak)} broken={int((t / 'src' / 'BROKEN').exists())}")
(t / "stale").mkdir(exist_ok=True)
(t / "stale" / "report.txt").write_text("x")
(t / "node_modules" / ".cache").mkdir(parents=True, exist_ok=True)
(t / "node_modules" / ".cache" / "m").write_text("x")
if (t / "src" / "MUTATE").exists():
    # A check that rewrites a file the install made (same bytes, a new
    # change time), as a code generator writing into a package does.
    dep.write_text(dep.read_text())
sys.exit(0 if ok and not (t / "src" / "BROKEN").exists() else 1)
'''

# A strict variant red at the baseline: a diagnostic ratchet, so a round
# whose start was never measured measures it in `ci-round-pre`.
LINT = r'''
import pathlib, sys
dep = pathlib.Path("node_modules/dep/index.js")
ok = dep.exists() and dep.read_text() == pathlib.Path("package.json").read_text()
leak = pathlib.Path("stale").exists() or pathlib.Path("node_modules/.cache").exists()
print(f"OBS ok={int(ok)} leak={int(leak)} broken=0")
pathlib.Path("stale").mkdir(exist_ok=True)
pathlib.Path("node_modules/.cache").mkdir(parents=True, exist_ok=True)
rc = 0 if ok else 1
if "--strict" in sys.argv:
    for p in sorted(pathlib.Path(sys.argv[1]).rglob("*.txt")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if "WARN" in line:
                print(f"{p}:{n}:3 lint/style {line.strip()}"); rc = 1
sys.exit(rc)
'''


def _count(tree: Path) -> int:
    return int((tree / "node_modules" / ".count").read_text())


def _dep(tree: Path) -> Path:
    return tree / "node_modules" / "dep" / "index.js"


def _obs(env, label: str) -> list[str]:
    """The OBS line(s) the check run logged as `label` printed."""
    p = env["run_dir"] / "logs" / "ci" / f"{label}.log"
    return [ln.strip() for ln in p.read_text(errors="replace").splitlines()
            if ln.startswith("OBS ")]


def _head(env) -> str:
    return _git(env["staging"], "rev-parse", "HEAD")


def _wt(leerie, env, name: str) -> Path:
    return leerie._helper_worktree_path(env["st"], name)


@pytest.fixture
def ci(leerie, env):
    """A repository whose one merge check gates (green at the baseline) and,
    with `lint`, a diagnostic ratchet (the scripts sit at the workflow's own
    names, so their provenance holds); the provision recipe's install is the
    counting one. Returns the env after the baseline."""
    st = env["st"]
    _commit(env, {"build.py": CHECK, "lint.py": LINT,
                  "package.json": '{"name": "x"}\n',
                  ".gitignore": ".leerie/\n.flaky_ran\nnode_modules/\n"
                                "stale/\n"})
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL],
         "working_dir": "."}]}
    st.save()
    env["checks"][:] = [
        _check("check", [PY, "build.py"], kind="build"),
        _check("lint", [PY, "lint.py", "src"],
               strict=[PY, "lint.py", "src", "--strict"])]
    env["caps"]["confirm_pairs"] = 1
    env["caps"]["confirm_pairs_escalated"] = 1
    _run(leerie._capture_ci_parity_baseline(
        env["run_dir"], st, env["caps"], {"ci_checks": "sonnet"},
        {"ci_checks": "medium"}))
    recs = {c["id"]: c for c in st.data["ci_parity"]["checks"]}
    assert recs["check"].get("gate") == "plain", recs["check"]
    assert recs["lint"].get("ratchet") == "diagnostic", recs["lint"]
    yield env
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._KEPT_TREES,
              leerie._KEPT_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS, leerie._DEPS_FAILED):
        d.clear()
    leerie._DEPS_INSTALLED.clear()


def _round(leerie, env, pre: str, post: str, label: str) -> dict:
    return _run(leerie._ci_round_regressions(
        env["st"], env["caps"], env["staging"], pre, post, label=label))


def test_ci_base_stays_disposable(leerie, ci):
    """One use per process: the baseline's tree is gone once it ran, never
    kept to hold its disk."""
    assert not _wt(leerie, ci, "ci-base").exists()
    assert os.path.realpath(_wt(leerie, ci, "ci-base")) not in \
        leerie._KEPT_TREES


def test_rounds_keep_the_ci_round_install(pin, leerie, ci):
    """Two kept rounds whose commits change code, not the install's inputs:
    one `ci-round` tree, one install — the second round reuses it in place
    (same dep, the count incremental, never reset) — and each round's check
    saw a pristine tree with its install: no report or cache the first left,
    and the same verdict (nothing broke)."""
    base = _head(ci)
    _commit(ci, {"src/b.txt": "fine\n"})
    c1 = _head(ci)
    out1 = _round(leerie, ci, base, c1, "r1")
    wt = _wt(leerie, ci, "ci-round")
    assert out1["broke"] == [], out1
    assert wt.is_dir()                              # kept, not torn down
    same, count = pin(_dep(wt)), _count(wt)

    _commit(ci, {"src/c.txt": "fine\n"})
    c2 = _head(ci)
    out2 = _round(leerie, ci, c1, c2, "r2")
    assert out2["broke"] == out1["broke"] == []     # outcome-identity
    assert same()          # the same install
    assert _count(wt) == count + 1                  # incremental, not cold
    assert _git(wt, "rev-parse", "HEAD") == c2
    assert _obs(ci, "r2-post-check") == ["OBS ok=1 leak=0 broken=0"]


def test_a_round_whose_manifest_changed_reinstalls(pin, leerie, ci):
    """The install-key guard: a round commit that changed what the install
    read is a different key, so the kept tree is remade and the deps
    installed afresh — never a stale install for changed inputs."""
    base = _head(ci)
    _commit(ci, {"src/b.txt": "fine\n"})
    c1 = _head(ci)
    _round(leerie, ci, base, c1, "r1")
    wt = _wt(leerie, ci, "ci-round")
    same = pin(_dep(wt))

    _commit(ci, {"package.json": '{"name": "y"}\n'})
    c2 = _head(ci)
    out = _round(leerie, ci, c1, c2, "r2")
    assert out["broke"] == [], out
    assert not same()          # a fresh install
    assert _count(wt) == 1                          # cold: the count reset
    assert _dep(wt).read_text() == '{"name": "y"}\n'


def test_a_red_round_confirms_on_a_kept_base(pin, leerie, ci):
    """A gate red on a round's commit is confirmed under B* against its start
    in `ci-confirm-base`; a second round's confirm reuses that tree's install
    and reaches the same verdict — its base reruns see a pristine checkout,
    so the reports and caches the first confirm's runs left are gone."""
    base = _head(ci)
    _commit(ci, {"src/BROKEN": "1\n"})
    bad1 = _head(ci)
    out1 = _round(leerie, ci, base, bad1, "r1")
    assert [b["id"] for b in out1["broke"]] == ["check"], out1
    cb = _wt(leerie, ci, "ci-confirm-base")
    assert cb.is_dir()                              # kept
    same, count = pin(_dep(cb)), _count(cb)

    _commit(ci, {"src/BROKEN": "2\n"})
    bad2 = _head(ci)
    out2 = _round(leerie, ci, base, bad2, "r2")
    assert [b["id"] for b in out2["broke"]] == ["check"], out2
    assert out2["pre_existing"] == out1["pre_existing"] == []
    assert same()          # the same install
    assert _count(cb) == count + 1                  # incremental, not cold
    # The base tree's first check run in this confirm (its set-up) found
    # nothing the first confirm's runs left; its reruns passed (no BROKEN).
    assert _obs(ci, "r2-confirm-base-setup-check") == [
        "OBS ok=1 leak=0 broken=0"]
    assert _git(cb, "rev-parse", "HEAD") == base


def test_round_starts_never_measured_keep_ci_round_pre(pin, leerie, ci):
    """A round whose start no kept round measured measures its ratchets in
    `ci-round-pre`; a second such round reuses that tree's install."""
    base = _head(ci)
    _commit(ci, {"src/b.txt": "fine\n"})
    c1 = _head(ci)
    _round(leerie, ci, base, c1, "r1")
    pre = _wt(leerie, ci, "ci-round-pre")
    assert pre.is_dir()                             # measured there, kept
    same, count = pin(_dep(pre)), _count(pre)

    _commit(ci, {"src/c.txt": "fine\n"})
    c2 = _head(ci)
    _commit(ci, {"src/d.txt": "fine\n"})
    c3 = _head(ci)
    out = _round(leerie, ci, c2, c3, "r2")          # c2: never measured
    assert out["broke"] == [] and "ratchets_unmeasured" not in out, out
    assert same()
    assert _count(pre) == count + 1
    assert _git(pre, "rev-parse", "HEAD") == c2
    assert _obs(ci, "r2-pre-lint") == ["OBS ok=1 leak=0 broken=0"]


def test_final_passes_keep_ci_final(pin, leerie, ci):
    """Two final passes at different commits (the pass reruns after a later
    commit) share one `ci-final` install, with the same verdict."""
    _commit(ci, {"src/b.txt": "fine\n"})
    assert _run(leerie._run_ci_parity_final(
        ci["run_dir"], ci["st"], ci["caps"])) == []
    wt = _wt(leerie, ci, "ci-final")
    assert wt.is_dir()                              # kept
    same, count = pin(_dep(wt)), _count(wt)

    _commit(ci, {"src/c.txt": "fine\n"})
    assert _run(leerie._run_ci_parity_final(
        ci["run_dir"], ci["st"], ci["caps"])) == []
    assert same()
    assert _count(wt) == count + 1
    assert _git(wt, "rev-parse", "HEAD") == _head(ci)
    assert _obs(ci, "final-check") == ["OBS ok=1 leak=0 broken=0"]


def test_a_check_that_changed_the_install_makes_the_next_use_reinstall(
        pin, leerie, ci):
    """A check that rewrote a file the install made leaves a tree no use may
    trust: the next round's move refuses it (`_install_intact`) and installs
    afresh, with the same verdict."""
    base = _head(ci)
    _commit(ci, {"src/MUTATE": "1\n"})
    c1 = _head(ci)
    out1 = _round(leerie, ci, base, c1, "r1")
    wt = _wt(leerie, ci, "ci-round")
    same = pin(_dep(wt))

    _git(ci["staging"], "rm", "-q", "src/MUTATE")
    _git(ci["staging"], "commit", "-qm", "no mutate")
    c2 = _head(ci)
    out2 = _round(leerie, ci, c1, c2, "r2")
    assert out2["broke"] == out1["broke"] == []     # outcome-identity
    assert not same()          # not the changed install
    assert _count(wt) == 1                          # cold: installed afresh
    assert _obs(ci, "r2-post-check") == ["OBS ok=1 leak=0 broken=0"]


# Each bounded git step a kept tree's reuse runs: the move's checkout and
# clean, the tip's listing for its install key, and the install's read
# watch's and ignored-output listings.
_STEPS = {"checkout": ("checkout", "--detach", "--force"),
          "clean": ("clean", "-ffdqx"), "key": ("ls-tree", "-t"),
          "watch": ("ls-tree", "-d"),
          "outputs": ("ls-files", "--ignored", "--directory")}


@pytest.mark.parametrize("step", sorted(_STEPS))
def test_a_move_that_times_out_remakes_the_tree(pin, leerie, ci, monkeypatch,
                                                step):
    """The move's git commands are bounded (`git clean` in `_move_view`, the
    tip's listing in `_install_key`): one that times out is a difference
    like any other — the tree is remade and installed afresh and the commit
    measured, never skipped (a skipped final pass would be read as no red
    check on that commit for good), and never an exception out of the
    measurement (the install's own key read and read watch list the tip
    too: a key that cannot be read is no reuse)."""
    import subprocess
    base = _head(ci)
    _commit(ci, {"src/b.txt": "fine\n"})
    c1 = _head(ci)
    _round(leerie, ci, base, c1, "r1")
    _commit(ci, {"src/x.txt": "fine\n"})       # a final no round measured
    _run(leerie._run_ci_parity_final(ci["run_dir"], ci["st"], ci["caps"]))
    wt = _wt(leerie, ci, "ci-final")
    same = pin(_dep(wt))

    real = subprocess.run

    def _slow(argv, *a, **k):
        if all(w in argv for w in _STEPS[step]):
            raise subprocess.TimeoutExpired(argv, k.get("timeout") or 1)
        return real(argv, *a, **k)
    monkeypatch.setattr(leerie.subprocess, "run", _slow)
    _commit(ci, {"src/BROKEN": "1\n"})            # key unchanged: a reuse
    assert [u["id"] for u in _run(leerie._run_ci_parity_final(
        ci["run_dir"], ci["st"], ci["caps"]))] == ["check"]
    final = ci["st"].data["ci_parity"]["final"]
    assert final["sha"] == _head(ci) and "skipped" not in final
    if step in ("watch", "outputs"):
        # The move needs no listing of its own: the tree is reused, and
        # with its reads or outputs unknown nothing is recorded, so the next
        # use remakes it rather than trust an install it could not see.
        assert same()
        assert os.path.realpath(wt) not in leerie._KEPT_INSTALLS
    else:
        assert not same()      # remade, installed afresh
        assert _count(wt) == 1
