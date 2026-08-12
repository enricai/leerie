"""Unit tests for scripts/host-finalize.sh's mechanical pre-push hook
classification probe (N24).

scripts/host-finalize.sh:389 used to detect a pre-push hook failure by
grepping push stderr for vendor-specific prose ("husky", "pre-push script
failed", "exit code 254") — arbitrary third-party text that misses any
non-husky-branded or newer-husky hook failure entirely. These tests drive
the replacement structural probe (`_host_finalize_pre_push_hook_present` +
`_host_finalize_is_auth_or_network_push_error`) directly against real git
repositories, reproducing the work order's verified control pair:

  1. a real repo with a non-husky-branded pre-push hook that fails →
     must classify as a hook failure (HOOK PRESENT: yes)
  2. a real repo with no hook but an unreachable remote → must NOT
     classify as a hook failure (HOOK PRESENT: no)

These are pure git/bash tests — no `jq` involved (unlike
test_host_finalize_sh.py's full `host_finalize` harness, which needs jq
to parse run.json), so they run unconditionally.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _hook_present(repo: Path) -> bool:
    r = subprocess.run(
        ["bash", "-c", f". {HOST_FINALIZE_SH}; "
                        f"_host_finalize_pre_push_hook_present {repo}"],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def _is_auth_or_network(stderr_text: str) -> bool:
    r = subprocess.run(
        ["bash", "-c",
         f". {HOST_FINALIZE_SH}; "
         f'_host_finalize_is_auth_or_network_push_error "$1"',
         "_", stderr_text],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def test_non_husky_branded_hook_classifies_as_hook_present(tmp_path):
    """The work order's control pair, case 1: a real repo with a
    non-husky-branded, failing pre-push hook must be detected by the
    structural probe — the old text grep (husky / 'pre-push script
    failed' / 'exit code 254') would have missed this shape entirely
    since the hook here mentions none of those strings."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook = hooks_dir / "pre-push"
    hook.write_text("#!/bin/sh\necho 'acme-corp: commit policy violation' >&2\nexit 1\n")
    hook.chmod(0o755)

    assert _hook_present(repo) is True
    # The hook's own stderr carries no auth/network marker, so the
    # combined classification (probe AND NOT exclusion-listed) is HOOK.
    assert _is_auth_or_network("acme-corp: commit policy violation") is False


def test_no_hook_and_unreachable_remote_classifies_as_not_hook(tmp_path):
    """The work order's control pair, case 2: a real repo with no
    pre-push hook installed, whose push fails for an unreachable-remote
    reason, must NOT classify as a hook failure."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    assert _hook_present(repo) is False


def test_non_executable_hook_file_does_not_count(tmp_path):
    """A pre-push file that exists but lacks the executable bit is not a
    live hook (git itself skips non-executable hook files)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 1\n")

    assert _hook_present(repo) is False


def test_custom_hooksPath_relative_is_resolved_against_repo_root(tmp_path):
    """core.hooksPath can point anywhere, and a relative value is
    resolved against the worktree root — not .git/hooks."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    custom = repo / "custom-hooks"
    custom.mkdir()
    hook = custom / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", "custom-hooks"],
                    check=True)

    assert _hook_present(repo) is True


def test_custom_hooksPath_with_no_hook_file_is_absent(tmp_path):
    """A configured hooksPath with no pre-push file inside it is
    correctly reported as no hook present."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    custom = repo / "custom-hooks"
    custom.mkdir()
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", "custom-hooks"],
                    check=True)

    assert _hook_present(repo) is False


def test_auth_and_network_exclusion_list_overrides_hook_presence():
    """Even with a hook present, a genuine auth/network failure message
    (git's own fixed wording, not third-party hook prose) must be
    recognized so it is never misclassified as a hook failure."""
    cases = [
        "fatal: Authentication failed for 'https://github.com/o/r.git/'",
        "ssh: connect to host github.com port 22: Connection refused",
        "fatal: unable to access 'https://github.com/o/r.git/': Could not resolve host: github.com",
        "git@github.com: Permission denied (publickey).",
        "remote: Repository not found.",
    ]
    for stderr_text in cases:
        assert _is_auth_or_network(stderr_text) is True, stderr_text


def test_ordinary_hook_related_text_is_not_treated_as_auth_or_network():
    """A hook's own failure text (arbitrary vendor prose) must not
    accidentally match the narrow, git-owned exclusion list."""
    assert _is_auth_or_network("husky - pre-push script failed (code 254)") is False
    assert _is_auth_or_network("acme-corp: commit policy violation") is False
