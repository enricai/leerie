"""Tests for `_validate_provision_recipe` — the mechanical bound on
both table-emitted and LLM-emitted recipes.

The validator is the §12 carve-out's containment: the LLM provision
worker's recipe is structurally schema-validated by `claude_p`, then
flows through this validator, which rejects any command whose program is
not a bare name or a relative path in the repository, smuggles shell
metacharacters, includes sudo, or points at a working_dir outside the repo
(DESIGN §6½). Whether the run can run the program is
`_recipe_program_issues`'s, checked in the worker's own loop.
"""
from __future__ import annotations

import contextlib
import os

import pytest


def _ok(cmd, working_dir="."):
    """Build a minimal install entry."""
    return {"kind": "install", "command": cmd, "working_dir": working_dir,
            "timeout_s": 600}


# --- program names, not a list -------------------------------------------

@pytest.mark.parametrize("prog", [
    "pnpm", "uv", "zig", "mix", "mvn", "gradle", "make", "deno",
    "./gradlew", "bin/setup", "tools/bootstrap.sh",
])
def test_any_program_name_or_relative_path_is_well_formed(leerie, prog):
    """There is no list of programs: the repository's own tool is the
    recipe's (DESIGN §6½ *The §12 carve-out*). A Zig or Elixir repository's
    `zig` / `mix` was refused by the old allowlist (lane LG2's bench)."""
    leerie._validate_provision_recipe([_ok([prog, "install"])])


@pytest.mark.parametrize("prog", [
    "/usr/bin/make", "../outside/tool", "bin/../../x", "-rf", "",
    "pnpm install", "~/bin/x", "a$b",
])
def test_an_absolute_or_escaping_or_malformed_program_is_refused(leerie,
                                                                 prog):
    with pytest.raises(ValueError):
        leerie._validate_provision_recipe([_ok([prog, "x"])])


def _exe(d, name):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def test_a_program_the_run_provides_raises_nothing(leerie, tmp_path,
                                                    monkeypatch):
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "md"))
    repo = tmp_path / "repo"
    _exe(repo, "gradlew")
    _exe(tmp_path / "md" / "installs" / "zig" / "0.13.0", "zig")
    _exe(tmp_path / "usr-bin", "make")
    issues = leerie._recipe_program_issues(
        [_ok(["zig", "build"]), _ok(["./gradlew", "build"]),
         _ok(["make"]), {"kind": "none", "command": [], "working_dir": "."}],
        repo, [str(tmp_path / "md" / "installs" / "zig" / "0.13.0")],
        str(tmp_path / "usr-bin"))
    assert issues == []


def test_a_program_the_run_lacks_is_named(leerie, tmp_path, monkeypatch):
    """A shim exists for every tool any run installed: it does not make the
    program one this run provides."""
    md = tmp_path / "md"
    monkeypatch.setenv("MISE_DATA_DIR", str(md))
    _exe(md / "shims", "poetry")
    repo = tmp_path / "repo"
    repo.mkdir()
    issues = leerie._recipe_program_issues(
        [_ok(["poetry", "install"]), _ok(["./mvnw", "install"])],
        repo, [], f"{md / 'shims'}{os.pathsep}{tmp_path / 'empty'}")
    assert len(issues) == 2, issues
    assert "'poetry'" in issues[0] and "'./mvnw'" in issues[1]


def test_the_provision_loop_feeds_an_unknown_program_back(leerie, tmp_path,
                                                          monkeypatch):
    """Driven through `phase_provision`'s worker loop: a recipe naming a
    program the run lacks is re-asked, and the second answer is kept."""
    import asyncio
    root = tmp_path / ".leerie"
    (root / "runs" / "_t" / "logs").mkdir(parents=True)
    st = leerie.State(root, "_t")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.zig").write_text("")
    md = tmp_path / "md"
    zig_dir = md / "installs" / "zig" / "0.13.0"
    _exe(zig_dir, "zig")
    monkeypatch.setenv("MISE_DATA_DIR", str(md))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    st.data = {"task": "t", "worker_count": 0,
               "planning_worktree": str(repo),
               "provision": {"bin_paths": [str(zig_dir)],
                             "mise_versions": {}}}
    st.save()

    async def _no_tools(*a, **k):
        return None

    @contextlib.asynccontextmanager
    async def _checkout(*a, **k):
        yield repo

    monkeypatch.setattr(leerie, "_discover_tools", _no_tools)
    monkeypatch.setattr(leerie, "_judge_checkout", _checkout)
    monkeypatch.setattr(leerie, "_provision_hook_and_toolchain", _no_tools)
    prompts: list[str] = []
    answers = [{"recipe": [_ok(["make", "--version"])]},
               {"recipe": [_ok(["zig", "build"])]}]

    async def _fake(**kw):
        prompts.append(kw["user_prompt"])
        return answers[len(prompts) - 1]

    monkeypatch.setattr(leerie, "claude_p", _fake)
    asyncio.run(leerie.phase_provision(repo, st, dict(leerie.DEFAULT_CAPS),
                                       {}, {}))
    assert len(prompts) == 2 and "UNKNOWN_PROGRAM" in prompts[1]
    assert st.data["provision"]["recipe"][0]["command"] == ["zig", "build"]


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

def test_sudo_in_argv0_is_rejected(leerie):
    with pytest.raises(ValueError, match="sudo"):
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
            _ok(["/bin/rm", "-rf", "/"]),  # second entry: an absolute program
        ])


# --- top-level shape ------------------------------------------------------

def test_recipe_must_be_list(leerie):
    with pytest.raises(ValueError, match="must be a list"):
        leerie._validate_provision_recipe({"recipe": []})


def test_entry_must_be_dict(leerie):
    with pytest.raises(ValueError, match="is not a dict"):
        leerie._validate_provision_recipe(["not-a-dict"])
