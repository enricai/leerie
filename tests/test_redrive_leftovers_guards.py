"""Review round 11 M1: the leftovers commit of a delivery re-drive group
is bounded (DESIGN §8 *A re-drive group's leftovers are its implementer's*).

The reviewer's driver: leerie's own install and test run produce an
unignored `node_modules/` (300 packages), a coverage report, `junit.xml`,
`.coverage` and a core dump; the implementer then commits its source edit,
runs `npm i` (500 more packages) and its tests, and leaves an editor swap
file. The leftovers commit took 505 files. Now leerie's artifacts stay
leerie's when the implementer's run rewrites them (first producer), an
artifact-shaped name is withheld, and past `redrive_leftovers_max_files`
or `redrive_leftovers_max_bytes` nothing is committed — every withheld
path is listed in the PR, never dropped unseen."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_redrive import World  # noqa: E402
from test_redrive_test_id_diff_refresh import _git  # noqa: E402

ELF_CORE = b"\x7fELF" + b"\0" * 60


def _repo(tmp_path):
    s = tmp_path / "s"
    (s / "src").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "src" / "a.ts").write_text("export const a = 1\n")
    (s / "package.json").write_text("{}\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    return s


def _before(leerie, s):
    return leerie._tracked_content(s), leerie._untracked_signatures(s)


def _leftovers(leerie, s, before, caps=None):
    withheld: dict = {}
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), withheld=withheld, caps=caps))
    return got, withheld


def _head_files(s):
    return sorted(_git(s, "show", "--name-only", "--format=",
                       "HEAD").split())


def test_the_reviewers_driver_commits_nothing_and_lists_it(leerie,
                                                           tmp_path):
    s = _repo(tmp_path)

    async def leerie_cmds():
        async with leerie._leaves_tracked_files(s, "install/build", "t"):
            for i in range(300):
                d = s / "node_modules" / f"pkg{i}"
                d.mkdir(parents=True)
                (d / "index.js").write_text("x\n")
        async with leerie._leaves_tracked_files(s, "test run", "t"):
            (s / "coverage").mkdir()
            (s / "coverage" / "index.html").write_text("<html>1</html>")
            (s / "junit.xml").write_text("<testsuite n='1'/>")
            (s / ".coverage").write_bytes(b"\0" * 4096)
            (s / "core").write_bytes(b"\x7fELF" + b"\0" * (2 << 20))
    asyncio.run(leerie_cmds())
    before = _before(leerie, s)
    (s / "src" / "a.ts").write_text("export const a = 2\n")
    _git(s, "add", "src/a.ts")
    _git(s, "commit", "-qm", "fix")
    head = _git(s, "rev-parse", "HEAD")
    for i in range(300, 800):
        d = s / "node_modules" / f"pkg{i}"
        d.mkdir(parents=True)
        (d / "index.js").write_text("y\n")
    (s / "coverage" / "index.html").write_text("<html>2</html>")
    (s / "junit.xml").write_text("<testsuite n='2'/>")
    (s / ".coverage").write_bytes(b"\1" * 4096)
    (s / "a.ts.swp").write_bytes(b"swap")
    (s / "core").write_bytes(b"\x7fELF" + b"\1" * (2 << 20))
    got, withheld = _leftovers(leerie, s, before)
    assert got == [] and _git(s, "rev-parse", "HEAD") == head
    # Leerie's rewritten artifacts are no candidates at all; the swap file
    # is withheld by shape; the 500 new packages are over the cap.
    assert withheld["artifacts"] == ["a.ts.swp"]
    over = withheld["over_cap"]
    assert len(over) == 500 and all(p.startswith("node_modules/")
                                    for p in over)


def test_artifact_shapes_are_withheld_and_a_source_beside_them_ships(
        leerie, tmp_path):
    s = _repo(tmp_path)
    before = _before(leerie, s)
    (s / "src" / "helper.ts").write_text("export const h = 1\n")
    (s / "src" / ".a.ts.swp").write_bytes(b"swap")
    (s / "src" / "a.ts~").write_text("old\n")
    (s / "src" / ".#a.ts").write_text("lock\n")
    (s / "src" / "#a.ts#").write_text("auto\n")
    (s / "mod.cpython-312.pyc").write_bytes(b"\0")
    (s / ".DS_Store").write_bytes(b"\0")
    (s / "core.4242").write_bytes(ELF_CORE)
    (s / "core").write_text("#!/bin/sh\necho a script named core\n")
    got, withheld = _leftovers(leerie, s, before)
    assert got == ["core", "src/helper.ts"]
    assert _head_files(s) == ["core", "src/helper.ts"]
    assert sorted(withheld["artifacts"]) == sorted([
        "src/.a.ts.swp", "src/a.ts~", "src/.#a.ts", "src/#a.ts#",
        "mod.cpython-312.pyc", ".DS_Store", "core.4242"])


def test_the_file_cap_and_the_byte_cap_each_withhold_everything(leerie,
                                                                tmp_path):
    caps = {**leerie.DEFAULT_CAPS, "redrive_leftovers_max_files": 3,
            "redrive_leftovers_max_bytes": 1000}
    s = _repo(tmp_path)
    before = _before(leerie, s)
    for i in range(3):
        (s / f"f{i}.ts").write_text("x\n")
    got, withheld = _leftovers(leerie, s, before, caps)
    assert got == ["f0.ts", "f1.ts", "f2.ts"]          # at the cap: taken
    before = _before(leerie, s)
    (s / "src" / "a.ts").write_text("export const a = 3\n")   # tracked
    for i in range(3, 6):
        (s / f"f{i}.ts").write_text("x\n")
    head = _git(s, "rev-parse", "HEAD")
    got, withheld = _leftovers(leerie, s, before, caps)
    assert got == [] and _git(s, "rev-parse", "HEAD") == head
    assert withheld["over_cap"] == ["f3.ts", "f4.ts", "f5.ts", "src/a.ts"]
    # One file over the byte cap.
    s2 = _repo(tmp_path / "b")
    before = _before(leerie, s2)
    (s2 / "big.json").write_text("x" * 1001)
    got, withheld = _leftovers(leerie, s2, before, caps)
    assert got == [] and withheld["over_cap"] == ["big.json"]


def test_the_defaults_are_real_caps(leerie):
    assert leerie.DEFAULT_CAPS["redrive_leftovers_max_files"] == 100
    assert leerie.DEFAULT_CAPS["redrive_leftovers_max_bytes"] == 5 << 20


def test_a_kept_groups_withheld_files_reach_the_pr(leerie, tmp_path,
                                                   monkeypatch):
    """End to end: the implementer fixes the defect, leaves its new helper
    and an editor swap file uncommitted. The helper ships; the swap file is
    listed under "Needs human verification" by path."""
    w = World(leerie, tmp_path)
    w.install(monkeypatch)

    def fix(w):
        (w.staging / "src" / "helper.ts").write_text("export const h = 1\n")
        (w.staging / "src" / ".a.ts.swp").write_bytes(b"swap")
        w.write("export const a = 1; // FIXED\n")
    w.edits = [fix]
    w.run()
    ge = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not ge.get("rolled_back")
    assert ge["leftovers"] == ["src/helper.ts"]
    assert ge["leftovers_withheld"] == {"artifacts": ["src/.a.ts.swp"]}
    nhv = [e for e in w.st.data.get("needs_human_verification") or []
           if e.get("source") == "redrive_leftovers"]
    assert len(nhv) == 1 and "src/.a.ts.swp" in nhv[0]["detail"]
    assert "src/.a.ts.swp" in leerie._format_pr_end_state_section(w.st.data)


def test_a_rolled_back_groups_withheld_files_are_not_listed(leerie):
    class St:
        data: dict = {}

        def save(self):
            pass
    st = St()
    st.data = {"needs_human_verification": [{"source": "other",
                                             "detail": "kept"}]}
    rec = {"rounds": [{"groups": [
        {"leftovers_withheld": {"artifacts": ["x.swp"]},
         "rolled_back": True},
        {"leftovers_withheld": {"over_cap": ["a.ts", "b.ts"]}}]}]}
    leerie._set_redrive_leftovers_gap(st, rec)
    entries = st.data["needs_human_verification"]
    assert entries[0] == {"source": "other", "detail": "kept"}
    assert "a.ts, b.ts" in entries[1]["detail"]
    assert "x.swp" not in entries[1]["detail"]
    leerie._set_redrive_leftovers_gap(st, {"rounds": []})
    assert st.data["needs_human_verification"] == [
        {"source": "other", "detail": "kept"}]
