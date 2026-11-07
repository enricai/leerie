"""A report the task names is readable by the workers that write the fix
(DESIGN §6 *A report the task names is read, not searched for*).

Measured on 8 finished runs: implementers and conformers searched for the
user's report 28 times and found it 0 times, because the planning worktree
was the only tree it was ever copied into and a committing worker's tree is
committed code. The copies are made once at plan time, outside every tree,
and the worker's prompt names where they are.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_run_repo import _git, _user_repo

REPORT = "# Report\n\nA payload token comes from the preceding HTML response.\n"


@pytest.fixture()
def planned(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    # Untracked: the case. A tracked file is in every checkout anyway.
    (repo / "notes").mkdir()
    (repo / "notes" / "report.md").write_text(REPORT)
    root = tmp_path / "state"
    run = root / "runs" / "r1"
    (run / "subtasks").mkdir(parents=True)
    st = leerie.State(root, "r1")
    st.repo_root = repo
    task = "Fix what notes/report.md describes."
    st.data = {"task": task, "worker_count": 0,
               "answers": {"source_of_truth": "codebase"}}
    st.save()
    sub = {"feat-001": {"id": "feat-001", "title": "x",
                        "success_criteria_seed": "y", "size": "small",
                        "provides": [], "requires": [], "depends_on": []}}
    leerie._write_plan(st.run_dir, task, st, sub, [["feat-001"]])
    return st, repo


def test_the_report_is_copied_outside_every_tree(leerie, planned):
    st, repo = planned
    copy = st.run_dir / leerie.TASK_FILES_DIRNAME / "notes" / "report.md"
    assert copy.read_text() == REPORT
    # The user's checkout is untouched: the one untracked directory, nothing
    # added beside it.
    assert _git(repo, "status", "--porcelain").strip() == "?? notes/"
    assert not copy.is_relative_to(repo)


def _env(run_dir: Path, tag: str) -> dict:
    own = run_dir / "workers" / tag
    env = {"CLAUDE_CONFIG_DIR": str(own / "config"),
           "TMPDIR": str(own / "tmp")}
    for p in env.values():
        Path(p).mkdir(parents=True)
    return env


@pytest.mark.parametrize("kind,readable", [
    ("implementer", True), ("conformer", True),
    # Workers that never write the fix are not given the copies.
    ("delivery_judge", False), ("falsifier", False),
    ("acceptance_writer", False)])
def test_exactly_the_fix_writers_read_it(leerie, planned, tmp_path, kind,
                                         readable):
    st, _repo = planned
    cwd = tmp_path / f"tree-{kind}"
    cwd.mkdir()
    g = leerie._worker_grants(
        run_dir=st.run_dir, state={}, schema_key=kind, cwd=cwd,
        allowed_tools=leerie.ACT_TOOLS, env=_env(st.run_dir, kind))
    copy = os.path.realpath(
        st.run_dir / leerie.TASK_FILES_DIRNAME / "notes" / "report.md")
    reads = [os.path.realpath(p) for p in g.ro]
    writes = [os.path.realpath(p) for p in g.rw]
    assert any(Path(copy).is_relative_to(p) for p in reads) is readable
    assert not any(Path(copy).is_relative_to(p) for p in writes)


def test_the_prompts_name_the_copy_as_data(leerie, planned):
    st, _repo = planned
    copy = str(st.run_dir / leerie.TASK_FILES_DIRNAME / "notes" / "report.md")
    caps = dict(leerie.DEFAULT_CAPS)
    impl = "\n".join(leerie._implementer_base_prompt(
        "feat-001", st.run_dir, caps, st, cwd_line="cwd"))
    assert f"- {copy}" in impl
    conf: list[str] = []
    leerie._append_conformer_context_sections(conf, {}, "full", st)
    assert f"- {copy}" in "\n".join(conf)


def test_a_task_naming_no_file_adds_nothing(leerie, tmp_path):
    repo = _user_repo(tmp_path)
    root = tmp_path / "state"
    (root / "runs" / "r2" / "subtasks").mkdir(parents=True)
    st = leerie.State(root, "r2")
    st.repo_root = repo
    st.data = {"task": "Fix the timeout", "worker_count": 0,
               "answers": {"source_of_truth": "codebase"}}
    leerie._write_plan(st.run_dir, "Fix the timeout", st, {}, [])
    assert leerie._task_files_section(st) == []
    assert not any((st.run_dir / leerie.TASK_FILES_DIRNAME).rglob("*"))
