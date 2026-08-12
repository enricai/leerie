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

import pytest
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
    """Even with a hook present, a genuine auth/network failure must be
    recognised so it is never misclassified as a hook failure.

    Cases are FULL stderr, as `host_finalize` captures it — not isolated
    fragments. That matters since the classifier became git-framed: an
    `ssh:` or `git@host:` line counts only when git also emits its
    `fatal: Could not read from remote repository` companion, which is what
    separates the push's own transport failure from a hook that ssh'd
    somewhere and failed. Every real `git push` reproduction shows the two
    lines together; a bare fragment is deliberately not classified. The full
    20-case corpus is at the bottom of this file.
    """
    cases = [
        "fatal: Authentication failed for 'https://github.com/o/r.git/'",
        "ssh: connect to host github.com port 22: Connection refused\n"
        "fatal: Could not read from remote repository.",
        "fatal: unable to access 'https://github.com/o/r.git/': "
        "Could not resolve host: github.com",
        "git@github.com: Permission denied (publickey).\n"
        "fatal: Could not read from remote repository.",
        "remote: Repository not found.\n"
        "fatal: repository 'https://github.com/o/r.git/' not found",
        # N24 regression, reproduced against real git 2026-08-12: the
        # non-interactive HTTPS credential failure. A pre-push hook runs
        # BEFORE authentication, so with a hook installed this stderr was
        # classified as a HOOK failure and the operator was told to retry
        # with `--no-verify` — which cannot fix a credential problem.
        "fatal: could not read Username for 'https://github.com': "
        "terminal prompts disabled",
        # OpenSSH's offered-method list form.
        "git@github.com: Permission denied (publickey,password).\n"
        "fatal: Could not read from remote repository.",
    ]
    for stderr_text in cases:
        assert _is_auth_or_network(stderr_text) is True, stderr_text


def test_ordinary_hook_related_text_is_not_treated_as_auth_or_network():
    """A hook's own failure text (arbitrary vendor prose) must not
    accidentally match the narrow, git-owned exclusion list."""
    assert _is_auth_or_network("husky - pre-push script failed (code 254)") is False
    assert _is_auth_or_network("acme-corp: commit policy violation") is False
    # The verbatim stderr of a real failing pre-push hook, captured from git
    # 2026-08-12. Git contributes only the second line — there is no
    # hook-identifying text at all, which is why classification is
    # structural (_host_finalize_pre_push_hook_present) rather than textual.
    # Broadening the exclusion list for `could not read` must not start
    # swallowing this.
    real_hook_failure = (
        "✖ typecheck failed: 3 errors in src/app.ts\n"
        "error: failed to push some refs to '../remote.git'"
    )
    assert _is_auth_or_network(real_hook_failure) is False

    # Tool prose that contains git-ish words. The exclusion list must match
    # git's OWN sentences, not the bare prefix: a bare `could not read`
    # swallows both of these, classifies a real hook failure as
    # auth/network, and suppresses the `--no-verify` hint that is the whole
    # point of the probe. Measured against the real patterns 2026-08-12.
    tool_prose_with_could_not_read = [
        "Error: Could not read config file: .eslintrc.json\n"
        "error: failed to push some refs to 'origin'",
        "Could not read source map for file.js\n"
        "error: failed to push some refs to 'origin'",
    ]
    for stderr_text in tool_prose_with_could_not_read:
        assert _is_auth_or_network(stderr_text) is False, stderr_text


# ---------------------------------------------------------------------------
# The N24 corpus. Eight of the nine GIT_FAILURES entries were reproduced
# against real `git push` on a real repo (including a local HTTP server
# returning 401 for the Authentication-failed shape, and a real publickey
# denial against github.com). The exception is `ssh-timeout`, which is
# hand-authored: it carries BSD/macOS's `Operation timed out` wording, which
# a Linux host does not emit (Linux says `Connection timed out`), and the
# classifier accepts both. Flagged rather than quietly presented as a
# reproduction. Every HOOK_FAILURES entry is realistic pre-push hook output.
#
# The classifier gates an operator hint, so both directions matter: a missed
# git failure sends them to `--no-verify`, which cannot fix credentials, and a
# matched hook failure hides the hint that would have helped.
#
# Scored when this landed: the previous bare-phrase list got 9/9 git but only
# 4/11 hook; this two-part, git-framed classifier gets 9/9 and 11/11.
# ---------------------------------------------------------------------------

GIT_FAILURES = [
    ("https-no-credentials",
     "git: 'credential-osxkeychain' is not a git command. See 'git --help'.\n"
     "fatal: could not read Username for 'https://github.com': "
     "terminal prompts disabled"),
    ("unresolvable-host",
     "fatal: unable to access 'https://nope.invalid/x.git/': "
     "Could not resolve host: nope.invalid"),
    ("connection-refused",
     "ssh: connect to host localhost port 1: Connection refused\n"
     "fatal: Could not read from remote repository."),
    ("not-a-repository",
     "fatal: '/nonexistent/path/repo.git' does not appear to be a git repository\n"
     "fatal: Could not read from remote repository."),
    ("authentication-failed",
     "fatal: Authentication failed for 'http://127.0.0.1:8771/x.git/'"),
    ("publickey-denied",
     "git@github.com: Permission denied (publickey).\n"
     "fatal: Could not read from remote repository."),
    ("publickey-multi-method",
     "git@github.com: Permission denied (publickey,password).\n"
     "fatal: Could not read from remote repository."),
    ("repository-not-found",
     "remote: Repository not found.\n"
     "fatal: repository 'https://github.com/o/r.git/' not found"),
    ("ssh-timeout",
     "ssh: connect to host github.com port 22: Operation timed out\n"
     "fatal: Could not read from remote repository."),
]

HOOK_FAILURES = [
    ("eslint-config", "Error: Could not read config file: .eslintrc.json\n"
                      "error: failed to push some refs to 'origin'"),
    ("jest-source-map", "Could not read source map for file.js\n"
                        "error: failed to push some refs to 'origin'"),
    # Postgres says FATAL: too, and case-insensitively that is `fatal:`.
    ("postgres-auth", 'FATAL: password authentication failed for user "postgres"\n'
                      "error: failed to push some refs to 'origin'"),
    ("psycopg-refused",
     "psycopg2.OperationalError: could not connect to server: Connection refused\n"
     "error: failed to push some refs to 'origin'"),
    ("curl-dns", "curl: (6) Could not resolve host: registry.example.com\n"
                 "error: failed to push some refs to 'origin'"),
    # Java's `Error:` is why `error:` is not a git-framing prefix.
    ("java-jarfile", "Error: Unable to access jarfile build/app.jar"),
    ("npm-404", "npm ERR! 404 repository not found : @acme/pkg"),
    ("jest-timeout", "FAIL src/a.test.ts - connection timed out after 5000ms"),
    ("husky-branded", "husky - pre-push script failed (code 1)"),
    ("typecheck", "\u2716 typecheck failed: 3 errors in src/app.ts\n"
                  "error: failed to push some refs to '../remote.git'"),
    # A hook that ssh'd somewhere: an ssh: line WITHOUT git's companion
    # "could not read from remote repository" is the hook's problem.
    ("hook-ssh-deploy",
     "ssh: connect to host deploy.internal port 22: Connection refused\n"
     "error: deploy smoke-test hook failed"),
]


@pytest.mark.parametrize("label,stderr_text", GIT_FAILURES,
                         ids=[c[0] for c in GIT_FAILURES])
def test_real_git_failures_classify_as_auth_or_network(label, stderr_text):
    assert _is_auth_or_network(stderr_text) is True, (
        f"{label}: a real git failure was classified as a hook failure; the "
        "operator would be told to retry with --no-verify, which cannot fix "
        "a credential or network problem")


@pytest.mark.parametrize("label,stderr_text", HOOK_FAILURES,
                         ids=[c[0] for c in HOOK_FAILURES])
def test_hook_output_never_classifies_as_auth_or_network(label, stderr_text):
    assert _is_auth_or_network(stderr_text) is False, (
        f"{label}: ordinary tool output matched the auth/network list, so a "
        "genuine hook failure loses the --no-verify hint this gates")


def test_corpus_covers_both_directions():
    """Anti-vacuity: a corpus that drifted to one side would let a
    classifier that answers a constant score perfectly.

    This asserts on literals in this file, so it cannot detect a product
    regression — its only job is to stop the corpus itself from being
    hollowed out until one direction is untested.
    """
    assert len(GIT_FAILURES) >= 8 and len(HOOK_FAILURES) >= 8
