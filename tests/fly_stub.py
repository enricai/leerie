"""Shared fake-`flyctl` test double for Fly.io remote-script tests.

Mirrors tests/ec2_stub.py's precedent on the EC2 side: rather than five
independently reimplemented `_make_fake_flyctl` bash builders each
re-parsing the same `-C`/`auth`/`machine`/`ssh` flags, callers supply only
the routing logic that differs for their script under test.
"""
from __future__ import annotations

from pathlib import Path


def make_fake_flyctl(
    tmp_path: Path,
    case_body: str,
    *,
    extra_preamble: str = "",
    filename: str = "flyctl",
) -> Path:
    """Write a stub `flyctl` binary and return its path.

    Shared plumbing common to every call site: parse `-C <cmd>` into
    `$CMD`, `auth ...` into `$SUB=auth` (exiting 0 immediately — every
    call site's only use of `auth` is the `auth status` preflight probe,
    so any auth subcommand succeeding is equivalent), `machine <subsub>`
    into `$SUB=machine` / `$MSUB`, `ssh` into `$SUB=ssh`, and
    `apps <subsub> [arg]` (`apps list --json` / `apps create <name>`)
    into `$SUB=apps` / `$MSUB` / `$AARG` — `$AARG` carries the app name
    for `create` (unused, but harmlessly consumed, for `list --json`).

    `extra_preamble` is emitted verbatim before flag parsing (for
    caller-specific path/state variables the case body needs).
    `case_body` is emitted verbatim after flag parsing and holds each
    caller's own routing over `$CMD`/`$SUB`/`$MSUB` — the part that
    genuinely differs between the five scripts under test (ssh-console
    payload routing, machine start/stop/status simulation, bash -s
    payload routing, etc.).
    """
    stub = tmp_path / filename
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        + extra_preamble
        + 'CMD=""\n'
        'SUB=""\n'
        'MSUB=""\n'
        'AARG=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -C) CMD="$2"; shift 2 ;;\n'
        '    auth) SUB="auth"; shift ;;\n'
        '    machine) SUB="machine"; shift; MSUB="${1:-}"; shift || true ;;\n'
        '    ssh) SUB="ssh"; shift ;;\n'
        '    apps) SUB="apps"; shift; MSUB="${1:-}"; shift || true; '
        'AARG="${1:-}"; shift || true ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        'if [ "$SUB" = "auth" ]; then exit 0; fi\n'
        + case_body
    )
    stub.chmod(0o755)
    return stub
