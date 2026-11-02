"""Tests for provision.sh's `flyctl machine run` output parsing.

`flyctl machine run` does NOT accept `--json` (verified in real
flyctl as of May 2026). Leerie parses the human-readable text output
for the `Machine ID: <id>` line via awk. This test exercises the awk
parser in isolation against several output shapes flyctl has produced.

Also pins the regression fixes for two prior failures:
- `local create_output machine_id` was declared without initialization
  → when flyctl exits non-zero, machine_id is unset, and the
  `[ -z "$machine_id" ]` check at provision.sh:255 triggers
  `set -u: machine_id: unbound variable`. The fix initializes both
  to empty strings.
- The previous parser used `python3 -c 'json.load' ...` which silently
  returned empty on non-JSON input → identical "machine_id empty"
  failure mode, only diagnosable via the printf of $create_output
  (which `set -u` killed before it could print).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"


# The awk-extraction pipeline used in provision.sh. Pinned here so a
# refactor of provision.sh that breaks the parser fails THIS test.
_PARSE = r'''
sed 's/\x1b\[[0-9;]*m//g' | awk '/Machine ID:/ && !f { for (i=1; i<=NF; i++) if ($i == "ID:" && !f) { print $(i+1); f = 1 } }'
'''


def _parse(text: str) -> str:
    """Run the parser pipeline against the given text. Returns stdout."""
    r = subprocess.run(
        ["bash", "-c", _PARSE],
        input=text,
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip()


def test_parser_extracts_id_from_typical_output():
    """The shape flyctl produces in normal interactive mode."""
    out = """\
Searching for image 'registry.fly.io/leerie:0.2.1' remotely...
image found: img_lrjxpggly9d5p7n6
Image: registry.fly.io/leerie:0.2.1@sha256:abc

Success! A Machine has been successfully launched in app leerie
 Machine ID: e8204deb7e4578
 Instance ID: 01KT0NDTAN7FPZ0GQDVN6XT8TB
 State: created
"""
    assert _parse(out) == "e8204deb7e4578"


def test_parser_extracts_id_with_ansi_color_codes():
    """flyctl sometimes wraps output in ANSI color codes when stdout is
    a TTY. provision.sh strips ESC[<...>m sequences via sed before
    awk."""
    # \x1b[32m is green; \x1b[0m is reset
    out = "\x1b[32m Machine ID: \x1b[0mmach-color-123\n"
    assert _parse(out) == "mach-color-123"


def test_parser_returns_empty_on_no_match():
    """If flyctl's output doesn't contain a Machine ID line (e.g. error
    output), the parser returns empty. The shell then takes the
    "failed to create" branch and surfaces the original stderr."""
    out = "Error: app not found\n"
    assert _parse(out) == ""


def test_parser_extracts_first_id_when_multiple_present():
    """Defensive: if for some reason multiple Machine ID lines appear
    (concurrent prints, retries), take the first."""
    out = " Machine ID: first-id-aaa\n Machine ID: second-id-bbb\n"
    assert _parse(out) == "first-id-aaa"


# --- source-text pins ----------------------------------------------------

def test_provision_initializes_machine_id_under_set_u():
    """provision.sh must initialize machine_id="" (not just declare it
    with `local machine_id`). Without the initialization, a failed
    flyctl call leaves the variable unset and `[ -z "$machine_id" ]`
    triggers set -u. This was the live-test bug fixed in Part H.
    """
    text = PROVISION_SH.read_text()
    assert 'local create_output=""' in text, \
        "create_output must be initialized to '' (set -u safety)"
    assert 'local machine_id=""' in text, \
        "machine_id must be initialized to '' (set -u safety)"


def test_provision_does_not_pass_json_to_machine_run():
    """flyctl machine run does NOT accept --json. The previous code
    passed it and caused a non-zero exit that left machine_id empty.
    Pin the source to ensure --json never returns to this call.

    Filters out comment lines (which may mention --json as part of the
    warning) and only checks the actual flag-passing lines."""
    text = PROVISION_SH.read_text()
    # Find the flyctl machine run block.
    import re
    m = re.search(
        r'flyctl machine run.*?(?=\n  if \[ -z "\$machine_id" \])',
        text, re.DOTALL
    )
    assert m is not None, "could not locate flyctl machine run block"
    block = m.group(0)
    # Strip lines that are comments (start with optional whitespace + #).
    code_lines = [ln for ln in block.split("\n") if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "--json" not in code, \
        "flyctl machine run must NOT be invoked with --json " \
        "(unsupported by flyctl; causes the call to fail silently). " \
        f"Found --json in non-comment line of:\n{code}"


def test_provision_uses_awk_parser_for_machine_id():
    """Pin the awk parser used to extract Machine ID from text output."""
    text = PROVISION_SH.read_text()
    # The awk filter signature.
    # The awk program this file executes is provision.sh's own, verbatim, so
    # the behaviour tests above exercise the shipped parser.
    assert _PARSE.strip().split(" | ", 1)[1] in text


# --- wait_for_started also affected: flyctl machine status has no --json --

def test_wait_for_started_does_not_pass_json_to_machine_status():
    """flyctl machine status (like machine run) does NOT accept --json.
    wait_for_started parses the text output's `State: <state>` line
    via awk instead. Pin the source-text to ensure --json never returns
    to this call.

    Filters out comment lines (which mention --json in the explanatory
    block).
    """
    text = PROVISION_SH.read_text()
    import re
    # Find the wait_for_started function block.
    m = re.search(
        r'wait_for_started\(\) \{.*?\n\}',
        text, re.DOTALL
    )
    assert m is not None, "could not locate wait_for_started block"
    block = m.group(0)
    code_lines = [ln for ln in block.split("\n") if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "--json" not in code, \
        "flyctl machine status must NOT be invoked with --json " \
        "(flyctl machine status does not support --json). " \
        f"Found --json in non-comment line of:\n{code}"


def _parse_state(text: str) -> str:
    """Run the State-extraction pipeline used in wait_for_started."""
    r = subprocess.run(
        ["bash", "-c",
         "sed 's/\\x1b\\[[0-9;]*m//g' | "
         "awk -F': *' '/^State: / && !f { print $2; f = 1 }' | "
         "tr -d '[:space:]'"],
        input=text,
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip()


def test_state_parser_extracts_state_from_typical_output():
    """flyctl machine status produces a header block with `State: <state>`
    early in the output. The parser extracts that line."""
    out = """\
Machine ID: d895909b432658
Instance ID: 01KT0PA18AXHST6SFQ17YH1W0F
State: started
HostStatus: ok
"""
    assert _parse_state(out) == "started"


def test_state_parser_returns_first_state_not_table_state():
    """flyctl machine status output also contains a box-drawing table
    with `State │ <state>`. The parser must match the prefix-aligned
    `State: ` line near the top, NOT the table row (which has a
    different separator character)."""
    out = """\
Machine ID: mach-test
State: stopped

VM
 State         │ stopped
"""
    assert _parse_state(out) == "stopped"


def test_state_parser_returns_empty_on_no_match():
    """If flyctl errored and produced no State line, parser returns
    empty. wait_for_started's case statement then waits (no transition
    on this poll) until the next iteration or timeout."""
    assert _parse_state("Error: machine not found\n") == ""
