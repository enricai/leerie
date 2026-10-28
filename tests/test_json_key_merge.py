"""N3 upstream prevention: a merge conflict confined to JSON files is merged
by key, mechanically (DESIGN §6 *A conflict confined to JSON files is merged
by key*)."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path: Path, base: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    for f, text in base.items():
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _conflict(repo: Path, ours: dict[str, str], theirs: dict[str, str]):
    _git(repo, "checkout", "-q", "-b", "side")
    for f, t in theirs.items():
        (repo / f).write_text(t)
    _git(repo, "commit", "-q", "-am", "theirs")
    _git(repo, "checkout", "-q", "main")
    for f, t in ours.items():
        (repo / f).write_text(t)
    _git(repo, "commit", "-q", "-am", "ours")
    r = subprocess.run(["git", "merge", "--no-ff", "-m",
                        "leerie: integrate feat-002", "side"],
                       cwd=str(repo), capture_output=True, text=True)
    assert r.returncode != 0, "expected a conflict"


def _dump(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


BASE = {"Card": {"title": "Card", "save": "Save"}}


class TestMerge3:
    def test_both_sides_add_keys(self, leerie):
        ours = {"Card": {"title": "Card", "save": "Save", "a": "A"}}
        theirs = {"Card": {"title": "Card", "save": "Save", "b": "B"}}
        merged, conflicts = leerie._json_key_merge3(BASE, ours, theirs)
        assert conflicts == []
        assert merged == {"Card": {"title": "Card", "save": "Save",
                                   "a": "A", "b": "B"}}
        assert list(merged["Card"]) == ["title", "save", "a", "b"]

    def test_one_side_change_and_one_side_delete(self, leerie):
        ours = {"Card": {"title": "Card!", "save": "Save"}}
        theirs = {"Card": {"title": "Card"}}
        merged, conflicts = leerie._json_key_merge3(BASE, ours, theirs)
        assert conflicts == []
        assert merged == {"Card": {"title": "Card!"}}

    def test_same_key_changed_differently_conflicts(self, leerie):
        ours = {"Card": {"title": "One", "save": "Save"}}
        theirs = {"Card": {"title": "Two", "save": "Save"}}
        _m, conflicts = leerie._json_key_merge3(BASE, ours, theirs)
        assert conflicts == ["/Card/title"]

    def test_non_object_top_level(self, leerie):
        assert leerie._json_key_merge3([1], [1, 2], [1])[1] == []
        assert leerie._json_key_merge3([1], [1, 2], [1, 3])[1] == ["/"]


class TestTryJsonKeyMerge:
    def test_i18n_conflict_is_committed(self, leerie, tmp_path):
        repo = _repo(tmp_path, {"messages/en.json": _dump(BASE)})
        _conflict(
            repo,
            ours={"messages/en.json": _dump(
                {"Card": {"title": "Card", "save": "Save", "a": "A"}})},
            theirs={"messages/en.json": _dump(
                {"Card": {"title": "Card", "save": "Save", "b": "B"}})})
        files = asyncio.run(leerie._try_json_key_merge(repo, "feat-002"))
        assert files == ["messages/en.json"]
        assert not (repo / ".git" / "MERGE_HEAD").exists()
        head = json.loads(_git(repo, "show", "HEAD:messages/en.json"))
        assert head == {"Card": {"title": "Card", "save": "Save",
                                 "a": "A", "b": "B"}}
        parents = _git(repo, "rev-list", "--parents", "-n", "1",
                       "HEAD").split()
        assert len(parents) == 3
        assert _git(repo, "log", "-1", "--format=%s").strip() == \
            "leerie: integrate feat-002"
        # Formatting kept: 2-space indent, trailing newline.
        text = (repo / "messages/en.json").read_text()
        assert text.endswith("}\n") and '\n  "Card"' in text

    def test_key_conflict_leaves_the_merge_for_the_integrator(
            self, leerie, tmp_path):
        repo = _repo(tmp_path, {"messages/en.json": _dump(BASE)})
        _conflict(
            repo,
            ours={"messages/en.json": _dump(
                {"Card": {"title": "One", "save": "Save"}})},
            theirs={"messages/en.json": _dump(
                {"Card": {"title": "Two", "save": "Save"}})})
        assert asyncio.run(
            leerie._try_json_key_merge(repo, "feat-002")) is None
        assert (repo / ".git" / "MERGE_HEAD").exists()
        assert "<<<<<<<" in (repo / "messages/en.json").read_text()

    def test_non_json_conflict_is_not_touched(self, leerie, tmp_path):
        repo = _repo(tmp_path, {"messages/en.json": _dump(BASE),
                                "src/a.ts": "x\n"})
        _conflict(
            repo,
            ours={"messages/en.json": _dump(
                {"Card": {"title": "Card", "save": "Save", "a": "A"}}),
                  "src/a.ts": "ours\n"},
            theirs={"messages/en.json": _dump(
                {"Card": {"title": "Card", "save": "Save", "b": "B"}}),
                    "src/a.ts": "theirs\n"})
        assert asyncio.run(
            leerie._try_json_key_merge(repo, "feat-002")) is None
        assert (repo / ".git" / "MERGE_HEAD").exists()
        assert "<<<<<<<" in (repo / "messages/en.json").read_text()
