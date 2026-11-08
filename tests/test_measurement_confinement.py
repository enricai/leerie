"""A command leerie runs on a repository's code is confined like a worker
(DESIGN §6 *Leerie's runs of the repository's commands are confined too*).
A real child confined by `scripts/landlock_exec.py` with the grants
`_measurement_grants` derives, on the run layout `test_worker_confinement`
builds: it works in its own tree and measurement directory, and is refused
everything else of the run's state."""
from __future__ import annotations

import os
from pathlib import Path

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _abi, _probe, pytestmark, run)


def _state(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _measure(leerie, run, tree: Path, kind=None):
    st = _state(run)
    if kind is None:
        return leerie._measurement_grants(st, tree)
    return leerie._measurement_grants(st, tree, kind)


def test_a_measured_command_works_in_its_own_tree(leerie, run):
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = _measure(leerie, run, view)
    own = leerie._measure_dir(_state(run), view)
    stores = run["run_dir"] / "worktrees" / ".stores" / "stores"
    stores.mkdir(parents=True, exist_ok=True)
    (stores / "pkg.txt").write_text("stored\n")
    got = _probe(leerie, g, f"""
write tree|echo x > {view}/out.txt
write own dir|echo x > {own}/report.xml
read tree|cat {view}/app.py
git status|git -C {view} status --porcelain
read store|cat {stores}/pkg.txt
write the run store|echo x > {stores}/planted
""", dict(g.env))
    assert all(got.values()), got


def test_a_measured_command_is_refused_the_runs_state(leerie, run):
    rd, w = run["run_dir"], run["wts"]
    view = w[f"{rd.name}-view-staging"]
    g = _measure(leerie, run, view)
    stores = run["state_root"] / "runs" / "another-run" / "worktrees" / ".stores" / "stores"
    stores.mkdir(parents=True, exist_ok=True)
    cache = rd / "worktrees" / ".stores" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    got = _probe(leerie, g, f"""
read held-out set|cat {rd}/acceptance/set-1/test_held.py
list held-out sets|ls {rd}/acceptance
write held-out dir|echo x > {rd}/acceptance/new.py
read state|cat {rd}/state.json
write state|echo x >> {rd}/state.json
write worker cache|echo x > {cache}/planted
write another run store|echo x > {stores}/planted
move run repo ref|git --git-dir={run['repo']} update-ref refs/heads/main HEAD
write run repo refs|echo x > {run['repo']}/refs/heads/planted
write tree repo|echo x > {rd}/tree-repos/staging/planted
write sibling tree|echo x > {w['wt-b']}/planted.py
read sibling tree|cat {w['wt-b']}/secret.py
write staging|echo x > {w['staging']}/planted.py
write user checkout|echo x > {run['user']}/planted.py
""", dict(g.env))
    assert not any(got.values()), {k: v for k, v in got.items() if v}
    assert (rd / "state.json").read_text() == "{}\n"


def test_a_measurement_dir_is_its_trees_alone(leerie, run):
    rd, w = run["run_dir"], run["wts"]
    view = w[f"{rd.name}-view-staging"]
    other = leerie._measure_dir(_state(run), w["wt-a"])
    other.mkdir(parents=True, exist_ok=True)
    (other / "left.txt").write_text("from another tree\n")
    g = _measure(leerie, run, view)
    got = _probe(leerie, g, f"""
read other measurement|cat {other}/left.txt
write other measurement|echo x > {other}/planted
""", dict(g.env))
    assert not any(got.values()), {k: v for k, v in got.items() if v}


def test_its_temp_and_cache_are_its_trees_private_dir(leerie, run):
    """`TMPDIR`, `TMP`, `TEMP` and `XDG_CACHE_HOME` name the tree's private
    temp directory under `/tmp/l/` (DESIGN §6 *A confined command's scratch
    is short and private*), recorded in its measurement directory's
    sidecar; mise's state stays in the measurement directory."""
    import json
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = _measure(leerie, run, view)
    own = leerie._measure_dir(_state(run), view).resolve()
    env = dict(g.env)
    tmp = Path(env["TMPDIR"])
    assert tmp.parent == leerie.PRIVATE_TMP_ROOT
    assert env["TMP"] == env["TEMP"] == env["XDG_CACHE_HOME"] == str(tmp)
    side = json.loads((own.parent / f"{own.name}.tree").read_text())
    assert side["tmp"] == str(tmp)
    # Every place it names — a value that is an absolute path; the rest
    # (pnpm's read-only-store booleans) are settings, not places — is the
    # private temp directory, beneath it, or beneath the measurement
    # directory.
    places = {k: Path(v) for k, v in env.items() if v.startswith("/")}
    assert set(places) >= {"TMPDIR", "MISE_STATE_DIR", "XDG_STATE_HOME",
                           "XDG_RUNTIME_DIR"}
    assert all(p.is_relative_to(tmp) or p.resolve().is_relative_to(own)
               for p in places.values()), places
    assert places["XDG_RUNTIME_DIR"].is_relative_to(tmp)
    assert all(not v.startswith(("/", ".")) for k, v in env.items()
               if k not in places)
    got = _probe(leerie, g, 'temp write|echo x > "$TMPDIR/t"', env)
    assert got == {"temp write": True}
    # Its runtime directory and its gpg home are in it too, made with it.
    gnupg = Path(env["GNUPGHOME"])
    assert gnupg.parent == tmp
    assert sorted(os.listdir(tmp)) == sorted(["run", "t", gnupg.name])
