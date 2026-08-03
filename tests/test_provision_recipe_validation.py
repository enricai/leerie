"""Tests for `_validate_provision_recipe` — the mechanical bound on
both table-emitted and LLM-emitted recipes.

The validator is the §12 carve-out's containment: the LLM provision
worker's recipe is structurally schema-validated by `claude_p`, then
flows through this validator, which rejects any command that escapes
the argv allowlist, smuggles shell metacharacters, includes sudo, or
points at a working_dir outside the repo (DESIGN §6½).
"""
from __future__ import annotations

import pytest


def _ok(cmd, working_dir="."):
    """Build a minimal install entry."""
    return {"kind": "install", "command": cmd, "working_dir": working_dir,
            "timeout_s": 600}


# --- allowlist coverage -----------------------------------------------------

ALLOWED_TOOLS = [
    "pnpm", "npm", "yarn", "pip", "pip3", "uv", "poetry", "pipenv",
    "go", "cargo", "bundle", "gem", "mvn", "gradle", "gradlew", "make",
]


@pytest.mark.parametrize("tool", ALLOWED_TOOLS)
def test_allowlist_accepts_every_documented_manager(leerie, tool):
    leerie._validate_provision_recipe([_ok([tool, "install"])])


@pytest.mark.parametrize("tool", [
    "rm", "curl", "wget", "bash", "sh", "python", "ruby", "node",
    "docker", "kubectl", "ssh", "cat", "sudo",
])
def test_allowlist_rejects_non_package_managers(leerie, tool):
    """The whole point of the allowlist is to keep destructive tools and
    shell entry points out of the recipe."""
    with pytest.raises(ValueError, match="not in the allowed"):
        leerie._validate_provision_recipe([_ok([tool, "something"])])


# --- shell-metacharacter rejection ------------------------------------------

@pytest.mark.parametrize("smuggled", [
    "pkg|other",          # pipe
    "pkg&other",          # background
    "pkg;other",          # statement separator
    "$(echo evil)",       # command substitution
    "`echo evil`",        # backtick command substitution
    "pkg>/dev/null",      # stdout redirection
    "pkg<input",          # stdin redirection
    "pkg\nrest",          # newline (multi-command)
])
def test_shell_metacharacters_in_args_are_rejected(leerie, smuggled):
    with pytest.raises(ValueError, match="shell metacharacters"):
        leerie._validate_provision_recipe([_ok(["pnpm", smuggled])])


# --- sudo rejection ---------------------------------------------------------

def test_sudo_in_argv0_is_rejected_by_allowlist(leerie):
    """sudo is not in the allowlist."""
    with pytest.raises(ValueError, match="not in the allowed"):
        leerie._validate_provision_recipe([_ok(["sudo", "pnpm", "install"])])


def test_sudo_in_inner_argv_is_rejected_explicitly(leerie):
    """sudo as an inner argv is also explicitly caught — the message
    surfaces the smuggling attempt clearly."""
    with pytest.raises(ValueError, match="sudo"):
        leerie._validate_provision_recipe([_ok(["pnpm", "sudo", "install"])])


# --- working_dir rules ------------------------------------------------------

def test_relative_working_dir_accepted(leerie):
    leerie._validate_provision_recipe([_ok(["pnpm", "install"], working_dir="packages/web")])


def test_dot_working_dir_accepted(leerie):
    leerie._validate_provision_recipe([_ok(["pnpm", "install"], working_dir=".")])


def test_absolute_working_dir_rejected(leerie):
    with pytest.raises(ValueError, match="must be relative"):
        leerie._validate_provision_recipe(
            [_ok(["pnpm", "install"], working_dir="/etc/passwd")])


@pytest.mark.parametrize("traversal", [
    "..",
    "../../etc",
    "packages/../..",
    "packages/web/..",
    "packages\\..\\sneaky",   # Windows-style separator smuggling
])
def test_dotdot_in_working_dir_rejected(leerie, traversal):
    with pytest.raises(ValueError, match=r"\.\."):
        leerie._validate_provision_recipe(
            [_ok(["pnpm", "install"], working_dir=traversal)])


def test_empty_working_dir_rejected(leerie):
    with pytest.raises(ValueError, match="non-empty"):
        leerie._validate_provision_recipe(
            [_ok(["pnpm", "install"], working_dir="")])


# --- structural rules ------------------------------------------------------

def test_command_must_be_non_empty_list(leerie):
    with pytest.raises(ValueError, match="non-empty argv list"):
        leerie._validate_provision_recipe([{
            "kind": "install", "command": [], "working_dir": ".",
        }])


def test_command_must_be_list_of_strings(leerie):
    with pytest.raises(ValueError, match="list of strings"):
        leerie._validate_provision_recipe([{
            "kind": "install", "command": ["pnpm", 42], "working_dir": ".",
        }])


def test_unknown_kind_rejected(leerie):
    with pytest.raises(ValueError, match="kind"):
        leerie._validate_provision_recipe([{
            "kind": "unknown", "command": ["pnpm", "install"], "working_dir": ".",
        }])


def test_none_kind_skips_command_requirements(leerie):
    """A `kind: none` entry is a bypass marker — no command needed."""
    leerie._validate_provision_recipe([{
        "kind": "none", "command": [], "working_dir": ".", "timeout_s": 0,
    }])


# --- multi-entry recipes --------------------------------------------------

def test_polyglot_recipe_validates_each_entry(leerie):
    leerie._validate_provision_recipe([
        _ok(["bundle", "install"]),
        _ok(["yarn", "install", "--frozen-lockfile"]),
    ])


def test_polyglot_recipe_rejects_if_any_entry_fails(leerie):
    with pytest.raises(ValueError):
        leerie._validate_provision_recipe([
            _ok(["bundle", "install"]),
            _ok(["rm", "-rf", "/"]),  # second entry fails the allowlist
        ])


# --- top-level shape ------------------------------------------------------

def test_recipe_must_be_list(leerie):
    with pytest.raises(ValueError, match="must be a list"):
        leerie._validate_provision_recipe({"recipe": []})


def test_entry_must_be_dict(leerie):
    with pytest.raises(ValueError, match="is not a dict"):
        leerie._validate_provision_recipe(["not-a-dict"])
