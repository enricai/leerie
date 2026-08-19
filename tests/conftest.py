"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    """The leerie module loaded from orchestrator/leerie.py."""
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_treesitter() -> bool:
    """True only if the installed tree-sitter stack can actually extract a
    symbol. Delegates to leerie's own `_tree_sitter_extraction_works()`
    functional probe (mere importability is insufficient — an
    installed-but-incompatible language-pack version imports fine yet extracts
    nothing). Shared here so extraction-dependent repo-map test modules gate
    on it without duplication."""
    spec = importlib.util.spec_from_file_location("_leerie_ts_probe", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._tree_sitter_extraction_works()
    except Exception:
        return False


# Evaluated once at collection; extraction-dependent repo-map test modules
# import this to skip cleanly on hosts without a working parser.
HAS_TREESITTER = _has_treesitter()


# Evaluated once at collection; test modules that exercise *host-side* bash
# import this to skip cleanly where `jq` is absent.
#
# Why a gate rather than adding jq to the image: the host/container split is
# deliberate. Host bash uses `jq` — the launcher hard-fails at preflight
# without it (`leerie`'s "jq not found on PATH" check, which tells you to
# `brew install jq`) — while code that runs *inside* the container uses
# python3, exactly as `scripts/remote/seed-auth.sh` documents: "python3 over
# jq because jq isn't in the leerie image (see Dockerfile)".
#
# The gated modules source scripts the host owns (`host-finalize.sh`,
# `provision.sh`'s `decide_teardown`, the launcher's finalize path) and stub
# `git`/`gh` on PATH but not `jq`, so they silently inherit it from whichever
# machine runs pytest. They pass on a dev host and in CI (both ship jq) and
# fail only inside `leerie:<version>` — where the scripts under test could
# never succeed anyway, since gh auth, ssh-agent, and Keychain are all
# host-side (DESIGN §6 *Finalization*).
#
# Do NOT "fix" a skip here by installing jq into the image: that buys a green
# tick, not working code, and erodes the boundary.
HAS_JQ = shutil.which("jq") is not None


def fake_claude_on_path(tmp_path: Path, monkeypatch) -> Path:
    """Put a stub `claude` binary on PATH and return it.

    `main()` gates on `shutil.which("claude")` and `die()`s (exit 1) long
    before anything a `main()`-driving test asserts on -- 87 lines before
    `State(...)` mints the run dir, and well before the top-level
    try/except. The binary is on a developer's PATH and NOT on the CI
    runner's, so a suite that drives the real `main()` without this passes
    locally and fails on CI with every expected exit code collapsed to
    `die()`'s 1. That is the trap CLAUDE.md records ("A local pass is not
    evidence until the host lacks `claude`"), and it is how 14 tests in
    `test_main_exception_arms.py` shipped red.

    Single owner on purpose: this lived in `test_main_cli_wiring.py` while
    `test_main_exception_arms.py` -- the same harness minus this one call --
    had no copy at all. Anything driving the real `main()` imports it from
    here rather than reproducing it.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\necho '{}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return stub
