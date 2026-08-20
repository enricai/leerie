"""Shared test helpers reused across tests/*.py.

Single-owner discipline (see CLAUDE.md's `launcher_blocks.py`/`ec2_stub.py`
precedent): each helper here previously existed as byte-identical copies in
every consuming test file, which is the drift risk this module exists to
remove.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"


def _entry_sh_rootful_block() -> str:
    text = ENTRY_SH.read_text()
    marker = 'if [ "$ROOTLESS" != "true" ] && getent passwd leerie'
    start = text.index(marker)
    end = text.index("\nfi", start)
    return text[start:end]


def _make_stub_timeout(stub_dir: Path) -> None:
    """No-op `timeout`: run the child, ignore the cap.

    Adequate for tests that merely need the binary to exist on the
    stubbed PATH. A test that asserts the cap actually *fires* must
    use `_stub_timeout` (imported from tests.test_ec2_transport) instead.

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
    stub = stub_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
while [[ "$1" == --* ]]; do shift; done
shift
exec "$@"
"""
    )
    stub.chmod(0o755)
