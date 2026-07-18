"""Tests for the launcher's _extract_claude_credentials_json helper.

The helper is the single source of truth for "where do Claude OAuth
credentials live on this host" (DRY across the existing STAGE-assembly
flow and the new --chain credential pack-up). Defined near the top of
the leerie launcher so it's callable from any verb arm.

We test by sourcing the launcher in a sub-bash with --version short-
circuit so only the function definitions and pre-dispatch code load,
then invoking the function with a controlled HOME and PATH so the
fallback chain (env var → Keychain → ~/.claude/.credentials.json) is
exercised deterministically. The long-lived CLAUDE_CODE_OAUTH_TOKEN
wins outright when set (D0a): a container holds a snapshot of the
subscription OAuth credential that it can never refresh, so the token
designed for headless/CI use takes precedence over Keychain and the
on-disk file, which remain the fallback chain when it's unset.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _invoke_helper(
    tmp_path: Path,
    env: dict[str, str],
    *,
    credentials_file: str | None = None,
    stub_security_returns: str | None = None,
) -> tuple[int, str]:
    """Source the launcher in bash, call _extract_claude_credentials_json,
    return (rc, stdout).

    Args:
        env: Environment for the bash subprocess. HOME is auto-set to
            tmp_path; PATH is auto-set to point at a stub-bin dir.
        credentials_file: When set, the file's contents become
            $HOME/.claude/.credentials.json.
        stub_security_returns: When set on Darwin, a stub `security`
            binary on PATH prints this and exits 0; when None, the
            stub exits 1 (Keychain miss).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    if credentials_file is not None:
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text(credentials_file)

    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    sec = bin_dir / "security"
    if stub_security_returns is None:
        sec.write_text("#!/bin/sh\nexit 1\n")
    else:
        sec.write_text(f"#!/bin/sh\nprintf '%s' '{stub_security_returns}'\nexit 0\n")
    sec.chmod(0o755)

    # The launcher does `case "$1" in ... --version) ... ;; esac` early.
    # We can't easily source it because main code paths run unconditionally.
    # Instead, extract the helper function via sed and source just that.
    # Source the launcher in a side bash, but exit before main flow:
    # the simplest way is to extract the function definition into its
    # own file and source that.
    extract = tmp_path / "extract-helper.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Pull just the _extract_claude_credentials_json function out of
        # the launcher and source it. Anchor on the function name and
        # close-brace at column 1.
        awk '
          /^_extract_claude_credentials_json\\(\\)/ {{ p=1 }}
          p {{ print }}
          p && /^}}$/ {{ p=0 }}
        ' "{LAUNCHER}"
        """))
    extract.chmod(0o755)
    helper_src = subprocess.run(
        ["bash", str(extract)], capture_output=True, text=True, check=True,
    ).stdout
    helper_file = tmp_path / "helper.sh"
    helper_file.write_text(helper_src)

    full_env = {
        "HOME": str(fake_home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
    }
    full_env.update(env)
    result = subprocess.run(
        ["bash", "-c", f". {helper_file} && _extract_claude_credentials_json"],
        env=full_env, capture_output=True, text=True, timeout=10,
    )
    return result.returncode, result.stdout


# ---------------------------------------------------------------------------
# Credential acquisition fallback chain
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_returns_keychain_blob_on_darwin(tmp_path: Path) -> None:
    """On Darwin, when the stub `security` binary returns a JSON blob,
    the helper prints it verbatim."""
    blob = '{"claudeAiOauth":{"accessToken":"sk-keychain"}}'
    rc, out = _invoke_helper(
        tmp_path, env={},
        stub_security_returns=blob,
    )
    assert rc == 0
    assert out == blob


def test_falls_back_to_credentials_file(tmp_path: Path) -> None:
    """When Keychain misses (or we're on Linux), reads the on-disk file."""
    blob = '{"claudeAiOauth":{"accessToken":"sk-disk"}}'
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file=blob,
        # Keychain miss (default).
    )
    assert rc == 0
    assert out == blob


def test_env_var_wins_over_keychain_and_file(tmp_path: Path) -> None:
    """CLAUDE_CODE_OAUTH_TOKEN (the long-lived, refreshable-by-nobody-but-
    that's-fine token) wins outright over both a present Keychain blob and
    a present on-disk file — D0a's inverted precedence.

    A container holds a snapshot of the subscription OAuth credential that
    it can never refresh (no interactive host session, and refresh rotates
    the host's token out from under it), so the long-lived env-var token
    must be preferred whenever it's set, regardless of platform. No
    skipif(darwin) here: this must hold both when the Keychain branch is
    exercised (Darwin, stubbed to return a blob) and when it's structurally
    absent (Linux) — D0a's `[ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]` check runs
    before the `uname -s = Darwin` gate, so the win must be observable on
    both platforms this suite runs on.
    """
    rc, out = _invoke_helper(
        tmp_path,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-env"},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-fresh-keychain"}}',
    )
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env"}}'
    assert "sk-stale-disk" not in out
    assert "sk-fresh-keychain" not in out


def test_returns_nonzero_when_no_creds_available(tmp_path: Path) -> None:
    """Empty Keychain, no file, no env var → rc 1 + empty stdout.

    The launcher's --chain arm treats this as a fatal error with a
    user-actionable diagnostic; the STAGE-assembly block falls back to
    the legacy env-var bridge.
    """
    rc, out = _invoke_helper(tmp_path, env={})
    assert rc != 0
    assert out == ""


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_keychain_wins_over_file_on_darwin(tmp_path: Path) -> None:
    """When CLAUDE_CODE_OAUTH_TOKEN is unset, Keychain is the macOS
    canonical source; prefer it over a stale on-disk file."""
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-fresh-keychain"}}',
    )
    assert rc == 0
    assert "sk-fresh-keychain" in out
    assert "sk-stale-disk" not in out
