"""The forbidden-command matcher reads each package manager's own flags, and
reads backslash escapes as bash does (DESIGN §9 *A command the task forbids
is denied in every spelling*; review r1 L1).

One value-option table served every manager, and it listed `-w`: npm's `-w`
is `--workspace <name>`, but pnpm's is the FLAG `--workspace-root`, so
`pnpm -w test` read as "`test` is -w's value" and ran past a forbidden
`pnpm test`. The table also missed most of pnpm's value options, there was
no reading of the scope subcommands (`pnpm recursive run test`, `yarn
workspace web test`), and an unquoted escaped blank split the word, so
`bash -c pnpm\\ test` passed `-c` only `pnpm`.

Three layers, each the review's method:
- the expected verdicts, from each tool's `--help`;
- real bash with argv-printing stubs: what bash actually hands the stub is
  the ground truth for the shell-reading cases;
- the real pnpm/npm/yarn, when installed, running a workspace whose `test`
  script prints a marker: "did `test` run" is the ground truth.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FORBIDDEN = ["pnpm test"]

# (command, the test script runs) — from pnpm 11 / npm 11 / yarn 1 `--help`.
TABLE = [
    ("pnpm -w test", True),                 # -w is a flag in pnpm
    ("pnpm --workspace-root test", True),
    ("pnpm -w build", False),
    ("npm -w web test", True),              # ...and takes a value in npm
    ("npm -w test build", False),           # `test` is the workspace name
    ("pnpm recursive run test", True),
    ("pnpm multi run test", True),
    ("pnpm m run test", True),
    ("pnpm recursive --filter web run test", True),
    ("pnpm --filter-prod web test", True),
    ("pnpm --workspace-concurrency 1 -r test", True),
    ("pnpm --resume-from web -r test", True),
    ("pnpm --test-pattern x -r test", True),
    ("pnpm --changed-files-ignore-pattern x -r test", True),
    ("pnpm run --filter web test", True),
    ("pnpm run --filter test build", False),
    ("npm --script-shell bash test", True),
    ("npm run --workspace web test", True),
    ("yarn workspace web test", True),
    ("yarn workspace web run test", True),
    ("yarn workspace test build", False),   # `test` is the workspace name
    ("yarn workspaces run test", True),
    ("yarn workspaces foreach -A run test", True),
    ("yarn --cwd web test", True),
    ("bun --cwd web test", True),
    ("bash -c pnpm\\ test", True),
    ("sh -c pnpm\\ \\ test", True),
    ("bash -c 'pnpm test'", True),
    ("pnpm te\\\nst", True),                # backslash-newline is removed
    ("pnpm build \\\n  && pnpm test", True),
    ("pnpm\\ test", False),                 # one word: no such command
]


@pytest.mark.parametrize("cmd,runs", TABLE)
def test_matcher_reads_each_managers_own_flags(leerie, cmd, runs):
    assert bool(leerie._forbidden_command_hit(cmd, FORBIDDEN)) is runs


# --- real bash, argv-printing stubs ------------------------------------------

STUB_CASES = [
    "bash -c pnpm\\ test",
    "sh -c pnpm\\ \\ test",
    "bash -c 'pnpm test'",
    "bash -c \"pnpm\\ test\"",
    "env FOO=1 pnpm test",
    "timeout 30 pnpm test",
    "true && pnpm\\ test",
    "pnpm\\ test",
    "pnpm te\\\nst",
    "pnpm build \\\n  test",
    "echo pnpm\\ test",
    "bash -c 'echo pnpm test'",
]


@pytest.mark.parametrize("cmd", STUB_CASES)
def test_shell_reading_matches_real_bash(leerie, tmp_path, cmd):
    """Ground truth is what real bash hands an argv-printing `pnpm` stub:
    the matcher hits exactly when the stub was run with `test` as its
    first argument (the shape of the forbidden command)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    stub = bin_dir / "pnpm"
    stub.write_text("#!/bin/sh\nprintf '%s\\037' \"$@\" >> \"$ARGV_LOG\"\n"
                    "printf '\\n' >> \"$ARGV_LOG\"\n")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "ARGV_LOG": str(log)}
    subprocess.run(["bash", "-c", cmd], env=env, capture_output=True,
                   timeout=60, check=False, cwd=str(tmp_path))
    calls = ([line.split("\037")[:-1] for line in
              log.read_text().splitlines()] if log.exists() else [])
    ran_test = any(c[:1] == ["test"] for c in calls)
    assert bool(leerie._forbidden_command_hit(cmd, FORBIDDEN)) is ran_test, (
        cmd, calls)


# --- the real package managers -----------------------------------------------

REAL_CASES = [
    "pnpm test", "pnpm -w test", "pnpm -w build", "pnpm -r test",
    "pnpm recursive run test", "pnpm m run test", "pnpm --filter x test",
    "pnpm --filter-prod x test", "pnpm --workspace-concurrency 1 -r test",
    "pnpm --resume-from x -r test", "pnpm run --filter x test",
    "pnpm run --filter x build", "npm test", "npm -w x test",
    "npm --script-shell sh test", "npm run --workspace x test",
    "npm run build", "yarn test", "yarn workspace x test",
    "yarn workspace x run test", "yarn workspaces run test", "yarn build",
    "bash -c pnpm\\ test",
]


def _workspace(root: Path) -> None:
    def w(rel: str, text: str) -> None:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    scripts = {"test": "echo RAN-TEST", "build": "echo RAN-BUILD"}
    w("package.json", json.dumps({"name": "root", "version": "1.0.0",
                                  "private": True,
                                  "workspaces": ["packages/*"],
                                  "scripts": scripts}))
    w("pnpm-workspace.yaml", "packages:\n  - 'packages/*'\n")
    w("packages/x/package.json", json.dumps(
        {"name": "x", "version": "1.0.0", "scripts": scripts}))


@pytest.mark.skipif(not all(shutil.which(t) for t in ("pnpm", "npm", "yarn")),
                    reason="pnpm, npm and yarn are not all installed")
def test_matcher_agrees_with_the_real_package_managers(leerie, tmp_path):
    root = tmp_path / "ws"
    _workspace(root)
    env = {**os.environ, "npm_config_cache": str(tmp_path / "npm-cache"),
           "npm_config_update_notifier": "false",
           "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    wrong = []
    for cmd in REAL_CASES:
        r = subprocess.run(["bash", "-c", cmd], cwd=str(root), env=env,
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL, check=False)
        ran = "RAN-TEST" in r.stdout + r.stderr
        if bool(leerie._forbidden_command_hit(cmd, FORBIDDEN)) is not ran:
            wrong.append((cmd, ran, (r.stdout + r.stderr)[-300:]))
    assert not wrong, wrong
