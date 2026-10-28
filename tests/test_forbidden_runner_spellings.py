"""The forbidden-command matcher reads every common spelling of a runner
(DESIGN §9 *A command the task forbids*, *One runner, every spelling*).

Round-3 review M3: since 48e9120 this matcher is the only check on CI
parity, recipe and coverage launches, yet `vitest run` read `run` as a
test filter and `python -m pytest`, `uv run pytest`, `poetry run pytest`
were not `pytest` at all — so a task forbidding the whole suite still ran
it through the CI spelling. The reviewer's drivers (`gate_probe3.py`,
`gate_probe4.py`) are kept below with their assertions inverted.

The option tables are derived from each tool's real `--help` and pinned
against `tests/fixtures/forbid_runner_options.json`. The semantics rows
come from running the real runners (vitest 5.0, jest 30.5, mocha 12.0,
pytest, go 1.25, cargo 1.99, rspec 3.13) on a two-file project whose files
each write a marker when loaded; the unwrapping rows from running each
spelling through real bash with argv-printing stubs. The real-bash checks
below repeat that for the tools present on this host and skip the rest.
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

TABLES = {
    "vitest": "_VITEST_VALUE_OPTS", "jest": "_JEST_VALUE_OPTS",
    "mocha": "_MOCHA_VALUE_OPTS", "go test": "_GO_TEST_VALUE_OPTS",
    "cargo test": "_CARGO_TEST_VALUE_OPTS", "libtest": "_LIBTEST_VALUE_OPTS",
    "rspec": "_RSPEC_VALUE_OPTS", "rails test": "_RAILS_TEST_VALUE_OPTS",
    "python": "_PYTHON_VALUE_OPTS", "node": "_NODE_VALUE_OPTS",
    "uv": "_UV_VALUE_OPTS", "uv run": "_UV_RUN_VALUE_OPTS",
    "uvx": "_UVX_VALUE_OPTS", "poetry run": "_POETRY_VALUE_OPTS",
    "pipenv": "_PIPENV_VALUE_OPTS", "pipenv run": "_PIPENV_RUN_VALUE_OPTS",
    "hatch": "_HATCH_VALUE_OPTS", "hatch run": "_HATCH_RUN_VALUE_OPTS",
    "pdm": "_PDM_VALUE_OPTS", "pdm run": "_PDM_RUN_VALUE_OPTS",
    "rye run": "_RYE_RUN_VALUE_OPTS", "bundle exec": "_BUNDLE_EXEC_VALUE_OPTS",
}


def _ws(cmd: str) -> list[dict]:
    return [{"command": cmd, "scope": "whole_suite", "test_roots": ["tests"]}]


def _refused(leerie, argv, forbidden) -> bool:
    try:
        leerie._blt_shell_argv(argv, forbidden)
    except leerie.ForbiddenByTask:
        return True
    return False


# --- the derived tables -------------------------------------------------------

@pytest.mark.parametrize("tool", sorted(TABLES))
def test_each_table_is_the_tools_own_value_options(leerie, tool):
    assert getattr(leerie, TABLES[tool]) == frozenset(
        FIXTURE[tool]["value_opts"])
    assert not getattr(leerie, TABLES[tool]) & set(FIXTURE[tool]["flags"])


def test_the_runner_tables_use_the_derived_option_tables(leerie):
    R = leerie._FORBID_RUNNERS
    assert R[("pytest",)]["value"] is leerie._PYTEST_VALUE_OPTS
    assert R[("vitest",)]["value"] is leerie._VITEST_VALUE_OPTS
    assert R[("cargo", "test")]["after_dashdash"] is \
        leerie._LIBTEST_VALUE_OPTS
    for key in [("jest",), ("mocha",), ("go", "test"), ("rspec",),
                ("rails", "test")]:
        assert R[key]["value"] is getattr(
            leerie, TABLES[" ".join(key)])


# --- the reviewer's drivers, inverted ----------------------------------------

@pytest.mark.parametrize("forb, argv", [
    ("vitest", ["npx", "vitest", "run"]),
    ("vitest", ["pnpm", "exec", "vitest", "run"]),
    ("vitest", ["pnpm", "vitest", "run"]),
    ("vitest", ["node_modules/.bin/vitest", "run"]),
    ("vitest", ["yarn", "vitest"]),
    ("vitest", ["npm", "exec", "--", "vitest", "run"]),
    ("vitest", ["node", "node_modules/vitest/vitest.mjs", "run"]),
    ("vitest", ["vitest"]),
    ("vitest", ["vitest", "run"]),
    ("vitest", ["npx", "vitest"]),
    ("vitest", ["vitest", "watch"]),
    ("vitest", ["bash", "-e", "-c", "pnpm vitest run --coverage"]),
    ("vitest run", ["npx", "vitest", "run"]),
    ("vitest run", ["pnpm", "exec", "vitest", "run"]),
    ("vitest run", ["vitest"]),
    ("pytest", ["python", "-m", "pytest"]),
    ("pytest", ["python3", "-m", "pytest", "-q"]),
    ("pytest", ["uv", "run", "pytest"]),
    ("pytest", ["poetry", "run", "pytest"]),
    ("pytest", ["pipenv", "run", "pytest"]),
    ("pytest", ["hatch", "run", "pytest"]),
    ("pytest", ["pdm", "run", "pytest"]),
    ("pytest", ["rye", "run", "pytest"]),
    ("pnpm test", ["pnpm", "test"]),
    ("pnpm test", ["pnpm", "run", "test"]),
    ("pnpm test", ["npm", "test"]),
    ("python -m pytest", ["pytest"]),
    ("python -m pytest", ["uv", "run", "python", "-m", "pytest"]),
])
def test_reviewer_spellings_are_refused(leerie, forb, argv):
    assert _refused(leerie, argv, _ws(forb)), (forb, argv)


@pytest.mark.parametrize("argv", [
    ["git", "commit", "-m", "npm test"],
    ["grep", "-r", "npm test"],
    ["echo", "npm", "test"],
    ["npm", "run", "lint", "--", "--ignore-pattern", "npm test"],
    ["node", "x.js", "npm test"],
    ["echo", "python", "-m", "pytest"],
    ["uv", "sync"],
    ["poetry", "install"],
    ["python3", "scripts/x.py", "-m", "pytest"],
])
def test_reviewer_negatives_are_not_refused(leerie, argv):
    forbidden = _ws("npm test") + _ws("pytest") + [
        {"command": "vitest", "scope": "any"}]
    assert not _refused(leerie, argv, forbidden), argv


# --- the semantics each real runner showed ------------------------------------

@pytest.mark.parametrize("forb, cmd, hit", [
    # vitest: run/watch/dev run the suite; related <files> is scoped
    ("vitest", "vitest run", True),
    ("vitest", "vitest run --reporter verbose", True),
    ("vitest", "vitest --reporter dot run", True),
    ("vitest", "vitest run --maxWorkers 1", True),
    ("vitest", "vitest run -t b", True),
    ("vitest", "vitest run --globals false", True),
    ("vitest", "vitest run --isolate false", True),
    ("vitest", "vitest -- run", True),
    ("vitest", "vitest run -- tests/b.test.mjs", True),
    ("vitest", "vitest run tests", True),
    ("vitest", "vitest run tests/b.test.mjs", False),
    ("vitest", "vitest run --bail 1 tests/b.test.mjs", False),
    ("vitest", "vitest run --globals true tests/b.test.mjs", False),
    ("vitest", "vitest related src/a.mjs --run", False),
    ("vitest", "vitest run --changed", False),
    ("vitest", "vitest run --project x", False),
    # jest
    ("jest", "jest --ci", True),
    ("jest", "jest -w 1", True),
    ("jest", "jest --ci false", True),
    ("jest", "jest -t b", True),
    ("jest", "jest --ci true tests/b.test.js", False),
    ("jest", "jest --testPathPatterns b.test", False),
    ("jest", "jest --findRelatedTests tests/a.test.js", False),
    ("jest", "jest -i tests/a.test.js", False),
    # mocha does not read true/false as a flag's value
    ("mocha", "mocha -R dot", True),
    ("mocha", "mocha --grep b", True),
    ("mocha", "mocha --exit false", False),
    ("mocha", "mocha --spec test/b.spec.js", False),
    ("mocha", "mocha -t 5000 test/a.spec.js", False),
    # pytest and its module / wrapper spellings
    ("pytest", "python3 -Bm pytest -x", True),
    ("pytest", "uv run --with pytest-cov pytest -q", True),
    ("pytest", "uv run -m pytest", True),
    ("pytest", "uvx --from pytest pytest", True),
    ("pytest", "hatch run test:pytest", True),
    ("pytest", "hatch -e test run +py=3.12 pytest", True),
    ("pytest", "pdm run -p . pytest", True),
    ("pytest", "poetry -C . run pytest -k b", True),
    ("pytest", "python3 -m pytest -q tests/test_b.py", False),
    ("pytest", "uv run pytest tests/test_a.py", False),
    # go test: ./... names the whole suite; a name filter still loads it
    ("go test", "go test ./...", True),
    ("go test", "go test -run TestB ./...", True),
    ("go test", "go test -count 1 -timeout 30s ./...", True),
    ("go test", "go test ./a", False),
    ("go test", "go test -coverprofile c.out ./a", False),
    # cargo test, with libtest's options behind --
    ("cargo test", "cargo test", True),
    ("cargo test", "cargo t -q", True),
    ("cargo test", "cargo test -j 1", True),
    ("cargo test", "cargo test -- --test-threads 1", True),
    ("cargo test", "cargo test -- --skip t_a", True),
    ("cargo test", "cargo test --test a", False),
    ("cargo test", "cargo test t_b", False),
    # rspec
    ("rspec", "rspec -f d", True),
    ("rspec", "rspec --tag foo", True),
    ("rspec", "bundle exec rspec spec", True),
    ("rspec", "rspec -P 'spec/b_*'", False),
    ("rspec", "rspec spec/b_spec.rb", False),
    # rails test (option table derived; not run — it needs an app)
    ("rails test", "bin/rails test", True),
    ("rails test", "rails test:all", True),
    ("rails test", "rails t -n test_x", True),
    ("rails test", "rails test test/models/user_test.rb", False),
])
def test_runner_semantics(leerie, forb, cmd, hit):
    roots = {"mocha": ["test"], "rspec": ["spec"],
             "rails test": ["test"]}.get(forb, ["tests"])
    spec = [{"command": forb, "scope": "whole_suite", "test_roots": roots}]
    assert bool(leerie._forbidden_command_hit(cmd, spec)) is hit, cmd


# --- real bash with argv-printing stubs --------------------------------------

STUB = ("#!{py}\nimport json, os, sys\n"
        "print('STUB' + json.dumps([os.path.basename(sys.argv[0])]"
        " + sys.argv[1:]))\n")


@pytest.fixture
def stubbed(tmp_path):
    bin_ = tmp_path / "bin"
    mods = tmp_path / "mods"
    bin_.mkdir()
    mods.mkdir()
    for r in ("pytest", "vitest"):
        (bin_ / r).write_text(STUB.format(py=sys.executable))
        (bin_ / r).chmod(0o755)
    (mods / "pytest.py").write_text(
        "import json, sys\nprint('STUB' + json.dumps(['pytest'] "
        "+ sys.argv[1:]))\n")
    nm = tmp_path / "node_modules"
    (nm / ".bin").mkdir(parents=True)
    (nm / ".bin" / "vitest").symlink_to(bin_ / "vitest")
    (tmp_path / "package.json").write_text(json.dumps(
        {"name": "x", "version": "1.0.0", "private": True}))
    env = dict(os.environ, PYTHONPATH=str(mods),
               PATH=os.pathsep.join([str(bin_), os.environ["PATH"]]))
    return tmp_path, env


def _ran(cmd: str, ws: Path, env: dict) -> list[list[str]]:
    r = subprocess.run(["bash", "-c", cmd], cwd=ws, env=env, text=True,
                       capture_output=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    return [json.loads(line[4:]) for line in (r.stdout + r.stderr)
            .splitlines() if line.startswith("STUB[")]


@pytest.mark.parametrize("cmd", [
    "python3 -m pytest", "python3 -B -m pytest -q tests/a.py",
    "python3 -Bm pytest", "python3 -mpytest -x",
    "python3 -W ignore -m pytest", "python3 -X dev -m pytest tests",
    "python3 -Wignore -m pytest", "env PYTHONDONTWRITEBYTECODE=1 "
    "python3 -m pytest", "timeout 60 python3 -m pytest",
    "bash -c 'python3 -m pytest -q'",
])
def test_python_module_spellings_through_real_bash(leerie, stubbed, cmd):
    ws, env = stubbed
    env = dict(env, PATH=os.pathsep.join(
        [str(ws / "bin"), os.path.dirname(sys.executable),
         env["PATH"]]))
    ran = _ran(cmd.replace("python3", sys.executable, 1)
               if cmd.startswith("python3") else cmd, ws, env)
    assert ran, cmd
    assert all(e in leerie._forbidden_argvs(cmd) for e in ran), (ran, cmd)
    scoped = "tests/a.py" in cmd
    assert bool(leerie._forbidden_command_hit(cmd, _ws("pytest"))) is \
        (not scoped)


WRAPPED = [
    ("uv", "uv run --no-project pytest -q"),
    ("uv", "uv run --no-project --with-requirements /dev/null pytest"),
    ("npx", "npx vitest run"),
    ("npm", "npm exec vitest -- run"),
    ("npm", "npm exec -- vitest -- run"),
    ("pnpm", "pnpm vitest run"),
    ("pnpm", "pnpm exec vitest -- run"),
    ("yarn", "yarn vitest -- run"),
    ("yarn", "yarn exec vitest -- run"),
]


@pytest.mark.parametrize("tool, cmd", WRAPPED)
def test_wrapper_spellings_through_real_bash(leerie, stubbed, tool, cmd):
    """The argv the runner actually received is one the matcher reads."""
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed here")
    ws, env = stubbed
    env = dict(env, UV_CACHE_DIR=str(ws / ".uvcache"),
               npm_config_update_notifier="false", CI="1",
               COREPACK_ENABLE_DOWNLOAD_PROMPT="0")
    ran = _ran(cmd, ws, env)
    if not ran:
        pytest.skip(f"{cmd!r} did not reach the stub here")
    assert all(e in leerie._forbidden_argvs(cmd) for e in ran), (ran, cmd)
