"""Tests for the shared `_resolve_enum_pref` / `_resolve_str_pref`
primitives and their thin wrappers `resolve_judge_dir`, `resolve_heal_dir`,
and `resolve_dangerously_allow_uncapped`.

These mirror the existing `_resolve_bool_pref`-shape test files
(`test_resolve_dangerously_skip_permissions.py`, `test_resolve_no_push.py`)
but cover the two sibling resolvers (enum-valued, unvalidated-string) that
had no direct precedence/die() tests of their own — only indirectly via
callers like `resolve_runtime`/`resolve_pr_base_branch`.
"""
from __future__ import annotations

import pytest


# --- _resolve_enum_pref ---------------------------------------------------


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_TEST_ENUM", raising=False)
    return tmp_path


def _call_enum(leerie, repo_root, cli_value=None):
    return leerie._resolve_enum_pref(
        repo_root, cli_value,
        env_var="LEERIE_TEST_ENUM", file_key="test_enum",
        file_name="leerie.toml",
        allowed=("a", "b", "c"), default="a")


def test_enum_default_when_nothing_set(leerie, repo_root):
    assert _call_enum(leerie, repo_root) == "a"


def test_enum_cli_wins(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("test_enum = b\n")
    monkeypatch.setenv("LEERIE_TEST_ENUM", "c")
    assert _call_enum(leerie, repo_root, cli_value="a") == "a"


def test_enum_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("test_enum = c\n")
    monkeypatch.setenv("LEERIE_TEST_ENUM", "b")
    assert _call_enum(leerie, repo_root) == "b"


def test_enum_file_used_when_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("test_enum = c\n")
    assert _call_enum(leerie, repo_root) == "c"


def test_enum_full_precedence_cli_beats_env_beats_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("test_enum = c\n")
    monkeypatch.setenv("LEERIE_TEST_ENUM", "b")
    assert _call_enum(leerie, repo_root) == "b"
    assert _call_enum(leerie, repo_root, cli_value="a") == "a"


def test_enum_empty_env_treated_as_unset(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TEST_ENUM", "")
    assert _call_enum(leerie, repo_root) == "a"


def test_enum_empty_cli_falls_through(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TEST_ENUM", "b")
    assert _call_enum(leerie, repo_root, cli_value=None) == "b"


def test_enum_bad_env_value_dies(leerie, repo_root, monkeypatch, capsys):
    monkeypatch.setenv("LEERIE_TEST_ENUM", "bogus")
    with pytest.raises(SystemExit) as exc:
        _call_enum(leerie, repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "is not one of" in err
    assert "bogus" in err


def test_enum_bad_file_value_dies(leerie, repo_root, capsys):
    (repo_root / "leerie.toml").write_text("test_enum = bogus\n")
    with pytest.raises(SystemExit) as exc:
        _call_enum(leerie, repo_root)
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "is not one of" in err
    assert "bogus" in err


def test_enum_cli_value_bypasses_allowed_check(leerie, repo_root):
    """CLI values are trusted (argparse choices= already validated them at
    the parser level) — _resolve_enum_pref itself does not re-check `cli_value`
    against `allowed`, mirroring resolve_source_of_truth's own docstring."""
    assert _call_enum(leerie, repo_root, cli_value="not-in-allowed") == "not-in-allowed"


@pytest.mark.parametrize("value", ["a", "b", "c"])
def test_enum_all_allowed_values_accepted_in_env(leerie, repo_root, monkeypatch, value):
    monkeypatch.setenv("LEERIE_TEST_ENUM", value)
    assert _call_enum(leerie, repo_root) == value


@pytest.mark.parametrize("value", ["a", "b", "c"])
def test_enum_all_allowed_values_accepted_in_file(leerie, repo_root, value):
    (repo_root / "leerie.toml").write_text(f"test_enum = {value}\n")
    assert _call_enum(leerie, repo_root) == value


# --- _resolve_str_pref -----------------------------------------------------


@pytest.fixture
def str_repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_TEST_STR", raising=False)
    return tmp_path


def _call_str(leerie, repo_root, cli_value=None, default=None):
    return leerie._resolve_str_pref(
        repo_root, cli_value,
        env_var="LEERIE_TEST_STR", file_key="test_str",
        file_name="leerie.toml", default=default)


def test_str_default_none_when_nothing_set(leerie, str_repo_root):
    assert _call_str(leerie, str_repo_root) is None


def test_str_default_custom_when_nothing_set(leerie, str_repo_root):
    assert _call_str(leerie, str_repo_root, default="fallback") == "fallback"


def test_str_cli_wins(leerie, str_repo_root, monkeypatch):
    (str_repo_root / "leerie.toml").write_text("test_str = from-toml\n")
    monkeypatch.setenv("LEERIE_TEST_STR", "from-env")
    assert _call_str(leerie, str_repo_root, cli_value="from-cli") == "from-cli"


def test_str_env_wins_over_file(leerie, str_repo_root, monkeypatch):
    (str_repo_root / "leerie.toml").write_text("test_str = from-toml\n")
    monkeypatch.setenv("LEERIE_TEST_STR", "from-env")
    assert _call_str(leerie, str_repo_root) == "from-env"


def test_str_file_used_when_env_unset(leerie, str_repo_root):
    (str_repo_root / "leerie.toml").write_text("test_str = from-toml\n")
    assert _call_str(leerie, str_repo_root) == "from-toml"


def test_str_full_precedence_cli_beats_env_beats_file(leerie, str_repo_root, monkeypatch):
    (str_repo_root / "leerie.toml").write_text("test_str = from-toml\n")
    monkeypatch.setenv("LEERIE_TEST_STR", "from-env")
    assert _call_str(leerie, str_repo_root) == "from-env"
    assert _call_str(leerie, str_repo_root, cli_value="from-cli") == "from-cli"


def test_str_empty_cli_falls_through_to_env(leerie, str_repo_root, monkeypatch):
    """Empty/whitespace CLI value (argparse default) must not shadow env,
    mirroring test_resolve_pr_base_branch.py."""
    monkeypatch.setenv("LEERIE_TEST_STR", "from-env")
    assert _call_str(leerie, str_repo_root, cli_value="") == "from-env"
    assert _call_str(leerie, str_repo_root, cli_value="   ") == "from-env"


def test_str_empty_env_treated_as_unset(leerie, str_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TEST_STR", "")
    assert _call_str(leerie, str_repo_root, default="fallback") == "fallback"


def test_str_whitespace_only_env_treated_as_unset(leerie, str_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TEST_STR", "   ")
    assert _call_str(leerie, str_repo_root, default="fallback") == "fallback"


def test_str_whitespace_only_file_value_treated_as_unset(leerie, str_repo_root):
    (str_repo_root / "leerie.toml").write_text('test_str = "   "\n')
    assert _call_str(leerie, str_repo_root, default="fallback") == "fallback"


def test_str_no_enum_validation(leerie, str_repo_root):
    """Free-form value — any string is accepted, no die()."""
    (str_repo_root / "leerie.toml").write_text("test_str = anything-goes\n")
    assert _call_str(leerie, str_repo_root) == "anything-goes"


def test_str_env_value_stripped(leerie, str_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_TEST_STR", "  padded  ")
    assert _call_str(leerie, str_repo_root) == "padded"


def test_str_cli_value_stripped(leerie, str_repo_root):
    assert _call_str(leerie, str_repo_root, cli_value="  padded  ") == "padded"


# --- resolve_judge_dir / resolve_heal_dir -----------------------------


@pytest.fixture
def dir_repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_JUDGE_DIR", raising=False)
    monkeypatch.delenv("LEERIE_HEAL_DIR", raising=False)
    return tmp_path


def test_judge_dir_default(leerie, dir_repo_root):
    assert leerie.resolve_judge_dir(dir_repo_root) == "judge-out"


def test_judge_dir_env_var_name(leerie, dir_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_JUDGE_DIR", "custom-judge")
    assert leerie.resolve_judge_dir(dir_repo_root) == "custom-judge"


def test_judge_dir_file_key(leerie, dir_repo_root):
    (dir_repo_root / "leerie.toml").write_text("judge_dir = custom-judge\n")
    assert leerie.resolve_judge_dir(dir_repo_root) == "custom-judge"


def test_judge_dir_cli_wins(leerie, dir_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_JUDGE_DIR", "from-env")
    assert leerie.resolve_judge_dir(dir_repo_root, cli_value="from-cli") == "from-cli"


def test_judge_dir_env_wins_over_file(leerie, dir_repo_root, monkeypatch):
    (dir_repo_root / "leerie.toml").write_text("judge_dir = from-toml\n")
    monkeypatch.setenv("LEERIE_JUDGE_DIR", "from-env")
    assert leerie.resolve_judge_dir(dir_repo_root) == "from-env"


def test_heal_dir_default(leerie, dir_repo_root):
    assert leerie.resolve_heal_dir(dir_repo_root) == "heal-out"


def test_heal_dir_env_var_name(leerie, dir_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_HEAL_DIR", "custom-heal")
    assert leerie.resolve_heal_dir(dir_repo_root) == "custom-heal"


def test_heal_dir_file_key(leerie, dir_repo_root):
    (dir_repo_root / "leerie.toml").write_text("heal_dir = custom-heal\n")
    assert leerie.resolve_heal_dir(dir_repo_root) == "custom-heal"


def test_heal_dir_cli_wins(leerie, dir_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_HEAL_DIR", "from-env")
    assert leerie.resolve_heal_dir(dir_repo_root, cli_value="from-cli") == "from-cli"


def test_heal_dir_env_wins_over_file(leerie, dir_repo_root, monkeypatch):
    (dir_repo_root / "leerie.toml").write_text("heal_dir = from-toml\n")
    monkeypatch.setenv("LEERIE_HEAL_DIR", "from-env")
    assert leerie.resolve_heal_dir(dir_repo_root) == "from-env"


def test_judge_dir_and_heal_dir_are_independent(leerie, dir_repo_root, monkeypatch):
    """The two resolvers must not cross-read each other's env var/file key."""
    monkeypatch.setenv("LEERIE_JUDGE_DIR", "only-judge")
    assert leerie.resolve_judge_dir(dir_repo_root) == "only-judge"
    assert leerie.resolve_heal_dir(dir_repo_root) == "heal-out"


# --- resolve_dangerously_allow_uncapped --------------------------------


@pytest.fixture
def uncapped_repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", raising=False)
    return tmp_path


def test_uncapped_default_is_false(leerie, uncapped_repo_root):
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=False) is False


def test_uncapped_cli_flag_wins(leerie, uncapped_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", "0")
    (uncapped_repo_root / "leerie.toml").write_text(
        "dangerously_allow_uncapped = false\n")
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=True) is True


def test_uncapped_env_true(leerie, uncapped_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", "1")
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=False) is True


def test_uncapped_file_true_no_env(leerie, uncapped_repo_root):
    (uncapped_repo_root / "leerie.toml").write_text(
        "dangerously_allow_uncapped = true\n")
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=False) is True


def test_uncapped_env_wins_over_file(leerie, uncapped_repo_root, monkeypatch):
    (uncapped_repo_root / "leerie.toml").write_text(
        "dangerously_allow_uncapped = true\n")
    monkeypatch.setenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", "false")
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=False) is False


def test_uncapped_env_garbage_dies(leerie, uncapped_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_dangerously_allow_uncapped(
            uncapped_repo_root, cli_value=False)


def test_uncapped_file_garbage_dies(leerie, uncapped_repo_root):
    (uncapped_repo_root / "leerie.toml").write_text(
        "dangerously_allow_uncapped = sometimes\n")
    with pytest.raises(SystemExit):
        leerie.resolve_dangerously_allow_uncapped(
            uncapped_repo_root, cli_value=False)


def test_uncapped_env_empty_string_falls_through(leerie, uncapped_repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_DANGEROUSLY_ALLOW_UNCAPPED", "")
    assert leerie.resolve_dangerously_allow_uncapped(
        uncapped_repo_root, cli_value=False) is False
