"""The anti-cheat that stays after I3: no line of a shown set's source
reaches history (DESIGN §8 *A held-out set never enters a tree a worker
commits from*, "Kept as anti-cheat").

The sets are no longer staged in the tree a worker commits from (review
round 11's bytecode leak, and the unstage rules rounds 12-13 drove, are
gone with that: tests/test_redrive_selfcheck_out_of_tree.py). A worker can
still copy a set's readable source into a commit, so every group's commits
are searched for spans of it. Review round 12 H1 drove the over-reach of a
literal-based check (an honest fix holding the expected message was
voided); the check matches spans of the test's SOURCE, never a value it
asserts. Both directions are pinned here."""
from __future__ import annotations

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


def _shown(leerie, sets):
    """The shown sets as the history check reads them."""
    return [{"dir": s_["dir"], "files": leerie._set_files(s_)}
            for s_ in sets]


def _holds(leerie, path, prints) -> bool:
    """Whether the file at `path` holds one of `prints`' spans."""
    return leerie._holds_heldout_span(
        leerie._bytes_pieces(path.read_bytes()), prints)


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
    assert not _holds(leerie, probe, prints)     # idiom only
    probe.write_text(f"MSG = '{SECRET}'\nraise ValueError(MSG)\n")
    assert not _holds(leerie, probe, prints)     # a value only
    probe.write_text("    from mod import f\n  CLIENT  =  object()\n\n"
                     "def test_only_here():\n}\n"
                     f"  assert f() == 2, '{SECRET}'\n")
    assert _holds(leerie, probe, prints)         # reindented copy


def test_every_commit_is_read_not_the_net_diff(leerie, tmp_path,
                                               monkeypatch):
    """Rule 3: a file that copies the hidden test, committed and then
    deleted, is still in the history; a commit holding only the value the
    test asserts (the fix) is not a hit."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = _shown(leerie, [set_])
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


def test_a_copy_made_only_in_a_merge_commit_voids(leerie, tmp_path,
                                                  monkeypatch):
    """Review round 12's suspicion, confirmed: `git log --raw` lists
    nothing for a merge commit, so a copy added only in a merge's
    resolution (an evil merge) was never read. `-m` reads it."""
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = _shown(leerie, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    _git(s, "checkout", "-q", "-b", "side")
    (s / "side.txt").write_text("side\n")
    _git(s, "add", "side.txt")
    _git(s, "commit", "-qm", "side")
    _git(s, "checkout", "-q", "main")
    (s / "mod.py").write_text("def f():\n    return 2\n")
    _git(s, "commit", "-qam", "fix")
    _git(s, "merge", "-q", "--no-ff", "--no-commit", "side")
    (s / "notes.md").write_text(HIDDEN)
    _git(s, "add", "notes.md")
    _git(s, "commit", "-qm", "merge side")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            "notes.md"]


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
            "/*\n" + open(os.path.join(w.sets[0]["dir"], DEFECT)).read()
            + "*/\n")
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
    staged = _shown(leerie, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    (s / "src" / "orders.ts").write_text(
        "export const total = (xs: number[]) => xs.reduce((a, b) => a + b, 0);\n")
    (s / "src" / "index.ts").write_text(
        "export * from './cart';\nexport * from './orders';\n")
    _git(s, "add", "src/orders.ts", "src/index.ts")
    _git(s, "commit", "-qm", "add orders")
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
    assert not _holds(leerie, fix, prints)
    fix.write_text("'''\n" + src + "'''\n")
    assert _holds(leerie, fix, prints)


def test_an_honest_tracked_fix_holding_the_message_is_not_a_hit(
        leerie, tmp_path):
    """On b40117f the implementer's uncommitted fix of a tracked file was
    checked out from HEAD because it held the expected message, and the PR
    gap did not list it. Rewritten for I1 (the implementer commits its own
    work; leerie no longer commits or reverts leftovers): the committed fix
    shares only the asserted value with the test, so it is no hit."""
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
    staged = _shown(leerie, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    fix = ("export const cart = 1;\nexport const add = (n: number) => {"
           " if (n < 1) throw new Error('quantity must be positive'); };\n")
    (s / "src" / "cart.ts").write_text(fix)
    _git(s, "commit", "-qam", "reject a non-positive quantity")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == []
    assert _git(s, "show", "HEAD:src/cart.ts") + "\n" == fix


def test_a_copied_test_file_under_another_name_voids(leerie, tmp_path,
                                                     monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    set_ = _held(tmp_path)
    staged = _shown(leerie, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    # The worker reads the shown set's source and copies it in.
    subprocess.run(["cp", os.path.join(set_["dir"],
                                       "tests/test_hidden_accept.py"),
                    "tests/test_mine.py"], cwd=s, check=True)
    (s / "tests" / "test_mine.py").chmod(0o644)
    _git(s, "add", "tests/test_mine.py")
    _git(s, "commit", "-qm", "mine")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            "tests/test_mine.py"]


# --------------------------------------------------------------------------
# Review round 13 M1: a value the test asserts, broken by a formatter over
# several lines, was a 4-line span, so the honest fix defining that value
# was voided when committed and reverted when not. A literal-only line is
# never in a span, and a span needs two code lines.

CODES = ["ERR_QUANTITY_NOT_POSITIVE", "ERR_QUANTITY_EXCEEDS_STOCK",
         "ERR_SKU_UNKNOWN_OR_RETIRED", "ERR_CURRENCY_MISMATCH_ON_CART"]
VALUE_TEST = ("from mod import ERROR_CODES\n\n\n"
              "def test_error_codes_are_stable():\n"
              "    assert ERROR_CODES == [\n"
              + "".join(f'        "{c}",\n' for c in CODES) + "    ]\n")
VALUE_FIX = ("def f():\n    return 1\n\n\nERROR_CODES = [\n"
             + "".join(f'    "{c}",\n' for c in CODES) + "]\n")


def _value_setup(leerie, tmp_path, monkeypatch):
    s = _repo(tmp_path, monkeypatch)
    base = _git(s, "rev-parse", "HEAD")
    held = tmp_path / "held"
    (held / "tests").mkdir(parents=True)
    (held / "tests" / "test_codes.py").write_text(VALUE_TEST)
    staged = _shown(leerie, [{
        "index": 0, "dir": str(held),
        "defect_files": ["tests/test_codes.py"]}])
    assert staged
    return s, base, staged, leerie._heldout_fingerprints(s, staged, base)


def test_a_committed_fix_defining_a_multiline_value_is_kept(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver, committed side: `_history_heldout_hits` was
    `['mod.py']`, voiding the group."""
    s, base, staged, prints = _value_setup(leerie, tmp_path, monkeypatch)
    assert prints                      # the test's code lines still count
    (s / "mod.py").write_text(VALUE_FIX)
    _git(s, "commit", "-qam", "add error codes")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == []


def test_a_copy_of_a_value_test_still_voids(leerie, tmp_path, monkeypatch):
    """The other direction: dropping literal lines leaves the copied case's
    code lines — import, signature, assertion — which still match."""
    s, base, staged, prints = _value_setup(leerie, tmp_path, monkeypatch)
    (s / "tests" / "test_mine.py").write_text(VALUE_TEST)
    _git(s, "add", "tests/test_mine.py")
    _git(s, "commit", "-qm", "mine")
    assert leerie._history_heldout_hits(
        s, base, _git(s, "rev-parse", "HEAD"), staged, prints) == [
            "tests/test_mine.py"]


def test_literal_only_lines_are_not_code(leerie):
    pieces = list(leerie._bytes_pieces(
        b'"ERR_A",\n  42,\n0x1F, 3.5e-3,\nNone,\n"a": 1,\n'
        b"assert ERROR_CODES == [\nkey: 'v',\nreturn 1\n]\n"))
    assert pieces == [b"assert ERROR_CODES == [", b"key: 'v',",
                      b"return 1"]


def test_a_one_code_line_file_has_no_span(leerie, tmp_path):
    held = tmp_path / "held"
    held.mkdir()
    (held / "t.py").write_text('X = [\n  "a",\n  "b",\n]\n')
    assert leerie._heldout_fingerprints(
        tmp_path, [{"set_index": 0, "dir": str(held), "files": ["t.py"],
                    "made": []}], "") == {}


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
    staged = _shown(leerie, [set_])
    prints = leerie._heldout_fingerprints(s, staged, base)
    assert prints
    copy = tmp_path / "copy.py"
    copy.write_text(src)
    assert _holds(leerie, copy, prints)


def test_a_fingerprinting_crash_leaves_nothing_in_staging(
        leerie, tmp_path, monkeypatch):
    """Review round 12 L1's shape after I3: the fingerprints are computed
    before the drive, and nothing of a set was ever in staging to be left
    behind by a crash there."""
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
    assert "test.ts" not in _git(w.staging, "status", "--porcelain",
                                 "--ignored", "--untracked-files=all")
