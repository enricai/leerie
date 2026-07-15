"""Tests for resolve_aws_region() and resolve_aws_profile().

Covers the CLI value → env var → per-repo file → None resolution order,
mirroring tests/test_resolve_runtime.py's structure but for the
free-form-string _resolve_str_pref machinery (like resolve_pr_template),
not the enum-validated _resolve_enum_pref machinery. No die() path: these
are unvalidated strings.

Also pins the argparse wiring itself (--aws-region / --aws-profile): the
resolver functions' `cli_value` parameter was already fully wired before
this file's argparse pins were added, but no flag existed to ever
populate it, leaving the documented top tier of the precedence chain
(docs/IMPLEMENTATION.md's "AWS region/profile prefs" section) unreachable
dead code. Since `main()`'s argparse setup lives inline (no standalone
build_arg_parser() to import), the wiring is pinned via source-coupling
(mirroring tests/test_dep_capture_wiring.py's inspect.getsource approach)
rather than by constructing and parsing a real ArgumentParser.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    """An empty repo-root directory with the AWS env prefs unset."""
    monkeypatch.delenv("LEERIE_AWS_REGION", raising=False)
    monkeypatch.delenv("LEERIE_AWS_PROFILE", raising=False)
    return tmp_path


# ---- resolve_aws_region ----------------------------------------------

def test_region_default_is_none(leerie, repo_root):
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_file_present_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_file_absent_env_set(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"


def test_region_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"


def test_region_cli_value_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_region = us-east-1\n")
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root, cli_value="eu-west-1") == "eu-west-1"


def test_region_cli_value_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    assert leerie.resolve_aws_region(repo_root, cli_value=None) == "us-west-2"


def test_region_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text('aws_region = "us-east-1"\n')
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_single_quoted_file_value(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_region = 'us-east-1'\n")
    assert leerie.resolve_aws_region(repo_root) == "us-east-1"


def test_region_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "")
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "   ")
    assert leerie.resolve_aws_region(repo_root) is None


def test_region_no_validation_of_value(leerie, repo_root):
    # Free-form string — no enum check, unlike resolve_runtime.
    (repo_root / "leerie.toml").write_text("aws_region = not-a-real-region\n")
    assert leerie.resolve_aws_region(repo_root) == "not-a-real-region"


# ---- resolve_aws_profile ----------------------------------------------

def test_profile_default_is_none(leerie, repo_root):
    assert leerie.resolve_aws_profile(repo_root) is None


def test_profile_file_present_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("aws_profile = leerie-ec2\n")
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"


def test_profile_file_absent_env_set(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "leerie-ec2")
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"


def test_profile_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_profile = file-profile\n")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root) == "env-profile"


def test_profile_cli_value_wins_over_env_and_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("aws_profile = file-profile\n")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root, cli_value="cli-profile") == "cli-profile"


def test_profile_cli_value_none_falls_back(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "env-profile")
    assert leerie.resolve_aws_profile(repo_root, cli_value=None) == "env-profile"


def test_profile_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "")
    assert leerie.resolve_aws_profile(repo_root) is None


def test_profile_whitespace_only_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "   ")
    assert leerie.resolve_aws_profile(repo_root) is None


def test_region_and_profile_are_independent(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_AWS_REGION", "us-west-2")
    monkeypatch.setenv("LEERIE_AWS_PROFILE", "leerie-ec2")
    assert leerie.resolve_aws_region(repo_root) == "us-west-2"
    assert leerie.resolve_aws_profile(repo_root) == "leerie-ec2"


# ---- argparse wiring: the CLI tier must actually be reachable ----------
#
# The resolver-level tests above prove `cli_value` beats env/file/default
# when populated — but until an argparse flag exists to populate it, that
# tier is unreachable dead code (the exact drift this subtask closes).
# main()'s argparse setup is inline (no standalone build_arg_parser()), so
# behaviorally invoking argument parsing means invoking main() itself,
# which performs full orchestration. Source-coupling is the correct tier
# here, mirroring tests/test_dep_capture_wiring.py's inspect.getsource
# approach for wiring seams that can't be behaviorally driven in isolation.

class TestArgparseFlagsWired:
    """--aws-region / --aws-profile must exist as real argparse flags and
    their parsed values must be threaded through resolve_aws_region() /
    resolve_aws_profile() before use, exactly like --runtime is threaded
    through resolve_runtime()."""

    def _main_source(self, leerie) -> str:
        return inspect.getsource(leerie.main)

    def test_aws_region_flag_registered(self, leerie):
        src = self._main_source(leerie)
        assert 'add_argument("--aws-region"' in src

    def test_aws_profile_flag_registered(self, leerie):
        src = self._main_source(leerie)
        assert 'add_argument("--aws-profile"' in src

    def test_aws_region_resolved_from_parsed_args(self, leerie):
        src = self._main_source(leerie)
        assert "resolve_aws_region(" in src
        assert "args.aws_region = resolve_aws_region(" in src

    def test_aws_profile_resolved_from_parsed_args(self, leerie):
        src = self._main_source(leerie)
        assert "args.aws_profile = resolve_aws_profile(" in src

    def test_aws_region_flag_precedes_resolution_call(self, leerie):
        # Ordering guard: the flag must be registered on the parser before
        # the resolution call site — otherwise `args.aws_region` wouldn't
        # exist yet (argparse.Namespace has no attribute until parse_args()
        # runs against a parser that declares the flag).
        src = self._main_source(leerie)
        flag_pos = src.index('add_argument("--aws-region"')
        resolve_pos = src.index("args.aws_region = resolve_aws_region(")
        assert flag_pos < resolve_pos

    def test_aws_profile_flag_precedes_resolution_call(self, leerie):
        src = self._main_source(leerie)
        flag_pos = src.index('add_argument("--aws-profile"')
        resolve_pos = src.index("args.aws_profile = resolve_aws_profile(")
        assert flag_pos < resolve_pos

    def test_no_choices_validation_on_cli_flags(self, leerie):
        # Unlike --runtime (choices=RUNTIME_VALUES), these are free-form
        # strings — argparse must not constrain them via choices=.
        src = self._main_source(leerie)
        region_start = src.index('add_argument("--aws-region"')
        region_call = src[region_start:src.index(")", src.index("help=", region_start))]
        assert "choices=" not in region_call
        profile_start = src.index('add_argument("--aws-profile"')
        profile_call = src[profile_start:src.index(")", src.index("help=", profile_start))]
        assert "choices=" not in profile_call
