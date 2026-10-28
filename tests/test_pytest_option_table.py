"""leerie's pytest option table is derived from pytest's own parser, not
written by hand (review r2 M3: the hand table listed the flag `--cov-branch`
as value-taking, so `pytest --cov=src --cov-branch tests` kept `tests` as
the option's value and the inferred scoped template ran the whole suite
beside every held-out set).

`tests/fixtures/pytest_options.json` is a capture of the argparse actions
of pytest 9.2 with pytest-cov, pytest-xdist, pytest-timeout,
pytest-rerunfailures and pytest-randomly loaded (`nargs == 0` is a flag).
The live test re-reads the parser of the pytest running this suite, so a
fixture that drifted from real pytest fails here too."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures"
                      / "pytest_options.json").read_text())


def test_the_value_table_is_the_parsers_value_options(leerie):
    assert leerie._PYTEST_VALUE_OPTS == frozenset(FIXTURE["value"])
    assert leerie._SCOPED_RUNNER_VALUE_OPTS["pytest"] is \
        leerie._PYTEST_VALUE_OPTS


def test_the_long_flag_table_is_the_parsers_long_flags(leerie):
    assert leerie._PYTEST_LONG_FLAGS == frozenset(
        f for f in FIXTURE["flag"] if f.startswith("--"))


def test_no_value_table_entry_is_a_flag(leerie):
    assert sorted(leerie._PYTEST_VALUE_OPTS & set(FIXTURE["flag"])) == []
    assert "--cov-branch" not in leerie._PYTEST_VALUE_OPTS
    assert "--cov-branch" in leerie._PYTEST_LONG_FLAGS


def test_the_fixture_agrees_with_the_running_pytest(request):
    """Every option the running pytest's parser knows that the fixture also
    names has the same arity there — so the fixture is real pytest, not a
    second hand-written table."""
    actions = request.config._parser.optparser._actions
    live: dict[str, bool] = {}
    for a in actions:
        for o in a.option_strings:
            live[o] = a.nargs != 0
    known = set(FIXTURE["value"]) | set(FIXTURE["flag"])
    shared = sorted(o for o in live if o in known)
    assert len(shared) > 60          # pytest's own options at least
    wrong = [o for o in shared
             if live[o] != (o in set(FIXTURE["value"]))]
    assert wrong == []


@pytest.mark.parametrize("canonical,template", [
    ("pytest --cov=src --cov-branch tests",
     "pytest --cov=src --cov-branch {test_files}"),
    ("pytest -x --cov-branch tests", "pytest -x --cov-branch {test_files}"),
    ("poetry run pytest --cov=app --cov-branch --cov-report=term-missing "
     "tests/",
     "poetry run pytest --cov=app --cov-branch --cov-report=term-missing "
     "{test_files}"),
    ("pytest --no-cov tests", "pytest --no-cov {test_files}"),
    ("pytest --reuse-db tests", None),     # unknown plugin option
])
def test_a_flag_never_keeps_the_test_root(leerie, canonical, template):
    got, _why = leerie._infer_scoped_test_template(canonical)
    assert got == template
    if got is not None:
        rendered = got.replace("{test_files}", "tests/test_set.py")
        assert leerie._forbidden_command_hit(
            rendered, [{"command": "pytest", "scope": "whole_suite",
                        "test_roots": ["tests"]}]) is None
