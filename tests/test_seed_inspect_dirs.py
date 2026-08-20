"""Tests for seed_inspect_dirs in scripts/remote/seed-repo.sh.

seed_inspect_dirs ships each --inspect-dir host path to /inspect/<basename>
on the Fly machine. For git repos: `git bundle create - --all` + machine-side
`git clone` + dirty rsync delta. For non-git dirs: plain rsync. See
docs/IMPLEMENTATION.md §0.5 "Remote runtime (Fly.io) transport".

These tests stub flyctl and exercise the bash function via subprocess.
The stub recognizes:

  1. `-C "true"`: defensive — seed_inspect_dirs no longer re-probes
     hallpass (seed_auth's single cold-start probe is the only one),
     but the handler stays in case a future regression reintroduces a
     probe call.
  2. `-C "sh -c 'mkdir -p /inspect && chown leerie: /inspect'"`: parent
     setup. Rewrite /inspect → DEST/inspect and eval.
  3. `-C "sh -c 'rm -rf /inspect/<base> ... mkdir -p ...'"`: per-dir
     reset before bundle clone.
  4. `-C "sh -c 'cat > /tmp/leerie-inspect-<base>.bundle'"`: capture
     stdin to DEST/inspect-<base>.bundle.
  5. `-C "sh -c 'cat > /tmp/leerie-inspect-<base>-subs/<sm>.bundle'"`:
     capture stdin to DEST/inspect-<base>-subs/<sm>.bundle.
  6. Machine-side clone script (`git clone /tmp/leerie-inspect-<base>.bundle
     /inspect/<base>`): rewrite the absolute paths, strip the chown +
     cleanup, and eval locally.
  7. `-C "test -d /inspect/<base>/.git"` and `-C "sh -c 'test -d /inspect/<base>
     && [ -n "$(ls -A ...)" ]'"`: resume probes. Map /inspect → DEST/inspect
     and run the test; the test fixture pre-creates the path to simulate a
     prior-seeded state.
  8. `rsync*`: dirty rsync or non-git fallback. Rewrite trailing /inspect →
     DEST/inspect and eval locally.
  9. `chown -R leerie: /inspect/<base>`: exit 0 (no leerie user on test host).
 10. Anything else: drain stdin and exit 0.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.conftest import run_bash

from tests.stub_helpers import _make_stub_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SH = REPO_ROOT / "scripts" / "remote" / "seed-repo.sh"


def _make_stub_flyctl(stub_path: Path, exec_log: Path, dest_dir: Path,
                      fail_rsync: bool = False) -> None:
    """Stub flyctl that handles seed_inspect_dirs's bundle + rsync flow
    and actually executes the machine-side clone + rsync locally against
    `dest_dir/inspect/...`.

    fail_rsync=True makes every rsync invocation exit 1 (used to verify
    abort-on-failure behavior).
    """
    dest = str(dest_dir.resolve())
    log_path = str(exec_log.resolve())
    rsync_fail_clause = "exit 1" if fail_rsync else """
    # The driver rsync's -e wrapper invokes flyctl with the receiver-side
    # rsync command, e.g. `rsync --server ... /inspect/<base>/`. Rewrite
    # the path so the receiver runs against DEST/inspect/<base>/ locally.
    local_cmd="${remote_cmd// \\/inspect/ $DEST/inspect}"
    eval "$local_cmd"
    exit $?
"""
    stub_path.write_text(
        f"""#!/usr/bin/env bash
echo "flyctl $*" >> {log_path!r}

DEST={dest!r}

# Parse args: pull -C "<cmd>" out of argv.
remote_cmd=""
while [ $# -gt 0 ]; do
  case "$1" in
    -C) remote_cmd="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Defensive hallpass-probe handler (see module docstring item 1).
if [ "$remote_cmd" = "true" ]; then
  exit 0
fi

_drain_to() {{
  mkdir -p "$(dirname "$1")"
  cat > "$1"
}}

case "$remote_cmd" in
  *"mkdir -p /inspect &&"*)
    # One-shot parent setup. Rewrite /inspect → DEST/inspect.
    local_cmd="${{remote_cmd//chown leerie: \\/inspect/true}}"
    local_cmd="${{local_cmd//\\/inspect/$DEST/inspect}}"
    eval "$local_cmd"
    exit $?
    ;;
  *"rm -rf /inspect/"*"mkdir -p"*)
    # Per-dir reset before bundle clone. Rewrite /inspect → DEST/inspect
    # and eval; this nukes the prior copy (if any) and re-creates the
    # subs staging dir locally.
    local_cmd="${{remote_cmd//\\/tmp\\/leerie-inspect-/$DEST/tmp-leerie-inspect-}}"
    local_cmd="${{local_cmd//\\/inspect/$DEST/inspect}}"
    eval "$local_cmd"
    exit $?
    ;;
  *"cat > /tmp/leerie-inspect-"*"-subs/"*)
    # Submodule bundle pipe. Extract <base>-subs/<bn> from the command.
    bn_with_subs="${{remote_cmd##*cat > /tmp/leerie-inspect-}}"
    # bn_with_subs is now like "sibling-service-subs/vendor_foo.bundle'"
    bn_with_subs="${{bn_with_subs%\\'*}}"  # strip trailing quote
    _drain_to "$DEST/tmp-leerie-inspect-$bn_with_subs"
    exit 0
    ;;
  *"cat > /tmp/leerie-inspect-"*".bundle"*)
    # Parent bundle pipe. Extract <base>.bundle.
    bn="${{remote_cmd##*cat > /tmp/leerie-inspect-}}"
    bn="${{bn%\\'*}}"  # strip trailing quote
    _drain_to "$DEST/tmp-leerie-inspect-$bn"
    exit 0
    ;;
  *"git clone /tmp/leerie-inspect-"*)
    # Machine-side clone script for an inspect dir. Strip the chown
    # (no leerie user on test host), strip the post-clone /tmp cleanup
    # (so tests can inspect the captured bundles), then rewrite the
    # absolute paths to point inside DEST.
    local_cmd="$remote_cmd"
    # Strip chown lines (any /inspect/<base> target). Match up to the
    # newline so we don't gobble a trailing close-quote on the last line.
    local_cmd="$(printf '%s\\n' "$local_cmd" | sed 's|chown -R leerie: /inspect/[^[:space:]]*|true|g')"
    # Strip the post-clone rm cleanup. Same caveat: the rm line is the
    # last line of the inner script (just before the closing `'`); a
    # greedy [^ ]* match would gobble the closing quote. Use [^[:space:]'\\\\'][^[:space:]'\\\\']* — anything but space or quote.
    local_cmd="$(printf '%s\\n' "$local_cmd" | sed "s|rm -rf /tmp/leerie-inspect-[^[:space:]']* /tmp/leerie-inspect-[^[:space:]']*|true|g")"
    # Rewrite absolute paths: /tmp/leerie-inspect-* → DEST/tmp-leerie-inspect-*
    # and /inspect → DEST/inspect.
    local_cmd="${{local_cmd//\\/tmp\\/leerie-inspect-/$DEST/tmp-leerie-inspect-}}"
    local_cmd="${{local_cmd//\\/inspect/$DEST/inspect}}"
    eval "$local_cmd"
    exit $?
    ;;
  "test -d /inspect/"*"/.git")
    # Resume probe for git inspect dirs. Rewrite /inspect → DEST/inspect.
    local_cmd="${{remote_cmd//\\/inspect/$DEST/inspect}}"
    eval "$local_cmd"
    exit $?
    ;;
  *"test -d /inspect/"*"ls -A /inspect/"*)
    # Resume probe for non-git inspect dirs.
    local_cmd="${{remote_cmd//\\/inspect/$DEST/inspect}}"
    eval "$local_cmd"
    exit $?
    ;;
  rsync*)
    {rsync_fail_clause}
    ;;
  *"chown -R leerie: /inspect"*)
    # Per-dir chown after rsync / clone. No leerie user on test host.
    exit 0
    ;;
  *)
    cat > /dev/null
    exit 0
    ;;
esac
"""
    )
    stub_path.chmod(0o755)


def _make_git_repo(root: Path, name: str, files: dict[str, str],
                   dirty_files: dict[str, str] | None = None) -> Path:
    """Create a real git repo at root/name with committed files + optional
    uncommitted edits."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    for relpath, content in files.items():
        f = repo / relpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"],
                   check=True, capture_output=True)
    if dirty_files:
        for relpath, content in dirty_files.items():
            f = repo / relpath
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
    return repo


def _make_plain_dir(root: Path, name: str, files: dict[str, str]) -> Path:
    """Create a plain directory (no .git) at root/name."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        f = d / relpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return d


# ----------------------------------------------------------------------------
# Basic guard tests
# ----------------------------------------------------------------------------


def test_seed_inspect_dirs_no_op_when_empty(tmp_path):
    """Empty LEERIE_INSPECT_HOST_TARGETS → returns 0; no ssh-console / rsync."""
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, tmp_path)
    _make_stub_timeout(tmp_path)

    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": "",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log_text = exec_log.read_text() if exec_log.exists() else ""
    assert "ssh console" not in log_text, log_text
    assert "rsync" not in log_text, log_text


def test_seed_inspect_dirs_fails_without_machine_id(tmp_path):
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": f"{tmp_path}/x\t/inspect/x",
        },
    )
    assert result.returncode != 0
    assert "LEERIE_MACHINE_ID" in result.stderr


def test_seed_inspect_dirs_skips_malformed_record(tmp_path):
    """Record without a tab separator → log + skip; no rsync."""
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": "/some/path-without-tab",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text() if exec_log.exists() else ""
    assert "rsync" not in log
    assert "malformed record" in result.stderr


def test_seed_inspect_dirs_skips_non_inspect_targets(tmp_path):
    """Records whose remote target is NOT under /inspect/ are skipped."""
    src = _make_plain_dir(tmp_path / "hostroot", "weird", {"x.txt": "x"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/work/sub"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text() if exec_log.exists() else ""
    assert "rsync" not in log
    assert "skipping non-/inspect target" in result.stderr


def test_seed_inspect_dirs_creates_inspect_parent_first(tmp_path):
    """`mkdir -p /inspect && chown leerie: /inspect` appears in the log
    BEFORE any per-dir operation."""
    src = _make_git_repo(tmp_path / "hostroot", "first", {"a.txt": "a"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/first"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text()
    mkdir_pos = log.find("mkdir -p /inspect &&")
    bundle_pos = log.find("cat > /tmp/leerie-inspect-")
    rsync_pos = log.find("rsync")
    assert mkdir_pos != -1, log
    # The bundle pipe must come after the parent mkdir.
    if bundle_pos != -1:
        assert mkdir_pos < bundle_pos, log
    if rsync_pos != -1:
        assert mkdir_pos < rsync_pos, log


# ----------------------------------------------------------------------------
# Git-repo bundle-clone path
# ----------------------------------------------------------------------------


def test_seed_inspect_dirs_git_repo_uses_bundle_clone(tmp_path):
    """A git-repo inspect dir is shipped via bundle + machine-side clone.
    The committed file lands under DEST/inspect/<base>/."""
    src = _make_git_repo(tmp_path / "hostroot", "beacon",
                         {"src/api.ts": "export const x = 1\n"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/beacon"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text()
    # Bundle pipe + machine-side clone must both appear.
    assert "cat > /tmp/leerie-inspect-beacon.bundle" in log, log
    assert "git clone /tmp/leerie-inspect-beacon.bundle /inspect/beacon" in log, log
    # The clone landed.
    landed = dest / "inspect" / "beacon" / "src" / "api.ts"
    assert landed.exists(), f"committed file did not land at {landed}; log:\n{log}"
    assert landed.read_text() == "export const x = 1\n"
    # And it's a real git clone.
    assert (dest / "inspect" / "beacon" / ".git").is_dir()


def test_seed_inspect_dirs_git_repo_includes_dirty_delta(tmp_path):
    """After bundle-clone, the dirty/uncommitted delta is rsync'd on top —
    workers see the host's in-flight edits."""
    src = _make_git_repo(
        tmp_path / "hostroot", "beacon",
        files={"src/api.ts": "export const x = 1\n"},
        dirty_files={"src/api.ts": "export const x = 2 // edited\n",
                     "untracked.md": "new!"},
    )
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/beacon"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The dirty edit landed.
    modified = dest / "inspect" / "beacon" / "src" / "api.ts"
    assert modified.read_text() == "export const x = 2 // edited\n", (
        f"dirty delta did not land; content: {modified.read_text()!r}"
    )
    # The untracked file also landed.
    untracked = dest / "inspect" / "beacon" / "untracked.md"
    assert untracked.exists() and untracked.read_text() == "new!"


def test_seed_inspect_dirs_skips_bundle_on_resume(tmp_path):
    """If /inspect/<base>/.git already exists on the machine, the bundle
    phase is skipped — only the dirty delta refreshes."""
    src = _make_git_repo(
        tmp_path / "hostroot", "beacon",
        files={"src/api.ts": "v1\n"},
        dirty_files={"src/api.ts": "v2\n"},
    )
    dest = tmp_path / "machine"
    dest.mkdir()
    # Pre-create the marker: DEST/inspect/beacon/.git is what the resume
    # probe checks for.
    pre = dest / "inspect" / "beacon"
    pre.mkdir(parents=True)
    (pre / ".git").mkdir()

    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/beacon"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text()
    # No bundle pipe — the resume probe short-circuited the clone.
    assert "cat > /tmp/leerie-inspect-beacon.bundle" not in log, (
        f"bundle should be skipped on resume; log:\n{log}"
    )
    assert "git clone /tmp/leerie-inspect-beacon.bundle" not in log, log
    # But the rsync (dirty) still ran.
    assert "rsync" in log, f"dirty rsync should still run on resume; log:\n{log}"
    # And the resume marker shows in stderr.
    assert "already present" in result.stderr


def test_seed_inspect_dirs_clones_new_dir_added_at_resume(tmp_path):
    """When two inspect dirs are passed and only one was previously seeded,
    the new one goes the full bundle path and the old one only refreshes."""
    src_a = _make_git_repo(tmp_path / "hostroot", "old",
                           {"a.txt": "a"})
    src_b = _make_git_repo(tmp_path / "hostroot", "new",
                           {"b.txt": "b"})
    dest = tmp_path / "machine"
    dest.mkdir()
    # Mark "old" as already seeded.
    (dest / "inspect" / "old" / ".git").mkdir(parents=True)

    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src_a}\t/inspect/old\n{src_b}\t/inspect/new"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text()
    # "new" must be bundle-cloned.
    assert "cat > /tmp/leerie-inspect-new.bundle" in log, log
    assert "git clone /tmp/leerie-inspect-new.bundle /inspect/new" in log, log
    # "old" must NOT be bundle-cloned.
    assert "cat > /tmp/leerie-inspect-old.bundle" not in log, log
    assert "git clone /tmp/leerie-inspect-old.bundle" not in log, log
    # The new dir landed.
    assert (dest / "inspect" / "new" / "b.txt").exists()


def test_seed_inspect_dirs_aborts_on_rsync_failure(tmp_path):
    """A failed rsync (dirty or fallback) makes seed_inspect_dirs return 1
    and stop — the second record is not attempted."""
    src_a = _make_git_repo(
        tmp_path / "hostroot", "alpha",
        files={"a.txt": "v1"},
        dirty_files={"a.txt": "v2"},
    )
    src_b = _make_git_repo(tmp_path / "hostroot", "bravo",
                           {"b.txt": "b"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest, fail_rsync=True)
    _make_stub_timeout(tmp_path)

    record = f"{src_a}\t/inspect/alpha\n{src_b}\t/inspect/bravo"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    log = exec_log.read_text()
    # bravo's bundle must NOT appear — we bailed after alpha failed.
    assert "cat > /tmp/leerie-inspect-bravo.bundle" not in log, (
        f"bravo should not have been attempted; log:\n{log}"
    )


# ----------------------------------------------------------------------------
# Non-git rsync fallback
# ----------------------------------------------------------------------------


def test_seed_inspect_dirs_non_git_dir_uses_plain_rsync(tmp_path):
    """A non-git directory takes the rsync fallback path. No bundle pipe
    appears in the log; the file lands via rsync."""
    src = _make_plain_dir(tmp_path / "hostroot", "docs",
                          {"README.md": "hi docs"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/docs"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    log = exec_log.read_text()
    assert "cat > /tmp/leerie-inspect-" not in log, (
        f"non-git dir should not bundle; log:\n{log}"
    )
    assert "rsync" in log
    assert (dest / "inspect" / "docs" / "README.md").read_text() == "hi docs"


def test_seed_inspect_dirs_non_git_preserves_nfc_unicode_filenames(tmp_path):
    """rsync (fallback path) preserves filename bytes verbatim — non-ASCII
    filenames survive the round trip. Regression for the rationale that
    drove the rsync-vs-tar choice (seed-repo.sh:22-38)."""
    nfc_name = "Planón.pdf"  # 'ó' as single codepoint U+00F3 (NFC)
    src = _make_plain_dir(tmp_path / "hostroot", "docs",
                          {nfc_name: "binary-ish"})
    dest = tmp_path / "machine"
    dest.mkdir()
    exec_log = tmp_path / "exec_log.txt"
    fake_flyctl = tmp_path / "flyctl"
    _make_stub_flyctl(fake_flyctl, exec_log, dest)
    _make_stub_timeout(tmp_path)

    record = f"{src}\t/inspect/docs"
    result = run_bash(
        f"source {SEED_SH}; seed_inspect_dirs",
        env={
            "LEERIE_MACHINE_ID": "test-machine-001",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(tmp_path),
            "LEERIE_INSPECT_HOST_TARGETS": record,
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    landed_files = [p.name for p in (dest / "inspect" / "docs").iterdir()]
    assert nfc_name in landed_files, (
        f"NFC filename not preserved; landed: "
        f"{[name.encode('utf-8') for name in landed_files]}"
    )
