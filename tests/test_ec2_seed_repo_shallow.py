"""Round-trip regression for the shallow-seed path in
scripts/remote/ec2-seed-repo.sh.

EC2 counterpart to tests/test_seed_repo_shallow_roundtrip.py. The
shallow-seed payload logic (host `git clone --depth=N` + tar-of-`.git`;
instance untar + `git checkout`) is IDENTICAL to the Fly path — DESIGN
§6 *Shallow seeding for heavy repos* is transport-agnostic, and only the
wire transport differs (ec2_tar_pipe/ec2_remote_exec instead of
`flyctl ssh console`). This test reproduces the same host-side and
instance-side commands `_ec2_seed_shallow_parent` / `ec2_seed_repo_clone`
issue (pinned against the real file by
test_reconstruction_matches_source below) and asserts the same
correctness properties:

  1. The shallow reconstruction materializes /work with a byte-identical
     tracked tree to the host tip (checkout parity), stays shallow, and
     preserves non-ASCII (NFC) filenames.
  2. Fetch-back from the shallow instance repo (bundle the run branch by
     name, exactly as ec2-fetch-branch.sh / fetch-branch.sh do) verifies
     + fetches into the full host with full ancestry, and the PR
     merge-base equals the host tip (so the PR diff shows only the
     worker's change).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_SEED_SH = REPO_ROOT / "scripts" / "remote" / "ec2-seed-repo.sh"

SEED_DEPTH = 5


def _git(*args, cwd, **kw):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, **kw)


def _make_host_repo(root: Path) -> Path:
    """A host repo with >SEED_DEPTH commits + a non-ASCII filename."""
    host = root / "host"
    host.mkdir()
    _git("init", "-q", "-b", "main", cwd=host)
    _git("config", "user.email", "t@t", cwd=host)
    _git("config", "user.name", "t", cwd=host)
    for i in range(20):
        (host / "f.txt").write_text(f"line {i}\n")
        _git("add", "f.txt", cwd=host)
        _git("commit", "-qm", f"c{i}", cwd=host)
    # Non-ASCII (NFC) filename — the case that motivated bundles/git-tar
    # over a working-tree tar. The .git-only tar must preserve it.
    (host / "canción.txt").write_text("x")
    _git("add", "-A", cwd=host)
    _git("commit", "-qm", "non-ascii", cwd=host)
    return host


def _shallow_seed_to_work(host: Path, work: Path, root: Path) -> None:
    """Reproduce the EC2 shallow seed forward path end to end:
    host `git clone --depth` + tar .git  ->  instance untar + checkout.
    `work` plays the role of /work on the instance. Identical commands
    to _ec2_seed_shallow_parent (host side) + ec2_seed_repo_clone's
    shallow _parent_materialize block (instance side); the transport
    (ec2_tar_pipe/ec2_remote_exec) is orthogonal to this correctness
    contract and is covered separately in tests/test_ec2_seed_repo.py."""
    shallow = root / "shallow"
    tarf = root / "leerie-seed-git.tar"
    branch = "main"
    # --- host side (_ec2_seed_shallow_parent) ---
    _git("clone", "--quiet", f"--depth={SEED_DEPTH}", "--no-local",
         "--branch", branch, f"file://{host}", str(shallow), cwd=root)
    subprocess.run(["tar", "-C", str(shallow), "-cf", str(tarf), ".git"],
                   check=True)
    # --- instance side (ec2_seed_repo_clone step 3, shallow prefix) ---
    work.mkdir(exist_ok=True)
    script = f"""set -e
find {work} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +
tar -C {work} -xf {tarf}
cd {work}
git checkout -f {branch}
git remote remove origin 2>/dev/null || true
"""
    subprocess.run(["bash", "-c", script], check=True,
                   capture_output=True, text=True)


def test_checkout_parity_and_shallow(tmp_path):
    """Shallow reconstruction yields a byte-identical tracked tree to the
    host tip, stays shallow, drops origin, and preserves NFC filenames."""
    host = _make_host_repo(tmp_path)
    work = tmp_path / "work"
    _shallow_seed_to_work(host, work, tmp_path)

    host_tree = _git("rev-parse", "HEAD^{tree}", cwd=host).stdout.strip()
    work_tree = _git("rev-parse", "HEAD^{tree}", cwd=work).stdout.strip()
    assert work_tree == host_tree, "shallow checkout tree must match host tip"

    assert (work / ".git" / "shallow").exists(), "instance repo must stay shallow"

    status = _git("status", "--porcelain", cwd=work).stdout.strip()
    assert status == "", f"working tree must be clean; got: {status!r}"

    depth = len(_git("log", "--oneline", cwd=work).stdout.strip().splitlines())
    assert depth == SEED_DEPTH, f"expected depth {SEED_DEPTH}, got {depth}"

    remotes = _git("remote", cwd=work).stdout.strip()
    assert "origin" not in remotes, "stale file:// origin must be removed"

    # NFC filename survived (git tracks it; a working-tree tar would have
    # normalized it to NFD and it would show dirty/missing). Use -z for
    # raw, unquoted bytes (ls-files C-quotes non-ASCII by default), and
    # compare the exact NFC byte sequence (ó = U+00F3 = c3 b3).
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=work, check=True, capture_output=True
    ).stdout
    assert "canción.txt".encode("utf-8") in files, (
        "non-ASCII (NFC) filename must survive verbatim (no NFC->NFD "
        "normalization from the transport)"
    )


def test_fetchback_roundtrip_and_merge_base(tmp_path):
    """A worker commit on the shallow instance repo bundles back (by
    branch name), verifies + fetches into the full host with full
    ancestry, and the merge-base equals the host tip (PR-diff
    correctness) — mirrors ec2-fetch-branch.sh's contract."""
    host = _make_host_repo(tmp_path)
    work = tmp_path / "work"
    _shallow_seed_to_work(host, work, tmp_path)

    _git("config", "user.email", "w@w", cwd=work)
    _git("config", "user.name", "w", cwd=work)
    run_branch = "leerie/runs/xyz/run"
    _git("checkout", "-qb", run_branch, cwd=work)
    (work / "f.txt").write_text("worker change\n")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-qm", "worker commit", cwd=work)

    # ec2-fetch-branch.sh bundles the run branch BY NAME (not --all),
    # same as the Fly-path fetch-branch.sh.
    back = tmp_path / "back.bundle"
    _git("bundle", "create", str(back), run_branch, cwd=work)

    _git("bundle", "verify", str(back), cwd=host)
    _git("fetch", str(back), f"+{run_branch}:refs/probe/run", cwd=host)

    host_commits = int(_git("rev-list", "--count", "main", cwd=host).stdout)
    probe_commits = int(_git("rev-list", "--count", "refs/probe/run",
                             cwd=host).stdout)
    assert probe_commits == host_commits + 1, (
        "worker commit must land on the host with FULL ancestry (no "
        "missing objects from the shallow boundary)"
    )

    mb = _git("merge-base", "main", "refs/probe/run", cwd=host).stdout.strip()
    tip = _git("rev-parse", "main", cwd=host).stdout.strip()
    assert mb == tip, (
        "merge-base(main, run) must equal the host tip so the PR diff "
        "shows only the worker's change (a re-rooted shallow would break "
        "this)"
    )

    diff = _git("diff", "--stat", f"main...refs/probe/run", cwd=host).stdout
    assert "f.txt" in diff and "canci" not in diff, (
        "PR diff must show only the worker's f.txt change"
    )


def test_reconstruction_matches_source():
    """Coupling: the shallow reconstruction commands this test reproduces
    must still be present in ec2-seed-repo.sh. If the real script's
    shallow prefix drifts, update this test in lockstep."""
    src = EC2_SEED_SH.read_text()
    # Host-side shallow clone + .git-only tar.
    assert 'git clone --quiet --depth="$LEERIE_SEED_DEPTH" --no-local' in src
    assert 'tar -C "$_tmp_shallow/repo" -cf "$_tmp_shallow/leerie-seed-git.tar" .git' in src
    # Instance-side shallow prefix: inode-preserving empty, untar, checkout,
    # drop origin. The branch is injected directly ($_branch), NOT via a
    # __BRANCH__ placeholder (which would collide + risk shell injection) —
    # same discipline as seed-repo.sh.
    assert "find /work -mindepth 1 -maxdepth 1 -exec rm -rf {} +" in src
    assert "tar -C /work -xf /tmp/leerie-seed-git.tar" in src
    assert "git checkout -f $_branch" in src
    assert "__BRANCH__" not in src, (
        "branch must be injected directly (shell-safe by the gate), not via "
        "a __BRANCH__ placeholder"
    )
    assert "git remote remove origin" in src
    # NEVER a working-tree tar (that would reintroduce the NFC->NFD bug).
    assert "tar -C /work -cf" not in src, (
        "shallow path must never tar the working tree — only .git"
    )


def _branch_shallow_safe(branch: str) -> bool:
    """Invoke the REAL _seed_branch_shallow_safe from ec2-seed-repo.sh so
    the test is coupled to the shipped charset, not a reproduction of
    it."""
    script = (
        f'. "{EC2_SEED_SH}" >/dev/null 2>&1 || true\n'
        f'if _seed_branch_shallow_safe "$1"; then echo safe; else echo unsafe; fi\n'
    )
    r = subprocess.run(["bash", "-c", script, "bash", branch],
                       capture_output=True, text=True)
    return r.stdout.strip() == "safe"


@pytest.mark.parametrize("branch", [
    "main", "master", "feat/login-fix", "release/1.2.3",
    "user/andres_test", "a__b", "v1.2.3-rc.1",
])
def test_safe_branches_allow_shallow(branch):
    """Normal branch names are shell-safe -> shallow path is allowed."""
    assert _branch_shallow_safe(branch) is True


@pytest.mark.parametrize("branch", [
    "feat/it's-a-branch",   # apostrophe closes the sh -c '...' wrapper
    "v$(echo pwned)",       # command substitution
    "back`echo bad`tick",   # backtick command substitution
    "sp ace",               # space
    'has"quote',            # double quote
    "semi;colon",
    "__PARENT_MATERIALIZE__",  # live placeholder token (would be mangled)
    "__CLEANUP_TMP__",         # live placeholder token (would be mangled)
    "",                        # empty
])
def test_unsafe_branches_fall_back(branch):
    """A branch name with any non-shell-safe character (or a placeholder
    token) must be rejected so ec2_seed_repo_clone falls back to the
    full bundle instead of risking a broken/injected instance-side
    command."""
    assert _branch_shallow_safe(branch) is False


def test_safe_branch_gate_wired_into_decision():
    """Coupling: the shallow decision must actually consult the gate — a
    refactor that drops the check would re-open the injection bug."""
    src = EC2_SEED_SH.read_text()
    assert "_seed_branch_shallow_safe" in src
    assert "_seed_branch_shallow_safe \"$_branch\"" in src, (
        "the shallow decision in ec2_seed_repo_clone must gate on "
        "_seed_branch_shallow_safe \"$_branch\""
    )
