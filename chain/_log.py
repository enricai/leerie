"""chain._log — timestamped log + error-prefixed die helpers.

CLAUDE.md "Code style" forbids ``print(...)`` and ``sys.exit(...)``
outside of two documented exceptions in ``orchestrator/leerie.py``,
plus a third exception for this module: the chain subpackage cannot
import from the orchestrator (the package-isolation invariant), so
this file provides the chain-side equivalents.

Behavior:
    log("hello")      → stdout: "2026-06-14T17:23:04Z [chain] hello"
    die("bad input")  → stderr: "leerie-chain: error: bad input", exit 1
    die("bad", code=2) → exit code 2.

Both helpers flush so any caller-side stream capture sees lines in
real time.

Active call sites (under v5 Shape A): only ``chain.git_ops`` invokes
``die()`` on git/gh failures during the laptop-side
``synth_merge_branches`` step (the wave loop's only entry into this
module). The other ``chain.git_ops`` functions
(``clone_target``/``fetch_branch``/``finalize_run``/etc.) are kept
for the existing test suite and any future automated paths but are
not on the active chain code path.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import NoReturn


# Prefix used by ``log()``. "[chain]" groups all chain-helper output
# under a single grep-friendly tag; if a future caller needs a
# different scope, parameterize this.
_LOG_PREFIX = "[chain]"

# Prefix matching orchestrator's ``die`` for grep'ability across logs.
_DIE_PREFIX = "leerie-chain: error:"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    """Write a timestamped, prefixed line to stdout.

    The caller (typically a ``python3 -c`` invocation from the
    ``leerie`` launcher's ``--chain`` arm) sees this on stdout in
    real time.
    """
    print(f"{_iso_now()} {_LOG_PREFIX} {msg}", flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    """Write an error-prefixed line to stderr and exit with *code*.

    Mirrors ``orchestrator.leerie.die`` semantics: a single fatal-exit
    helper so call sites stay terse and grep-friendly.
    """
    print(f"{_DIE_PREFIX} {msg}", file=sys.stderr, flush=True)
    sys.exit(code)
