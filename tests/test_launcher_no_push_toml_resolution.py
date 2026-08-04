"""Tests for the launcher's NO_PUSH resolution from leerie.toml.

Regression pin for an asymmetry between the launcher and the
orchestrator's `resolve_no_push`:

The orchestrator reads `no_push` from CLI flag → LEERIE_NO_PUSH env →
`leerie.toml` (`orchestrator/leerie.py:2635` `resolve_no_push`). The
launcher historically read only CLI + env (`leerie:874-882`), missing
TOML. That asymmetry was harmless before the Fly auto-finalize landed:

  - On local runtime, the in-container orchestrator wrote
    `run.json.no_push=true` (from TOML), and `host_finalize`
    short-circuited.
  - On Fly, no auto-finalize existed, so the launcher's
    `host_no_push=false` only affected the manual `leerie finalize`
    recovery command.

After the Fly auto-finalize landed (decide_teardown now calls
host_finalize on the host immediately after sync), the asymmetry
became active: a TOML-only opt-out would propagate as
`--host-no-push false` into the in-Fly orchestrator, which then
writes `run.json.no_push=false` based on intent
(`push_will_happen(no_push=True, host_no_push=False) → True`), and
the host auto-pushes against user wishes.

The launcher's TOML fallback must match the orchestrator's
`_parse_bool_envtoml` (`orchestrator/leerie.py:2590`) in two ways:

  1. Truthy/falsy vocabulary is case-insensitive (1|true|yes|on /
     0|false|no|off). Mixed-case spellings (`Yes`, `On`, `TruE`) must
     resolve correctly on both layers.
  2. Garbage values (`no_push = sometimes`) must die loudly on both
     layers — silent fallthrough to "push" surprises a user who
     clearly tried to opt out.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


# The launcher's NO_PUSH TOML fallback block, reproduced byte-for-byte
# so the tests exercise the launcher's logic and a refactor that
# changes parsing makes the coupling test fail (see
# test_launcher_block_present_in_launcher_file).
_LAUNCHER_NO_PUSH_BLOCK = r"""
case "${LEERIE_NO_PUSH:-}" in
  1|true|TRUE|yes|YES) NO_PUSH=true ;;
esac
if [ "$NO_PUSH" = "false" ] && [ -f "$USER_REPO/leerie.toml" ]; then
  _toml_no_push="$( { grep -E '^[[:space:]]*no_push[[:space:]]*=' \
                          "$USER_REPO/leerie.toml" 2>/dev/null \
                      || true; } \
                    | head -1 \
                    | sed -E 's/^[[:space:]]*no_push[[:space:]]*=[[:space:]]*//;
                              s/[[:space:]]*$//;
                              s/^"(.*)"$/\1/;
                              s/^'"'"'(.*)'"'"'$/\1/')"
  _toml_no_push_lc="$(printf '%s' "$_toml_no_push" \
                      | tr '[:upper:]' '[:lower:]')"
  case "$_toml_no_push_lc" in
    1|true|yes|on)
      NO_PUSH=true ;;
    0|false|no|off|"")
      : ;;
    *)
      echo "leerie: error: $USER_REPO/leerie.toml: no_push='$_toml_no_push' is not a boolean" >&2
      echo "  (expected one of: 1, 0, true, false, yes, no, on, off — case-insensitive)" >&2
      exit 1 ;;
  esac
fi
"""


def _run_launcher_block(user_repo: Path, *, env_extra: dict | None = None,
                        ) -> subprocess.CompletedProcess:
    """Source the launcher's NO_PUSH resolution block in a subshell
    with USER_REPO pointed at the test directory. Returns the full
    CompletedProcess so callers can assert on stdout, stderr, and rc.

    Caller invariants:
      - On success the printed stdout is `true` or `false`.
      - On failure rc != 0 and stderr explains the rejection.
    """
    script = (
        "set -euo pipefail\n"
        f"USER_REPO={user_repo!s}\n"
        "NO_PUSH=false\n"
        f"{_LAUNCHER_NO_PUSH_BLOCK}\n"
        'echo "$NO_PUSH"\n'
    )
    env = {**os.environ}
    env.pop("LEERIE_NO_PUSH", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=env,
    )


def _eval(user_repo: Path) -> str:
    """Convenience wrapper for the common case: launcher must succeed,
    return the resolved NO_PUSH value."""
    result = _run_launcher_block(user_repo)
    assert result.returncode == 0, (
        f"launcher block unexpectedly failed: {result.stderr}"
    )
    return result.stdout.strip()


def test_absent_toml_defaults_to_false(tmp_path):
    """No leerie.toml present → NO_PUSH=false."""
    assert _eval(tmp_path) == "false"


def test_toml_with_true_unquoted(tmp_path):
    """no_push = true (unquoted) → NO_PUSH=true."""
    (tmp_path / "leerie.toml").write_text("no_push = true\n")
    assert _eval(tmp_path) == "true"


def test_toml_with_true_double_quoted(tmp_path):
    """no_push = \"true\" (double-quoted; TOML allows this) → true."""
    (tmp_path / "leerie.toml").write_text('no_push = "true"\n')
    assert _eval(tmp_path) == "true"


def test_toml_with_true_single_quoted(tmp_path):
    """no_push = 'true' (single-quoted) → true. Mirror _read_toml_key
    which strips both quote styles."""
    (tmp_path / "leerie.toml").write_text("no_push = 'true'\n")
    assert _eval(tmp_path) == "true"


def test_toml_with_false(tmp_path):
    """no_push = false → NO_PUSH stays false."""
    (tmp_path / "leerie.toml").write_text("no_push = false\n")
    assert _eval(tmp_path) == "false"


@pytest.mark.parametrize("spelling", [
    "1", "true", "True", "TRUE", "tRuE",
    "yes", "Yes", "YES", "yEs",
    "on", "On", "ON", "oN",
])
def test_truthy_case_insensitive(tmp_path, spelling):
    """Truthy spellings must work in any case. The launcher case-folds
    via `tr [:upper:] [:lower:]` so the case-statement vocabulary
    matches the orchestrator's `_parse_bool_envtoml` exactly (which
    also case-folds via `.lower()`)."""
    (tmp_path / "leerie.toml").write_text(f"no_push = {spelling}\n")
    assert _eval(tmp_path) == "true", (
        f"spelling {spelling!r} should resolve to NO_PUSH=true; the "
        "launcher's case statement must match the orchestrator's "
        "case-insensitive vocabulary."
    )


@pytest.mark.parametrize("spelling", [
    "0", "false", "False", "FALSE", "fAlSe",
    "no", "No", "NO", "nO",
    "off", "Off", "OFF", "oFf",
])
def test_falsy_case_insensitive(tmp_path, spelling):
    """Falsy spellings in any case must keep NO_PUSH=false. They must
    NOT trigger the die-on-garbage path."""
    (tmp_path / "leerie.toml").write_text(f"no_push = {spelling}\n")
    assert _eval(tmp_path) == "false"


def test_toml_with_other_keys_present(tmp_path):
    """Other TOML keys around no_push don't confuse the grep."""
    (tmp_path / "leerie.toml").write_text(
        "# leerie config\n"
        "source_of_truth = both\n"
        "no_push = true\n"
        "runtime = fly\n"
    )
    assert _eval(tmp_path) == "true"


def test_toml_with_no_push_key_absent(tmp_path):
    """A leerie.toml that doesn't mention no_push → NO_PUSH=false."""
    (tmp_path / "leerie.toml").write_text(
        "runtime = fly\n"
        "source_of_truth = codebase\n"
    )
    assert _eval(tmp_path) == "false"


def test_toml_with_leading_whitespace_around_key(tmp_path):
    """Leading whitespace on the key line is tolerated (flat TOML is
    not strictly indented, but be forgiving)."""
    (tmp_path / "leerie.toml").write_text("   no_push = true\n")
    assert _eval(tmp_path) == "true"


def test_toml_with_commented_no_push(tmp_path):
    """`# no_push = true` is a comment and must NOT trigger the
    resolution (the grep anchors on optional leading whitespace, then
    the literal `no_push` — a leading `#` cannot match)."""
    (tmp_path / "leerie.toml").write_text(
        "# no_push = true  # commented out for now\n"
        "runtime = fly\n"
    )
    assert _eval(tmp_path) == "false"


def test_env_overrides_toml(tmp_path):
    """LEERIE_NO_PUSH=1 with TOML no_push=false → env wins (env > TOML)."""
    (tmp_path / "leerie.toml").write_text("no_push = false\n")
    result = _run_launcher_block(tmp_path, env_extra={"LEERIE_NO_PUSH": "1"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"


def test_toml_with_garbage_value_dies(tmp_path):
    """Unrecognized boolean spelling → launcher exits non-zero with a
    helpful error. Mirrors orchestrator's `_resolve_bool_pref` die()
    so the two layers agree on what's a valid boolean. Silent
    fallthrough to push would surprise a user who clearly opted out."""
    (tmp_path / "leerie.toml").write_text("no_push = sometimes\n")
    result = _run_launcher_block(tmp_path)
    assert result.returncode != 0, (
        "launcher must die on garbage TOML value, not silently "
        "treat it as false — the orchestrator dies (see "
        "tests/test_resolve_no_push.py::test_file_garbage_dies); the "
        "two layers must agree."
    )
    assert "is not a boolean" in result.stderr, (
        f"error message must explain why the value was rejected; got "
        f"stderr: {result.stderr!r}"
    )
    assert "sometimes" in result.stderr, (
        "error message must echo the user's exact spelling so they "
        "can find the typo; got: " + repr(result.stderr)
    )


def test_toml_with_garbage_value_quoted_dies(tmp_path):
    """Quoted garbage is still garbage."""
    (tmp_path / "leerie.toml").write_text('no_push = "maybe"\n')
    result = _run_launcher_block(tmp_path)
    assert result.returncode != 0
    assert "maybe" in result.stderr


def test_launcher_block_present_in_launcher_file():
    """Coupling test: the test reproduces the launcher's resolution
    block. If the launcher's block drifts, this test must drift with
    it. Pin the key grep/sed/case/case-fold/die structure here so
    refactors that subtly change parsing surface a failure."""
    src = LAUNCHER.read_text()
    assert "grep -E '^[[:space:]]*no_push[[:space:]]*='" in src, (
        "Launcher's NO_PUSH TOML grep is missing or has drifted. "
        "If the launcher changed its TOML parsing, update this test "
        "to match — but do not let the launcher and the test drift "
        "apart silently."
    )
    assert "|| true; }" in src, (
        "Launcher's NO_PUSH TOML grep must be wrapped in `{ ... || true; }` "
        "so set -e doesn't abort the launcher when the no_push key is "
        "absent from leerie.toml (the common case). Without the guard, "
        "every launcher run in a repo without `no_push` in leerie.toml "
        "would fail at this line."
    )
    assert "tr '[:upper:]' '[:lower:]'" in src, (
        "Launcher's NO_PUSH TOML value must be case-folded via tr "
        "before the case-statement match. Without case-folding, "
        "spellings like `Yes` or `On` (which the orchestrator's "
        ".lower() handles) would silently be treated as garbage on "
        "the launcher side."
    )
    assert "is not a boolean" in src, (
        "Launcher must die with a 'is not a boolean' error on garbage "
        "TOML values, matching the orchestrator's die() in "
        "_resolve_bool_pref. Silent fallthrough to NO_PUSH=false would "
        "make a typo'd `no_push = tre` silently push against user "
        "intent."
    )
