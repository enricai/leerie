"""Tests for container-entry.sh's `--run-id` injection (DESIGN §6).

The run_id IS the local container id, so container-entry.sh injects it
from the cidfile for a fresh run. It must NOT do so on `--resume`: a
resume container is a *new* container with a new id, but the run being
resumed already has one. Injecting there handed the orchestrator an id
matching no run on disk, and `resolve_run_id` fails closed on an unknown
explicit id — so bare `--resume` died with "does not match any known run"
naming an id the user never typed.

An explicit `--resume <id>` survived only by luck: the launcher rewrites
it to `--run-id <id>`, and argparse takes the last occurrence, so the real
id beat the injected one.

The injection block is extracted from the real script at test time (the
`_extract_config_arm` pattern in test_config_verb.py) so this can never
silently drift from what ships.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"


def _extract_injection_block() -> str:
    """Pull the real cidfile/--run-id block out of container-entry.sh.

    Anchored on the `_is_resume` guard through the closing `unset`.
    """
    src = ENTRY.read_text()
    m = re.search(r"^_is_resume=false$.*?^unset _is_resume _a$",
                  src, re.MULTILINE | re.DOTALL)
    assert m, ("could not find the --run-id injection block in "
               "container-entry.sh — did it get restructured?")
    return m.group(0)


def _run_entry(tmp_path, argv, cid="c" * 64):
    """Run the extracted block against a fake cidfile, echo the final argv."""
    cidfile = tmp_path / "cidfile"
    cidfile.write_text(cid)
    block = _extract_injection_block().replace(
        "/run/leerie-cidfile", str(cidfile))
    script = tmp_path / "harness.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                      + block + '\nprintf "%s\\n" "$@"\n')
    script.chmod(0o755)
    r = subprocess.run(["bash", str(script), *argv],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.split("\n")


def test_fresh_run_gets_the_container_id_injected(tmp_path):
    """The baseline behavior must be untouched: run_id = container id."""
    out = _run_entry(tmp_path, ["some task"])
    assert out[0] == "--run-id"
    assert out[1] == "c" * 64
    assert out[2] == "some task"


def test_bare_resume_gets_no_injected_run_id(tmp_path):
    """The bug: bare --resume must reach the orchestrator with no --run-id
    so resolve_run_id() can auto-pick the real run."""
    out = _run_entry(tmp_path, ["--resume"])
    assert "--run-id" not in out
    assert out[0] == "--resume"


def test_explicit_resume_keeps_only_the_real_run_id(tmp_path):
    """The launcher already rewrote `--resume <id>` to `--run-id <id>`;
    the injected container id must not be prepended alongside it."""
    real = "a" * 64
    out = _run_entry(tmp_path, ["--resume", "--run-id", real])
    assert out.count("--run-id") == 1
    assert real in out
    assert "c" * 64 not in out


def test_resume_flag_detected_in_any_position(tmp_path):
    """--resume is not guaranteed to be argv[0]."""
    out = _run_entry(tmp_path, ["--verbose", "--resume"])
    assert "--run-id" not in out


def test_no_cidfile_is_a_clean_no_op(tmp_path):
    """Remote (Fly) runs have no cidfile; the block must not fire or fail."""
    block = _extract_injection_block().replace(
        "/run/leerie-cidfile", str(tmp_path / "does-not-exist"))
    script = tmp_path / "h.sh"
    script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                      + block + '\nprintf "%s\\n" "$@"\n')
    script.chmod(0o755)
    r = subprocess.run(["bash", str(script), "task"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "task"


def test_script_is_valid_bash():
    """The real script parses (guards the edit itself)."""
    r = subprocess.run(["bash", "-n", str(ENTRY)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
