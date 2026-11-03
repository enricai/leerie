"""Tests for `_find_pr_template()` — locating the target repo's PR
template in GitHub's canonical order, as committed (a git blob of the
repository at a commit, never a file of the checkout: DESIGN §6 *Leerie
never reads through a link a repository holds*).

Covers:
- Single-template locations (.github/, root, docs/), priority order.
- PULL_REQUEST_TEMPLATE/ directory: alphabetically first default.
- --pr-template override picks a specific basename (with or without .md).
- No template anywhere → returns None.
- Override naming a non-existent template falls back to default
  (we do not die() on a bad pr_template selector — that would block
  finalize over a cosmetic preference).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _find(leerie, repo: Path, override: str | None = None):
    """Commit the repository as it stands and look for its template at the
    commit, in its git directory."""
    (repo / ".keep").write_text("")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "x"], check=True)
    return leerie._find_pr_template(repo / ".git", "HEAD", override)


def _write(p: Path, content: str = "TEMPLATE\n") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_no_template_returns_none(leerie, repo):
    assert _find(leerie, repo) is None


def test_dot_github_single_wins(leerie, repo):
    _write(repo / ".github/pull_request_template.md", "github-version")
    result = _find(leerie, repo)
    assert result is not None
    rel, text, truncated = result
    assert rel == ".github/pull_request_template.md"
    assert (text, truncated) == ("github-version", False)


def test_root_single_when_no_dot_github(leerie, repo):
    _write(repo / "pull_request_template.md", "root-version")
    result = _find(leerie, repo)
    assert result is not None
    rel, _, _ = result
    assert rel == "pull_request_template.md"


def test_docs_single_when_no_others(leerie, repo):
    _write(repo / "docs/pull_request_template.md", "docs-version")
    result = _find(leerie, repo)
    assert result is not None
    rel, _, _ = result
    assert rel == "docs/pull_request_template.md"


def test_dot_github_beats_root(leerie, repo):
    _write(repo / ".github/pull_request_template.md")
    _write(repo / "pull_request_template.md")
    rel, _, _ = _find(leerie, repo)
    assert rel == ".github/pull_request_template.md"


def test_root_beats_docs(leerie, repo):
    _write(repo / "pull_request_template.md")
    _write(repo / "docs/pull_request_template.md")
    rel, _, _ = _find(leerie, repo)
    assert rel == "pull_request_template.md"


def test_multi_dir_alphabetical_first(leerie, repo):
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/zebra.md", "z")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/alpha.md", "a")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/middle.md", "m")
    result = _find(leerie, repo)
    assert result is not None
    rel, text, _ = result
    assert rel == ".github/PULL_REQUEST_TEMPLATE/alpha.md"
    assert text == "a"


def test_multi_dir_override_with_md_suffix(leerie, repo):
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/bug.md", "b")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/feature.md", "f")
    rel, _, _ = _find(leerie, repo, override="feature.md")
    assert rel == ".github/PULL_REQUEST_TEMPLATE/feature.md"


def test_multi_dir_override_without_md_suffix(leerie, repo):
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/bug.md", "b")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/feature.md", "f")
    rel, _, _ = _find(leerie, repo, override="feature")
    assert rel == ".github/PULL_REQUEST_TEMPLATE/feature.md"


def test_multi_dir_override_no_match_falls_back_to_first(leerie, repo):
    # Bad override should NOT die — finalize must keep working. The
    # caller logs a warning; we just return the alphabetical default.
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/bug.md", "b")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/feature.md", "f")
    rel, _, _ = _find(leerie, repo, override="nonexistent")
    assert rel == ".github/PULL_REQUEST_TEMPLATE/bug.md"


def test_single_template_beats_multi_dir(leerie, repo):
    # GitHub's canonical order: single .github/pull_request_template.md
    # outranks any PULL_REQUEST_TEMPLATE/ directory.
    _write(repo / ".github/pull_request_template.md", "single")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/feature.md", "multi")
    rel, _, _ = _find(leerie, repo)
    assert rel == ".github/pull_request_template.md"


def test_multi_dir_ignores_non_md(leerie, repo):
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/feature.md", "f")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/README.txt", "ignore")
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/.DS_Store", "ignore")
    rel, _, _ = _find(leerie, repo)
    assert rel == ".github/PULL_REQUEST_TEMPLATE/feature.md"


def test_multi_dir_empty_returns_none(leerie, repo):
    # git keeps no empty directory; one with no .md files is the shape
    # that falls through to None rather than crashing on sorted([]).
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/notes.txt", "x")
    assert _find(leerie, repo) is None


def test_multi_dir_falls_through_when_empty(leerie, repo):
    # A .github/PULL_REQUEST_TEMPLATE holding no .md should NOT block
    # discovery of docs/PULL_REQUEST_TEMPLATE — the loop must keep scanning.
    _write(repo / ".github/PULL_REQUEST_TEMPLATE/notes.txt", "x")
    _write(repo / "docs/PULL_REQUEST_TEMPLATE/feature.md", "docs-f")
    result = _find(leerie, repo)
    assert result is not None
    rel, _, _ = result
    assert rel == "docs/PULL_REQUEST_TEMPLATE/feature.md"


def test_resolve_pr_template_cli_wins(leerie, repo, monkeypatch):
    monkeypatch.setenv(leerie.PR_TEMPLATE_ENV, "from-env")
    assert leerie.resolve_pr_template(repo, cli_value="from-cli") == "from-cli"


def test_resolve_pr_template_env_wins_over_toml(leerie, repo, monkeypatch):
    (repo / "leerie.toml").write_text('pr_template = "from-toml"\n')
    monkeypatch.setenv(leerie.PR_TEMPLATE_ENV, "from-env")
    assert leerie.resolve_pr_template(repo, cli_value=None) == "from-env"


def test_resolve_pr_template_toml_when_unset(leerie, repo, monkeypatch):
    monkeypatch.delenv(leerie.PR_TEMPLATE_ENV, raising=False)
    (repo / "leerie.toml").write_text('pr_template = "from-toml"\n')
    assert leerie.resolve_pr_template(repo, cli_value=None) == "from-toml"


def test_resolve_pr_template_none_when_nothing_set(leerie, repo, monkeypatch):
    monkeypatch.delenv(leerie.PR_TEMPLATE_ENV, raising=False)
    assert leerie.resolve_pr_template(repo, cli_value=None) is None
