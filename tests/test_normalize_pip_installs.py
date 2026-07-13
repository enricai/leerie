"""Tests for `_normalize_pip_installs` / `_is_pip_install` (DESIGN §6½ +
§9): pip install recipe entries must get `--break-system-packages` so
they run on the container's Debian-13 externally-managed system Python.
Without it the baseline's `pip install` fails PEP-668, `pytest` never
installs, and the base-tree test axis records `command not found` — a
degenerate baseline that provokes the conformer to re-derive the base
destructively.
"""
from __future__ import annotations


def _entry(cmd: list[str]) -> dict:
    return {"kind": "install", "command": cmd, "working_dir": ".",
            "timeout_s": 600}


class TestIsPipInstall:
    def test_pip_install(self, leerie):
        assert leerie._is_pip_install(["pip", "install", "x"])
        assert leerie._is_pip_install(["pip3", "install", "-r", "req.txt"])

    def test_python_m_pip_install(self, leerie):
        assert leerie._is_pip_install(["python3", "-m", "pip", "install", "x"])
        assert leerie._is_pip_install(["python", "-m", "pip", "install", "x"])

    def test_global_flag_before_install_is_true(self, leerie):
        # A global option token before the `install` subcommand must not
        # hide it (the subcommand is the first non-option token).
        assert leerie._is_pip_install(["pip", "-v", "install", "x"])
        assert leerie._is_pip_install(["pip", "--isolated", "install", "-r", "r"])
        assert leerie._is_pip_install(["python3", "-m", "pip", "-q", "install", "x"])

    def test_global_flag_before_non_install_is_false(self, leerie):
        assert not leerie._is_pip_install(["pip", "-v", "list"])

    def test_uv_and_pipx_not_matched(self, leerie):
        # uv/pipx manage their own environments — the flag is N/A/invalid.
        assert not leerie._is_pip_install(["uv", "pip", "install", "x"])
        assert not leerie._is_pip_install(["pipx", "install", "x"])

    def test_pip_non_install_is_false(self, leerie):
        assert not leerie._is_pip_install(["pip", "list"])
        assert not leerie._is_pip_install(["pip", "--version"])
        assert not leerie._is_pip_install(["python3", "-m", "pip", "list"])

    def test_non_pip_is_false(self, leerie):
        for cmd in (["npm", "ci"], ["pnpm", "install"], ["bundle", "install"],
                    ["cargo", "build"], []):
            assert not leerie._is_pip_install(cmd), cmd


class TestNormalizePipInstalls:
    def test_requirements_install_flagged(self, leerie):
        r = leerie._normalize_pip_installs(
            [_entry(["pip", "install", "-r", "requirements.txt"])])
        assert r[0]["command"] == [
            "pip", "install", "--break-system-packages", "-r",
            "requirements.txt"]

    def test_incident_packages_install_flagged(self, leerie):
        # The exact recipe entry from run b5d82a9a that produced the bug.
        r = leerie._normalize_pip_installs(
            [_entry(["pip", "install", "pytest", "pytest-cov", "jsonschema"])])
        assert r[0]["command"] == [
            "pip", "install", "--break-system-packages", "pytest",
            "pytest-cov", "jsonschema"]

    def test_editable_install_flagged(self, leerie):
        r = leerie._normalize_pip_installs([_entry(["pip3", "install", "-e", "."])])
        assert r[0]["command"] == [
            "pip3", "install", "--break-system-packages", "-e", "."]

    def test_python_m_pip_flagged(self, leerie):
        r = leerie._normalize_pip_installs(
            [_entry(["python3", "-m", "pip", "install", "foo"])])
        assert r[0]["command"] == [
            "python3", "-m", "pip", "install", "--break-system-packages", "foo"]

    def test_already_flagged_is_idempotent(self, leerie):
        orig = ["pip", "install", "--break-system-packages", "-r", "req.txt"]
        r = leerie._normalize_pip_installs([_entry(list(orig))])
        assert r[0]["command"] == orig  # no double-add

    def test_non_pip_untouched(self, leerie):
        for cmd in (["npm", "ci"], ["pnpm", "install"], ["pip", "list"],
                    ["pip", "--version"]):
            r = leerie._normalize_pip_installs([_entry(list(cmd))])
            assert r[0]["command"] == cmd, cmd

    def test_other_fields_preserved(self, leerie):
        r = leerie._normalize_pip_installs([_entry(["pip", "install", "x"])])
        assert r[0]["kind"] == "install"
        assert r[0]["working_dir"] == "."
        assert r[0]["timeout_s"] == 600

    def test_global_flag_install_flag_inserted_after_verb(self, leerie):
        r = leerie._normalize_pip_installs(
            [_entry(["pip", "-v", "install", "-r", "req.txt"])])
        assert r[0]["command"] == [
            "pip", "-v", "install", "--break-system-packages", "-r", "req.txt"]

    def test_mixed_recipe_only_pip_touched(self, leerie):
        recipe = [_entry(["pnpm", "install"]),
                  _entry(["pip", "install", "-r", "req.txt"])]
        r = leerie._normalize_pip_installs(recipe)
        assert r[0]["command"] == ["pnpm", "install"]
        assert "--break-system-packages" in r[1]["command"]


def test_phase_provision_normalizes_before_persist(leerie):
    """Source-coupling: phase_provision must call _normalize_pip_installs
    before persisting prov['recipe'] — the fix is inert otherwise."""
    import inspect
    src = inspect.getsource(leerie.phase_provision)
    assert "_normalize_pip_installs(" in src
    norm_pos = src.index("_normalize_pip_installs(")
    persist_pos = src.index('prov["recipe"] = recipe')
    assert norm_pos < persist_pos, (
        "_normalize_pip_installs must run before prov['recipe'] = recipe")
