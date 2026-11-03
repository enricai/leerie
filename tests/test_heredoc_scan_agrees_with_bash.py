"""The subcommand scan drops a here-document's body only where bash reads
one (DESIGN §9, the forbidden-command scan's here-document rule).

A differential test against `bash -c` itself, never a list of expected
splits: each form runs in real bash with `pkill`, `kill`, `pgrep`, `cat` and
`npx` replaced by shell functions that write a marker to stderr, under an
empty `PATH` (builtins and those functions only, nothing on PATH runs). The
scan's verdict (`_process_kill_hit`, `_forbidden_command_hit`) must be a hit
exactly when bash runs the marked command. The one deliberate disagreement
is pinned on its own: where the scan cannot show a body is one (here, no
terminating line), it reads the text as commands — a false denial, never a
missed command. The first twelve forms are round
30's isolation H1 (`review-r30-isolation.md`): each hid a command bash ran.
"""
from __future__ import annotations

import subprocess

import pytest

STUBS = ("pkill(){ echo RAN-pkill \"$@\" >&2; }\n"
         "kill(){ echo RAN-kill \"$@\" >&2; }\n"
         "pgrep(){ echo 999999; }\n"
         "cat(){ :; }\n"
         "npx(){ echo RAN-npx \"$@\" >&2; }\n")

KILL_FORMS = {
    # round 30 H1
    "comment": ": # <<X\npkill -f victim\n",
    "arith_subst": "echo $((1<<2))\npkill -f victim\n",
    "arith_cmd": "(( y = 1<<3 ))\npkill -f victim\n",
    "comment_kill_pgrep": ": # <<X\nkill $(pgrep -f victim)\nX\n",
    "param_expansion": "x=a; echo ${x//<<X/b}\npkill -f victim\nX\n",
    "old_arith": "echo $[1<<2]\npkill -f victim\n",
    "comment_terminated": ": # <<X\npkill -f victim\nX\n",
    "arith_terminated": "echo $((1<<2))\npkill -f victim\n2\n",
    "ansi_c_delim": "cat <<$'X'\nbody\nX\npkill -f victim\n",
    "body_subst_quoted_paren":
        "cat <<X\n$(echo \")\"; pkill -f victim)\nX\n",
    "body_subst_case": "cat <<X\n$(case a in a) pkill -f victim;; esac)\nX\n",
    # the same shapes, nearby
    "quoted_arith": "echo \"$((1<<2))\"\npkill -f victim\n2\n",
    "nested_arith": "echo $(( (1+2) << 3 ))\npkill -f victim\n3\n",
    "plain_subst_case": "echo $(case a in a) pkill -f victim;; esac)\n",
    # controls: a body bash does not run, and one it does
    "quoted_body": "cat <<'EOF'\npkill -f victim\nEOF\necho done\n",
    "unquoted_body_text": "cat <<EOF\npkill -f victim\nEOF\n",
    "dash_body": "cat <<-EOF\n\tpkill -f victim\n\tEOF\necho ok\n",
    "after_body": "cat <<'EOF'\ntext\nEOF\npkill -f victim\n",
    "unquoted_body_subst": "cat <<EOF\n$(pkill -f victim)\nEOF\n",
    "in_substitution": "echo \"$(cat <<'EOF'\npkill -f victim\nEOF\n)\"\n",
}

SUITE_FORMS = {
    "fullsuite_comment": ": # <<X\nnpx vitest\nX\n",
    "fullsuite_arith": "echo $((1<<2))\nnpx vitest\n2\n",
    # the live 0.43.0 barnacle shape: a quoted body, then a scoped run
    "live_quoted_body": ("cat >> f.test.ts <<'EOF'\n"
                         "const { vi } = await import(\"vitest\");\n"
                         "EOF\nnpx vitest run tests/a.test.ts\n"),
}


def _bash_runs(cmd: str, marker: str) -> bool:
    r = subprocess.run(["/bin/bash", "-c", STUBS + cmd], capture_output=True,
                       text=True, cwd="/", env={"PATH": ""}, timeout=30)
    return marker in r.stderr


@pytest.mark.parametrize("name", sorted(KILL_FORMS))
def test_the_kill_scan_agrees_with_bash(leerie, name):
    cmd = KILL_FORMS[name]
    ran = _bash_runs(cmd, "RAN-pkill") or _bash_runs(cmd, "RAN-kill")
    assert bool(leerie._process_kill_hit(cmd)) is ran, (
        name, leerie._shell_subcommands(cmd))


@pytest.mark.parametrize("name", sorted(SUITE_FORMS))
def test_the_whole_suite_scan_agrees_with_bash(leerie, name):
    cmd = SUITE_FORMS[name]
    whole = _bash_runs(cmd, "RAN-npx vitest\n") or (
        _bash_runs(cmd, "RAN-npx") and "vitest run tests/" not in cmd)
    spec = [{"command": "vitest run", "scope": "whole_suite",
             "test_roots": ["tests"]}]
    assert bool(leerie._forbidden_command_hit(cmd, spec)) is whole, (
        name, leerie._shell_subcommands(cmd))


def test_the_forms_are_what_bash_says_they_are():
    """The stubs work: bash runs the hidden command in an H1 form and not in
    a quoted body, so the agreement above is not vacuous."""
    assert _bash_runs(KILL_FORMS["comment"], "RAN-pkill")
    assert _bash_runs(KILL_FORMS["body_subst_case"], "RAN-pkill")
    assert not _bash_runs(KILL_FORMS["quoted_body"], "RAN-pkill")
    assert _bash_runs(SUITE_FORMS["fullsuite_comment"], "RAN-npx vitest")


def test_a_body_the_scan_cannot_prove_is_read_as_commands(leerie):
    """No terminating line: bash reads to the end as body and runs nothing
    after it; the scan, unable to agree line by line, over-matches."""
    cmd = "cat <<X\nbody\npkill -f victim\n"
    assert not _bash_runs(cmd, "RAN-pkill")
    assert leerie._process_kill_hit(cmd)
