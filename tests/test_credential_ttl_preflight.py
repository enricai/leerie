"""Tests for the _check_claude_credential_ttl preflight in leerie.

D0b: once D0a prefers the long-lived CLAUDE_CODE_OAUTH_TOKEN (which
carries no expiresAt), the subscription-token path (Keychain / on-disk
file) is still reachable whenever the caller hasn't set the env var —
and that path's token cannot be refreshed inside a container. This
preflight parses claudeAiOauth.expiresAt (ms-epoch) from whichever
credential _extract_claude_credentials_json resolved and:
  - refuses when it's already expired, naming `claude /login`.
  - warns when it's within the threshold, naming the exact expiry and
    `claude setup-token`.
  - stays silent when healthy, or when expiresAt is absent/malformed
    (never harden a best-effort path into a hard gate on missing data).

We extract the function (plus its threshold constant) via awk and
source it in a sub-bash, mirroring
tests/test_credential_precedence.py's harness for
_extract_claude_credentials_json.
"""
from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_ttl_helper(tmp_path: Path) -> Path:
    """Extract _check_claude_credential_ttl (and its threshold constant)
    from the launcher into a standalone sourceable file."""
    extract = tmp_path / "extract-helper.sh"
    extract.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        awk '
          /^_CLAUDE_TTL_WARN_THRESHOLD_SEC=/ {{ print; next }}
          /^_check_claude_credential_ttl\\(\\)/ {{ p=1 }}
          p {{ print }}
          p && /^}}$/ {{ p=0 }}
        ' "{LAUNCHER}"
        """))
    extract.chmod(0o755)
    helper_src = subprocess.run(
        ["bash", str(extract)], capture_output=True, text=True, check=True,
    ).stdout
    assert "_check_claude_credential_ttl" in helper_src
    assert "_CLAUDE_TTL_WARN_THRESHOLD_SEC=" in helper_src
    helper_file = tmp_path / "ttl-helper.sh"
    helper_file.write_text(helper_src)
    return helper_file


def _run_ttl_check(tmp_path: Path, blob: str) -> tuple[int, str, str]:
    """Run _check_claude_credential_ttl against `blob`, return
    (rc, stdout, stderr)."""
    helper_file = _extract_ttl_helper(tmp_path)
    result = subprocess.run(
        ["bash", "-c", f'. {helper_file} && _check_claude_credential_ttl "$1"',
         "--", blob],
        capture_output=True, text=True, timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


def _epoch_ms(seconds_from_now: float) -> int:
    return int((time.time() + seconds_from_now) * 1000)


def _blob(expires_at_ms: int | None) -> str:
    if expires_at_ms is None:
        return '{"claudeAiOauth":{"accessToken":"tok"}}'
    return f'{{"claudeAiOauth":{{"accessToken":"tok","expiresAt":{expires_at_ms}}}}}'


# ---------------------------------------------------------------------------
# Expired: refuse
# ---------------------------------------------------------------------------

def test_expired_refuses_and_names_login(tmp_path: Path) -> None:
    rc, _out, err = _run_ttl_check(tmp_path, _blob(_epoch_ms(-3600)))
    assert rc != 0
    assert "claude /login" in err
    assert "expired" in err.lower()


# ---------------------------------------------------------------------------
# Within threshold: warn
# ---------------------------------------------------------------------------

def test_within_threshold_warns_with_expiry_and_setup_token(tmp_path: Path) -> None:
    # 60 minutes remaining, below the 90-minute warn threshold.
    exp_ms = _epoch_ms(60 * 60)
    rc, _out, err = _run_ttl_check(tmp_path, _blob(exp_ms))
    assert rc == 0
    assert "claude setup-token" in err
    # The exact expiry must be named — assert the ISO-8601 rendering of
    # the same ms-epoch value appears in the warning.
    import datetime
    expected_iso = datetime.datetime.fromtimestamp(
        exp_ms / 1000.0, datetime.timezone.utc
    ).isoformat()
    assert expected_iso in err


def test_regression_b57027d3_shape_warns(tmp_path: Path) -> None:
    """Issued ~7h before launch, 8h TTL -> 1h remaining -> warns.

    This is the exact shape of run b57027d3's dead token: access token
    issued ~07:14 UTC, run launched 14:15:58, token TTL ~8h -> died
    ~15:14:35, well inside the 90-minute warn threshold at launch time.
    """
    issued_ms = _epoch_ms(-7 * 3600)
    ttl_ms = 8 * 3600 * 1000
    exp_ms = issued_ms + ttl_ms
    rc, _out, err = _run_ttl_check(tmp_path, _blob(exp_ms))
    assert rc == 0
    assert "claude setup-token" in err


# ---------------------------------------------------------------------------
# Healthy: silent
# ---------------------------------------------------------------------------

def test_healthy_ttl_is_silent(tmp_path: Path) -> None:
    # 10 hours remaining, well outside the 90-minute threshold.
    rc, out, err = _run_ttl_check(tmp_path, _blob(_epoch_ms(10 * 3600)))
    assert rc == 0
    assert out == ""
    assert err == ""


# ---------------------------------------------------------------------------
# Absent / malformed expiresAt: proceed silently (best-effort, never a
# hard gate on missing data)
# ---------------------------------------------------------------------------

def test_absent_expires_at_proceeds_silently(tmp_path: Path) -> None:
    """The long-lived CLAUDE_CODE_OAUTH_TOKEN synthesizes no expiresAt
    field at all — this must never block that path."""
    rc, out, err = _run_ttl_check(tmp_path, _blob(None))
    assert rc == 0
    assert out == ""
    assert err == ""


def test_malformed_json_proceeds_silently(tmp_path: Path) -> None:
    rc, out, err = _run_ttl_check(tmp_path, "not json at all")
    assert rc == 0
    assert out == ""
    assert err == ""


def test_non_numeric_expires_at_proceeds_silently(tmp_path: Path) -> None:
    rc, out, err = _run_ttl_check(
        tmp_path, '{"claudeAiOauth":{"accessToken":"tok","expiresAt":"not-a-number"}}'
    )
    assert rc == 0
    assert out == ""
    assert err == ""


def test_missing_claude_ai_oauth_key_proceeds_silently(tmp_path: Path) -> None:
    rc, out, err = _run_ttl_check(tmp_path, '{"unrelated":true}')
    assert rc == 0
    assert out == ""
    assert err == ""


def test_negative_expires_at_proceeds_silently(tmp_path: Path) -> None:
    """A negative epoch-ms predates 1970 -- no real OAuth token has ever
    carried one, so it's garbage data, not a genuinely-expired
    credential. Must not be treated as "expired" (which would refuse
    to launch on bogus input)."""
    rc, out, err = _run_ttl_check(tmp_path, _blob(-1000))
    assert rc == 0
    assert out == ""
    assert err == ""


# ---------------------------------------------------------------------------
# The threshold is a local, named constant (never a hard-coded 8h)
# ---------------------------------------------------------------------------

def test_threshold_constant_is_ninety_minutes() -> None:
    src = LAUNCHER.read_text()
    assert "_CLAUDE_TTL_WARN_THRESHOLD_SEC=$((90 * 60))" in src


def test_launcher_never_hardcodes_eight_hour_ttl() -> None:
    src = LAUNCHER.read_text()
    # Regression guard against reintroducing a hard-coded 8h assumption
    # in the TTL preflight (community-reported TTLs range 2-15h;
    # expiresAt is the only authoritative value).
    assert "8 * 3600" not in src
    assert "28800" not in src
