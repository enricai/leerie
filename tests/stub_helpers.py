"""Shared test-stub builders used across the launcher test suite."""
from __future__ import annotations

import textwrap
from pathlib import Path


def _stub_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Build a stub binary that records its argv to a log and exits 0.

    Used to intercept ``LEERIE_SELF_CMD`` dispatch in chain/group launcher
    tests so they don't actually shell out to a real launcher recursion.

    Returns ``(stub_path, log_path)``.
    """
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log
