"""Tests for CLAUDE_CODE_OAUTH_TOKENS (plural) launcher-side parsing and
forwarding (DESIGN §6 *Multi-token rotation*).

CLAUDE_CODE_OAUTH_TOKENS (comma-separated) supersedes the singular
CLAUDE_CODE_OAUTH_TOKEN when set: the launcher reassigns the singular var
to the list's first element (so the existing _extract_claude_credentials_json
/ _check_claude_credential_ttl / mcpOAuth-guard machinery applies unchanged),
and forwards the raw plural value into the container as its own `-e` flag
so the orchestrator can probe/select across the full list.

The harness extracts the real launcher block verbatim (same technique as
test_launcher_env_forwarding.py's _extract_forwarding_loop) so this test
cannot silently diverge from the shipped code.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_token_block() -> str:
    """Pull the CLAUDE_CODE_OAUTH_TOKENS parsing + AUTH_MOUNTS forwarding
    block verbatim from the launcher."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(if \[ -n \"\$\{CLAUDE_CODE_OAUTH_TOKENS:-\}\" \]; then\n"
        r"  _leerie_token_list=\(\).*?"
        r"if \[ -n \"\$\{CLAUDE_CODE_OAUTH_TOKENS:-\}\" \]; then\n"
        r"  AUTH_MOUNTS\+=\(-e \"CLAUDE_CODE_OAUTH_TOKENS=\$CLAUDE_CODE_OAUTH_TOKENS\"\)\nfi\n)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate the CLAUDE_CODE_OAUTH_TOKENS parsing block in the launcher"
    return m.group(1)


_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

AUTH_MOUNTS=()

# ---- token parsing + forwarding block, extracted verbatim ---------------
__TOKEN_BLOCK__

echo "TOKEN=${CLAUDE_CODE_OAUTH_TOKEN:-}"
for a in ${AUTH_MOUNTS[@]+"${AUTH_MOUNTS[@]}"}; do printf 'MOUNT:%s\n' "$a"; done
"""


def _run(env: dict) -> tuple[str, list[str]]:
    """Run the harness with `env`; return (resolved singular token,
    AUTH_MOUNTS entries)."""
    harness = _HARNESS.replace("__TOKEN_BLOCK__", _extract_token_block())
    result = subprocess.run(
        ["bash", "-c", harness],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    token = ""
    mounts = []
    for line in result.stdout.splitlines():
        if line.startswith("TOKEN="):
            token = line[len("TOKEN="):]
        elif line.startswith("MOUNT:"):
            mounts.append(line[len("MOUNT:"):])
    return token, mounts


def test_plural_supersedes_singular():
    token, _ = _run({
        "CLAUDE_CODE_OAUTH_TOKEN": "old-single-token",
        "CLAUDE_CODE_OAUTH_TOKENS": "token-a,token-b",
    })
    assert token == "token-a", (
        "CLAUDE_CODE_OAUTH_TOKENS must supersede the singular var — the "
        "first list element should win, not the stale singular value"
    )


def test_plural_forwarded_as_its_own_dash_e():
    _, mounts = _run({"CLAUDE_CODE_OAUTH_TOKENS": "token-a,token-b"})
    assert "CLAUDE_CODE_OAUTH_TOKENS=token-a,token-b" in mounts


def test_singular_also_forwarded_from_first_element():
    _, mounts = _run({"CLAUDE_CODE_OAUTH_TOKENS": "token-a,token-b"})
    assert "CLAUDE_CODE_OAUTH_TOKEN=token-a" in mounts


def test_single_element_list_behaves_like_singular():
    token, mounts = _run({"CLAUDE_CODE_OAUTH_TOKENS": "only-token"})
    assert token == "only-token"
    assert "CLAUDE_CODE_OAUTH_TOKEN=only-token" in mounts
    assert "CLAUDE_CODE_OAUTH_TOKENS=only-token" in mounts


def test_whitespace_trimmed_around_entries():
    token, _ = _run({"CLAUDE_CODE_OAUTH_TOKENS": "  token-a  , token-b "})
    assert token == "token-a"


def test_empty_entries_dropped():
    token, _ = _run({"CLAUDE_CODE_OAUTH_TOKENS": ",,token-a,,token-b,"})
    assert token == "token-a"


def test_all_empty_entries_leaves_singular_untouched():
    """A plural var that parses to an empty list (e.g. just commas) must
    not clobber an existing singular token — falls through unchanged."""
    token, _ = _run({
        "CLAUDE_CODE_OAUTH_TOKEN": "existing-token",
        "CLAUDE_CODE_OAUTH_TOKENS": " , , ",
    })
    assert token == "existing-token"


def test_singular_only_path_unchanged():
    """No CLAUDE_CODE_OAUTH_TOKENS set at all — singular var passes through
    byte-for-byte, no plural -e flag emitted."""
    token, mounts = _run({"CLAUDE_CODE_OAUTH_TOKEN": "solo-token"})
    assert token == "solo-token"
    assert "CLAUDE_CODE_OAUTH_TOKEN=solo-token" in mounts
    assert not any(m.startswith("CLAUDE_CODE_OAUTH_TOKENS=") for m in mounts)


def test_neither_set_no_mounts():
    token, mounts = _run({})
    assert token == ""
    assert mounts == []


# ── coupling guard: the Fly/EC2 detached-launch heredocs also forward the
# plural var (the seed-auth.sh scripts only ever write a single-token
# .credentials.json file fallback — they never touch process env vars, so
# the actual multi-token env forwarding for those runtimes is the
# child_env injection in the heredocs, not seed-auth.sh). ─────────────────

def test_fly_heredoc_forwards_oauth_tokens():
    src = LAUNCHER.read_text()
    assert 'child_env["CLAUDE_CODE_OAUTH_TOKENS"] = ${_oauth_tokens_json}' in src, (
        "the Fly detached-launch child_env no longer injects "
        "CLAUDE_CODE_OAUTH_TOKENS — the remote orchestrator's multi-token "
        "probe/rotation would see only whatever seed-auth.sh staged as a "
        "single-token .credentials.json file"
    )


def test_fly_heredoc_json_encodes_oauth_tokens_before_substitution():
    """Same injection-safety discipline as the Bedrock bearer-token
    values: an opaque token could contain `"`/`\\` that would break out of
    a raw Python string literal in the unquoted heredoc."""
    src = LAUNCHER.read_text()
    assert re.search(
        r'_oauth_tokens_json="\$\(python3 -c \'import json, sys; '
        r'print\(json\.dumps\(sys\.argv\[1\]\)\)\' '
        r'"\$\{CLAUDE_CODE_OAUTH_TOKENS:-\}"\)"',
        src,
    ), "CLAUDE_CODE_OAUTH_TOKENS is not JSON-encoded before Fly heredoc substitution"


def test_ec2_heredoc_forwards_oauth_tokens():
    src = LAUNCHER.read_text()
    assert 'child_env["CLAUDE_CODE_OAUTH_TOKENS"] = ${_ec2_oauth_tokens_json}' in src, (
        "the EC2 detached-launch child_env no longer injects "
        "CLAUDE_CODE_OAUTH_TOKENS"
    )


def test_ec2_heredoc_json_encodes_oauth_tokens_before_substitution():
    src = LAUNCHER.read_text()
    assert re.search(
        r'_ec2_oauth_tokens_json="\$\(python3 -c \'import json, sys; '
        r'print\(json\.dumps\(sys\.argv\[1\]\)\)\' '
        r'"\$\{CLAUDE_CODE_OAUTH_TOKENS:-\}"\)"',
        src,
    ), "CLAUDE_CODE_OAUTH_TOKENS is not JSON-encoded before EC2 heredoc substitution"
