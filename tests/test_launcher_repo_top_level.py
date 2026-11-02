"""DESIGN §6 *The host never runs a program the checkout chose*, part 1: the
checkout is the repository's top level (review round 27 O4).

Started from a subdirectory, the launcher mounted that subdirectory at
`/work` — a tree with no `.git` — and keyed the state directory by the
subdirectory's name. The checkout is `git rev-parse --show-toplevel` of
where the operator stood; a path the operator gave relative to where they
stood still means that path, on the host and in the container.

Real launcher, stub `nerdctl`, started from `<repo>/sub`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.launcher_stub_run import make_world, run_argv, run_to_end

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")


def _run_from_sub(w: dict, args: list[str]) -> tuple[list[str], str]:
    rc, out = run_to_end(w, args, cwd=w["repo"] / "sub")
    assert rc == 0, out
    return run_argv(w), out


def _mount_of_work(argv: list[str]) -> str:
    return next(a for a in argv if a.endswith(":/work:ro")).rsplit(
        ":/work:ro", 1)[0]


def test_a_run_from_a_subdirectory_mounts_the_top_level(tmp_path: Path):
    w = make_world(tmp_path)
    argv, _ = _run_from_sub(w, ["--no-push", "a task"])
    assert _mount_of_work(argv) == str(w["repo"].resolve())
    assert "USER_REPO=repo" in argv
    assert "-w" in argv and argv[argv.index("-w") + 1] == "/work"


def test_the_default_state_dir_is_keyed_by_the_top_level(tmp_path: Path):
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "a task"], cwd=w["repo"] / "sub",
                         env_extra={"LEERIE_STATE_DIR": ""})
    assert rc == 0, out
    leerie = w["home"] / ".leerie"
    assert sorted(d.name for d in leerie.iterdir()) == ["repo"]
    assert (leerie / "repo" / ".owner").read_text().strip() == str(
        w["repo"].resolve())


def test_operator_relative_paths_resolve_from_where_they_stood(tmp_path):
    """The task file and `--answers` exist only under `sub/`: the host check
    finds them there, and the container — whose working directory is the
    top level — is handed them as `sub/...`. `--log-file` lands beside
    `sub/`, as the operator wrote it."""
    w = make_world(tmp_path)
    sub = w["repo"] / "sub"
    (sub / "task.md").write_text("do the thing\n")
    (sub / "answers.json").write_text("{}\n")
    argv, out = _run_from_sub(
        w, ["--no-push", "--answers", "answers.json", "--log-file",
            "../run.log", "task.md"])
    tail = argv[next(k for k, a in enumerate(argv)
                     if a.startswith("leerie:")) + 1:]
    assert tail[tail.index("--answers") + 1] == "sub/answers.json", tail
    assert "sub/task.md" in tail and "task.md" not in tail, tail
    assert (w["repo"] / "run.log").exists(), out
    assert not (w["repo"].parent / "run.log").exists()


def test_a_missing_task_file_is_looked_for_where_they_stood(tmp_path):
    """`task.md` exists at the top level only: from `sub/` it is missing."""
    w = make_world(tmp_path)
    (w["repo"] / "task.md").write_text("t\n")
    rc, out = run_to_end(w, ["--no-push", "task.md"], cwd=w["repo"] / "sub")
    assert rc == 1, out
    assert "task file not found: task.md" in out
    assert run_argv(w) == []


def test_at_the_top_level_nothing_is_rewritten(tmp_path: Path):
    w = make_world(tmp_path)
    (w["repo"] / "task.md").write_text("t\n")
    rc, out = run_to_end(w, ["--no-push", "task.md"])
    assert rc == 0, out
    argv = run_argv(w)
    tail = argv[next(k for k, a in enumerate(argv)
                     if a.startswith("leerie:")) + 1:]
    assert "task.md" in tail and "sub/task.md" not in tail, tail
    assert _mount_of_work(argv) == str(w["repo"].resolve())
