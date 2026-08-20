"""The single owner of the no-op `timeout` bash stub used across test PATHs.

Six test files each defined a byte-identical `_make_stub_timeout(stub_dir)`,
differing only in docstring wording: it writes a `timeout` shim that skips
any leading `--flag`/duration args and execs the wrapped command, ignoring
the time cap. Adequate for tests that merely need the `timeout` binary to
exist on the stubbed PATH (macOS ships no `/usr/bin/timeout`; it's coreutils,
via Homebrew). A single-owner module keeps future changes to the stub's
semantics from silently desyncing across call sites — same precedent as
`tests/ec2_stub.py` and `tests/launcher_blocks.py`.

This is deliberately NOT the stub for tests that assert the cap actually
*fires*: `_stub_timeout` in `tests/test_ec2_transport.py` is a distinct,
intentionally different killing variant for that case, and stays there.
"""
from __future__ import annotations

from pathlib import Path


def _make_stub_timeout(stub_dir: Path) -> None:
    """No-op `timeout`: run the child, ignore the cap.

    Adequate for tests that merely need the binary to exist on the
    stubbed PATH. A test that asserts the cap actually *fires* must
    use `_stub_timeout` (imported from tests.test_ec2_transport) instead.
    """
    stub = stub_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
while [[ "$1" == --* ]]; do shift; done
shift
exec "$@"
"""
    )
    stub.chmod(0o755)
