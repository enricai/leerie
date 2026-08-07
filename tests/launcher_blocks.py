"""The single derivation of the launcher's orchestrator launch blocks.

Each remote runtime builds its own `child_env = dict(os.environ)` inside its own
unquoted `<<PY` launch heredoc. Several guards need that set — the
`LEERIE_COMMIT` forwarding check in `test_leerie_commit.py`, and the
stray-`${...}` and backtick scans in `test_bedrock_bearer_token.py` — and all of
them must derive it rather than enumerate it, because a hard-coded list stops
covering reality the moment a runtime is added.

This module exists because that lesson applied to the guards themselves. PRs
#180-#183 each replaced a hard-coded enumeration with a derivation, after a
missed instance shipped: `ContextOverflow` in 1 of 9 capture guards,
`leerie_commit` in 1 of 2 state-init branches, then in 1 of 2 launch blocks --
the last caught by a reviewer, not the suite. The derivation was then written
*twice*, once per consuming test file, replicating three magic constants. Two
copies of a rule drift exactly the way two copies of a list do, so there is one
copy, here.

Deliberately a neutral module rather than a cross-test import (both patterns are
idiomatic in this repo -- cf. `tests/ec2_stub.py` for the former,
`test_fetch_branch_leerie_streamback` importing `test_fetch_branch_sh` for the
latter): the two consumers are unrelated concerns, and neither should own it.
It reads the launcher itself rather than accepting the source as a parameter, so
it does not care whether a caller happens to hold a `str` or a `Path` -- the
incidental difference that made the two copies look unalike.
"""
from __future__ import annotations

import re
from pathlib import Path

LAUNCHER_PATH = Path(__file__).resolve().parents[1] / "leerie"

# What marks the start of a launch env block. Each launch heredoc builds its
# child environment from scratch because `ssh console` sets HOME=/root.
_BLOCK_START = re.compile(r"child_env = dict\(os\.environ\)")

# The heredoc terminator that bounds a block. Both launch heredocs use `<<PY`.
# Many unrelated heredocs in the launcher share this delimiter, so the bound is
# "first terminator after the block start" -- verified disjoint, see
# `test_no_duplicate_launcher_splitters.py`.
_BLOCK_END = "\nPY\n"

# How far above a block to look for its runtime label. The launcher prints a
# `leerie resume <id> --runtime <rt>` hint in each heredoc's duplicate-run
# guard, which sits a few dozen lines above the env block.
_PREAMBLE_WINDOW = 1200


def launch_env_blocks() -> list[tuple[str, str]]:
    """Every orchestrator launch env block, as `(runtime, body)`.

    `runtime` is `"ec2"` or `"fly"`, taken from the `--runtime <rt>` string in
    the duplicate-run message above each block. The label is for diagnostics —
    it is what lets a failing guard say *which* block is broken — so a
    mislabel would degrade a message, not weaken an assertion.
    """
    src = LAUNCHER_PATH.read_text()
    out: list[tuple[str, str]] = []
    for m in _BLOCK_START.finditer(src):
        end = src.find(_BLOCK_END, m.start())
        body = src[m.start():end if end > 0 else len(src)]
        preamble = src[max(0, m.start() - _PREAMBLE_WINDOW):m.start()]
        out.append(("ec2" if "--runtime ec2" in preamble else "fly", body))
    return out
