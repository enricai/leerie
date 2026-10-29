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
file that could be a source file is listed in the PR, never dropped unseen.
Review round 12 M1: an artifact shape is logged only, never listed."""
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


def test_a_kept_groups_artifacts_are_withheld_but_not_listed(
        leerie, tmp_path, monkeypatch):
    """End to end: the implementer fixes the defect, leaves its new helper
    and an editor swap file uncommitted. The helper ships; the swap file is
    withheld and recorded on the group, and the PR has nothing to say about
    it (review round 12 M1 — round 11 listed it under "Needs human
    verification")."""
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
    assert not [e for e in w.st.data.get("needs_human_verification") or []
                if e.get("source") == "redrive_leftovers"]
    assert leerie._format_pr_end_state_section(w.st.data) == ""


def test_a_clean_pytest_redrive_lists_no_bytecode(leerie, tmp_path,
                                                  monkeypatch):
    """The round-12 reviewer's driver (`test_zz_r12_pyc_noise.py`, which
    asserted the entry on b40117f): a repository that does not ignore
    `__pycache__/`, an implementer that adds a module and its test, runs
    pytest on them and commits only its sources. A fully clean group."""
    import subprocess
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    w = World(leerie, tmp_path)
    w.install(monkeypatch)

    def fix(w):
        s = w.staging
        (s / "helper.py").write_text("def h():\n    return 1\n")
        (s / "test_helper.py").write_text(
            "from helper import h\n\ndef test_h():\n    assert h() == 1\n")
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                        "no:cacheprovider", "test_helper.py"], cwd=s,
                       capture_output=True)
        (s / "src" / "a.ts").write_text("export const a = 1; // FIXED\n")
        _git(s, "add", "helper.py", "test_helper.py", "src/a.ts")
        _git(s, "commit", "-qm", "fix")
    w.edits = [fix]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not g.get("rolled_back") and "FIXED" in w.a_text("HEAD")
    assert any(p.endswith(".pyc")
               for p in g["leftovers_withheld"]["artifacts"])
    assert not any(".pyc" in (e.get("detail") or "") for e in
                   w.st.data.get("needs_human_verification") or [])
    assert leerie._format_pr_end_state_section(w.st.data) == ""


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
    # A kept group's artifact shape is never listed on its own.
    leerie._set_redrive_leftovers_gap(st, {"rounds": [{"groups": [
        {"leftovers_withheld": {"artifacts": ["y.pyc"]}}]}]})
    assert st.data["needs_human_verification"] == [
        {"source": "other", "detail": "kept"}]
    leerie._set_redrive_leftovers_gap(st, {"rounds": []})
    assert st.data["needs_human_verification"] == [
        {"source": "other", "detail": "kept"}]
