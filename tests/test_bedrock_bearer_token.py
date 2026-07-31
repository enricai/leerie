"""Tests for the Bedrock bearer-token auth path (AWS_BEARER_TOKEN_BEDROCK).

The static-bearer-token analogue of CLAUDE_CODE_OAUTH_TOKEN: a plain,
non-expiring-by-refresh credential the Claude CLI sends as
`Authorization: Bearer <token>` directly to the Bedrock runtime endpoint.
Unlike the existing settings.json-driven SSO/profile Bedrock path
(`detect_bedrock_mode()` / `bedrock_preflight()`), it needs no `aws` CLI, no
SSO session, and no ~/.aws/ staging — verified live against the real Claude
CLI (v2.1.220, see docs/DESIGN.md §6 *Credential strategy*): the bearer
token alone is a no-op (the CLI falls through to firstParty/OAuth dispatch)
unless CLAUDE_CODE_USE_BEDROCK is also set.

The harness extracts the real launcher block verbatim (from the
`_BEDROCK_BEARER_ACTIVE` assignment through the SSO/profile block's closing
`fi`) so the tests exercise real code, not a copy — same discipline as
tests/test_launcher_env_forwarding.py's `_extract_forwarding_loop`. Also
mirrors tests/test_credential_precedence.py's forwarding-block extraction
approach for the `-e` injection surface.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"

# The launcher's own shebang is `#!/usr/bin/env bash` — it runs under
# whichever bash the invoking PATH resolves first, not necessarily macOS's
# ancient /usr/bin/bash 3.2 (which has real gaps here: an empty array under
# `set -u` throws "unbound variable" on bash 3.2 but not modern bash — see
# CLAUDE.md's bash-3.2 portability discipline for the EC2 scripts). Resolve
# a real bash from the *test-runner's* PATH once, up front, so the harness
# subprocess (deliberately given a minimal PATH to isolate the `aws` stub)
# still runs under the same bash the launcher itself expects.
_BASH = shutil.which("bash") or "/bin/bash"


def _extract_bedrock_block() -> str:
    """Pull the full Bedrock activation region (bearer-token block +
    SSO/profile block) verbatim from the launcher, from the
    `_BEDROCK_BEARER_ACTIVE` assignment through the SSO block's closing
    `fi`, just before the "Mount each staged file/dir" comment."""
    src = LAUNCHER.read_text()
    start = src.index("_BEDROCK_BEARER_ACTIVE=false")
    end = src.index("# -- Mount each staged file/dir at its default in-container path. --")
    block = src[start:end]
    assert "AWS_BEARER_TOKEN_BEDROCK=$AWS_BEARER_TOKEN_BEDROCK" in block
    assert "detect_bedrock_mode" in block
    return block


def _extract_bedrock_functions() -> str:
    """Pull detect_bedrock_mode() and bedrock_preflight() verbatim from the
    launcher so the harness has real implementations, not stubs, for the
    SSO/profile control-flow paths this module also covers."""
    src = LAUNCHER.read_text()
    start = src.index("detect_bedrock_mode() {")
    end = src.index("\n}\n", src.index("bedrock_preflight() {")) + len("\n}\n")
    block = src[start:end]
    assert "detect_bedrock_mode() {" in block
    assert "bedrock_preflight() {" in block
    return block


_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

# ---- shared host-side logging helper (real file, sourced) ---------------
. "__LOG_SH__"

# ---- detect_bedrock_mode / bedrock_preflight, extracted verbatim --------
__BEDROCK_FUNCTIONS__

# ---- stub AUTH_MOUNTS-consuming state the block reads/writes ------------
AUTH_MOUNTS=()
HOME="__HOME__"
USER_REPO="__USER_REPO__"
STAGE="__STAGE__"
mkdir -p "$STAGE"

# ---- Bedrock activation block, extracted verbatim from the launcher -----
__BEDROCK_BLOCK__

# ---- emit AUTH_MOUNTS so the test can inspect it -------------------------
for a in "${AUTH_MOUNTS[@]+"${AUTH_MOUNTS[@]}"}"; do printf '%s\n' "$a"; done
echo "---"
echo "BEARER_ACTIVE=$_BEDROCK_BEARER_ACTIVE"
echo "SSO_ACTIVE=$_BEDROCK_ACTIVE"
"""


def _run(env: dict, *, aws_on_path: bool = False, aws_succeeds: bool = True,
          settings_json: dict | None = None, home_has_aws: bool = True,
          tmp_path: Path) -> tuple[int, list[str], bool, bool]:
    """Run the harness; return (rc, auth_mounts_tokens, bearer_active, sso_active).

    settings_json seeds ~/.claude/settings.json (the SSO path's detection
    source). home_has_aws controls whether $HOME/.aws exists (the SSO
    path's precondition check).
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    if settings_json is not None:
        (fake_home / ".claude" / "settings.json").write_text(json.dumps(settings_json))
    if home_has_aws:
        (fake_home / ".aws").mkdir()

    user_repo = tmp_path / "repo"
    user_repo.mkdir()

    stage = tmp_path / "stage"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if aws_on_path:
        aws_stub = bin_dir / "aws"
        aws_stub.write_text(
            "#!/usr/bin/env bash\n"
            f"exit {0 if aws_succeeds else 1}\n"
        )
        aws_stub.chmod(0o755)

    harness = (
        _HARNESS
        .replace("__LOG_SH__", str(LOG_SH))
        .replace("__BEDROCK_FUNCTIONS__", _extract_bedrock_functions())
        .replace("__BEDROCK_BLOCK__", _extract_bedrock_block())
        .replace("__HOME__", str(fake_home))
        .replace("__USER_REPO__", str(user_repo))
        .replace("__STAGE__", str(stage))
    )
    result = subprocess.run(
        [_BASH, "-c", harness],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    bearer_active = False
    sso_active = False
    tokens = []
    for line in lines:
        if line == "---":
            continue
        if line.startswith("BEARER_ACTIVE="):
            bearer_active = line.split("=", 1)[1] == "true"
        elif line.startswith("SSO_ACTIVE="):
            sso_active = line.split("=", 1)[1] == "true"
        else:
            tokens.append(line)
    return result.returncode, tokens, bearer_active, sso_active


def _env_pairs(tokens: list[str]) -> dict[str, str]:
    pairs = {}
    for i, tok in enumerate(tokens):
        if tok == "-e" and i + 1 < len(tokens) and "=" in tokens[i + 1]:
            name, _, value = tokens[i + 1].partition("=")
            pairs[name] = value
    return pairs


def _has_aws_mount(tokens: list[str]) -> bool:
    return any(t.endswith(":/home/leerie/.aws:ro") for t in tokens)


# ---------------------------------------------------------------------------
# Bearer token set -> forwards AWS_BEARER_TOKEN_BEDROCK, defaults
# CLAUDE_CODE_USE_BEDROCK=1, forwards AWS_REGION when set.
# ---------------------------------------------------------------------------

def test_bearer_token_forwarded(tmp_path: Path) -> None:
    rc, tokens, bearer_active, sso_active = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"}, tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["AWS_BEARER_TOKEN_BEDROCK"] == "bedrock-tok-123"
    assert bearer_active is True
    assert sso_active is False


def test_claude_code_use_bedrock_defaults_to_one(tmp_path: Path) -> None:
    """CLAUDE_CODE_USE_BEDROCK is required alongside the bearer token —
    verified live against the real CLI that the token alone is a no-op
    (falls through to firstParty/OAuth). The launcher must default it."""
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"}, tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["CLAUDE_CODE_USE_BEDROCK"] == "1"


def test_claude_code_use_bedrock_explicit_override_wins(tmp_path: Path) -> None:
    """An explicit CLAUDE_CODE_USE_BEDROCK=0 alongside the bearer token is
    forwarded verbatim — a user override beats the launcher's default."""
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123", "CLAUDE_CODE_USE_BEDROCK": "0"},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["CLAUDE_CODE_USE_BEDROCK"] == "0"


def test_aws_region_forwarded_when_set(tmp_path: Path) -> None:
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123", "AWS_REGION": "us-west-2"},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["AWS_REGION"] == "us-west-2"


def test_aws_region_not_forwarded_when_unset(tmp_path: Path) -> None:
    """AWS_REGION is optional — the CLI defaults to us-east-1 internally
    (verified live), so no -e flag should be emitted when unset."""
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"}, tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert "AWS_REGION" not in pairs


# ---------------------------------------------------------------------------
# ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL forwarded when set, on both
# Bedrock auth modes (bearer-token and SSO/profile) — the CLI's Bedrock
# alias table lags the Anthropic-API one, and these vars are the CLI's
# documented mechanism for repointing what --model <tier> resolves to.
# Verified live against the real CLI (v2.1.220) that a container -e var is
# sufficient (plain process env, not settings.json-gated).
# ---------------------------------------------------------------------------

def test_anthropic_default_sonnet_model_forwarded_bearer_mode(tmp_path: Path) -> None:
    rc, tokens, _, _ = _run(
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-5",
        },
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "us.anthropic.claude-sonnet-5"


def test_anthropic_default_opus_and_haiku_model_forwarded_bearer_mode(tmp_path: Path) -> None:
    rc, tokens, _, _ = _run(
        {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "us.anthropic.claude-opus-5",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-haiku-4-5",
        },
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert pairs["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "us.anthropic.claude-opus-5"
    assert pairs["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "us.anthropic.claude-haiku-4-5"


def test_anthropic_default_model_vars_not_forwarded_when_unset_bearer_mode(tmp_path: Path) -> None:
    """No spurious -e ANTHROPIC_DEFAULT_*_MODEL= entries when the user
    hasn't set any of the three — the CLI's own Bedrock default must be
    left untouched."""
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"}, tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    pairs = _env_pairs(tokens)
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in pairs
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" not in pairs
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in pairs


def test_anthropic_default_sonnet_model_forwarded_sso_mode(tmp_path: Path) -> None:
    """Parity check: the SSO/profile Bedrock path forwards the same var."""
    rc, tokens, bearer_active, sso_active = _run(
        {"ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-5"},
        aws_on_path=True,
        aws_succeeds=True,
        settings_json={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert bearer_active is False
    assert sso_active is True
    pairs = _env_pairs(tokens)
    assert pairs["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "us.anthropic.claude-sonnet-5"


def test_anthropic_default_model_vars_not_forwarded_when_unset_sso_mode(tmp_path: Path) -> None:
    rc, tokens, _, sso_active = _run(
        {},
        aws_on_path=True,
        aws_succeeds=True,
        settings_json={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert sso_active is True
    pairs = _env_pairs(tokens)
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in pairs
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" not in pairs
    assert "ANTHROPIC_DEFAULT_HAIKU_MODEL" not in pairs


# ---------------------------------------------------------------------------
# Bearer token set -> no aws CLI / SSO preflight, no ~/.aws mount.
# ---------------------------------------------------------------------------

def test_bearer_token_skips_aws_preflight_and_mount(tmp_path: Path) -> None:
    """No `aws` binary on PATH at all — the bearer-token path must not
    invoke bedrock_preflight() (which would exit 1 without `aws`)."""
    rc, tokens, bearer_active, sso_active = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"},
        aws_on_path=False,
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert not _has_aws_mount(tokens)
    assert bearer_active is True
    assert sso_active is False


def test_bearer_token_skips_preflight_even_with_aws_present_but_failing(tmp_path: Path) -> None:
    """Even when `aws` IS on PATH and would fail sts get-caller-identity,
    the bearer-token path must not call it — it has no SSO dependency."""
    rc, tokens, _, _ = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"},
        aws_on_path=True,
        aws_succeeds=False,
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert not _has_aws_mount(tokens)


# ---------------------------------------------------------------------------
# Both bearer token AND settings.json CLAUDE_CODE_USE_BEDROCK present ->
# bearer-token path wins (no ~/.aws mount, no preflight call).
# ---------------------------------------------------------------------------

def test_bearer_token_wins_over_settings_json_sso(tmp_path: Path) -> None:
    rc, tokens, bearer_active, sso_active = _run(
        {"AWS_BEARER_TOKEN_BEDROCK": "bedrock-tok-123"},
        aws_on_path=True,
        aws_succeeds=True,
        settings_json={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert bearer_active is True
    assert sso_active is False
    assert not _has_aws_mount(tokens)


# ---------------------------------------------------------------------------
# Neither set -> unchanged existing behavior (regression control).
# ---------------------------------------------------------------------------

def test_neither_set_is_a_no_op(tmp_path: Path) -> None:
    rc, tokens, bearer_active, sso_active = _run({}, tmp_path=tmp_path)
    assert rc == 0, tokens
    assert bearer_active is False
    assert sso_active is False
    assert tokens == []


def test_settings_json_sso_still_works_without_bearer_token(tmp_path: Path) -> None:
    """Regression control: the pre-existing SSO/profile path is unaffected
    when the bearer token is absent."""
    rc, tokens, bearer_active, sso_active = _run(
        {},
        aws_on_path=True,
        aws_succeeds=True,
        settings_json={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        tmp_path=tmp_path,
    )
    assert rc == 0, tokens
    assert bearer_active is False
    assert sso_active is True
    assert _has_aws_mount(tokens)
    pairs = _env_pairs(tokens)
    assert pairs["CLAUDE_CODE_USE_BEDROCK"] == "1"


# ---------------------------------------------------------------------------
# Fly detached-launch heredoc mirrors the same precedence/defaults.
# ---------------------------------------------------------------------------

def test_fly_heredoc_forwards_bearer_token_vars() -> None:
    """Source-coupling: the Fly detached-launch child_env block must carry
    the same AWS_BEARER_TOKEN_BEDROCK / CLAUDE_CODE_USE_BEDROCK / AWS_REGION
    injection, gated on _BEDROCK_BEARER_ACTIVE, mirroring the nerdctl path's
    AUTH_MOUNTS block (test_launcher_env_forwarding.py's
    test_fly_path_also_delivers_user_repo pins the same "both paths must
    carry the same injection" discipline for USER_REPO). Values are
    JSON-encoded host-side (see test_fly_heredoc_values_are_json_encoded_not_raw
    below) rather than substituted as raw "${VAR}" strings."""
    src = LAUNCHER.read_text()
    assert 'if "${_BEDROCK_BEARER_ACTIVE}" == "true":' in src
    assert 'child_env["AWS_BEARER_TOKEN_BEDROCK"] = ${_bedrock_bearer_token_json}' in src
    assert 'child_env["CLAUDE_CODE_USE_BEDROCK"] = ${_bedrock_use_bedrock_json}' in src


def test_fly_heredoc_values_are_json_encoded_not_raw() -> None:
    """Regression guard for the injection defect found in post-implementation
    audit: a raw `"${AWS_BEARER_TOKEN_BEDROCK}"` substitution inside the
    unquoted heredoc lets a token containing `"` or `\\` break out of the
    Python string literal and run as arbitrary code on the remote Fly
    machine. Every value substituted into the heredoc must instead be
    pre-encoded via `python3 -c 'import json,sys; print(json.dumps(...))'`
    (the same technique `_launch_argv_json` already uses for argv) and
    referenced unquoted (already a valid Python literal)."""
    src = LAUNCHER.read_text()
    assert 'child_env["AWS_BEARER_TOKEN_BEDROCK"] = "${AWS_BEARER_TOKEN_BEDROCK}"' not in src, (
        "raw string substitution of a secret token into the Fly heredoc "
        "reintroduces the Python-injection defect — JSON-encode it host-side "
        "first (see _bedrock_bearer_token_json)"
    )
    assert "_bedrock_bearer_token_json=" in src
    assert "json.dumps(sys.argv[1])" in src
    assert "child_env[\"AWS_BEARER_TOKEN_BEDROCK\"] = ${_bedrock_bearer_token_json}" in src


def test_child_env_heredoc_body_has_no_stray_unbound_var_substitution() -> None:
    """Regression guard for a second, more severe defect found alongside the
    injection one: the heredoc is UNQUOTED (`<<PY`, not `<<'PY'`), so bash
    substitutes every `${...}`-shaped token in its body — including inside
    what looks like a Python comment. A draft of the fix above's own
    explanatory comment read `...a raw "${VAR}" string...`; under the
    launcher's `set -euo pipefail`, `$VAR` being unset made `${VAR}` an
    unbound-variable reference that crashed the ENTIRE launcher (not just
    a broken script) on every Fly run with Bedrock bearer-token mode active
    — worse than the injection defect, since it fired on every run rather
    than only a maliciously-crafted token. Verified live: reintroducing
    that exact comment text into the heredoc body reproduces
    `bash: line N: VAR: unbound variable` when the surrounding script is
    executed. Only the KNOWN, intentional substitution names may appear in
    `${...}` form inside this region."""
    block = _extract_child_env_bedrock_block()
    known = {
        "_host_tz_json", "_bedrock_bearer_token_json", "_bedrock_use_bedrock_json",
        "_bedrock_bearer_region_json", "_BEDROCK_BEARER_ACTIVE", "_bedrock_profile_json",
        "_BEDROCK_ACTIVE", "_bedrock_region_json",
    }
    found = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", block))
    unexpected = found - known
    assert not unexpected, (
        f"unexpected ${{...}}-shaped substitution(s) in the unquoted Fly "
        f"heredoc body: {sorted(unexpected)} — if these are meant to be "
        f"literal text (e.g. inside a comment), the heredoc's unquoted "
        f"nature means bash will still try to substitute them, crashing "
        f"under `set -u` if unset. Rephrase the comment to avoid the "
        f"${{...}} shape entirely."
    )


def test_child_env_heredoc_body_has_no_backtick_characters() -> None:
    """Regression guard for a third defect found on re-audit, distinct from
    the `${...}`-unbound-variable one above: this heredoc is unquoted, so a
    BALANCED backtick pair in a comment (e.g. `` `if <json>:` ``) is parsed
    by bash as a command-substitution delimiter — command substitution is a
    different expansion mechanism than `${...}` parameter expansion, so the
    `${...}`-allowlist scan above does not catch it. Verified live: bash
    tries to execute the literal text between the backticks as a shell
    command, fails with `syntax error: unexpected end of file` (printed to
    the user's terminal on every Fly + Bedrock-bearer-token launch), and
    silently drops that comment's text from the script actually sent to the
    remote machine. `shellcheck -x leerie` also flags this
    (SC1009/SC1073/SC1050/SC1072) — diffing shellcheck output against
    `git stash` is how this was originally caught; `bash -n` alone does not
    catch it. This heredoc's content is pure Python, which never needs a
    literal backtick, so the invariant is simply: none allowed."""
    block = _extract_child_env_bedrock_block()
    assert "`" not in block, (
        "a backtick character appeared in the unquoted Fly heredoc body — "
        "bash will treat a balanced pair as command substitution even "
        "inside a comment, corrupting the script and printing a spurious "
        "syntax error to the user on every launch. Rephrase to avoid "
        "backticks entirely (e.g. drop inline code-formatting in prose)."
    )


def test_fly_heredoc_bearer_precedes_sso_elif() -> None:
    """The Fly heredoc's bearer-token `if` must be an `elif`-paired
    predecessor of the SSO `_BEDROCK_ACTIVE` check, mirroring the nerdctl
    path's `_BEDROCK_BEARER_ACTIVE=false && detect_bedrock_mode` guard —
    both paths must apply the same precedence."""
    src = LAUNCHER.read_text()
    bearer_idx = src.index('if "${_BEDROCK_BEARER_ACTIVE}" == "true":')
    sso_idx = src.index('elif "${_BEDROCK_ACTIVE}" == "true":')
    assert bearer_idx < sso_idx, (
        "the Fly heredoc's bearer-token check must precede the SSO elif — "
        "otherwise the two could both fire or fire in the wrong order"
    )


# ---------------------------------------------------------------------------
# Coupling guard: nerdctl block's precedence variable name matches the
# Fly heredoc's substitution reference, so a rename on one side cannot
# silently diverge from the other.
# ---------------------------------------------------------------------------

def test_precedence_variable_name_shared_across_both_runtimes() -> None:
    src = LAUNCHER.read_text()
    assert src.count("_BEDROCK_BEARER_ACTIVE") >= 3, (
        "expected _BEDROCK_BEARER_ACTIVE to appear in both the nerdctl "
        "AUTH_MOUNTS block and the Fly heredoc substitution — a rename on "
        "one side without the other would silently break precedence"
    )


# ---------------------------------------------------------------------------
# Live end-to-end regression: a malicious bearer token must not break out of
# the Fly heredoc's Python string literal. Extracts the real JSON-encoding
# lines + a minimal heredoc body verbatim from the launcher, actually runs
# the resulting script through `python3 -` (mirroring how flyctl ssh console
# pipes it to the remote machine), and asserts no injected code executed.
# ---------------------------------------------------------------------------

def _extract_json_encoding_lines() -> str:
    """Pull the `_host_tz_json=...` through `_bedrock_region_json=...`
    JSON-encoding assignments verbatim from the launcher."""
    src = LAUNCHER.read_text()
    start = src.index('_host_tz_json="$(python3')
    end = src.index('\n', src.index('_bedrock_region_json="$(python3'))
    block = src[start:end]
    assert "_bedrock_bearer_token_json=" in block
    assert "_bedrock_region_json=" in block
    return block


def _extract_child_env_bedrock_block() -> str:
    """Pull the real `child_env["TZ"] = ...` through the SSO `elif` block's
    closing line verbatim from the launcher's Fly heredoc — the actual code
    under test, not a hand-copied reproduction (a hand-copy would not catch
    a regression reintroduced only in the real launcher; verified this
    matters live — see the git-history note on this test)."""
    src = LAUNCHER.read_text()
    start = src.index('child_env["TZ"] = ${_host_tz_json}')
    end = src.index('\n', src.index('child_env["AWS_REGION"] = ${_bedrock_region_json}'))
    block = src[start:end]
    assert 'child_env["AWS_BEARER_TOKEN_BEDROCK"] = ${_bedrock_bearer_token_json}' in block
    return block


# The token travels via the real env var (like the actual launcher receives
# it from the user's shell), NOT baked into the script text — embedding an
# arbitrary token directly into bash source would itself be a second,
# unrelated injection bug in the *test harness*, distinct from the one this
# test verifies is fixed in the launcher.
_INJECTION_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
CLAUDE_CODE_USE_BEDROCK=""
AWS_REGION=""
_BEDROCK_PROFILE=""
_BEDROCK_REGION=""
_BEDROCK_BEARER_ACTIVE=true
_BEDROCK_ACTIVE=false
_host_tz="America/Chicago"

__JSON_ENCODING_LINES__

_script="$(cat <<PY
child_env = {}
__CHILD_ENV_BLOCK__
import sys
print(child_env["AWS_BEARER_TOKEN_BEDROCK"], file=sys.stderr)
PY
)"
printf '%s' "$_script" | python3 -
"""


def _run_injection_harness(token: str, tmp_path: Path) -> tuple[int, str, str]:
    harness = (
        _INJECTION_HARNESS
        .replace("__JSON_ENCODING_LINES__", _extract_json_encoding_lines())
        .replace("__CHILD_ENV_BLOCK__", _extract_child_env_bedrock_block())
    )
    canary = tmp_path / "pwned"
    result = subprocess.run(
        [_BASH, "-c", harness],
        env={
            "PATH": "/usr/bin:/bin",
            "PWNED_CANARY": str(canary),
            "AWS_BEARER_TOKEN_BEDROCK": token,
        },
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_malicious_token_with_quote_does_not_break_out_of_python_literal(tmp_path: Path) -> None:
    """The historical defect payload: a token ending in a quote followed by
    Python code. Before the fix this executed as real Python on the target
    interpreter; after the fix it must round-trip as an inert string."""
    canary = tmp_path / "pwned"
    token = f'tok"; import os; os.system("touch {canary}"); x="'
    rc, _, stderr = _run_injection_harness(token, tmp_path)
    assert rc == 0, stderr
    assert not canary.exists(), (
        "the malicious token executed as Python — injection defect is back"
    )
    assert token in stderr, "the token must still round-trip through unchanged"


def test_malicious_token_with_backslash_does_not_break_out(tmp_path: Path) -> None:
    canary = tmp_path / "pwned2"
    token = f'tok\\"; import os; os.system("touch {canary}"); x="'
    rc, _, stderr = _run_injection_harness(token, tmp_path)
    assert rc == 0, stderr
    assert not canary.exists()
    assert token in stderr


def test_normal_token_unaffected_by_json_encoding(tmp_path: Path) -> None:
    """A realistic token (no special characters) round-trips unchanged."""
    token = "sk-bedrock-abc123XYZ"
    rc, _, stderr = _run_injection_harness(token, tmp_path)
    assert rc == 0, stderr
    assert token in stderr
