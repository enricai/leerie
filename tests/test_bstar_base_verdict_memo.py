"""The base-side `pre_existing` memo (DESIGN §9 *A settled base verdict is
reused, never recomputed*).

A `pre_existing` verdict — the base fails a candidate on every one of the
`k` reruns — is a fact about the base alone, so across a run's many
base-paired confirms the same candidate need not be rerun once it is
settled. These tests drive the real `_confirm_against_base` code path
against a real git repo and a real confined pytest suite (`@needs_landlock`)
and prove the two halves of the measurement bar:

- **outcome-identity**: a memoised confirm returns the same `new` and
  `pre_existing` sets as the un-memoised confirm that preceded it;
- **faster, by skipping work**: the memoised candidate's file is not rerun
  on either side, and when every candidate is memoised the base worktree is
  not built at all.

Plus pure unit coverage of the key derivation and resume-safety.
"""
from __future__ import annotations

import asyncio

import pytest
from tests.conftest import needs_landlock, skip_without_landlock, run_git_out  # noqa: E402

_git = run_git_out


def _caps(leerie, **over):
    return {**leerie.DEFAULT_CAPS, **over}


# --- pure unit coverage: key derivation and memo state ---------------------


def test_memo_key_is_stable_and_discriminating(leerie):
    """The same (base_sha, cmd, replace, remove_base) gives one key; any
    difference gives another — so a verdict measured against one base rerun
    is never read against a different one."""
    k = leerie._base_verdict_memo_key
    base = k("sha1", "pytest", (("a", "b"),), ("f.py", "g.py"))
    assert base == k("sha1", "pytest", (("a", "b"),), ("f.py", "g.py"))
    # remove_base is a set of files to drop: order does not matter.
    assert base == k("sha1", "pytest", (("a", "b"),), ("g.py", "f.py"))
    # Every field discriminates (each changes exactly one field from base).
    assert base != k("sha2", "pytest", (("a", "b"),), ("f.py", "g.py"))
    assert base != k("sha1", "jest", (("a", "b"),), ("f.py", "g.py"))
    assert base != k("sha1", "pytest", (("a", "c"),), ("f.py", "g.py"))
    assert base != k("sha1", "pytest", (("a", "b"),), ("f.py",))
    # replace order matters (substitutions apply in order).
    assert (k("s", "c", (("a", "b"), ("c", "d")), ())
            != k("s", "c", (("c", "d"), ("a", "b")), ()))


def test_cached_lookup_and_record_roundtrip(leerie, tmp_path):
    st = _bare_state(leerie, tmp_path)
    key = leerie._base_verdict_memo_key("sha", "cmd", (), ())
    assert leerie._base_verdict_cached(st, key, "t::x") is False
    leerie._record_base_verdict(st, key, ["t::x", "t::y"])
    assert leerie._base_verdict_cached(st, key, "t::x") is True
    assert leerie._base_verdict_cached(st, key, "t::y") is True
    # A different key shares nothing.
    other = leerie._base_verdict_memo_key("sha2", "cmd", (), ())
    assert leerie._base_verdict_cached(st, other, "t::x") is False
    # Persisted through State, reloadable by a fresh owner of the run dir.
    leerie_root, run_id = st.leerie_root, st.run_id
    st.release_lock()
    st2 = leerie.State(leerie_root, run_id)
    assert st2.load()
    assert leerie._base_verdict_cached(st2, key, "t::x") is True


def test_record_is_additive_and_saves_only_on_growth(leerie, tmp_path,
                                                     monkeypatch):
    st = _bare_state(leerie, tmp_path)
    key = leerie._base_verdict_memo_key("sha", "cmd", (), ())
    leerie._record_base_verdict(st, key, ["a"])
    saves = {"n": 0}
    real_save = st.save

    def counting_save() -> None:
        saves["n"] += 1
        real_save()

    monkeypatch.setattr(st, "save", counting_save)
    # Re-recording the same ID does not grow the memo, so it does not save.
    leerie._record_base_verdict(st, key, ["a"])
    assert saves["n"] == 0
    # A new ID grows it and saves once.
    leerie._record_base_verdict(st, key, ["a", "b"])
    assert saves["n"] == 1
    assert sorted(st.data["base_pre_existing_memo"][key]) == ["a", "b"]


def test_resume_without_a_memo_reads_empty(leerie, tmp_path):
    """A run saved before the memo existed has no such state and reads
    False for every candidate — it recomputes exactly as before."""
    st = _bare_state(leerie, tmp_path)
    assert "base_pre_existing_memo" not in st.data
    key = leerie._base_verdict_memo_key("sha", "cmd", (), ())
    assert leerie._base_verdict_cached(st, key, "anything") is False


def _bare_state(leerie, tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "fix-memo-abcdef"
    (leerie_root / "runs" / run_id / "logs").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x"}
    st.save()
    return st


# --- real-path: identity + skipped work ------------------------------------

_SUITE_BROKEN = """\
def test_broken():
    assert False
"""
_SUITE_OK = """\
def test_regressed():
    assert {ok}
"""

CMD = "python3 -m pytest -q -p no:cacheprovider tests"


@pytest.fixture
def memo_repo(leerie, tmp_path):
    """Base: tests/test_a.py::test_broken fails (assert False),
    tests/test_b.py::test_regressed passes. Final (staging): test_broken
    still fails (pre_existing), test_regressed now fails (a real new
    regression). Each case in its own file so a memoised candidate's file
    drops out of the rerun entirely."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n")
    (repo / "tests" / "test_a.py").write_text(_SUITE_BROKEN)
    (repo / "tests" / "test_b.py").write_text(_SUITE_OK.format(ok="True"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "worktrees").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "working_branch": "main", "base_sha": base,
               "conformance": {"_baseline": {"sha": base}}}
    st.repo_root = repo
    st.save()
    leerie._ensure_run_repo(st)
    staging = run_dir / "worktrees" / "staging"
    _git(st.run_repo, "worktree", "add", "-q", "-b", f"leerie/runs/{run_id}",
         str(staging), base)
    (staging / "tests" / "test_b.py").write_text(_SUITE_OK.format(ok="False"))
    _git(staging, "commit", "-q", "-am", "final")
    return {"repo": repo, "staging": staging, "st": st, "base": base}


def _spy_base_captures(leerie, monkeypatch):
    """Record each base-side rerun's (label, files). The base runner labels
    carry `-base`; the final runner's carry `-final`."""
    calls: list[dict] = []
    real = leerie._capture_failing_test_ids

    async def wrapped(tree, cmd, st, caps, *, label, **kw):
        if "-base" in label:
            calls.append({"label": label, "files": list(kw.get("files") or [])})
        return await real(tree, cmd, st, caps, label=label, **kw)

    monkeypatch.setattr(leerie, "_capture_failing_test_ids", wrapped)
    return calls


def _confirm(leerie, st, base, staging, cands, id_files):
    return asyncio.run(leerie._confirm_against_base(
        st, _caps(leerie), base_sha=base, final_tree=str(staging), cmd=CMD,
        candidates=cands, id_files=id_files, label="t",
        log_path=st.run_dir / "logs" / "t.log", worktree="confirm-base",
        deps_label="t-base", deps_log="t", run_prefix="t", confirm_label="t"))


@needs_landlock
@skip_without_landlock
def test_memoised_confirm_is_identical_and_skips_the_settled_file(
        leerie, memo_repo, monkeypatch):
    """Run the same base-paired confirm twice. The second run returns the
    identical `new`/`pre_existing` verdict, but does not rerun the
    pre_existing candidate's file on the base side."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging, base = memo_repo["st"], memo_repo["staging"], memo_repo["base"]

    # The real failing IDs on the final tree, as a measurement records them.
    got = asyncio.run(leerie._capture_failing_test_ids(
        str(staging), CMD, st, _caps(leerie), label="seed",
        log_path=st.run_dir / "logs" / "seed.log", timeout=120, serial=True))
    cands = sorted(got["ids"])
    id_files = dict(got["files"])
    broken = next(c for c in cands if "test_broken" in c)
    regressed = next(c for c in cands if "test_regressed" in c)
    assert id_files[broken].endswith("test_a.py")
    assert id_files[regressed].endswith("test_b.py")

    # Run 1 — the un-memoised confirm.
    base_calls = _spy_base_captures(leerie, monkeypatch)
    v1 = _confirm(leerie, st, base, staging, cands, id_files)
    assert set(v1["new"]) == {regressed}
    assert set(v1["pre_existing"]) == {broken}
    # Total base-side file-work: every file rerun on the base side, summed
    # over the base reruns (the saving is measured here, not in the pair
    # count — a sized confirm reruns all candidate files in one pass).
    work_1 = sum(len(c["files"]) for c in base_calls)
    files_reran_1 = {f for c in base_calls for f in c["files"]}
    assert any("test_a.py" in f for f in files_reran_1)   # broken was rerun
    # The memo now holds the settled pre_existing candidate.
    key = leerie._base_verdict_memo_key(base, CMD, (), ())
    assert leerie._base_verdict_cached(st, key, broken)

    # Run 2 — the memoised confirm, identical inputs.
    base_calls.clear()
    v2 = _confirm(leerie, st, base, staging, cands, id_files)
    # Outcome-identity: the verdicts that gate are byte-identical.
    assert set(v2["new"]) == set(v1["new"])
    assert set(v2["pre_existing"]) == set(v1["pre_existing"])
    assert set(v2["candidates"]) == set(v1["candidates"])
    assert v2["resource_affected"] == v1["resource_affected"]
    assert v2["no_verdict"] == v1["no_verdict"]
    # Faster: the settled file is not rerun on the base side; the real
    # regression's file still is, so strictly less base-side work.
    work_2 = sum(len(c["files"]) for c in base_calls)
    files_reran_2 = {f for c in base_calls for f in c["files"]}
    assert work_2 < work_1
    assert not any("test_a.py" in f for f in files_reran_2)
    assert any("test_b.py" in f for f in files_reran_2)


@needs_landlock
@skip_without_landlock
def test_all_memoised_builds_no_base_worktree(leerie, memo_repo, monkeypatch):
    """When every candidate is already settled pre_existing, the second
    confirm builds no base worktree at all and returns the pre_existing
    verdict from the memo."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging, base = memo_repo["st"], memo_repo["staging"], memo_repo["base"]

    got = asyncio.run(leerie._capture_failing_test_ids(
        str(staging), CMD, st, _caps(leerie), label="seed",
        log_path=st.run_dir / "logs" / "seed.log", timeout=120, serial=True))
    broken = next(c for c in got["ids"] if "test_broken" in c)
    id_files = {broken: got["files"][broken]}

    # Run 1 settles and memoises test_broken as pre_existing.
    v1 = _confirm(leerie, st, base, staging, [broken], id_files)
    assert v1["pre_existing"] == [broken]
    assert v1["new"] == []

    # Run 2 — count worktree builds; there must be none.
    builds = {"n": 0}
    real_add = leerie._worktree_add_detached_async

    async def counting_add(st_, wt, rev, **kw):
        builds["n"] += 1
        return await real_add(st_, wt, rev, **kw)

    monkeypatch.setattr(leerie, "_worktree_add_detached_async", counting_add)
    v2 = _confirm(leerie, st, base, staging, [broken], id_files)
    assert builds["n"] == 0
    assert v2["pre_existing"] == [broken]
    assert v2["new"] == []
    assert set(v2["candidates"]) == {broken}
    # No base worktree exists on disk.
    assert not leerie._helper_worktree_path(st, "confirm-base").exists()
