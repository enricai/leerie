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
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from tests.test_chain_credential_transport import LAUNCHER, _invoke_helper

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


# ---------------------------------------------------------------------------
# (h) Keychain/file blobs missing claudeAiOauth.accessToken (e.g. the
# upstream Claude Code bug — steipete/CodexBar#1844 — where a background
# MCP-plugin OAuth flow overwrites the shared "Claude Code-credentials"
# Keychain item with only {"mcpOAuth": {...}}) must be rejected rather than
# staged into the container, where the CLI would then report "Not logged
# in · Please run /login" despite the launcher believing it found creds.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_mcp_oauth_only_keychain_blob_is_rejected_falls_through_to_file(
    tmp_path: Path,
) -> None:
    """A Keychain blob shaped like the upstream MCP-OAuth-clobber bug
    (mcpOAuth present, claudeAiOauth absent) must not be accepted — it
    should fall through to the on-disk file."""
    disk_blob = '{"claudeAiOauth":{"accessToken":"sk-disk"}}'
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file=disk_blob,
        stub_security_returns='{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}}}',
    )
    assert rc == 0
    assert out == disk_blob


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_mcp_oauth_only_keychain_blob_with_no_file_returns_nonzero(
    tmp_path: Path,
) -> None:
    """Same mcpOAuth-only Keychain blob, with no on-disk file fallback
    available either -> rc 1, not a false-positive success."""
    rc, out = _invoke_helper(
        tmp_path, env={},
        stub_security_returns='{"mcpOAuth":{"plugin:stripe:stripe|xyz":{"accessToken":""}}}',
    )
    assert rc != 0
    assert out == ""


def test_mcp_oauth_only_credentials_file_is_rejected(tmp_path: Path) -> None:
    """Same defect shape on the on-disk-file fallback: an mcpOAuth-only
    file must not be accepted as valid Claude session credentials."""
    rc, out = _invoke_helper(
        tmp_path, env={},
        credentials_file='{"mcpOAuth":{"plugin:vercel:vercel|def":{"accessToken":""}}}',
    )
    assert rc != 0
    assert out == ""


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_keychain_blob_with_claude_ai_oauth_but_empty_access_token_is_rejected(
    tmp_path: Path,
) -> None:
    """claudeAiOauth present but accessToken empty/missing must also be
    rejected — the shape check looks at the actual token value, not just
    key presence."""
    rc, out = _invoke_helper(
        tmp_path, env={},
        stub_security_returns='{"claudeAiOauth":{"accessToken":""}}',
    )
    assert rc != 0
    assert out == ""


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_valid_keychain_blob_with_mcp_oauth_alongside_is_still_accepted(
    tmp_path: Path,
) -> None:
    """A blob carrying BOTH mcpOAuth and a real claudeAiOauth.accessToken
    (the healthy post-fix shape Claude Code should normally produce) is
    still accepted — the fix rejects on absence of a real token, not on
    the mere presence of an mcpOAuth sibling key."""
    blob = (
        '{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}},'
        '"claudeAiOauth":{"accessToken":"sk-real-token"}}'
    )
    rc, out = _invoke_helper(
        tmp_path, env={},
        stub_security_returns=blob,
    )
    assert rc == 0
    assert out == blob


# ---------------------------------------------------------------------------
# (i) The rejection reason: the call site (STAGE-assembly block) uses this
# to distinguish "found an mcpOAuth-only blob" (a confirmed, currently
# unresolved upstream Claude Code CLI bug -- steipete/CodexBar#1844 -- where
# /login does not help) from "found nothing at all" (which /login or
# Keychain access CAN plausibly fix), and die()s with an accurate message
# for each case rather than the previous single generic "note and continue".
#
# The reason travels via a PID-scoped temp file
# ($_CLAUDE_CREDS_REJECT_REASON_FILE), not a plain shell variable --
# _extract_claude_credentials_json is invoked at the real call site via
# $(...) command substitution, which forks a subshell, so a var it assigns
# internally would never be visible to the caller once that subshell exits.
# _invoke_helper_with_reason below deliberately invokes the function the
# same way (via $(...)) rather than as a bare statement, so it actually
# exercises this subshell boundary instead of silently sidestepping it --
# an earlier version of this helper invoked the function as a bare
# statement, which does NOT fork a subshell, so it could not have caught a
# regression back to a plain-variable reason channel. See
# test_reason_survives_a_dollar_paren_subshell_call below for a minimal,
# targeted regression pin of that exact class of bug.
# ---------------------------------------------------------------------------

def _invoke_helper_with_reason(
    tmp_path: Path,
    env: dict[str, str],
    *,
    credentials_file: str | None = None,
    stub_security_returns: str | None = None,
) -> tuple[int, str, str]:
    """Like _invoke_helper, but also captures the rejection reason after
    the call. Invokes _extract_claude_credentials_json via $(...) --
    exactly as the real call site does -- so this test exercises the same
    subshell boundary the real code crosses, not a shortcut around it."""
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

    extract = tmp_path / "extract-helper.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        grep '^_CLAUDE_CREDS_REJECT_REASON_FILE=' "{LAUNCHER}"
        awk '
          /^_claude_creds_has_oauth_token\\(\\)/ {{ p=1 }}
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

    reason_file = tmp_path / "reason-file-location"
    # TMPDIR redirects _CLAUDE_CREDS_REJECT_REASON_FILE's
    # ${TMPDIR:-/tmp}/... resolution into this test's own auto-cleaned
    # tmp_path, instead of leaking a scratch file into the real host
    # /tmp on every test invocation.
    reason_tmpdir = tmp_path / "reason-tmpdir"
    reason_tmpdir.mkdir()
    full_env = {
        "HOME": str(fake_home),
        "TMPDIR": str(reason_tmpdir),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
    }
    full_env.update(env)
    result = subprocess.run(
        ["bash", "-c",
         # Mirrors the real call site verbatim: creds captured via $(...)
         # (a subshell), reason read back from the file afterward in the
         # (now-resumed) parent shell.
         f". {helper_file} && "
         f"CREDS=\"$(_extract_claude_credentials_json)\"; rc=$?; "
         f"printf '%s' \"$CREDS\"; "
         f"cat \"$_CLAUDE_CREDS_REJECT_REASON_FILE\" 2>/dev/null "
         f">'{reason_file}'; "
         f"exit $rc"],
        env=full_env, capture_output=True, text=True, timeout=10,
    )
    reason = reason_file.read_text() if reason_file.exists() else ""
    return result.returncode, result.stdout, reason


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_reject_reason_set_for_mcp_oauth_only_keychain_blob(tmp_path: Path) -> None:
    """An mcpOAuth-only Keychain blob with no file fallback sets the
    reason to keychain-mcp-oauth-only, so the call site can name the
    upstream-bug-specific failure instead of a generic one."""
    rc, out, reason = _invoke_helper_with_reason(
        tmp_path, env={},
        stub_security_returns='{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}}}',
    )
    assert rc != 0
    assert out == ""
    assert reason == "keychain-mcp-oauth-only"


def test_reject_reason_set_for_mcp_oauth_only_file(tmp_path: Path) -> None:
    """An mcpOAuth-only on-disk file (no Keychain hit) sets the reason to
    file-mcp-oauth-only."""
    rc, out, reason = _invoke_helper_with_reason(
        tmp_path, env={},
        credentials_file='{"mcpOAuth":{"plugin:vercel:vercel|def":{"accessToken":""}}}',
    )
    assert rc != 0
    assert out == ""
    assert reason == "file-mcp-oauth-only"


def test_reject_reason_empty_when_nothing_found_at_all(tmp_path: Path) -> None:
    """No env var, no Keychain, no file -> reason stays empty (this is a
    genuinely-plain 'nothing found' case, distinct from the mcpOAuth-only
    upstream-bug shape; /login or granting Keychain access remain
    plausible fixes here, unlike the mcpOAuth-only case)."""
    rc, out, reason = _invoke_helper_with_reason(tmp_path, env={})
    assert rc != 0
    assert out == ""
    assert reason == ""


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_reject_reason_empty_when_successfully_resolved(tmp_path: Path) -> None:
    """A successful resolution must not leave a stale reason from a
    previous call lingering -- the function resets the reason at entry."""
    rc, out, reason = _invoke_helper_with_reason(
        tmp_path, env={},
        stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-good"}}',
    )
    assert rc == 0
    assert out == '{"claudeAiOauth":{"accessToken":"sk-good"}}'
    assert reason == ""


# ---------------------------------------------------------------------------
# (j) The STAGE-assembly call site's die() message content, pinned via
# source inspection (the launcher's main flow cannot easily be driven
# end-to-end in a unit test -- it requires a full container/image
# pipeline). Mirrors the source-coupling discipline used elsewhere in this
# suite (e.g. tests/test_no_result_event_retry.py's ast-based extraction).
# ---------------------------------------------------------------------------

def _stage_assembly_block_source() -> str:
    src = LAUNCHER.read_text()
    start = src.index('if _CLAUDE_CREDS_JSON="$(_extract_claude_credentials_json)"')
    end = src.index("\nfi\n", start) + len("\nfi\n")
    return src[start:end]


def test_die_message_names_mcp_oauth_only_reasons() -> None:
    """The call site's die() message for the mcpOAuth-only reasons must
    name the upstream bug and its tracking issue, and must explicitly
    say /login will not fix it."""
    block = _stage_assembly_block_source()
    assert "keychain-mcp-oauth-only|file-mcp-oauth-only" in block
    assert "steipete/CodexBar#1844" in block
    assert "will NOT fix this" in block
    assert "claude setup-token" in block
    assert "CLAUDE_CODE_OAUTH_TOKEN" in block


def test_die_message_recommends_setup_token_not_login_for_mcp_oauth_case() -> None:
    """Regression guard: the mcpOAuth-only branch of the die() message
    must not tell the user to run /login (verified ineffective against
    the confirmed upstream bug) -- only the genuinely-different
    nothing-found branch may mention /login-adjacent remediation
    ('grant Keychain access')."""
    block = _stage_assembly_block_source()
    mcp_branch_start = block.index("keychain-mcp-oauth-only|file-mcp-oauth-only")
    mcp_branch_end = block.index(";;", mcp_branch_start)
    mcp_branch = block[mcp_branch_start:mcp_branch_end]
    assert "/login" not in mcp_branch or "will NOT fix" in mcp_branch


def test_call_site_exits_before_reaching_generic_note_path() -> None:
    """The old soft 'note — could not extract...' message must be fully
    replaced by the hard die() -- no dead code path continues past this
    block into a container run with no valid credentials staged."""
    src = LAUNCHER.read_text()
    assert "note — could not extract Claude credentials from Keychain." not in src
    block = _stage_assembly_block_source()
    assert "exit 1" in block


def test_die_guard_exempts_bedrock_bearer_token() -> None:
    """Regression guard (v0.9.89 reported failure): a user authenticating
    via AWS_BEARER_TOKEN_BEDROCK needs no Claude subscription Keychain/file
    credential at all -- that path is handled entirely independently a few
    lines below this block. The die() guard must not fire just because
    AWS_BEARER_TOKEN_BEDROCK is set and no Claude credential resolved."""
    block = _stage_assembly_block_source()
    guard_start = block.index('if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]')
    guard_end = block.index("; then", guard_start)
    guard = block[guard_start:guard_end]
    assert "AWS_BEARER_TOKEN_BEDROCK" in guard


def test_die_guard_exempts_settings_json_bedrock_mode() -> None:
    """Same regression class as the bearer-token case, for the sibling
    settings.json-driven Bedrock auth path (detect_bedrock_mode) -- never
    independently reported, but has the identical latent gap and must be
    exempted the same way."""
    block = _stage_assembly_block_source()
    guard_start = block.index('if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]')
    guard_end = block.index("; then", guard_start)
    guard = block[guard_start:guard_end]
    assert "detect_bedrock_mode" in guard


# ---------------------------------------------------------------------------
# (k) Minimal, targeted regression pin for the exact bug class found in the
# 2026-07-30 correctness audit: _extract_claude_credentials_json is invoked
# at the call site via $(...) command substitution, which forks a subshell.
# A plain global var assigned inside the function (the original, buggy
# implementation) is invisible to the caller once that subshell exits --
# reproduced live during the audit via a minimal standalone script before
# the fix landed. This test is deliberately independent of
# _invoke_helper_with_reason (which already exercises the same boundary,
# see its docstring above) -- a direct, minimal reproduction so a future
# refactor back to a bare variable fails immediately and obviously, without
# depending on the larger helper's plumbing.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_reason_survives_a_dollar_paren_subshell_call(tmp_path: Path) -> None:
    """The rejection-reason channel must be readable by the caller after
    invoking _extract_claude_credentials_json via $(...) -- the exact
    invocation shape the real call site uses. A plain shell variable
    assigned inside the function cannot satisfy this (variables set in a
    $(...) subshell do not propagate to the parent shell); only a real
    file (or another channel that survives subshell exit) can."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    sec = bin_dir / "security"
    sec.write_text(
        "#!/bin/sh\n"
        "printf '%s' '{\"mcpOAuth\":{\"plugin:supabase:supabase|abc\":"
        "{\"accessToken\":\"\"}}}'\n"
        "exit 0\n"
    )
    sec.chmod(0o755)

    extract = tmp_path / "extract-helper.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        grep '^_CLAUDE_CREDS_REJECT_REASON_FILE=' "{LAUNCHER}"
        awk '
          /^_claude_creds_has_oauth_token\\(\\)/ {{ p=1 }}
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

    # TMPDIR redirects _CLAUDE_CREDS_REJECT_REASON_FILE's
    # ${TMPDIR:-/tmp}/... resolution into this test's own auto-cleaned
    # tmp_path, instead of leaking a scratch file into the real host
    # /tmp on every test invocation.
    reason_tmpdir = tmp_path / "reason-tmpdir"
    reason_tmpdir.mkdir()
    full_env = {
        "HOME": str(fake_home),
        "TMPDIR": str(reason_tmpdir),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
    }
    result = subprocess.run(
        ["bash", "-c",
         # The exact shape that broke in the audit: call the function via
         # $(...), THEN try to read whatever it left behind for the reason,
         # from the resumed parent shell -- after the subshell has exited.
         f". {helper_file} && "
         f"_=\"$(_extract_claude_credentials_json)\"; "
         f"cat \"$_CLAUDE_CREDS_REJECT_REASON_FILE\" 2>/dev/null"],
        env=full_env, capture_output=True, text=True, timeout=10,
    )
    assert result.stdout == "keychain-mcp-oauth-only", (
        "the rejection reason did not survive the $(...) subshell boundary "
        "-- this is the exact bug found in the 2026-07-30 audit; the "
        "reason channel must be a file (or another subshell-surviving "
        "mechanism), not a plain shell variable"
    )


# ---------------------------------------------------------------------------
# (l) Behavioral (not just source-coupling) confirmation of the Bedrock
# exemption above: actually run the real, extracted STAGE-assembly call
# site -- including its _extract_claude_credentials_json,
# detect_bedrock_mode, and _check_claude_credential_ttl dependencies -- end
# to end, against the exact real-world shape reported against v0.9.89: a
# broken (mcpOAuth-only) Keychain item plus a valid AWS_BEARER_TOKEN_BEDROCK.
# Source-coupling alone (the tests above) proves the guard *mentions* the
# right variable names; this proves the actual bash boolean composition
# behaves correctly when run.
# ---------------------------------------------------------------------------

def _build_call_site_harness(tmp_path: Path) -> Path:
    """Extract _extract_claude_credentials_json (+ its
    _claude_creds_has_oauth_token helper), detect_bedrock_mode,
    _check_claude_credential_ttl, and the real STAGE-assembly call site
    verbatim from the launcher, and assemble them into one sourceable
    script with STAGE/OS/USER_REPO stubbed. Mirrors
    _invoke_helper_with_reason's extraction technique."""
    extract = tmp_path / "extract-harness.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        grep '^_CLAUDE_CREDS_REJECT_REASON_FILE=' "{LAUNCHER}"
        awk '
          /^_claude_creds_has_oauth_token\\(\\)/ {{ p=1 }}
          /^_extract_claude_credentials_json\\(\\)/ {{ p=1 }}
          p {{ print }}
          p && /^}}$/ {{ p=0 }}
        ' "{LAUNCHER}"
        awk '
          /^detect_bedrock_mode\\(\\)/ {{ p=1 }}
          p {{ print }}
          p && /^}}$/ {{ p=0 }}
        ' "{LAUNCHER}"
        awk '/^_CLAUDE_TTL_WARN_THRESHOLD_SEC=/,/^}}$/' "{LAUNCHER}"
        """))
    extract.chmod(0o755)
    functions_src = subprocess.run(
        ["bash", str(extract)], capture_output=True, text=True, check=True,
    ).stdout

    src = LAUNCHER.read_text()
    start = src.index('if _CLAUDE_CREDS_JSON="$(_extract_claude_credentials_json)"')
    end = src.index("\nfi\n", start) + len("\nfi\n")
    call_site = src[start:end]

    harness = tmp_path / "call-site-harness.sh"
    harness.write_text(
        functions_src
        + "\n"
        + 'STAGE="${TEST_STAGE:?}"\n'
        + 'mkdir -p "$STAGE/.claude"\n'
        + 'OS="Darwin"\n'
        + 'USER_REPO="${TEST_USER_REPO:-/tmp/nonexistent-repo-leerie-test}"\n\n'
        + call_site
        + '\necho "CALL_SITE_RESULT=proceeded"\n'
    )
    return harness


def _run_call_site(
    tmp_path: Path,
    *,
    oauth_token: str | None = None,
    bearer_token: str | None = None,
    bedrock_settings: str | None = None,
    stub_security_returns: str | None = None,
) -> tuple[int, str]:
    """Run the real extracted call site with a controlled env, return
    (rc, stdout). rc != 0 means the call site died (exit 1); rc == 0
    with 'CALL_SITE_RESULT=proceeded' in stdout means it proceeded past
    the credential-extraction block, matching the real launcher's
    behavior for a --runtime local/fly/ec2 run reaching this point."""
    harness = _build_call_site_harness(tmp_path)

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    sec = bin_dir / "security"
    if stub_security_returns is None:
        sec.write_text("#!/bin/sh\nexit 1\n")
    else:
        sec.write_text(f"#!/bin/sh\nprintf '%s' '{stub_security_returns}'\nexit 0\n")
    sec.chmod(0o755)

    if bedrock_settings is not None:
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text(bedrock_settings)

    stage = tmp_path / "stage"
    stage.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    env = {
        "HOME": str(fake_home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TMPDIR": str(stage),
        "TEST_STAGE": str(stage),
        "TEST_USER_REPO": str(repo_dir),
    }
    if oauth_token is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    if bearer_token is not None:
        env["AWS_BEARER_TOKEN_BEDROCK"] = bearer_token

    result = subprocess.run(
        ["bash", str(harness)], env=env, capture_output=True, text=True, timeout=10,
    )
    return result.returncode, result.stdout


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_call_site_proceeds_with_bearer_token_despite_broken_keychain() -> None:
    """The exact regression reported against v0.9.89: AWS_BEARER_TOKEN_BEDROCK
    set, Keychain holds only an mcpOAuth-only blob (the confirmed upstream
    Claude Code bug). Must proceed, not die -- Bedrock auth doesn't need a
    Claude Keychain credential at all."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = _run_call_site(
            Path(d),
            bearer_token="AB-fake-bedrock-token",
            stub_security_returns='{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}}}',
        )
        assert rc == 0, f"call site died despite AWS_BEARER_TOKEN_BEDROCK being set (rc={rc}): {out}"
        assert "CALL_SITE_RESULT=proceeded" in out


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_call_site_proceeds_with_settings_json_bedrock_mode_despite_broken_keychain() -> None:
    """Sibling case to the bearer-token regression: CLAUDE_CODE_USE_BEDROCK
    enabled via settings.json (detect_bedrock_mode) rather than the env
    var, same broken-Keychain shape. Must also proceed."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = _run_call_site(
            Path(d),
            bedrock_settings='{"env":{"CLAUDE_CODE_USE_BEDROCK":"1"}}',
            stub_security_returns='{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}}}',
        )
        assert rc == 0, f"call site died despite settings.json Bedrock mode (rc={rc}): {out}"
        assert "CALL_SITE_RESULT=proceeded" in out


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_call_site_still_dies_with_no_auth_anywhere() -> None:
    """Negative control: the Bedrock exemption must not overly widen into
    a silent no-op for a genuinely unauthenticated run -- no
    CLAUDE_CODE_OAUTH_TOKEN, no AWS_BEARER_TOKEN_BEDROCK, no
    settings.json Bedrock mode, broken Keychain. Must still die()."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = _run_call_site(
            Path(d),
            stub_security_returns='{"mcpOAuth":{"plugin:supabase:supabase|abc":{"accessToken":""}}}',
        )
        assert rc != 0, "call site proceeded despite no auth mechanism being configured at all"
        assert "CALL_SITE_RESULT=proceeded" not in out


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Keychain code path is gated by `uname -s = Darwin` in the launcher",
)
def test_call_site_proceeds_with_healthy_keychain_unaffected_by_bedrock_change() -> None:
    """Regression control: the common/working case (a valid Claude
    subscription credential resolves normally from Keychain, no Bedrock
    involved at all) must be completely unaffected by widening the
    guard -- it should proceed exactly as it did before this fix."""
    with tempfile.TemporaryDirectory() as d:
        rc, out = _run_call_site(
            Path(d),
            stub_security_returns='{"claudeAiOauth":{"accessToken":"sk-good"}}',
        )
        assert rc == 0, f"call site died despite a healthy resolved Keychain credential (rc={rc}): {out}"
        assert "CALL_SITE_RESULT=proceeded" in out
