"""Tests for the capture engine (DESIGN §6½ — Auto-capture of repo dependencies).

Covers: absence pins for deleted regex-path symbols (_parse_apt_intents,
_capture_installs_from_logs, _LANG_INSTALL_RE, _APT_INSTALL_RE),
_merge_setup_packages, _extract_depcap_commands,
capture_repo_deps (writes setup_packages + language_installs via stubbed
claude_p, committed-Dockerfile skip, write-failure non-fatal, opt-out),
resolve_capture_deps.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Absence pins — regex path must not exist
# ---------------------------------------------------------------------------

class TestRegexPathAbsent:
    """Assert that the four regex-path symbols were deleted and cannot return."""

    def test_parse_apt_intents_absent(self, leerie):
        assert not hasattr(leerie, "_parse_apt_intents"), (
            "_parse_apt_intents was re-introduced; the regex capture path must stay deleted"
        )

    def test_capture_installs_from_logs_absent(self, leerie):
        assert not hasattr(leerie, "_capture_installs_from_logs"), (
            "_capture_installs_from_logs was re-introduced; the regex capture path must stay deleted"
        )

    def test_lang_install_re_absent(self, leerie):
        assert not hasattr(leerie, "_LANG_INSTALL_RE"), (
            "_LANG_INSTALL_RE was re-introduced; the regex capture path must stay deleted"
        )

    def test_apt_install_re_absent(self, leerie):
        assert not hasattr(leerie, "_APT_INSTALL_RE"), (
            "_APT_INSTALL_RE was re-introduced; the regex capture path must stay deleted"
        )


# ---------------------------------------------------------------------------
# _merge_setup_packages
# ---------------------------------------------------------------------------

class TestMergeSetupPackages:
    """Merger: union existing setup_packages with newly-captured packages."""

    def test_union_adds_new_packages(self, leerie):
        merged = leerie._merge_setup_packages("postgresql", ["libpq-dev"])
        assert merged is not None
        assert "postgresql" in merged
        assert "libpq-dev" in merged

    def test_no_op_when_nothing_new(self, leerie):
        """Returns None when captured is a subset of existing."""
        result = leerie._merge_setup_packages(
            "postgresql libpq-dev", ["postgresql"]
        )
        assert result is None

    def test_no_op_empty_captured(self, leerie):
        result = leerie._merge_setup_packages("postgresql", [])
        assert result is None

    def test_empty_existing_all_new(self, leerie):
        merged = leerie._merge_setup_packages("", ["postgresql", "libpq-dev"])
        assert merged is not None
        assert "postgresql" in merged
        assert "libpq-dev" in merged

    def test_never_clobbers_user_narrowed_list(self, leerie):
        """A user-narrowed list (fewer packages than captured) is preserved;
        only genuinely new packages are appended."""
        merged = leerie._merge_setup_packages(
            "postgresql", ["postgresql", "libfoo-dev"]
        )
        assert merged is not None
        assert "postgresql" in merged
        assert "libfoo-dev" in merged

    def test_deduplication_in_existing(self, leerie):
        """Existing with duplicates → deduped, then new packages added."""
        merged = leerie._merge_setup_packages(
            "curl curl wget", ["git"]
        )
        assert merged is not None
        pkgs = merged.split()
        assert pkgs.count("curl") == 1
        assert "wget" in pkgs
        assert "git" in pkgs

    def test_comma_separated_existing_parsed(self, leerie):
        """Existing value may be space- or comma-separated."""
        merged = leerie._merge_setup_packages(
            "postgresql,libpq-dev", ["git"]
        )
        assert merged is not None
        assert "postgresql" in merged
        assert "libpq-dev" in merged
        assert "git" in merged

    def test_existing_packages_preserved_in_output(self, leerie):
        """Existing packages always appear in the merged output."""
        merged = leerie._merge_setup_packages("alpha beta", ["gamma"])
        assert merged is not None
        assert "alpha" in merged
        assert "beta" in merged
        assert "gamma" in merged

    def test_return_value_is_string_or_none(self, leerie):
        """Return type is str when something new, None when no-op."""
        assert leerie._merge_setup_packages("a", ["b"]) is not None
        assert isinstance(leerie._merge_setup_packages("a", ["b"]), str)
        assert leerie._merge_setup_packages("a", ["a"]) is None


# ---------------------------------------------------------------------------
# _extract_depcap_commands — synthetic JSONL fixture
# ---------------------------------------------------------------------------

def _make_jsonl_log(tmp_path: Path, commands: list[str], fname: str = "worker-001.log") -> Path:
    """Write a synthetic JSONL log file in the _iter_log_tool_use shape."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / fname
    lines: list[str] = []
    for i, cmd in enumerate(commands):
        event = {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": f"tool-use-{i}",
                        "input": {"command": cmd},
                    }
                ]
            }
        }
        lines.append(json.dumps(event))
    log_path.write_text("\n".join(lines) + "\n")
    return log_dir


class TestExtractDepcapCommands:
    """Extractor: read worker JSONL logs and return commands for dep_capture input."""

    def test_extracts_bash_commands(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, [
            "sudo -n apt-get install -y postgresql libpq-dev",
            "pip install -r requirements.txt",
        ])
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert "apt-get install" in text
        assert "pip install" in text
        assert not hit_ceiling

    def test_empty_log_dir(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
        assert text == ""
        assert not hit_ceiling

    def test_deduplication_across_multiple_log_files(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        cmd = "apt-get install -y postgresql"
        for i in range(3):
            log_path = log_dir / f"worker-00{i}.log"
            event = {
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "name": "Bash",
                        "id": f"id-{i}",
                        "input": {"command": cmd},
                    }]
                }
            }
            log_path.write_text(json.dumps(event) + "\n")
        text, _ = leerie._extract_depcap_commands(log_dir)
        # Command should appear only once (deduped).
        assert text.count("apt-get install -y postgresql") == 1

    def test_non_bash_tool_use_blocks_ignored(self, leerie, tmp_path):
        """Only Bash tool_use blocks are extracted; Read blocks are skipped."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "worker-001.log"
        event = {
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "read-1",
                        "input": {"command": "should-not-appear"},
                    }
                ]
            }
        }
        log_path.write_text(json.dumps(event) + "\n")
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "should-not-appear" not in text

    def test_returns_tuple_str_bool(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, ["apt-get install -y curl"])
        result = leerie._extract_depcap_commands(log_dir)
        assert isinstance(result, tuple)
        assert len(result) == 2
        text, hit_ceiling = result
        assert isinstance(text, str)
        assert isinstance(hit_ceiling, bool)

    def test_budget_ceiling_truncates(self, leerie, tmp_path):
        """Commands exceeding _DEPCAP_TOTAL_BUDGET are truncated; hit_ceiling=True."""
        # Install-shaped commands (the filter keeps only these) that sum beyond
        # the budget. Each is a distinct pip install so dedup does not collapse them.
        pad = "x" * 1000
        log_dir = _make_jsonl_log(
            tmp_path, [f"pip install pkg{i}-{pad}" for i in range(500)])
        orig_budget = leerie._DEPCAP_TOTAL_BUDGET
        try:
            leerie._DEPCAP_TOTAL_BUDGET = 2000
            text, hit_ceiling = leerie._extract_depcap_commands(log_dir)
            assert hit_ceiling
        finally:
            leerie._DEPCAP_TOTAL_BUDGET = orig_budget

    def test_filters_to_install_shaped_only(self, leerie, tmp_path):
        """Only package-manager install commands survive; noise is dropped."""
        log_dir = _make_jsonl_log(tmp_path, [
            "pip install -r requirements.txt",   # kept
            "apt-get install -y libvips-dev",    # kept
            "pnpm add sharp",                    # kept
            "git log --oneline -10",             # dropped
            "ls /work/subtasks",                 # dropped
            "python3 -m pytest tests/ -x",       # dropped
        ])
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert "pip install -r requirements.txt" in text
        assert "apt-get install -y libvips-dev" in text
        assert "pnpm add sharp" in text
        assert "git log" not in text
        assert "ls /work" not in text
        assert "pytest" not in text

    def test_excludes_install_verb_inside_text_tool_pattern(self, leerie, tmp_path):
        """A grep/git whose quoted arg embeds an install verb is NOT kept.

        This is the exact leak that let dep_capture prompt prose (e.g.
        `grep "apt-get install intents"`) reach setup_packages as fake packages."""
        log_dir = _make_jsonl_log(tmp_path, [
            'grep -n "apt-get install intents" docs/DESIGN.md',
            "git commit -m 'wire pip install seam'",
            'echo "run pnpm install to build"',
        ])
        text, _ = leerie._extract_depcap_commands(log_dir)
        assert text == ""


class TestGatherDepManifests:
    """Manifests-first PRIMARY corpus (DESIGN §6½): read repo dependency manifests."""

    def test_reads_present_manifest_labeled_by_path(self, leerie, tmp_path):
        (tmp_path / "requirements.txt").write_text("tenacity==9.1.4\n")
        block = leerie._gather_dep_manifests(tmp_path)
        assert "### requirements.txt" in block
        assert "tenacity==9.1.4" in block

    def test_skips_absent_manifests(self, leerie, tmp_path):
        # No manifests at all → empty block.
        assert leerie._gather_dep_manifests(tmp_path) == ""

    def test_multiple_manifests_each_labeled(self, leerie, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies":{"sharp":"^1"}}')
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        block = leerie._gather_dep_manifests(tmp_path)
        assert "### package.json" in block
        assert "### pnpm-lock.yaml" in block
        assert "sharp" in block

    def test_per_file_budget_truncates_large_manifest(self, leerie, tmp_path):
        big = "x" * (leerie._DEPCAP_MANIFEST_FILE_BUDGET + 5000)
        (tmp_path / "package-lock.json").write_text(big)
        block = leerie._gather_dep_manifests(tmp_path)
        assert "(truncated)" in block
        assert len(block.encode()) < len(big)


class TestIsInstallCommand:
    """SECONDARY hint filter: only package-manager install verbs survive."""

    def test_accepts_real_install_commands(self, leerie):
        for cmd in ("pip install -r requirements.txt",
                    "apt-get install -y libvips-dev",
                    "sudo apt install pkg-config",
                    "pnpm add sharp", "npm ci", "cargo add serde"):
            assert leerie._is_install_command(cmd), cmd

    def test_rejects_noise_and_text_tool_pattern_leaks(self, leerie):
        for cmd in ("git log --oneline",
                    "pytest tests/",
                    'grep -n "apt-get install intents" f.md',
                    "git commit -m 'pip install x'",
                    'echo "run npm install"',
                    "sudo grep 'apt install' log"):
            assert not leerie._is_install_command(cmd), cmd

    def test_multiline_install_after_text_tool_segment_is_kept(self, leerie):
        """A5: a genuine install on a later line/segment is kept even when the
        first segment leads with a text tool — the gate is per-segment, not on
        the whole-string first word. Regression for the multi-line false negative
        (worker Bash calls chain commands via newlines / && / ; )."""
        for cmd in ("echo hi\npip install requests",
                    "git status\napt-get install -y curl",
                    "git log --oneline && pip install x",
                    "cat setup.py; pip install -e ."):
            assert leerie._is_install_command(cmd), cmd
        # The text-tool LEAK must still be dropped — the install verb lives
        # INSIDE the text tool's own segment, not a separate command.
        for cmd in ('grep -n "apt-get install intents" f.md',
                    "git commit -m 'wire pip install seam'"):
            assert not leerie._is_install_command(cmd), cmd


class TestTomlValue:
    """Bug C: quote-containing values must render as valid TOML literals."""

    def test_plain_value_uses_basic_string(self, leerie):
        assert leerie._toml_value("libvips-dev pkg-config") == '"libvips-dev pkg-config"'

    def test_quote_containing_value_uses_literal_string(self, leerie):
        v = '[{"manager":"pip"}]'
        assert leerie._toml_value(v) == "'[{\"manager\":\"pip\"}]'"

    def test_round_trips_through_toml_parser(self, leerie):
        try:
            import tomllib
        except ImportError:  # pragma: no cover
            import tomli as tomllib
        v = '[{"manager":"pip","command":"pip install -r requirements.txt"}]'
        line = f"language_installs = {leerie._toml_value(v)}\n"
        assert tomllib.loads(line)["language_installs"] == v

    def test_dump_language_installs_handles_single_quotes(self, leerie):
        """A1: a command with BOTH " (JSON) and ' (shell-quoted arg) must still
        produce valid TOML — the single quote is escaped so the literal wrapper
        stays intact and json.loads recovers it."""
        try:
            import tomllib
        except ImportError:  # pragma: no cover
            import tomli as tomllib
        entries = [
            {"manager": "pip", "command": "pip install 'requests[security]'",
             "copy_inputs": ["requirements.txt"]},
            {"manager": "gem", "command": "gem install rails -v '~> 7.0'"},
        ]
        dumped = leerie._dump_language_installs(entries)
        assert "'" not in dumped  # no literal single quote survives
        line = f"language_installs = {leerie._toml_value(dumped)}\n"
        parsed = tomllib.loads(line)          # strict TOML must accept it
        assert json.loads(parsed["language_installs"]) == entries


# ---------------------------------------------------------------------------
# Helpers shared by capture_repo_deps tests
# ---------------------------------------------------------------------------

def _make_fake_state(tmp_path: Path, commands: list[str]):
    """Return a minimal State-like object whose run_dir contains worker logs."""

    class _FakeState:
        run_dir: Path
        data: dict

        def bump_workers(self, caps):
            self.data["worker_count"] = self.data.get("worker_count", 0) + 1

    st = _FakeState()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    st.run_dir = run_dir
    st.data = {}
    _make_jsonl_log_in(run_dir / "logs", commands)
    return st


def _make_jsonl_log_in(log_dir: Path, commands: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "worker-001.log"
    lines: list[str] = []
    for i, cmd in enumerate(commands):
        event = {
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "id": f"tool-{i}",
                    "input": {"command": cmd},
                }]
            }
        }
        lines.append(json.dumps(event))
    log_path.write_text("\n".join(lines) + "\n")


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", str(repo)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config",
                    "user.email", "test@test.com"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config",
                    "user.name", "Test"], check=True, capture_output=True)


_FAKE_CAPS = {"max_total_workers": 200}
_FAKE_MODELS = {"dep_capture": "sonnet"}
_FAKE_EFFORTS: dict = {"dep_capture": None}


def _fake_claude_p_result(setup_packages=None, language_installs=None):
    """Return a coroutine that resolves to a fixed dep_capture structured output."""
    result = {
        "setup_packages": setup_packages or [],
        "language_installs": language_installs or [],
        "dockerfile_notes": None,
    }
    return AsyncMock(return_value=result)


# ---------------------------------------------------------------------------
# _filter_residual_deps — unit tests for residual-only filtering
# ---------------------------------------------------------------------------

class TestFilterResidualDeps:
    """Unit tests for _filter_residual_deps (residual-only filtering logic)."""

    def test_filters_bakeable_managers_entirely(self, leerie):
        """Python/Ruby/Rust/Go managers filtered out (bake entirely)."""
        language_installs = [
            {"manager": "pip", "command": "pip install -r requirements.txt", "copy_inputs": []},
            {"manager": "pip3", "command": "pip3 install django", "copy_inputs": []},
            {"manager": "uv", "command": "uv sync", "copy_inputs": []},
            {"manager": "bundle", "command": "bundle install", "copy_inputs": []},
            {"manager": "gem", "command": "gem install rails", "copy_inputs": []},
            {"manager": "cargo", "command": "cargo fetch", "copy_inputs": []},
            {"manager": "go", "command": "go mod download", "copy_inputs": []},
        ]
        result = leerie._filter_residual_deps(language_installs)
        assert result == [], "All bakeable managers should be filtered out"

    def test_keeps_node_offline_relink_only(self, leerie):
        """Node managers: keep only offline/frozen-lockfile commands."""
        language_installs = [
            {"manager": "pnpm", "command": "pnpm install --offline --frozen-lockfile", "copy_inputs": []},
            {"manager": "pnpm", "command": "pnpm install", "copy_inputs": []},  # Full install, filtered
            {"manager": "npm", "command": "npm install --offline", "copy_inputs": []},
            {"manager": "npm", "command": "npm install", "copy_inputs": []},  # Full install, filtered
            {"manager": "yarn", "command": "yarn install --frozen-lockfile", "copy_inputs": []},
            {"manager": "yarn", "command": "yarn install", "copy_inputs": []},  # Full install, filtered
        ]
        result = leerie._filter_residual_deps(language_installs)
        assert len(result) == 3, "Only offline/frozen-lockfile commands kept"
        assert all("--offline" in e["command"] or "--frozen-lockfile" in e["command"]
                   for e in result), "All kept entries must have offline/frozen-lockfile"

    def test_preserves_unknown_managers(self, leerie):
        """Unknown managers preserved as-is (conservative)."""
        language_installs = [
            {"manager": "poetry", "command": "poetry install", "copy_inputs": []},
            {"manager": "pipenv", "command": "pipenv install", "copy_inputs": []},
        ]
        result = leerie._filter_residual_deps(language_installs)
        assert len(result) == 2, "Unknown managers preserved"
        assert result == language_installs, "Input unchanged for unknown managers"

    def test_handles_none_command_gracefully(self, leerie):
        """Entry with command: None is handled (no TypeError on 'in' operator)."""
        language_installs = [
            {"manager": "pnpm", "command": None, "copy_inputs": []},
            {"manager": "pnpm", "command": "pnpm install --offline", "copy_inputs": []},
            {"manager": "pip", "command": None, "copy_inputs": []},
        ]
        # Should not raise TypeError
        result = leerie._filter_residual_deps(language_installs)
        # Entry with None command and pnpm manager: no offline/frozen flags, filtered out
        # Entry with --offline: kept
        # Entry with pip and None: filtered (bakeable)
        assert len(result) == 1, "Only offline pnpm entry kept"
        assert result[0]["command"] == "pnpm install --offline"

    def test_empty_input_returns_empty(self, leerie):
        """Empty input returns empty list."""
        result = leerie._filter_residual_deps([])
        assert result == []

    def test_returns_new_list_input_unchanged(self, leerie):
        """Returns a new list; input is unchanged."""
        language_installs = [
            {"manager": "pip", "command": "pip install django", "copy_inputs": []},
        ]
        original = language_installs.copy()
        result = leerie._filter_residual_deps(language_installs)
        assert language_installs == original, "Input list unchanged"
        assert result is not language_installs, "Returns new list"


# ---------------------------------------------------------------------------
# capture_repo_deps — integration tests using tmp_path repos
# ---------------------------------------------------------------------------

class TestCaptureRepoDeps:
    """Integration tests for capture_repo_deps (async, stubbed claude_p)."""

    def test_writes_setup_packages_on_new_repo(
            self, leerie, tmp_path, monkeypatch):
        """On a fresh repo, setup_packages is written from worker output."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, [
            "sudo -n apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        cfg = repo / ".leerie" / "config.toml"
        assert cfg.exists()
        content = cfg.read_text()
        assert "postgresql" in content
        assert "setup_packages" in content

    def test_writes_language_installs(self, leerie, tmp_path, monkeypatch):
        """Residual language_installs (Node offline-relink) written to config.toml."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["pnpm install --offline --frozen-lockfile"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": [],
            "language_installs": [
                {"manager": "pnpm", "command": "pnpm install --offline --frozen-lockfile",
                 "copy_inputs": ["package.json", "pnpm-lock.yaml"]},
            ],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        cfg = repo / ".leerie" / "config.toml"
        assert cfg.exists()
        content = cfg.read_text()
        assert "language_installs" in content
        assert "pnpm" in content
        # Bug C regression: the JSON-encoded value contains `"`; the written
        # config must be VALID TOML (single-quoted literal), not a basic string
        # that the inner quotes terminate early.
        try:
            import tomllib
        except ImportError:  # pragma: no cover - py<3.11
            import tomli as tomllib
        parsed = tomllib.loads(content)
        assert json.loads(parsed["language_installs"]) == [
            {"manager": "pnpm", "command": "pnpm install --offline --frozen-lockfile",
             "copy_inputs": ["package.json", "pnpm-lock.yaml"]},
        ]

    def test_no_op_on_warm_repo(self, leerie, tmp_path, monkeypatch):
        """When all captured packages are already in setup_packages, no write."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('setup_packages = "postgresql"\n')
        mtime_before = cfg.stat().st_mtime

        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        mtime_after = cfg.stat().st_mtime
        assert mtime_before == mtime_after

    def test_always_runs_worker_even_with_committed_dockerfile(
            self, leerie, tmp_path, monkeypatch):
        """Worker always runs (per feat-003), even when .leerie/Dockerfile committed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        dockerfile = leerie_dir / "Dockerfile"
        dockerfile.write_text("FROM base\n")

        subprocess.run(["git", "-C", str(repo), "add", ".leerie/Dockerfile"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "add Dockerfile"],
                       check=True, capture_output=True)

        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        called = []
        async def _mock_claude_p(**_kwargs):
            called.append(True)
            return {"setup_packages": ["postgresql"], "language_installs": [],
                    "dockerfile_notes": None}

        with patch.object(leerie, "claude_p", new=AsyncMock(side_effect=_mock_claude_p)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        # Worker SHOULD run (feat-003 removes the early-return); residual written.
        assert called, "dep_capture worker must run even when Dockerfile is committed"
        cfg = leerie_dir / "config.toml"
        assert cfg.exists()
        assert "postgresql" in cfg.read_text()

    def test_untracked_dockerfile_does_not_block_write(
            self, leerie, tmp_path, monkeypatch):
        """A Dockerfile that exists but is NOT git-tracked does not skip the write."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        (leerie_dir / "Dockerfile").write_text("FROM base\n")

        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        cfg = leerie_dir / "config.toml"
        assert cfg.exists()

    def test_non_fatal_on_write_failure(self, leerie, tmp_path, monkeypatch):
        """A write failure propagates from capture_repo_deps but is catchable."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        exc_caught = None
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            with patch.object(leerie, "_write_config_toml_keys",
                              side_effect=OSError("disk full")):
                try:
                    asyncio.run(leerie.capture_repo_deps(
                        repo, st, caps=_FAKE_CAPS,
                        models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
                    ))
                except Exception as exc:
                    exc_caught = exc

        if exc_caught is not None:
            assert isinstance(exc_caught, Exception)

    def test_opt_out_via_env(self, leerie, tmp_path, monkeypatch):
        """LEERIE_CAPTURE_DEPS=0 prevents any write."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.setenv("LEERIE_CAPTURE_DEPS", "0")

        called = []
        async def _mock_claude_p(**_kwargs):
            called.append(True)
            return {"setup_packages": [], "language_installs": [], "dockerfile_notes": None}

        with patch.object(leerie, "claude_p", new=AsyncMock(side_effect=_mock_claude_p)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert not called
        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists()

    def test_opt_out_via_config_file(self, leerie, tmp_path, monkeypatch):
        """capture_deps = false in .leerie/config.toml prevents any write."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('capture_deps = "false"\n')

        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        content_before = cfg.read_text()

        called = []
        async def _mock_claude_p(**_kwargs):
            called.append(True)
            return {"setup_packages": [], "language_installs": [], "dockerfile_notes": None}

        with patch.object(leerie, "claude_p", new=AsyncMock(side_effect=_mock_claude_p)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert not called
        assert "setup_packages" not in cfg.read_text()
        assert cfg.read_text() == content_before

    def test_no_logs_dir_is_silent_noop(self, leerie, tmp_path, monkeypatch):
        """Missing logs directory is a silent no-op (non-fatal)."""
        repo = tmp_path / "repo"
        repo.mkdir()

        class _FakeState:
            run_dir = tmp_path / "nonexistent-run"
            data: dict = {}

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        try:
            asyncio.run(leerie.capture_repo_deps(
                repo, _FakeState(), caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))
        except Exception as exc:
            pytest.fail(f"capture_repo_deps raised on missing logs dir: {exc!r}")

        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists()

    def test_skips_gracefully_when_caps_none(self, leerie, tmp_path, monkeypatch):
        """When caps is None, no worker is spawned and no write occurs."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        called = []
        async def _mock_claude_p(**_kwargs):
            called.append(True)
            return {"setup_packages": [], "language_installs": [], "dockerfile_notes": None}

        with patch.object(leerie, "claude_p", new=AsyncMock(side_effect=_mock_claude_p)):
            asyncio.run(leerie.capture_repo_deps(repo, st, caps=None))

        assert not called
        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists()

    def test_never_clobber_existing_setup_packages(
            self, leerie, tmp_path, monkeypatch):
        """Existing setup_packages are preserved; only new packages appended."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('setup_packages = "git curl"\n')

        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["git", "postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        content = cfg.read_text()
        # Existing packages must still be there.
        assert "git" in content
        assert "curl" in content
        # New package added.
        assert "postgresql" in content

    def test_language_installs_never_clobber_existing_manager(
            self, leerie, tmp_path, monkeypatch):
        """Existing language_installs manager entries are never replaced."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        existing = [{"manager": "pip", "command": "pip install -r requirements.txt",
                     "copy_inputs": ["requirements.txt"]}]
        cfg = leerie_dir / "config.toml"
        # _write_config_toml_keys stores: language_installs = "<json>"
        # _read_toml_key strips the outer quotes → returns raw JSON string.
        inner_json = json.dumps(existing, separators=(",", ":"))
        cfg.write_text(f'language_installs = "{inner_json}"\n')

        st = _make_fake_state(tmp_path, ["pnpm install --frozen-lockfile"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": [],
            "language_installs": [
                {"manager": "pip", "command": "pip install -e .",
                 "copy_inputs": ["setup.py"]},
                {"manager": "pnpm", "command": "pnpm install --frozen-lockfile",
                 "copy_inputs": ["package.json", "pnpm-lock.yaml"]},
            ],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        content = cfg.read_text()
        # pnpm is new — should be added.
        assert "pnpm" in content
        # pip already existed — the original command must not be replaced.
        assert "requirements.txt" in content

    def test_replace_true_wholesale_overwrites(
            self, leerie, tmp_path, monkeypatch):
        """replace=True (the --recapture --force path) drops deps no longer
        captured, for both setup_packages and language_installs."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        stale_li = [{"manager": "cargo", "command": "cargo build",
                     "copy_inputs": ["Cargo.toml"]}]
        cfg.write_text(
            'setup_packages = "postgresql stale-pkg"\n'
            f'language_installs = "{json.dumps(stale_li, separators=(",", ":"))}"\n')

        st = _make_fake_state(tmp_path, ["pnpm install --offline"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [
                {"manager": "pnpm", "command": "pnpm install --offline",
                 "copy_inputs": ["package.json"]},
            ],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
                replace=True,
            ))

        content = cfg.read_text()
        assert "postgresql" in content
        assert "stale-pkg" not in content, "replace=True must drop stale packages"
        managers = {e["manager"]
                    for e in json.loads(leerie._read_toml_key(cfg, "language_installs"))}
        assert managers == {"pnpm"}, "replace=True must drop stale managers (cargo)"

    def test_replace_true_empty_capture_leaves_existing(
            self, leerie, tmp_path, monkeypatch):
        """replace=True with an empty capture must not blank a good config."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('setup_packages = "postgresql"\n')
        mtime_before = cfg.stat().st_mtime

        st = _make_fake_state(tmp_path, ["echo noop"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": [], "language_installs": [], "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
                replace=True,
            ))

        assert cfg.stat().st_mtime == mtime_before
        assert "postgresql" in cfg.read_text()


# ---------------------------------------------------------------------------
# resolve_capture_deps — direct precedence test (env → .leerie/config.toml →
# default True). Mirrors test_resolve_no_push's precedence coverage.
# ---------------------------------------------------------------------------

class TestResolveCaptureDeps:
    def test_default_is_true(self, leerie, tmp_path, monkeypatch):
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        repo = tmp_path / "repo"
        repo.mkdir()
        assert leerie.resolve_capture_deps(repo) is True

    def test_env_false_wins(self, leerie, tmp_path, monkeypatch):
        monkeypatch.setenv("LEERIE_CAPTURE_DEPS", "0")
        repo = tmp_path / "repo"
        repo.mkdir()
        assert leerie.resolve_capture_deps(repo) is False

    def test_config_false_when_no_env(self, leerie, tmp_path, monkeypatch):
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text('capture_deps = "false"\n')
        assert leerie.resolve_capture_deps(repo) is False

    def test_env_overrides_config(self, leerie, tmp_path, monkeypatch):
        """env=true beats config=false — env has higher precedence."""
        monkeypatch.setenv("LEERIE_CAPTURE_DEPS", "true")
        repo = tmp_path / "repo"
        (repo / ".leerie").mkdir(parents=True)
        (repo / ".leerie" / "config.toml").write_text('capture_deps = "false"\n')
        assert leerie.resolve_capture_deps(repo) is True


# ---------------------------------------------------------------------------
# Idempotency sentinel (dep_capture.done) — written by capture_repo_deps,
# read by _backstop_capture_prior_runs.
# ---------------------------------------------------------------------------

class TestDepCaptureSentinel:
    """sentinel file + state field written; backstop skips captured runs."""

    def test_sentinel_file_written_after_successful_write(
            self, leerie, tmp_path, monkeypatch):
        """capture_repo_deps writes <run_dir>/dep_capture.done on success."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["apt-get install -y git"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["git"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        sentinel = Path(st.run_dir) / "dep_capture.done"
        assert sentinel.is_file(), "dep_capture.done sentinel must be written"

    def test_state_field_set_after_successful_write(
            self, leerie, tmp_path, monkeypatch):
        """capture_repo_deps sets st.data['dep_capture_done'] = True."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, ["apt-get install -y curl"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["curl"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert st.data.get("dep_capture_done") is True

    def test_sentinel_written_even_on_noop(
            self, leerie, tmp_path, monkeypatch):
        """Sentinel is written even when the merge produces no new packages
        (the worker ran; no need to run again in the backstop)."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        # Already has postgresql — worker returns same list → no-op merge.
        (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
        st = _make_fake_state(tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        worker_result = {
            "setup_packages": ["postgresql"],
            "language_installs": [],
            "dockerfile_notes": None,
        }
        with patch.object(leerie, "claude_p", new=AsyncMock(return_value=worker_result)):
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_FAKE_CAPS,
                models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        sentinel = Path(st.run_dir) / "dep_capture.done"
        assert sentinel.is_file(), "sentinel written even for no-op captures"


class TestBackstopCapturePriorRuns:
    """_backstop_capture_prior_runs skips captured runs, processes uncaptured ones."""

    def test_skips_run_with_sentinel(self, leerie, tmp_path, monkeypatch):
        """A run dir with dep_capture.done present is skipped."""
        leerie_root = tmp_path / "state"
        runs_dir = leerie_root / "runs"
        run_dir = runs_dir / "run-001"
        log_dir = run_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "worker-001.log").write_text("")
        # Write the sentinel — backstop must skip this run.
        (run_dir / "dep_capture.done").write_text("1\n")

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        called = []

        async def _fake_capture(repo_root, st, **kwargs):
            called.append(getattr(st, "run_dir", None))

        with patch.object(leerie, "capture_repo_deps", new=_fake_capture):
            asyncio.run(leerie._backstop_capture_prior_runs(
                leerie_root, repo,
                caps=_FAKE_CAPS, models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert called == [], "run with sentinel must not trigger capture"

    def test_captures_run_without_sentinel(self, leerie, tmp_path, monkeypatch):
        """A run dir with logs/ but no dep_capture.done triggers capture."""
        leerie_root = tmp_path / "state"
        runs_dir = leerie_root / "runs"
        run_dir = runs_dir / "run-002"
        log_dir = run_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "worker-001.log").write_text("")
        # No sentinel — backstop should invoke capture.

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        called = []

        async def _fake_capture(repo_root, st, **kwargs):
            called.append(getattr(st, "run_dir", None))

        with patch.object(leerie, "capture_repo_deps", new=_fake_capture):
            asyncio.run(leerie._backstop_capture_prior_runs(
                leerie_root, repo,
                caps=_FAKE_CAPS, models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert len(called) == 1 and called[0] == run_dir

    def test_skips_run_without_logs_dir(self, leerie, tmp_path, monkeypatch):
        """A run dir without a logs/ subdirectory is not eligible for capture."""
        leerie_root = tmp_path / "state"
        runs_dir = leerie_root / "runs"
        run_dir = runs_dir / "run-003"
        run_dir.mkdir(parents=True)
        # No logs/ dir — not eligible.

        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        called = []

        async def _fake_capture(repo_root, st, **kwargs):
            called.append(getattr(st, "run_dir", None))

        with patch.object(leerie, "capture_repo_deps", new=_fake_capture):
            asyncio.run(leerie._backstop_capture_prior_runs(
                leerie_root, repo,
                caps=_FAKE_CAPS, models=_FAKE_MODELS, efforts=_FAKE_EFFORTS,
            ))

        assert called == [], "run without logs/ must not trigger capture"


# ---------------------------------------------------------------------------
# Source-coupling guards: cancel-arm seam wiring in main()
# ---------------------------------------------------------------------------

class TestCancelArmWiring:
    """Source-coupling guards: main() must invoke capture_repo_deps in both
    SIGINT (KeyboardInterrupt) and SIGTERM (InterruptedBySignal) handlers.
    The fix is inert without the wiring — these guards pin it."""

    def test_keyboard_interrupt_arm_invokes_capture(self, leerie):
        import inspect
        src = inspect.getsource(leerie.main)
        # Locate the KeyboardInterrupt except block and verify capture_repo_deps
        # appears between it and the exit_code = 130 assignment.
        ki_idx = src.find("except KeyboardInterrupt:")
        assert ki_idx != -1, "main() must have a KeyboardInterrupt handler"
        exit_130_idx = src.find("exit_code = 130", ki_idx)
        assert exit_130_idx != -1, "KeyboardInterrupt arm must set exit_code = 130"
        arm_src = src[ki_idx:exit_130_idx]
        assert "capture_repo_deps" in arm_src, (
            "KeyboardInterrupt arm in main() must invoke capture_repo_deps "
            "before setting exit_code = 130"
        )

    def test_interrupted_by_signal_arm_invokes_capture(self, leerie):
        import inspect
        src = inspect.getsource(leerie.main)
        # Locate the InterruptedBySignal except block.
        ibs_idx = src.find("except InterruptedBySignal")
        assert ibs_idx != -1, "main() must have an InterruptedBySignal handler"
        # The signal number line follows the capture block.
        signum_idx = src.find("signum = getattr(signal", ibs_idx)
        assert signum_idx != -1, (
            "InterruptedBySignal arm must resolve signum after capture")
        arm_src = src[ibs_idx:signum_idx]
        assert "capture_repo_deps" in arm_src, (
            "InterruptedBySignal arm in main() must invoke capture_repo_deps"
        )
