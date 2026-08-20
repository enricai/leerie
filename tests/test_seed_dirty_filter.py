"""Tests for scripts/remote/seed_dirty_filter.py.

Direct unit coverage of the shared dirty-file transfer filter's
stdin/stdout NUL-delimited contract, in isolation from either transport
(seed-repo.sh / ec2-seed-repo.sh) that invokes it via
seed-common.sh's _seed_dirty_filter().
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FILTER_SCRIPT = REPO_ROOT / "scripts" / "remote" / "seed_dirty_filter.py"


def _run(lines: list[str], user_repo: str | None) -> list[str]:
    env = dict(os.environ)
    if user_repo is not None:
        env["USER_REPO"] = user_repo
    else:
        env.pop("USER_REPO", None)
    proc = subprocess.run(
        [sys.executable, str(FILTER_SCRIPT)],
        input="\n".join(lines).encode() + b"\n" if lines else b"",
        stdout=subprocess.PIPE,
        env=env,
        check=True,
    )
    if not proc.stdout:
        return []
    return proc.stdout.rstrip(b"\x00").split(b"\x00")


def test_nul_delimited_output(tmp_path):
    (tmp_path / "foo.py").write_text("x")
    out = _run(["foo.py"], str(tmp_path))
    assert out == [b"foo.py"]


def test_blank_lines_dropped(tmp_path):
    (tmp_path / "foo.py").write_text("x")
    out = _run(["", "foo.py", ""], str(tmp_path))
    assert out == [b"foo.py"]


def test_git_dir_excluded(tmp_path):
    out = _run([".git/HEAD", ".git"], str(tmp_path))
    assert out == []


def test_leerie_whitelist(tmp_path):
    (tmp_path / ".leerie").mkdir()
    for name in ("config.toml", "Dockerfile", ".leerie-setup.sh"):
        (tmp_path / ".leerie" / name).write_text("x")
    (tmp_path / ".leerie" / "runs").mkdir()
    (tmp_path / ".leerie" / "runs" / "state.json").write_text("x")

    out = _run(
        [
            ".leerie/config.toml",
            ".leerie/Dockerfile",
            ".leerie/.leerie-setup.sh",
            ".leerie/runs/state.json",
            ".leerie",
        ],
        str(tmp_path),
    )
    assert out == [
        b".leerie/config.toml",
        b".leerie/Dockerfile",
        b".leerie/.leerie-setup.sh",
    ]


def test_worktree_path_defense(tmp_path):
    out = _run(
        ["some/.leerie/runs/run-1/worktrees/wt/file.py"],
        str(tmp_path),
    )
    assert out == []


def test_editor_temp_excluded(tmp_path):
    out = _run([".#lock", "file.py~", ".file.swp"], str(tmp_path))
    assert out == []


def test_vim_swap_pattern_precise(tmp_path):
    (tmp_path / "notswap.txt").write_text("x")
    out = _run([".notaswapfile", "notswap.txt"], str(tmp_path))
    assert out == [b"notswap.txt"]


def test_vanished_entry_dropped(tmp_path):
    out = _run(["gone.txt"], str(tmp_path))
    assert out == []


def test_symlink_with_missing_target_kept(tmp_path):
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "does-not-exist")
    out = _run(["dangling"], str(tmp_path))
    assert out == [b"dangling"]


def test_no_user_repo_skips_vanished_check(tmp_path):
    out = _run(["never-checked.txt"], user_repo=None)
    assert out == [b"never-checked.txt"]


def test_empty_stdin(tmp_path):
    out = _run([], str(tmp_path))
    assert out == []
