"""Selectors that select everything, version-suffixed proxy packages and the
`xvfb-run` wrapper (DESIGN §9 *A selector reads as scoped unless it selects
everything*, *One runner, every spelling*).

Round-4 review L2: `npx --yes vitest@3 run` and `xvfb-run -a vitest run` ran
past a whole-suite vitest forbid. L3: selector options (`--project`,
`--selectProjects`, cargo `-p`/`--lib`, rspec `-P`) read as scoped even when
they select the whole suite. The decisions are per runner, from the real
CLIs; the rows below were measured through real bash with the real runners
(fixture key "selectors and proxies").
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures"
                      / "forbid_runner_options.json").read_text())
ROWS = FIXTURE["selectors and proxies"]["rows"]
ROOTS = {"vitest": ["tests"], "vitestproj": ["tests"], "jest": ["tests"],
         "jestproj": ["tests"], "mocha": ["test"], "rspec": ["spec"],
         "cargo": ["tests"], "cargows": [], "pytest": ["tests"]}
# Whole-suite runs only the runner's config or state can tell apart from a
# subset (DESIGN §9, "What it cannot see"): documented limits, pinned so a
# change that starts catching one is noticed and the doc updated.
KNOWN_MISSES = {
    ("vitestproj", "vitest run --project unit --project e2e"),
    ("jestproj", "jest --selectProjects unit e2e"),
    ("cargo", "cargo test -p ct"),
    ("cargows", "cargo test -p 'p*'"),
    ("cargows", "cargo test -p p1 -p p2"),
    ("cargows", "cargo test --lib"),
}


def _ws(cmd: str, roots: list[str]) -> list[dict]:
    return [{"command": cmd, "scope": "whole_suite", "test_roots": roots}]


@pytest.mark.parametrize("row", ROWS,
                         ids=[f"{r['project']}|{r['cmd']}" for r in ROWS])
def test_matcher_agrees_with_the_real_runner(leerie, row):
    hit = bool(leerie._forbidden_command_hit(
        row["cmd"], _ws(row["forbid"], ROOTS[row["project"]])))
    if (row["project"], row["cmd"]) in KNOWN_MISSES:
        assert row["whole"] and not hit, row
    else:
        assert hit is row["whole"], row


def test_rows_cover_selectors_both_ways():
    by = {(r["project"], r["cmd"]): r["whole"] for r in ROWS}
    assert by[("vitestproj", "vitest run --project unit")] is False
    assert by[("vitestproj", "vitest run --project '*'")] is True
    assert by[("jestproj", "jest --ignoreProjects e2e")] is False
    assert by[("jestproj", "jest --runTestsByPath")] is True
    assert by[("cargows", "cargo test -p '*'")] is True
    assert by[("cargows", "cargo test -p p1")] is False


# --- the reviewer's pnpm_probe.py rows, inverted -----------------------------

@pytest.mark.parametrize("cmd", [
    "xvfb-run -a vitest run", "npx --yes vitest@3 run", "npx -y vitest run",
    "npx -y vitest@3.2.1 run", "pnpm dlx vitest@3 run", "yarn dlx vitest@3 run",
    "bunx vitest@3 run", "bunx --bun vitest@latest run",
    "npm exec --yes vitest@3 -- run", "npx -p vitest@3 vitest run",
    "xvfb-run -as '-screen 0 1x1x8' npx -y vitest@3 run",
    "xvfb-run --server-args '-screen 0 1x1x8' -- vitest",
    "xvfb-run -e /dev/null -n 99 vitest run", "xvfb-run -w 1 vitest run",
])
def test_proxy_and_xvfb_spellings_are_refused(leerie, cmd):
    assert leerie._forbidden_command_hit(cmd, _ws("vitest", ["src"])), cmd


@pytest.mark.parametrize("cmd", [
    "xvfb-run -a vitest run src/a.test.ts",
    "npx -y vitest@3 run src/a.test.ts",
    "xvfb-run -s vitest run",  # `-s` takes `vitest`: the binary is `run`
])
def test_scoped_and_value_spellings_pass(leerie, cmd):
    assert not leerie._forbidden_command_hit(cmd, _ws("vitest", ["src"])), cmd


@pytest.mark.parametrize("word, pkg", [
    ("vitest@3", "vitest"), ("vitest@^3.2", "vitest"), ("vitest", "vitest"),
    ("@vitest/ui@1.0.0", "@vitest/ui"), ("@scope/pkg", "@scope/pkg"),
    ("pnpm@9.1.0", "pnpm"),
])
def test_package_word(leerie, word, pkg):
    assert leerie._forbidden_package_word(word) == pkg


def test_corepack_pinned_manager_reads_as_the_manager(leerie):
    forb = [{"command": "pnpm test", "scope": "any"}]
    assert leerie._forbidden_command_hit("corepack pnpm@9.1.0 test", forb)


# --- selector decisions ------------------------------------------------------

@pytest.mark.parametrize("forb, roots, cmd, hit", [
    ("vitest", ["tests"], "vitest run --project unit", False),
    ("vitest", ["tests"], "vitest run --project '*'", True),
    ("vitest", ["tests"], "vitest run --project='**'", True),
    ("vitest", ["tests"], "vitest run --project 'u*'", False),
    ("vitest", ["tests"], "vitest run --project '*' tests/a.test.ts", False),
    ("jest", ["tests"], "jest --ignoreProjects e2e", False),
    ("jest", ["tests"], "jest --runTestsByPath", True),
    ("jest", ["tests"], "jest --runTestsByPath tests/a.test.js", False),
    ("cargo test", [], "cargo test -p '*'", True),
    ("cargo test", [], "cargo test --package=*", True),
    ("cargo test", [], "cargo test -p core", False),
    ("cargo test", [], "cargo test --lib", False),
    ("mocha", ["test"], "mocha --spec test", True),
    ("mocha", ["test"], "mocha --spec=test/", True),
    ("mocha", ["test"], "mocha --spec 'test/**/*.spec.js'", True),
    ("mocha", ["test"], "mocha --spec 'test/**/a.spec.js'", False),
    ("mocha", ["test"], "mocha --spec test/a.spec.js", False),
    ("rspec", ["spec"], "rspec -P 'spec/**{,/*/**}/*_spec.rb'", True),
    ("rspec", ["spec"], "rspec --pattern '**/*_spec.rb'", True),
    ("rspec", ["spec"], "rspec -P 'spec/a_*'", False),
    ("rspec", ["spec"], "rspec -P 'lib/**/*_spec.rb'", False),
])
def test_selector_semantics(leerie, forb, roots, cmd, hit):
    assert bool(leerie._forbidden_command_hit(cmd, _ws(forb, roots))) is hit


# --- real bash: xvfb-run hands the matcher's argv to the runner -------------

STUB = ("#!{py}\nimport json, sys\n"
        "print('STUB' + json.dumps(sys.argv[1:]), flush=True)\n")


@pytest.mark.parametrize("cmd", [
    "xvfb-run -a vitest run", "xvfb-run -a -s '-screen 0 1x1x8' vitest run",
    "xvfb-run -as '-screen 0 1x1x8' vitest run x.test.ts",
    "xvfb-run -a -s'-screen 0 1x1x8' vitest",
    "xvfb-run --auto-servernum --server-args='-screen 0 1x1x8' vitest run",
    "xvfb-run --auto-servernum --server-args '-screen 0 1x1x8' vitest run",
    "xvfb-run -a -- vitest run", "xvfb-run -a -e /dev/null vitest run",
    "xvfb-run -a -w 0 vitest run x.test.ts",
])
def test_xvfb_run_through_real_bash(leerie, tmp_path, cmd):
    if shutil.which("xvfb-run") is None or shutil.which("Xvfb") is None:
        pytest.skip("xvfb-run is not installed here")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "vitest").write_text(STUB.format(py=sys.executable))
    (bin_ / "vitest").chmod(0o755)
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_),
                                                 os.environ["PATH"]]))
    r = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, env=env,
                       text=True, capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    ran = [json.loads(line[4:]) for line in (r.stdout + r.stderr)
           .splitlines() if line.startswith("STUB[")]
    if not ran:
        pytest.skip(f"{cmd!r} did not reach the stub here: {r.stderr[-200:]}")
    assert ["vitest", *ran[0]] in leerie._forbidden_argvs(cmd), (cmd, ran)
