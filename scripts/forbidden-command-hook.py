#!/usr/bin/env python3
"""PreToolUse hook: deny a Bash call that runs a command the task forbids, or
that kills processes by name or pattern.

Wired by `claude_p` through `--settings` (`_forbidden_command_hook_settings`
in orchestrator/leerie.py; DESIGN §9 *A command the task forbids is denied in
every spelling* and *A worker never kills a process by name*). argv[1] is the
payload, base64-encoded JSON: {"forbidden": [...], "kills": bool} (a bare list
is the forbidden-commands-only shape). The matcher is leerie's own, imported
from the sibling `orchestrator/` directory, so the hook and the orchestrator
can never disagree about what matches.

Importing leerie costs ~1.7 s where no bytecode cache can be written (a
root-owned image dir under a real uid switch: rootful, Fly) and this hook runs
before EVERY Bash call of every acting worker, so a kills-only payload first
checks the raw command text for the substrings every kill form the matcher
recognises must contain, and allows without importing when none is there.
`tests/test_process_kill_deny.py` pins that every recognised kill form passes
this prefilter. Fails open: any error allows the call — the deny rules still
stand."""
import base64
import io
import json
import os
import sys

# Every kill the matcher denies has a `kill`-family head (pkill, killall,
# killall5, kill) or `fuser -k` somewhere in the command text.
_KILL_SUBSTRINGS = ("kill", "fuser")


def _needs_matcher(payload: object, command: object) -> bool:
    if not isinstance(command, str):
        return False
    if isinstance(payload, list):
        return bool(payload)
    if not isinstance(payload, dict):
        return False
    if payload.get("forbidden"):
        return True
    return payload.get("kills") is True and any(
        s in command for s in _KILL_SUBSTRINGS)


def main() -> int:
    try:
        payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
        raw = sys.stdin.read()
        event = json.loads(raw)
        command = ((event.get("tool_input") or {}).get("command")
                   if isinstance(event, dict) else None)
    except Exception:
        return 0
    if not _needs_matcher(payload, command):
        return 0
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orchestrator"))
    try:
        import leerie
    except Exception:
        return 0
    return leerie._forbidden_command_hook_main(
        sys.argv, io.StringIO(raw), sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
