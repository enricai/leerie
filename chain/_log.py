"""chain._log — timestamped log + error-prefixed die helpers.

CLAUDE.md "Code style" forbids ``print(...)`` and ``sys.exit(...)``
outside of two documented exceptions in ``orchestrator/leerie.py``.
The chain subpackage cannot import from the orchestrator (the
package-isolation invariant), so this module provides the chain-side
equivalents.

Behavior:
    log("hello")      → stdout: "2026-06-14T17:23:04Z [coordinator] hello"
    die("bad input")  → stderr: "leerie-chain: error: bad input", exit 1
    die("bad", code=2) → exit code 2.

Both helpers flush so Fly's stdout/stderr stream-capture sees lines
in real time.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import NoReturn


# Prefix used by ``log()``. The coordinator is the only chain-side process
# today, so a single literal prefix is fine; if a second long-running
# chain process appears, parameterize this.
_LOG_PREFIX = "[coordinator]"

# Prefix matching orchestrator's ``die`` for grep'ability across logs.
_DIE_PREFIX = "leerie-chain: error:"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    """Write a timestamped, prefixed line to stdout.

    Fly captures the coordinator machine's stdout into the machine log
    stream that ``flyctl logs`` exposes; the format here is the canonical
    chain-side log line.
    """
    print(f"{_iso_now()} {_LOG_PREFIX} {msg}", flush=True)


def die(msg: str, code: int = 1) -> NoReturn:
    """Write an error-prefixed line to stderr and exit with *code*.

    Mirrors ``orchestrator.leerie.die`` semantics: a single fatal-exit
    helper so call sites stay terse and grep-friendly.
    """
    print(f"{_DIE_PREFIX} {msg}", file=sys.stderr, flush=True)
    sys.exit(code)
