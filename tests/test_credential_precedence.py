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

import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_chain_credential_transport import _invoke_helper

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_AUTH_SH = REPO_ROOT / "scripts" / "remote" / "seed-auth.sh"
EC2_SEED_AUTH_SH = REPO_ROOT / "scripts" / "remote" / "ec2-seed-auth.sh"

# The mandatory scope in every synthesized credential blob. CLI 2.1.210's
# file-auth path rejects a scope-less {claudeAiOauth.accessToken} blob with
# "Not logged in" (measured by field-ablation against the real image); only
# "user:inference" satisfies it. All three synthesized-blob sites
# (_extract_claude_credentials_json, seed-auth.sh, ec2-seed-auth.sh) must
# carry it.
_REQUIRED_SCOPE = "user:inference"


def _printf_credential_json(script_path: Path, token: str) -> str:
    """Reproduce the JSON shape a seed script emits for a bare
    CLAUDE_CODE_OAUTH_TOKEN, by extracting the printf format string out of
    the script at test time rather than hand-copying a literal — so the
    sites cannot silently diverge (same discipline as
    test_no_result_event_retry.py). Works for both seed-auth.sh and
    ec2-seed-auth.sh.
    """
    src = script_path.read_text()
    match = re.search(
        r'printf\s+\'([^\']*claudeAiOauth[^\']*)\'',
        src,
    )
    assert match, (
        f"failed to find the claudeAiOauth printf format string in {script_path.name}"
    )
    fmt = match.group(1)
    return fmt % token


def _seed_auth_credential_json(token: str) -> str:
    """Back-compat wrapper — seed-auth.sh's synthesized shape."""
    return _printf_credential_json(SEED_AUTH_SH, token)


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
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env-token","scopes":["user:inference"]}}'
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
    assert out == '{"claudeAiOauth":{"accessToken":"sk-env-token","scopes":["user:inference"]}}'
    assert "sk-stale-disk" not in out


# ---------------------------------------------------------------------------
# (e) Emitted JSON shape matches seed-auth.sh's printf, extracted at test
# time rather than hand-copied.
# ---------------------------------------------------------------------------

def test_emitted_json_matches_seed_auth_shape(tmp_path: Path) -> None:
    """The synthesized JSON matches seed-auth.sh's
    {"claudeAiOauth":{"accessToken":...,"scopes":[...]}} shape byte-for-byte,
    per the format string extracted live from seed-auth.sh — keeping leerie's
    _extract_claude_credentials_json and seed-auth.sh's fallback coupled."""
    rc, out = _invoke_helper(tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-abc123"})
    assert rc == 0
    assert out == _seed_auth_credential_json("sk-abc123")


# ---------------------------------------------------------------------------
# (f) The synthesized blob carries scopes:["user:inference"] at ALL THREE
# sites. CLI 2.1.210's file-auth path rejects a scope-less blob with
# "Not logged in" (measured); this is the whole bug. A regression that drops
# the scope re-breaks every headless run.
# ---------------------------------------------------------------------------

def test_leerie_synthesized_blob_carries_inference_scope(tmp_path: Path) -> None:
    """leerie's _extract_claude_credentials_json emits scopes:["user:inference"]."""
    rc, out = _invoke_helper(tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-x"})
    assert rc == 0
    parsed = json.loads(out)
    assert parsed["claudeAiOauth"]["scopes"] == [_REQUIRED_SCOPE]


def test_seed_auth_synthesized_blob_carries_inference_scope() -> None:
    """seed-auth.sh's fallback printf carries scopes:["user:inference"]."""
    parsed = json.loads(_printf_credential_json(SEED_AUTH_SH, "sk-x"))
    assert parsed["claudeAiOauth"]["scopes"] == [_REQUIRED_SCOPE]


def test_ec2_seed_auth_synthesized_blob_carries_inference_scope() -> None:
    """ec2-seed-auth.sh's fallback printf carries scopes:["user:inference"].

    Not exercised by _invoke_helper (which only sources leerie's function);
    pinned here by extracting the printf format string live, mirroring
    _seed_auth_credential_json's discipline for the Fly script.
    """
    parsed = json.loads(_printf_credential_json(EC2_SEED_AUTH_SH, "sk-x"))
    assert parsed["claudeAiOauth"]["scopes"] == [_REQUIRED_SCOPE]


def test_all_three_synthesized_sites_emit_identical_shape(tmp_path: Path) -> None:
    """The three synthesized-blob sites must emit byte-identical JSON so a
    fix (or regression) at one cannot silently diverge from the others."""
    rc, leerie_out = _invoke_helper(tmp_path, env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-tok"})
    assert rc == 0
    seed_out = _printf_credential_json(SEED_AUTH_SH, "sk-tok")
    ec2_out = _printf_credential_json(EC2_SEED_AUTH_SH, "sk-tok")
    assert leerie_out == seed_out == ec2_out


# ---------------------------------------------------------------------------
# (g) Fix 2: the launcher forwards -e CLAUDE_CODE_OAUTH_TOKEN into the
# container whenever the env var is set, independent of which credential
# branch resolves (durability — a file blob dies at expiresAt; the long-lived
# env-var token survives a headless run). Source-coupling: _invoke_helper only
# covers _extract_claude_credentials_json, not the AUTH_MOUNTS staging block.
# ---------------------------------------------------------------------------

LAUNCHER = REPO_ROOT / "leerie"


def _auth_mounts_token_block() -> str:
    """Extract the credential-staging region of the launcher (from the
    always-forward `if [ -n ...CLAUDE_CODE_OAUTH_TOKEN...` guard through the
    end of the resolve if/else) for source-coupling assertions."""
    src = LAUNCHER.read_text()
    start = src.index('if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then\n  AUTH_MOUNTS+=(-e')
    # take a generous window covering the resolve if/else that follows
    return src[start:start + 1500]


def test_launcher_always_forwards_token_env_var() -> None:
    """The -e CLAUDE_CODE_OAUTH_TOKEN injection sits at top level (fires
    whenever the token is set), NOT buried in the resolve-failure `else`
    arm where it was previously unreachable when the token staged into the
    file."""
    block = _auth_mounts_token_block()
    # The forward guard precedes the `if _CLAUDE_CREDS_JSON=...` resolve.
    fwd = block.index('AUTH_MOUNTS+=(-e "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN")')
    resolve = block.index('if _CLAUDE_CREDS_JSON=')
    assert fwd < resolve, (
        "the -e CLAUDE_CODE_OAUTH_TOKEN forward must precede (be independent "
        "of) the credential-resolve if/else — otherwise it only fires on the "
        "resolve-failure path, the original bug"
    )


def test_launcher_else_arm_no_longer_re_adds_token() -> None:
    """The resolve-failure `else` arm must NOT re-add the token env var (it is
    now forwarded unconditionally above), or it would be added twice."""
    block = _auth_mounts_token_block()
    else_idx = block.index("else\n")
    else_arm = block[else_idx:]
    assert 'AUTH_MOUNTS+=(-e "CLAUDE_CODE_OAUTH_TOKEN' not in else_arm, (
        "the else arm should no longer add -e CLAUDE_CODE_OAUTH_TOKEN — it is "
        "forwarded unconditionally before the if/else now"
    )


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
