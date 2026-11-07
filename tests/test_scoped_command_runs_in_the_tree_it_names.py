"""A scoped test command is rendered for the tree it runs in (DESIGN §9
*Operands that are already test files take the runner's direct form*).

Run d7c06fe3's re-drive group recorded `unmeasured: ["test ids"]` although
its vitest runs wrote a report. The command was rendered over the staging
tree and run in the staging tree's committed view: the `--exclude` it
carries for a file vitest's substring rule would also select named a path
under `worktrees/staging/`, outside the view the run's root is. Under the
worker's grants that path's directory cannot be listed, vitest found no test
file at all, and a report that ran no test is no verdict. The same
fingerprint is in all four recorded runs whose re-drive baseline command
carried such an exclude (035b587e, 396c84e3, 673a0cc6, d7c06fe3); the ones
without it measured.

Real `_measure_axes` and `_ratchet_test_ids` over a real repository, the
view a real copy elsewhere; only the command's execution is stubbed, and what
is checked is the value the stub is handed.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil

from tests.test_forbidden_commands import (SUITE, _coupled_repo, _measure_st,
                                           _stub_measure)


def _world(leerie, tmp_path, monkeypatch):
    root, base, _head = _coupled_repo(tmp_path)
    # `rand.test.ts` is named (coupled to the change); `rand.test.tsx` is not
    # (untracked, so in no diff), but vitest's substring filter selects it
    # too: a collider to exclude.
    (root / "src/lib/rand.test.tsx").write_text("export {};\n")
    # The committed view: a checkout of the same files at another path, as
    # `view-staging` is to `staging`.
    view = tmp_path / "worktrees" / "view-staging"
    shutil.copytree(root, view)
    leerie._SOURCE_GRAPHS.clear()
    ran: list[tuple[str, str]] = []
    run_cmd = _stub_measure(leerie, monkeypatch)

    async def fake_measure_blt(axis, cmd, tree, **kw):
        ran.append((cmd, str(tree)))
        run_cmd.append(cmd)
        return {"ran": True, "measured": True, "passed": True,
                "summary": "", "command": cmd}

    async def view_of(st, tree):
        return view

    monkeypatch.setattr(leerie, "_measure_blt", fake_measure_blt)
    monkeypatch.setattr(leerie, "_committed_view", view_of)
    st = _measure_st(leerie, root, base, {"process_constraints": {
        "entries": SUITE}})
    return root, view, st, ran


def _excluded(cmd: str) -> list[str]:
    words = shlex.split(cmd)
    return [words[i + 1].replace("\\", "") for i, w in enumerate(words)
            if w == "--exclude"]


def test_the_scoped_command_excludes_paths_of_the_tree_it_runs_in(
        leerie, tmp_path, monkeypatch):
    root, view, st, ran = _world(leerie, tmp_path, monkeypatch)
    asyncio.run(leerie._measure_axes(
        str(root), {"tests": "pnpm run test"}, st, dict(leerie.DEFAULT_CAPS),
        log_path=tmp_path / "l.log", verbosity="quiet", memo=False))
    ((cmd, ran_in),) = ran
    assert ran_in == str(view)
    excluded = _excluded(cmd)
    assert excluded == [os.path.join(os.path.realpath(view),
                                     "src/lib/rand.test.tsx")], cmd
    assert str(root) not in cmd


def test_the_ratchets_command_names_the_tree_its_ids_are_measured_in(
        leerie, tmp_path, monkeypatch):
    """What `_ratchet_test_ids` hands on as `command` is what ran, and is
    what the re-drive's discovery and base reruns are given."""
    root, view, st, ran = _world(leerie, tmp_path, monkeypatch)
    got = asyncio.run(leerie._ratchet_test_ids(
        st, dict(leerie.DEFAULT_CAPS), str(root), label="t",
        log_path=tmp_path / "l.log"))
    assert got is not None and got["ids"] == []
    assert [t for _c, t in ran] == [str(view)]
    for path in _excluded(got["command"]):
        assert os.path.commonpath([path, os.path.realpath(view)]) == \
            os.path.realpath(view), got["command"]
    assert _excluded(got["command"]), got["command"]
