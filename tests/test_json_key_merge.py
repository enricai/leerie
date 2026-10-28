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


# --- Formatting is kept byte for byte, or the merge is refused (r1 M3) ------
#
# The key merge used to re-render the whole file with `json.dumps`, so a
# prettier-style package.json came back with every inline array exploded and
# its `\uXXXX` escapes rewritten: a PR full of unrelated churn, and a red
# `prettier --check` merge check caused by leerie's own mechanical merge.

PKG = {"name": "app", "files": ["dist", "src"],
       "scripts": {"build": "tsc"}, "dependencies": {"left-pad": "1.0.0"}}
PKG_OURS = {**PKG, "dependencies": {"left-pad": "1.0.0", "zod": "3.0.0"}}
PKG_THEIRS = {**PKG, "scripts": {"build": "tsc", "lint": "eslint ."},
              "dependencies": {"left-pad": "1.0.0", "aaa": "1.0.0"}}


def _changed_lines(repo: Path) -> list[str]:
    """The `+`/`-` lines of the merge against its first parent."""
    diff = _git(repo, "diff", "HEAD^1", "HEAD", "--", "package.json")
    return [ln for ln in diff.splitlines()
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


class TestFormattingKept:
    def _merge(self, leerie, tmp_path, fmt) -> Path:
        repo = _repo(tmp_path, {"package.json": fmt(PKG)})
        _conflict(repo, ours={"package.json": fmt(PKG_OURS)},
                  theirs={"package.json": fmt(PKG_THEIRS)})
        return repo

    def _assert_only_merged_keys(self, repo: Path, fmt) -> None:
        text = (repo / "package.json").read_text()
        merged = {**PKG_OURS, "scripts": PKG_THEIRS["scripts"],
                  "dependencies": {"left-pad": "1.0.0", "zod": "3.0.0",
                                   "aaa": "1.0.0"}}
        assert text == fmt(merged)
        changed = _changed_lines(repo)
        # Only the theirs-side keys arrive (and the comma JSON needs on the
        # line before each): nothing of ours is re-rendered.
        assert sorted(ln[0] for ln in changed) == ["+", "+", "+", "+",
                                                   "-", "-"], changed
        assert all(("lint" in ln or "aaa" in ln or "tsc" in ln
                    or "zod" in ln) for ln in changed), changed

    def test_two_space_file_merges_with_its_bytes_kept(self, leerie,
                                                        tmp_path):
        fmt = lambda d: json.dumps(d, indent=2) + "\n"   # noqa: E731
        repo = self._merge(leerie, tmp_path, fmt)
        assert asyncio.run(leerie._try_json_key_merge(repo, "feat-002")) \
            == ["package.json"]
        self._assert_only_merged_keys(repo, fmt)

    def test_four_space_file_without_trailing_newline(self, leerie,
                                                      tmp_path):
        fmt = lambda d: json.dumps(d, indent=4)          # noqa: E731
        repo = self._merge(leerie, tmp_path, fmt)
        assert asyncio.run(leerie._try_json_key_merge(repo, "feat-002")) \
            == ["package.json"]
        self._assert_only_merged_keys(repo, fmt)

    def test_tab_file_with_ascii_escapes(self, leerie, tmp_path):
        def fmt(d):
            return json.dumps({**d, "description": "caf\u00e9"},
                              indent="\t", ensure_ascii=True) + "\n"
        repo = self._merge(leerie, tmp_path, fmt)
        assert asyncio.run(leerie._try_json_key_merge(repo, "feat-002")) \
            == ["package.json"]
        text = (repo / "package.json").read_text()
        assert '"caf\\u00e9"' in text and "\n\t\"name\"" in text
        assert json.loads(text)["dependencies"]["aaa"] == "1.0.0"

    def test_prettier_style_file_is_left_to_the_integrator(self, leerie,
                                                           tmp_path):
        """The review's jsonfmt.sh: inline arrays `json.dumps` cannot write.
        Refused, and the conflict stays exactly as git left it."""
        def fmt(d):
            text = json.dumps(d, indent=2) + "\n"
            return text.replace('[\n    "dist",\n    "src"\n  ]',
                                '["dist", "src"]')
        assert '"files": ["dist", "src"],' in fmt(PKG)
        repo = self._merge(leerie, tmp_path, fmt)
        assert asyncio.run(
            leerie._try_json_key_merge(repo, "feat-002")) is None
        assert (repo / ".git" / "MERGE_HEAD").exists()
        assert "<<<<<<<" in (repo / "package.json").read_text()

    def test_renderer_refuses_what_it_cannot_reproduce(self, leerie):
        r = leerie._json_faithful_renderer
        ok = json.dumps({"a": [1, 2]}, indent=2) + "\n"
        assert r(ok)({"a": [1, 2]}) == ok
        for bad in ('{"a": [1, 2]}\n',                    # inline array
                    ok.replace("\n", "\r\n"),             # CRLF
                    '{\n  "a": 1,\n  "a": 2\n}\n',        # duplicate key
                    '{\n  "a": 1e3\n}\n',                 # exponent float
                    '{\n  "a": "\\u00e9",\n  "b": "\u00e9"\n}\n',  # mixed
                    ok + "\n",                            # two newlines
                    "not json"):
            assert r(bad) is None, bad
