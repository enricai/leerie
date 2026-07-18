"""Tests for the inverted credential precedence in
_extract_claude_credentials_json (leerie).

The authentication docs (https://code.claude.com/docs/en/authentication)
rank CLAUDE_CODE_OAUTH_TOKEN (#5, a long-lived 1-year token minted by
`claude setup-token`) above subscription OAuth credentials (#6, an
8h-ish token stored in the macOS Keychain or ~/.claude/.credentials.json)
for exactly this case: a container holds a snapshot of the subscription
token that it cannot refresh, so a long headless run must prefer the
env-var token when the caller has set one.

Reuses _invoke_helper from tests/test_chain_credential_transport.py
(imported, not duplicated) — that module already extracts
_extract_claude_credentials_json out of the launcher via awk and sources
it in a sub-bash with a controlled HOME/PATH.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from tests.test_chain_credential_transport import _invoke_helper

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_AUTH_SH = REPO_ROOT / "scripts" / "remote" / "seed-auth.sh"


def _seed_auth_credential_json(token: str) -> str:
    """Reproduce the JSON shape seed-auth.sh emits for a bare
    CLAUDE_CODE_OAUTH_TOKEN, by extracting the printf format string out
    of seed-auth.sh at test time rather than hand-copying a literal —
    so the two sites (leerie:96, seed-auth.sh:223) cannot silently
    diverge (same discipline as test_no_result_event_retry.py).
    """
    src = SEED_AUTH_SH.read_text()
    match = re.search(
        r'printf\s+\'([^\']*claudeAiOauth[^\']*)\'',
        src,
    )
    assert match, "failed to find the claudeAiOauth printf format string in seed-auth.sh"
    fmt = match.group(1)
    return fmt % token


# ---------------------------------------------------------------------------
# (a) Inverted precedence: CLAUDE_CODE_OAUTH_TOKEN wins over Keychain/file
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_env_var_wins_over_keychain_and_file_on_darwin(tmp_path: Path) -> None:
    """CLAUDE_CODE_OAUTH_TOKEN set -> wins even when both a Keychain entry
    and an on-disk credentials file are present. Reverting D0a (restoring
    Keychain-first precedence) fails this test."""
    rc, out = _invoke_helper(
        tmp_path,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-env-token"},
        credentials_file='{"claudeAiOauth":{"accessToken":"sk-stale-disk"}}',
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-keychain"}}',
    )
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env-token"}}'
    assert "sk-keychain" not in out
    assert "sk-stale-disk" not in out


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
    assert "sk-stale-disk" not in out


# ---------------------------------------------------------------------------
# (e) Emitted JSON shape matches seed-auth.sh's printf, extracted at test
# time rather than hand-copied.
# ---------------------------------------------------------------------------

def test_emitted_json_matches_seed_auth_shape(tmp_path: Path) -> None:
    """The synthesized JSON matches seed-auth.sh's
    {"claudeAiOauth":{"accessToken":...}} shape byte-for-byte, per the
    format string extracted live from seed-auth.sh:223."""
    rc, out = _invoke_helper(tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-abc123"})
    assert rc == 0
    assert out == _seed_auth_credential_json("sk-abc123")


# ---------------------------------------------------------------------------
# (b)/(c) Unset env var: fallback chain is unchanged (Keychain, then file)
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


# ---------------------------------------------------------------------------
# (d) Nothing set -> rc 1, empty stdout.
# ---------------------------------------------------------------------------

def test_returns_nonzero_when_no_creds_available(tmp_path: Path) -> None:
    """No env var, no Keychain, no file -> rc 1, empty stdout."""
    rc, out = _invoke_helper(tmp_path, env={})
    assert rc != 0
    assert out == ""
