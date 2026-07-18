"""Tests for the inverted credential precedence in
_extract_claude_credentials_json (leerie).

The authentication docs (https://code.claude.com/docs/en/authentication)
rank CLAUDE_CODE_OAUTH_TOKEN (#5, a long-lived 1-year token minted by
`claude setup-token`) above subscription OAuth credentials (#6, an
8h-ish token stored in the macOS Keychain or ~/.claude/.credentials.json)
for exactly this case: a container holds a snapshot of the subscription
token that it cannot refresh, so a long headless run must prefer the
env-var token when the caller has set one.

We test by extracting just the helper function via awk and sourcing it
in a sub-bash with a controlled HOME/PATH, mirroring
tests/test_chain_credential_transport.py's harness.
"""
from __future__ import annotations

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
    """Source the launcher's _extract_claude_credentials_json in bash,
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

    # Extract just the _extract_claude_credentials_json function body via
    # awk, anchored on the function name and close-brace at column 1
    # (same technique as test_chain_credential_transport.py).
    extract = tmp_path / "extract-helper.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
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
    assert helper_src.strip(), "failed to extract _extract_claude_credentials_json from leerie"
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
# Inverted precedence: CLAUDE_CODE_OAUTH_TOKEN wins over Keychain/file
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_env_var_wins_over_keychain_and_file_on_darwin(tmp_path: Path) -> None:
    """CLAUDE_CODE_OAUTH_TOKEN set -> wins even when both a Keychain entry
    and an on-disk credentials file are present."""
    rc, out = _invoke_helper(
        tmp_path,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-env-token"},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-keychain"}}',
    )
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env-token"}}'


def test_env_var_wins_over_file(tmp_path: Path) -> None:
    """CLAUDE_CODE_OAUTH_TOKEN set -> wins over a present on-disk file
    (non-Darwin-independent: Keychain is unreachable off Darwin anyway)."""
    rc, out = _invoke_helper(
        tmp_path,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-env-token"},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
    )
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env-token"}}'


def test_emitted_json_matches_seed_auth_shape(tmp_path: Path) -> None:
    """The synthesized JSON matches seed-auth.sh's
    {"claudeAiOauth":{"accessToken":...}} shape exactly."""
    rc, out = _invoke_helper(tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-abc123"})
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-abc123"}}'


# ---------------------------------------------------------------------------
# Unset env var: fallback chain is unchanged (Keychain, then file)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_keychain_wins_over_file_when_env_var_unset(tmp_path: Path) -> None:
    """No CLAUDE_CODE_OAUTH_TOKEN -> Keychain still wins over a stale
    on-disk file, exactly as before this change."""
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-fresh-keychain"}}',
    )
    assert rc == 0
    assert "sk-fresh-keychain" in out
    assert "sk-stale-disk" not in out


def test_falls_back_to_credentials_file_when_env_var_unset(tmp_path: Path) -> None:
    """No CLAUDE_CODE_OAUTH_TOKEN, no Keychain hit -> reads the on-disk
    file, exactly as before this change."""
    blob = '{"claudeAiOauth":{"accessToken":"sk-disk"}}'
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file=blob,
    )
    assert rc == 0
    assert out == blob


def test_returns_nonzero_when_no_creds_available(tmp_path: Path) -> None:
    """No env var, no Keychain, no file -> rc 1, empty stdout."""
    rc, out = _invoke_helper(tmp_path, env={})
    assert rc != 0
    assert out == ""
