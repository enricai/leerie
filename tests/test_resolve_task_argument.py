"""Tests for resolve_task_argument().

Covers the literal-vs-file-path resolution rule applied to the
positional `task` argument: only existing files with a .txt/.md suffix
are read; everything else is treated as a literal task string.
"""
from __future__ import annotations

import pytest


def test_literal_string_no_matching_file(leerie, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert leerie.resolve_task_argument("fix the bug") == "fix the bug"


def test_existing_md_file_returns_contents(leerie, tmp_path):
    f = tmp_path / "task.md"
    f.write_text("Add a --dry-run flag.\n")
    assert leerie.resolve_task_argument(str(f)) == "Add a --dry-run flag."


def test_existing_txt_file_returns_contents(leerie, tmp_path):
    f = tmp_path / "task.txt"
    f.write_text("  multi\nline task  \n")
    assert leerie.resolve_task_argument(str(f)) == "multi\nline task"


def test_extension_is_case_insensitive(leerie, tmp_path):
    f = tmp_path / "TASK.MD"
    f.write_text("uppercase suffix\n")
    assert leerie.resolve_task_argument(str(f)) == "uppercase suffix"


def test_other_extension_treated_as_literal(leerie, tmp_path):
    f = tmp_path / "task.json"
    f.write_text('{"task": "do thing"}\n')
    # Even though the file exists, .json is not in the allowlist.
    assert leerie.resolve_task_argument(str(f)) == str(f)


def test_missing_md_file_dies(leerie, tmp_path):
    missing = tmp_path / "nope.md"
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument(str(missing))


def test_missing_txt_file_dies(leerie, tmp_path):
    missing = tmp_path / "prompt.txt"
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument(str(missing))


def test_embedded_nul_dies_does_not_crash(leerie):
    # stat() rejects an embedded NUL with ValueError — not OSError — before the
    # path reaches the kernel. Such a path can never name a file, so it must
    # die() like any other missing task file rather than escape as an unhandled
    # traceback.
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument("bad\0name.md")


def test_file_parent_dies_not_treated_as_literal(leerie, tmp_path):
    # A path whose parent is a regular file fails stat() with ENOTDIR, not
    # ENOENT — but it is equally definite: the target cannot exist. It must
    # still die() as a missing task file, NOT fall through to the literal-task
    # path reserved for errnos that mean "the filesystem could not answer"
    # (ENAMETOOLONG et al).
    parent = tmp_path / "notadir"
    parent.write_text("i am a file")
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument(str(parent / "task.md"))


def test_very_long_literal_string_treated_as_literal(leerie, tmp_path,
                                                     monkeypatch):
    # A task string longer than NAME_MAX (255 bytes on macOS/Linux)
    # makes Path.is_file() raise OSError(ENAMETOOLONG) instead of
    # returning False. resolve_task_argument must treat this as a
    # literal task, not crash.
    monkeypatch.chdir(tmp_path)
    long_task = "Rebrand the site " + ("x" * 300)
    assert leerie.resolve_task_argument(long_task) == long_task


def test_very_long_string_ending_in_suffix_treated_as_literal(leerie, tmp_path,
                                                              monkeypatch):
    # OSError from ENAMETOOLONG must suppress the missing-file die()
    # even when the string ends with a task-file suffix.
    monkeypatch.chdir(tmp_path)
    long_task = "x" * 300 + ".txt"
    assert leerie.resolve_task_argument(long_task) == long_task


def test_empty_file_dies(leerie, tmp_path):
    f = tmp_path / "task.md"
    f.write_text("")
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument(str(f))


def test_whitespace_only_file_dies(leerie, tmp_path):
    f = tmp_path / "task.md"
    f.write_text("   \n\t\n")
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument(str(f))
