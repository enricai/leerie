"""Tests for the settings.json-driven Bedrock SSO/profile mode
(`detect_bedrock_mode()` / `bedrock_preflight()` in `leerie`).

This mechanism shipped with zero test coverage before this module — see
docs/DESIGN.md §6 *Credential strategy* for how it relates to the newer
AWS_BEARER_TOKEN_BEDROCK path (tests/test_bedrock_bearer_token.py), which
sits directly on top of it (the bearer-token block short-circuits this one).
Closing this gap here so a future edit to the shared AUTH_MOUNTS/heredoc
region doesn't silently regress the SSO path.

Extracts detect_bedrock_mode()/bedrock_preflight() verbatim from the
launcher (same discipline as test_bedrock_bearer_token.py's
_extract_bedrock_functions) so the tests exercise real code, not a copy.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"

# See test_bedrock_bearer_token.py's _BASH comment: macOS's /usr/bin/bash
# (3.2) throws "unbound variable" on an empty array under `set -u` in
# bedrock_preflight()'s `aws_args=()` — a real gap in that pre-existing,
# previously-untested function, distinct from what this module is testing.
# Resolve a modern bash so these tests exercise the same logic the launcher
# itself runs under in practice (its own shebang is `#!/usr/bin/env bash`).
_BASH = shutil.which("bash") or "/bin/bash"


def _extract_bedrock_functions() -> str:
    src = LAUNCHER.read_text()
    start = src.index("detect_bedrock_mode() {")
    end = src.index("\n}\n", src.index("bedrock_preflight() {")) + len("\n}\n")
    block = src[start:end]
    assert "detect_bedrock_mode() {" in block
    assert "bedrock_preflight() {" in block
    return block


_DETECT_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
. "__LOG_SH__"
__BEDROCK_FUNCTIONS__
HOME="__HOME__"
USER_REPO="__USER_REPO__"
if detect_bedrock_mode; then
  echo DETECTED
else
  echo NOT_DETECTED
fi
"""

# bedrock_preflight() calls `exit 1` on failure (not `return 1` — it's
# meant to abort the whole launcher), so a plain `if bedrock_preflight;
# then ... else ...` never reaches the else arm: exit terminates this
# entire subshell. Run it in a subshell whose own exit is captured instead.
_PREFLIGHT_HARNESS = r"""
#!/usr/bin/env bash
set -uo pipefail
. "__LOG_SH__"
__BEDROCK_FUNCTIONS__
HOME="__HOME__"
USER_REPO="__USER_REPO__"
if (set -e; bedrock_preflight); then
  echo PREFLIGHT_OK
else
  echo PREFLIGHT_FAILED
fi
"""


def _write_settings(base: Path, rel: str, content: dict) -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))


def _run_detect(tmp_path: Path, *, user_settings: dict | None = None,
                 project_settings: dict | None = None,
                 local_settings: dict | None = None) -> str:
    fake_home = tmp_path / "home"
    fake_home.mkdir(parents=True)
    user_repo = tmp_path / "repo"
    user_repo.mkdir(parents=True)
    if user_settings is not None:
        _write_settings(fake_home, ".claude/settings.json", user_settings)
    if project_settings is not None:
        _write_settings(user_repo, ".claude/settings.json", project_settings)
    if local_settings is not None:
        _write_settings(user_repo, ".claude/settings.local.json", local_settings)

    harness = (
        _DETECT_HARNESS
        .replace("__LOG_SH__", str(LOG_SH))
        .replace("__BEDROCK_FUNCTIONS__", _extract_bedrock_functions())
        .replace("__HOME__", str(fake_home))
        .replace("__USER_REPO__", str(user_repo))
    )
    result = subprocess.run(
        [_BASH, "-c", harness],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _run_preflight(tmp_path: Path, *, aws_on_path: bool, aws_succeeds: bool = True,
                    profile: str | None = None) -> tuple[str, str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if profile is not None:
        _write_settings(fake_home, ".claude/settings.json", {"env": {"AWS_PROFILE": profile}})

    if aws_on_path:
        aws_stub = bin_dir / "aws"
        aws_stub.write_text(
            "#!/usr/bin/env bash\n"
            f"exit {0 if aws_succeeds else 1}\n"
        )
        aws_stub.chmod(0o755)

    harness = (
        _PREFLIGHT_HARNESS
        .replace("__LOG_SH__", str(LOG_SH))
        .replace("__BEDROCK_FUNCTIONS__", _extract_bedrock_functions())
        .replace("__HOME__", str(fake_home))
        .replace("__USER_REPO__", str(user_repo))
    )
    result = subprocess.run(
        [_BASH, "-c", harness],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.stderr


# ---------------------------------------------------------------------------
# detect_bedrock_mode(): 3-file merge / truthy matching
# ---------------------------------------------------------------------------

def test_detects_from_user_settings(tmp_path: Path) -> None:
    out = _run_detect(tmp_path, user_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}})
    assert out == "DETECTED"


def test_detects_from_project_settings(tmp_path: Path) -> None:
    out = _run_detect(tmp_path, project_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": "true"}})
    assert out == "DETECTED"


def test_detects_from_local_settings(tmp_path: Path) -> None:
    out = _run_detect(tmp_path, local_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": "yes"}})
    assert out == "DETECTED"


def test_truthy_values_case_insensitive(tmp_path: Path) -> None:
    for i, val in enumerate(("1", "TRUE", "Yes", "ON", "on")):
        out = _run_detect(tmp_path / str(i), user_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": val}})
        assert out == "DETECTED", f"expected {val!r} to be truthy"


def test_falsy_values_not_detected(tmp_path: Path) -> None:
    for i, val in enumerate(("0", "false", "", "no", "off")):
        out = _run_detect(tmp_path / str(i), user_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": val}})
        assert out == "NOT_DETECTED", f"expected {val!r} to be falsy"


def test_no_settings_files_not_detected(tmp_path: Path) -> None:
    out = _run_detect(tmp_path)
    assert out == "NOT_DETECTED"


def test_malformed_settings_json_tolerated(tmp_path: Path) -> None:
    """A malformed settings file must not crash detection — the function
    swallows JSON errors per-file and continues checking the others."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{not valid json")
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _write_settings(user_repo, ".claude/settings.json", {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}})

    harness = (
        _DETECT_HARNESS
        .replace("__LOG_SH__", str(LOG_SH))
        .replace("__BEDROCK_FUNCTIONS__", _extract_bedrock_functions())
        .replace("__HOME__", str(fake_home))
        .replace("__USER_REPO__", str(user_repo))
    )
    result = subprocess.run(
        [_BASH, "-c", harness],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "DETECTED"


def test_last_match_wins_precedence_is_or_semantics(tmp_path: Path) -> None:
    """CLAUDE_CODE_USE_BEDROCK has no 'disable' value (isEnvTruthy only
    matches truthy strings), so ANY of the three files being truthy is
    sufficient — a falsy localSettings does not override a truthy
    userSettings, unlike AWS_PROFILE/AWS_REGION's last-match-wins."""
    out = _run_detect(
        tmp_path,
        user_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        local_settings={"env": {"CLAUDE_CODE_USE_BEDROCK": "0"}},
    )
    assert out == "DETECTED"


# ---------------------------------------------------------------------------
# bedrock_preflight(): missing aws binary, expired/valid SSO
# ---------------------------------------------------------------------------

def test_preflight_fails_when_aws_not_on_path(tmp_path: Path) -> None:
    out, err = _run_preflight(tmp_path, aws_on_path=False)
    assert out == "PREFLIGHT_FAILED"
    assert "not found on PATH" in err


def test_preflight_fails_when_sts_call_fails(tmp_path: Path) -> None:
    """Simulates an expired/missing SSO token: `aws sts get-caller-identity`
    exits non-zero."""
    out, err = _run_preflight(tmp_path, aws_on_path=True, aws_succeeds=False)
    assert out == "PREFLIGHT_FAILED"
    assert "expired or missing" in err
    assert "aws sso login" in err


def test_preflight_succeeds_with_valid_sso(tmp_path: Path) -> None:
    out, err = _run_preflight(tmp_path, aws_on_path=True, aws_succeeds=True)
    assert out == "PREFLIGHT_OK"


def test_preflight_hint_names_profile_when_set(tmp_path: Path) -> None:
    """When AWS_PROFILE is set in settings.json, a failed preflight names
    that profile in its `aws sso login --profile <p>` recovery hint."""
    out, err = _run_preflight(
        tmp_path, aws_on_path=True, aws_succeeds=False, profile="my-profile",
    )
    assert out == "PREFLIGHT_FAILED"
    assert "aws sso login --profile my-profile" in err


def test_preflight_hint_omits_profile_when_unset(tmp_path: Path) -> None:
    out, err = _run_preflight(tmp_path, aws_on_path=True, aws_succeeds=False)
    assert out == "PREFLIGHT_FAILED"
    assert "--profile" not in err
    assert "aws sso login " in err or "aws sso login\n" in err or "aws sso login  " in err
