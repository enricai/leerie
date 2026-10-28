#!/usr/bin/env python3
"""PreToolUse hook: deny a Bash call that runs a command the task forbids.

Wired by `claude_p` through `--settings` (`_forbidden_command_hook_settings`
in orchestrator/leerie.py; DESIGN §9 *A command the task forbids is denied in
every spelling*). argv[1] is the forbidden list, base64-encoded JSON. The
matcher is leerie's own, imported from the sibling `orchestrator/` directory,
so the hook and the orchestrator can never disagree about what matches.
Fails open: any error allows the call — the deny rules still stand."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "orchestrator"))

try:
    import leerie
except Exception:
    sys.exit(0)

sys.exit(leerie._forbidden_command_hook_main(sys.argv, sys.stdin, sys.stdout))
