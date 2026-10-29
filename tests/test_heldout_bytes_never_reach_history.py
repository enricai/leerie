"""Review round 11 H1: no line of a staged held-out set's source reaches
history (DESIGN §8 *No line of a staged set's source reaches history*).

The reviewer's driver: a re-drive implementer runs the staged held-out test
as its note tells it to, in a repository that does not ignore
`__pycache__/`; pytest compiles the hidden test, and the leftovers commit
took `tests/__pycache__/test_hidden_accept.cpython-*.pyc`, whose constants
carry the hidden assertion message into `git show`. The void check compared
staged file NAMES only, so the group stood. Real git and real pytest.

Review round 12 H1 drove the over-reach of round 11's fix (every literal of
the test was a marker, so an honest fix holding the expected message was
voided); the barrier now matches spans of the test's SOURCE, never a value
it asserts. Both directions are pinned here."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_redrive import DEFECT, World  # noqa: E402
from test_redrive_test_id_diff_refresh import _git  # noqa: E402

SECRET = "SECRET_HELDOUT_EXPECTATION"
HIDDEN = ("from mod import f\n\ndef test_secret_case():\n"
          f"    assert f() == 2, '{SECRET}'\n")


def _repo(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    s = tmp_path / "s"
    (s / "tests").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "mod.py").write_text("def f():\n    return 1\n")
    (s / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    return s


def _held(tmp_path, rel="tests/test_hidden_accept.py"):
    held = tmp_path / "held"
    (held / os.path.dirname(rel)).mkdir(parents=True, exist_ok=True)
    (held / rel).write_text(HIDDEN)
    return {"index": 0, "dir": str(held), "defect_files": [rel],
            "cases": {rel: ["test_secret_case"]}}


def _pytest(s, *args):
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                    "no:cacheprovider", *args], cwd=s, capture_output=True,
                   env={**os.environ, "PYTHONPATH": str(s)})


def _history_bytes(s, since):
    """Every blob of every commit since `since`, concatenated."""
    out = b""
    for c in _git(s, "rev-list", f"{since}..HEAD").split():
        for p in _git(s, "show", "--name-only", "--format=", c).split():
            out += subprocess.run(["git", "-C", str(s), "show", f"{c}:{p}"],
                                  capture_output=True).stdout
    return out


def test_the_reviewers_driver_ships_no_bytecode_of_the_hidden_test(
        leerie, tmp_path, monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    assert staged
    prints = leerie._heldout_fingerprints(s, staged, base)
    before = (leerie._tracked_content(s), leerie._untracked_signatures(s))
    (s / "mod.py").write_text("def f():\n    return 2\n")
    _pytest(s, "tests/test_hidden_accept.py")
    # The staged file's bytecode is ignored while the group runs, so the
    # implementer's own `git add -A` cannot take it either.
    assert "test_hidden_accept" not in _git(s, "status", "--porcelain",
                                            "--untracked-files=all")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "fix")
    swept: list[str] = []
    assert leerie._redrive_unstage(s, staged, swept=swept) == []
    assert any("test_hidden_accept" in p and p.endswith(".pyc")
               for p in swept)
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), prints=prints))
    assert not any("test_hidden_accept" in p for p in got)
    assert SECRET.encode() not in _history_bytes(s, base)
    assert not list((s / "tests").rglob("test_hidden_accept*"))
    assert leerie._history_heldout_hits(s, base, _git(s, "rev-parse", "HEAD"),
                                        staged, prints) == []


def test_a_file_holding_the_sets_source_is_removed_not_committed(
        leerie, tmp_path, monkeypatch):
    """Rule 4: a file that copies the hidden test's source is removed
    before the leftovers commit and listed; a report naming the hidden case
    and its message carries no source line (the case names are given to a
    held-back implementer anyway), so it ships with the implementer's own
    new source beside it."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    before = (leerie._tracked_content(s), leerie._untracked_signatures(s))
    (s / "report.xml").write_text(
        f"<testcase name='test_secret_case'><failure>{SECRET}</failure>")
    (s / "copied.py").write_text("# kept for later\n" + HIDDEN)
    (s / "helper.py").write_text("H = 1\n")
    leerie._redrive_unstage(s, staged)
    withheld: dict = {}
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), prints=prints, withheld=withheld))
    assert got == ["helper.py", "report.xml"]
    assert withheld["heldout"] == ["copied.py"]
    assert not (s / "copied.py").exists()


def test_spans_already_in_the_base_are_not_distinctive(leerie, tmp_path,
                                                     monkeypatch):
    """A span the starting commit already holds is the repository's idiom
    (a shared test header), not the hidden test: dropped. A single value
    the test asserts is never a span on its own."""
    s = _repo(tmp_path, monkeypatch)
    header = ("import os\nimport sys\nfrom mod import f\n"
              "CLIENT = object()\n")
    (s / "tests" / "test_a.py").write_text(header + "def test_a():\n"
                                           "    assert 1\n")
    _git(s, "commit", "-qam", "shared header")
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "tests").mkdir(parents=True)
    (held / "tests" / "test_h.py").write_text(
        header + "\ndef test_only_here():\n"
        f"    assert f() == 2, '{SECRET}'\n")
    staged = [{"set_index": 0, "dir": str(held), "files": ["tests/test_h.py"],
               "made": []}]
    prints = leerie._heldout_fingerprints(s, staged, base)
    assert list(prints) == [leerie._HELDOUT_SPAN_LINES]
    # 6 lines → 3 windows of 4; the first is the base's header.
    assert len(prints[4]) == 2
    probe = tmp_path / "probe.py"
    probe.write_text(header + "def test_mine():\n    assert f() == 2\n")
    assert not leerie._file_holds_span(probe, prints)     # idiom only
    probe.write_text(f"MSG = '{SECRET}'\nraise ValueError(MSG)\n")
    assert not leerie._file_holds_span(probe, prints)     # a value only
    probe.write_text("    from mod import f\n  CLIENT  =  object()\n\n"
                     "def test_only_here():\n}\n"
                     f"  assert f() == 2, '{SECRET}'\n")
    assert leerie._file_holds_span(probe, prints)         # reindented copy


def test_every_commit_is_read_not_the_net_diff(leerie, tmp_path,
                                               monkeypatch):
    """Rule 3: a file that copies the hidden test, committed and then
    deleted, is still in the history; a commit holding only the value the
    test asserts (the fix) is not a hit."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    (s / "mod.py").write_text(f"def f():\n    return 2  # {SECRET}\n")
    _git(s, "add", "mod.py")
    _git(s, "commit", "-qm", "fix")
    head = _git(s, "rev-parse", "HEAD")
    assert leerie._history_heldout_hits(s, base, head, staged, prints) == []
    (s / "notes.md").write_text("```\n" + HIDDEN + "```\n")
    _git(s, "add", "notes.md")
    _git(s, "commit", "-qm", "copy")
    _git(s, "rm", "-q", "notes.md")
    _git(s, "commit", "-qm", "gone")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            "notes.md"]
    leerie._redrive_unstage(s, staged)


def test_a_directory_staging_created_is_staged_material_whole(
        leerie, tmp_path, monkeypatch):
    """Rule 1: everything that appears under a directory staging had to
    create is ignored while the group runs and removed with it."""
    s = _repo(tmp_path, monkeypatch)
    set_ = _held(tmp_path, rel="acc/deep/test_hidden_accept.py")
    staged = leerie._redrive_stage(s, [set_])
    assert staged[0]["made"] == ["acc"]
    (s / "acc" / "deep" / "conftest_copy.py").write_text("X = 1\n")
    (s / "acc" / "other.txt").write_text("y\n")
    assert _git(s, "status", "--porcelain", "--untracked-files=all") == ""
    swept: list[str] = []
    leerie._redrive_unstage(s, staged, swept=swept)
    assert sorted(swept) == ["acc/deep/conftest_copy.py", "acc/other.txt"]
    assert not (s / "acc").exists()
    assert _git(s, "status", "--porcelain", "--untracked-files=all") == ""


def test_a_group_whose_commit_carries_the_sets_bytes_is_voided(
        leerie, tmp_path, monkeypatch):
    """End to end: the implementer fixes the defect and also commits a
    copy of the hidden test's source under a name that is no staged path.
    Before round 11, `committed_staged` (names only) was empty and the
    group stood; it is voided and rolled back, and the next group's honest
    fix — which holds the value the test asserts — is kept."""
    w = World(leerie, tmp_path)
    secret = "SECRET_EXPECTATION_XYZ"
    sdir = w.sets[0]["dir"]
    with open(os.path.join(sdir, DEFECT), "w") as f:
        f.write("import { a } from './a';\n"
                f"it('x', () => expect(a).toBe('{secret}'));\n")
    w.install(monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")

    def leak(w):
        (w.staging / "src" / "notes.ts").write_text(
            "/*\n" + (w.staging / DEFECT).read_text() + "*/\n")
        w.write("export const a = 1; // FIXED\n")
        _git(w.staging, "add", "src/notes.ts")
        _git(w.staging, "commit", "-qm", "notes")

    def honest(w):
        w.write(f"export const a = '{secret}'; // FIXED\n")
    w.edits = [leak, honest]
    w.run()
    groups = [g for r in w.st.data["delivery_redrive"]["rounds"]
              for g in r["groups"] if "pre_sha" in g]
    assert groups[0]["committed_staged"] == ["src/notes.ts"]
    assert groups[0]["rolled_back"]
    assert b"expect(a).toBe" not in _history_bytes(w.staging, start)
    assert "FIXED" in w.a_text("HEAD")


# --------------------------------------------------------------------------
# Review round 12 H1: an honest fix holds the values the test asserts.
# The reviewer's drivers (`review-r12-proofs/test_zz_r12_honest_literal_void.py`),
# each asserting the defect on b40117f, now assert the fix ships.

def test_an_honest_fix_holding_the_expected_message_is_kept(
        leerie, tmp_path, monkeypatch):
    """End to end: the set expects `toThrow('quantity must be positive')`
    and the fix throws exactly that. On b40117f the group recorded
    `committed_staged=['src/a.ts']` and was rolled back, every retry the
    same, and HEAD never got the fix."""
    w = World(leerie, tmp_path)
    expected = "quantity must be positive"
    with open(os.path.join(w.sets[0]["dir"], DEFECT), "w") as f:
        f.write("import { a } from './a';\n"
                f"it('rejects', () => expect(() => a(-1)).toThrow("
                f"'{expected}'));\n")
    w.install(monkeypatch)

    def honest(w):
        w.write("export const a = (n: number) => { if (n < 1) throw new "
                f"Error('{expected}'); return n; }}; // FIXED\n")
    w.edits = [honest]
    w.run()
    g = [g for r in w.st.data["delivery_redrive"]["rounds"]
         for g in r["groups"] if "pre_sha" in g][0]
    assert g["committed_staged"] == [] and not g.get("rolled_back")
    assert "FIXED" in w.a_text("HEAD") and expected in w.a_text("HEAD")
    assert leerie._decide_outcome(w.st, any_commits=True) == "done"


def _js_repo(tmp_path):
    s = tmp_path / "s"
    (s / "src").mkdir(parents=True)
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "src" / "index.ts").write_text("export * from './cart';\n")
    (s / "src" / "cart.ts").write_text("export const cart = 1;\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    return s


def test_a_new_module_the_test_imports_is_not_a_hit(leerie, tmp_path):
    """The held-out test imports the module the task asks for; the honest
    fix creates it and re-exports it. On b40117f `'./orders'` (8 bytes)
    was a marker and `src/index.ts` voided the group."""
    s = _js_repo(tmp_path)
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "src").mkdir(parents=True)
    (held / "src" / "orders.test.ts").write_text(
        "import { total } from './orders';\n"
        "it('sums', () => expect(total([1, 2])).toBe(3));\n")
    set_ = {"index": 0, "dir": str(held),
            "defect_files": ["src/orders.test.ts"]}
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    (s / "src" / "orders.ts").write_text(
        "export const total = (xs: number[]) => xs.reduce((a, b) => a + b, 0);\n")
    (s / "src" / "index.ts").write_text(
        "export * from './cart';\nexport * from './orders';\n")
    _git(s, "add", "src/orders.ts", "src/index.ts")
    _git(s, "commit", "-qm", "add orders")
    leerie._redrive_unstage(s, staged)
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == []


def test_no_single_value_of_a_plain_python_test_is_a_span(leerie, tmp_path,
                                                          monkeypatch):
    """On b40117f `created_at`, `pending_verification` and
    `alice@example.com` were markers: code that sets those fields — the
    feature — voided its group."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "tests").mkdir(parents=True)
    src = ("from app import create_user\n\n"
           "def test_create_user_returns_record():\n"
           "    r = create_user('alice@example.com')\n"
           "    assert r['created_at'] is not None\n"
           "    assert r['status'] == 'pending_verification'\n")
    (held / "tests" / "test_h.py").write_text(src)
    prints = leerie._heldout_fingerprints(
        s, [{"set_index": 0, "dir": str(held), "files": ["tests/test_h.py"],
             "made": []}], base)
    fix = tmp_path / "app.py"
    fix.write_text(
        "def create_user(email):\n"
        "    # e.g. create_user('alice@example.com')\n"
        "    return {'email': email, 'created_at': now(),\n"
        "            'status': 'pending_verification'}\n"
        "def test_create_user_returns_record():\n    pass\n")
    assert not leerie._file_holds_span(fix, prints)
    fix.write_text("'''\n" + src + "'''\n")
    assert leerie._file_holds_span(fix, prints)


def test_an_uncommitted_honest_tracked_edit_is_committed(leerie, tmp_path):
    """On b40117f the implementer's uncommitted fix of a tracked file was
    checked out from HEAD because it held the expected message, and the PR
    gap did not list it."""
    s = _js_repo(tmp_path)
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "src").mkdir(parents=True)
    (held / "src" / "cart.test.ts").write_text(
        "import { add } from './cart';\n"
        "it('rejects', () => expect(() => add(-1)).toThrow("
        "'quantity must be positive'));\n")
    set_ = {"index": 0, "dir": str(held),
            "defect_files": ["src/cart.test.ts"]}
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    before = (leerie._tracked_content(s), leerie._untracked_signatures(s))
    fix = ("export const cart = 1;\nexport const add = (n: number) => {"
           " if (n < 1) throw new Error('quantity must be positive'); };\n")
    (s / "src" / "cart.ts").write_text(fix)
    leerie._redrive_unstage(s, staged)
    withheld: dict = {}
    got = asyncio.run(leerie._redrive_commit_leftovers(
        s, "r1 g0", *before, set(), prints=prints, withheld=withheld))
    assert got == ["src/cart.ts"] and withheld == {}
    assert _git(s, "show", "HEAD:src/cart.ts") + "\n" == fix


def test_a_force_committed_bytecode_of_the_staged_test_voids(
        leerie, tmp_path, monkeypatch):
    """Round 11's leak shape through `git add -f`: the bytecode holds no
    source line, but it is a runner's copy of the staged file by name."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    _pytest(s, "tests/test_hidden_accept.py")
    pyc = [p for p in (s / "tests" / "__pycache__").iterdir()
           if p.name.startswith("test_hidden_accept.")]
    assert pyc
    _git(s, "add", "-f", str(pyc[0].relative_to(s)))
    _git(s, "commit", "-qm", "oops")
    leerie._redrive_unstage(s, staged)
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            str(pyc[0].relative_to(s))]


def test_a_copied_test_file_under_another_name_voids(leerie, tmp_path,
                                                     monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    subprocess.run(["cp", "tests/test_hidden_accept.py",
                    "tests/test_mine.py"], cwd=s, check=True)
    (s / "tests" / "test_mine.py").chmod(0o644)
    _git(s, "add", "tests/test_mine.py")
    _git(s, "commit", "-qm", "mine")
    leerie._redrive_unstage(s, staged)
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            "tests/test_mine.py"]


# --------------------------------------------------------------------------
# Review round 12 L1: fingerprinting is bounded and can never leave a hidden
# test in staging.

def test_a_huge_one_line_literal_is_fingerprinted_not_raised(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver (`test_zz_r12_marker_argv.py`): a 160 KB
    inline base64 literal on one line made `_heldout_markers` pass it as one
    `git grep -e` argument and raise OSError (E2BIG) after staging, outside
    the `finally` that unstages. Lines are now read in bounded pieces and
    reach git through a pattern file."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "tests").mkdir(parents=True)
    src = ("PNG = '" + "QUJD" * 40000 + "'\n\ndef test_png():\n"
           "    assert len(PNG) > 0\n")
    (held / "tests" / "test_img.py").write_text(src)
    set_ = {"index": 0, "dir": str(held), "defect_files": ["tests/test_img.py"]}
    staged = leerie._redrive_stage(s, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    assert prints
    copy = tmp_path / "copy.py"
    copy.write_text(src)
    assert leerie._file_holds_span(copy, prints)
    leerie._redrive_unstage(s, staged)
    assert not (s / "tests" / "test_img.py").exists()


def test_a_fingerprinting_crash_still_unstages(leerie, tmp_path,
                                               monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    seen = []

    def boom(staging, staged, base):
        seen.append(sorted(f for s_ in staged for f in s_["files"]))
        raise OSError(7, "Argument list too long")
    monkeypatch.setattr(leerie, "_heldout_fingerprints", boom)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    try:
        w.run()
    except OSError:
        pass
    assert seen and DEFECT in seen[0]
    assert not list((w.staging / "src").glob("*.test.ts"))
    assert "test.ts" not in _git(w.staging, "status", "--porcelain",
                                 "--ignored", "--untracked-files=all")
