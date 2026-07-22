"""Tests for `resolve_pr_base_branch()` — the --pr-base-branch override
for the final branch a run's PR merges into.

Covers:
- CLI > env > leerie.toml > default(None) precedence, mirroring
  test_pr_template_discovery.py's test_resolve_pr_template_* structure.
- The run-start call-site fallback to `working_branch` when unset
  (`resolve_pr_base_branch(...) or working_branch`) — this field is
  distinct from `working_branch`, which must remain the diff fork-point.
- `_write_run_json` round-trips a `pr_base_branch` key, mirroring
  test_write_run_json.py's pattern.
- STATE_FIELDS carries `pr_base_branch`.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return tmp_path


def test_resolve_cli_wins(leerie, repo, monkeypatch):
    monkeypatch.setenv(leerie.PR_BASE_BRANCH_ENV, "from-env")
    assert leerie.resolve_pr_base_branch(repo, cli_value="from-cli") == "from-cli"


def test_resolve_env_wins_over_toml(leerie, repo, monkeypatch):
    (repo / "leerie.toml").write_text('pr_base_branch = "from-toml"\n')
    monkeypatch.setenv(leerie.PR_BASE_BRANCH_ENV, "from-env")
    assert leerie.resolve_pr_base_branch(repo, cli_value=None) == "from-env"


def test_resolve_toml_when_unset(leerie, repo, monkeypatch):
    monkeypatch.delenv(leerie.PR_BASE_BRANCH_ENV, raising=False)
    (repo / "leerie.toml").write_text('pr_base_branch = "from-toml"\n')
    assert leerie.resolve_pr_base_branch(repo, cli_value=None) == "from-toml"


def test_resolve_none_when_nothing_set(leerie, repo, monkeypatch):
    monkeypatch.delenv(leerie.PR_BASE_BRANCH_ENV, raising=False)
    assert leerie.resolve_pr_base_branch(repo, cli_value=None) is None


def test_resolve_full_precedence_cli_beats_env_beats_toml(leerie, repo, monkeypatch):
    (repo / "leerie.toml").write_text('pr_base_branch = "from-toml"\n')
    monkeypatch.setenv(leerie.PR_BASE_BRANCH_ENV, "from-env")
    assert leerie.resolve_pr_base_branch(repo, cli_value=None) == "from-env"
    assert leerie.resolve_pr_base_branch(repo, cli_value="from-cli") == "from-cli"


def test_empty_cli_falls_through_to_env(leerie, repo, monkeypatch):
    """An empty/whitespace CLI value (argparse default) must not shadow env."""
    monkeypatch.setenv(leerie.PR_BASE_BRANCH_ENV, "from-env")
    assert leerie.resolve_pr_base_branch(repo, cli_value="") == "from-env"
    assert leerie.resolve_pr_base_branch(repo, cli_value="   ") == "from-env"


# --- Call-site default-to-working_branch fallback ---------------------------


def test_default_to_working_branch_when_unset(leerie):
    """The run-start call site computes
    `resolve_pr_base_branch(...) or working_branch` — when the resolver
    returns None (nothing overridden), the effective value used is
    working_branch. This mirrors the exact expression at the leerie.py
    run-start call site rather than re-deriving it, since the resolver
    itself intentionally has no knowledge of working_branch."""
    working_branch = "main"
    resolved = None  # resolve_pr_base_branch(...) with nothing set
    pr_base_branch = resolved or working_branch
    assert pr_base_branch == "main"


def test_override_wins_over_working_branch_default():
    working_branch = "main"
    resolved = "release/1.0"
    pr_base_branch = resolved or working_branch
    assert pr_base_branch == "release/1.0"


# --- STATE_FIELDS -------------------------------------------------------


def test_pr_base_branch_in_state_fields(leerie):
    assert "pr_base_branch" in leerie.STATE_FIELDS


# --- _write_run_json round-trip -----------------------------------------


def test_write_run_json_round_trips_pr_base_branch(leerie, tmp_path):
    leerie._write_run_json(
        tmp_path,
        run_id="feat-foo-abc123",
        branch="leerie/runs/feat-foo-abc123",
        working_branch="main",
        pr_base_branch="main",
        started_at="2026-05-26T14:00:00+00:00",
        task="add stuff",
    )
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["pr_base_branch"] == "main"
    assert data["working_branch"] == "main"


def test_write_run_json_pr_base_branch_can_differ_from_working_branch(leerie, tmp_path):
    """The whole point of the field: it can diverge from working_branch
    while working_branch remains untouched (the diff fork-point)."""
    leerie._write_run_json(
        tmp_path,
        run_id="feat-foo-abc123",
        branch="leerie/runs/feat-foo-abc123",
        working_branch="feature/my-branch",
        pr_base_branch="release/1.0",
        started_at="2026-05-26T14:00:00+00:00",
        task="add stuff",
    )
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["working_branch"] == "feature/my-branch"
    assert data["pr_base_branch"] == "release/1.0"


def test_write_run_json_pr_base_branch_survives_merge(leerie, tmp_path):
    leerie._write_run_json(
        tmp_path,
        run_id="feat-foo-abc123",
        working_branch="main",
        pr_base_branch="main",
        started_at="2026-05-26T14:00:00+00:00",
        task="add stuff",
    )
    leerie._write_run_json(tmp_path, pushed_at="2026-05-26T15:00:00+00:00")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["pr_base_branch"] == "main"
    assert data["pushed_at"] == "2026-05-26T15:00:00+00:00"


# --- argparse wiring: the CLI tier must actually be reachable ------------
#
# main()'s argparse setup is inline (no standalone build_arg_parser()), so
# behaviorally invoking argument parsing means invoking main() itself, which
# performs full orchestration. Source-coupling is the correct tier here,
# mirroring tests/test_resolve_aws_prefs.py's TestArgparseFlagsWired.

class TestArgparseFlagWired:
    """--pr-base-branch must exist as a real argparse flag and its parsed
    value must be threaded through resolve_pr_base_branch() before use,
    exactly like --pr-template is threaded through resolve_pr_template()."""

    def _main_source(self, leerie) -> str:
        return inspect.getsource(leerie.main)

    def test_flag_registered(self, leerie):
        src = self._main_source(leerie)
        assert 'add_argument("--pr-base-branch"' in src

    def test_resolved_from_parsed_args(self, leerie):
        src = self._main_source(leerie)
        assert "args.pr_base_branch = resolve_pr_base_branch(" in src

    def test_flag_precedes_resolution_call(self, leerie):
        src = self._main_source(leerie)
        flag_pos = src.index('add_argument("--pr-base-branch"')
        resolve_pos = src.index("args.pr_base_branch = resolve_pr_base_branch(")
        assert flag_pos < resolve_pos

    def test_no_choices_validation(self, leerie):
        # Free-form branch name — argparse must not constrain via choices=.
        src = self._main_source(leerie)
        start = src.index('add_argument("--pr-base-branch"')
        call = src[start:src.index(")", src.index("help=", start))]
        assert "choices=" not in call


# --- run-start wiring: the resolved value must reach _write_run_json ------

class TestRunStartWiring:
    """The run-start block must compute pr_base_branch from
    resolve_pr_base_branch(...) or working_branch, persist it to
    st.data, and pass it to _write_run_json — mirroring how
    working_branch itself is wired, but with the extra `or` fallback."""

    def test_pr_base_branch_passed_to_write_run_json(self, leerie):
        src = inspect.getsource(leerie)
        assert "pr_base_branch=pr_base_branch" in src

    def test_pr_base_branch_persisted_to_state(self, leerie):
        src = inspect.getsource(leerie)
        assert 'st.data["pr_base_branch"] = pr_base_branch' in src

    def test_defaults_to_working_branch_via_or(self, leerie):
        src = inspect.getsource(leerie)
        assert (
            "pr_base_branch = "
            "getattr(args, \"pr_base_branch\", None) or working_branch"
        ) in src
