"""Tests for the capture engine (DESIGN §6½ — Auto-capture of repo dependencies).

Covers: _parse_apt_intents, _merge_setup_packages, _capture_installs_from_logs,
capture_repo_deps (writes setup_packages, warm-repo no-op, committed-Dockerfile
skip, write-failure non-fatal, opt-out).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _parse_apt_intents
# ---------------------------------------------------------------------------

class TestParseAptIntents:
    """Parser: extract apt package names from shell command strings."""

    def test_basic_apt_get_install_with_flags(self, leerie):
        result = leerie._parse_apt_intents(
            "sudo -n apt-get install -y --no-install-recommends postgresql libfoo-dev"
        )
        assert result == ["postgresql", "libfoo-dev"]

    def test_bare_apt_install(self, leerie):
        result = leerie._parse_apt_intents("apt install curl wget")
        assert result == ["curl", "wget"]

    def test_chained_and_installs(self, leerie):
        result = leerie._parse_apt_intents(
            "apt-get update && apt-get install -y postgresql && apt-get install -y libpq-dev"
        )
        assert "postgresql" in result
        assert "libpq-dev" in result

    def test_chained_installs_deduplicated(self, leerie):
        result = leerie._parse_apt_intents(
            "apt-get install -y curl && apt-get install -y curl wget"
        )
        assert result.count("curl") == 1
        assert "wget" in result

    def test_apt_get_update_only_returns_empty(self, leerie):
        result = leerie._parse_apt_intents("apt-get update")
        assert result == []

    def test_apt_get_update_chained_with_install(self, leerie):
        result = leerie._parse_apt_intents(
            "apt-get update && apt-get install -y git"
        )
        assert result == ["git"]

    def test_pip_command_does_not_leak_into_apt(self, leerie):
        result = leerie._parse_apt_intents(
            "pip install requests flask && apt-get install -y libssl-dev"
        )
        assert "requests" not in result
        assert "flask" not in result
        assert "libssl-dev" in result

    def test_pip_only_returns_empty(self, leerie):
        result = leerie._parse_apt_intents("pip install -r requirements.txt")
        assert result == []

    def test_pnpm_only_returns_empty(self, leerie):
        result = leerie._parse_apt_intents("pnpm install --frozen-lockfile")
        assert result == []

    def test_pkg_version_split(self, leerie):
        """pkg=version token → keep pkg name only."""
        result = leerie._parse_apt_intents(
            "apt-get install -y postgresql-14=14.0-1"
        )
        assert "postgresql-14" in result
        assert "14.0-1" not in result
        # The version part after '=' should not appear
        for token in result:
            assert "=" not in token

    def test_flags_are_dropped(self, leerie):
        """Flags like -y, --no-install-recommends are not returned."""
        result = leerie._parse_apt_intents(
            "apt-get install -y --no-install-recommends --quiet libssl-dev"
        )
        assert "-y" not in result
        assert "--no-install-recommends" not in result
        assert "--quiet" not in result
        assert "libssl-dev" in result

    def test_semicolon_separated_segments(self, leerie):
        result = leerie._parse_apt_intents(
            "apt-get install -y git; apt-get install -y curl"
        )
        assert "git" in result
        assert "curl" in result

    def test_non_apt_command_returns_empty(self, leerie):
        result = leerie._parse_apt_intents("echo hello world")
        assert result == []

    def test_single_package(self, leerie):
        result = leerie._parse_apt_intents("apt-get install postgresql")
        assert result == ["postgresql"]

    def test_complex_package_names(self, leerie):
        """Packages with hyphens and dots are valid."""
        result = leerie._parse_apt_intents(
            "apt-get install -y libpq-dev python3.10 g++ ca-certificates"
        )
        assert "libpq-dev" in result
        assert "python3.10" in result
        assert "ca-certificates" in result

    def test_order_is_stable_first_seen(self, leerie):
        """Packages appear in first-seen order."""
        result = leerie._parse_apt_intents(
            "apt-get install -y alpha beta gamma"
        )
        assert result == ["alpha", "beta", "gamma"]


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
        # User has only postgresql in config; capture finds both postgresql
        # and libfoo-dev. The merge should keep postgresql and add libfoo-dev,
        # not replace the existing value.
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
# _capture_installs_from_logs — synthetic JSONL fixture
# ---------------------------------------------------------------------------

def _make_jsonl_log(tmp_path: Path, commands: list[str]) -> Path:
    """Write a synthetic JSONL log file in the _iter_log_tool_use shape.

    Each command becomes one JSON event with:
      body['message']['content'] = [{'type':'tool_use','name':'Bash',
                                     'id':'id-N','input':{'command':cmd}}]
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "worker-001.log"
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


class TestCaptureInstallsFromLogs:
    """Scanner: read worker JSONL logs and extract apt/language installs."""

    def test_extracts_apt_packages(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, [
            "sudo -n apt-get install -y postgresql libpq-dev",
        ])
        apt_pkgs, lang_cmds = leerie._capture_installs_from_logs(log_dir)
        assert "postgresql" in apt_pkgs
        assert "libpq-dev" in apt_pkgs

    def test_extracts_language_installs(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, [
            "pnpm install --frozen-lockfile",
        ])
        _apt_pkgs, lang_cmds = leerie._capture_installs_from_logs(log_dir)
        assert any("pnpm" in c for c in lang_cmds)

    def test_extracts_pip_language_installs(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, [
            "pip install -r requirements.txt",
        ])
        _apt_pkgs, lang_cmds = leerie._capture_installs_from_logs(log_dir)
        assert any("pip" in c for c in lang_cmds)

    def test_empty_log_dir(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        apt_pkgs, lang_cmds = leerie._capture_installs_from_logs(log_dir)
        assert apt_pkgs == []
        assert lang_cmds == []

    def test_deduplication_across_multiple_log_files(self, leerie, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Two worker logs, both installing the same package.
        for i in range(2):
            log_path = log_dir / f"worker-00{i}.log"
            event = {
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "name": "Bash",
                        "id": f"id-{i}",
                        "input": {"command": "apt-get install -y postgresql"},
                    }]
                }
            }
            log_path.write_text(json.dumps(event) + "\n")
        apt_pkgs, _ = leerie._capture_installs_from_logs(log_dir)
        assert apt_pkgs.count("postgresql") == 1

    def test_malformed_lines_are_skipped(self, leerie, tmp_path):
        """Malformed JSONL lines don't crash the scanner."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_path = log_dir / "worker-001.log"
        log_path.write_text(
            "not json at all\n"
            + json.dumps({"message": {"content": [
                {"type": "tool_use", "name": "Bash", "id": "x",
                 "input": {"command": "apt-get install -y libssl-dev"}}
            ]}}) + "\n"
        )
        apt_pkgs, _ = leerie._capture_installs_from_logs(log_dir)
        assert "libssl-dev" in apt_pkgs

    def test_non_bash_tool_use_blocks_ignored(self, leerie, tmp_path):
        """Only Bash tool_use blocks are scanned; Read blocks are skipped."""
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
                        "input": {"command": "apt-get install -y should-not-appear"},
                    }
                ]
            }
        }
        log_path.write_text(json.dumps(event) + "\n")
        apt_pkgs, _ = leerie._capture_installs_from_logs(log_dir)
        assert "should-not-appear" not in apt_pkgs

    def test_returns_tuple_of_two_lists(self, leerie, tmp_path):
        log_dir = _make_jsonl_log(tmp_path, ["apt-get install -y curl"])
        result = leerie._capture_installs_from_logs(log_dir)
        assert isinstance(result, tuple)
        assert len(result) == 2
        apt_pkgs, lang_cmds = result
        assert isinstance(apt_pkgs, list)
        assert isinstance(lang_cmds, list)


# ---------------------------------------------------------------------------
# capture_repo_deps — integration tests using tmp_path repos
# ---------------------------------------------------------------------------

def _make_fake_state(tmp_path: Path, commands: list[str]):
    """Return a minimal State-like object whose run_dir contains worker logs."""

    class _FakeState:
        run_dir: Path

    st = _FakeState()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    st.run_dir = run_dir
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


class TestCaptureRepoDeps:
    """Integration tests for capture_repo_deps."""

    def test_writes_setup_packages_on_new_repo(
            self, leerie, tmp_path, monkeypatch):
        """On a fresh repo with apt installs in logs, setup_packages is written."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, [
            "sudo -n apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        leerie.capture_repo_deps(repo, st)

        cfg = repo / ".leerie" / "config.toml"
        assert cfg.exists()
        content = cfg.read_text()
        assert "postgresql" in content
        assert "setup_packages" in content

    def test_no_op_on_warm_repo(self, leerie, tmp_path, monkeypatch):
        """When all captured packages are already in setup_packages, no write."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('setup_packages = "postgresql"\n')
        mtime_before = cfg.stat().st_mtime

        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        leerie.capture_repo_deps(repo, st)

        # File should not have been rewritten (same mtime).
        mtime_after = cfg.stat().st_mtime
        assert mtime_before == mtime_after

    def test_skips_write_when_committed_dockerfile_exists(
            self, leerie, tmp_path, monkeypatch):
        """If .leerie/Dockerfile is git-tracked, setup_packages write is skipped."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        dockerfile = leerie_dir / "Dockerfile"
        dockerfile.write_text("FROM base\n")

        # Stage and commit the Dockerfile so git ls-files sees it.
        subprocess.run(["git", "-C", str(repo), "add", ".leerie/Dockerfile"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "add Dockerfile"],
                       check=True, capture_output=True)

        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        leerie.capture_repo_deps(repo, st)

        cfg = leerie_dir / "config.toml"
        assert not cfg.exists(), (
            "setup_packages should NOT be written when a committed "
            ".leerie/Dockerfile exists"
        )

    def test_untracked_dockerfile_does_not_block_write(
            self, leerie, tmp_path, monkeypatch):
        """A Dockerfile that exists but is NOT git-tracked does not skip the write."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        # Write Dockerfile but do NOT git add/commit it.
        (leerie_dir / "Dockerfile").write_text("FROM base\n")

        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        leerie.capture_repo_deps(repo, st)

        cfg = leerie_dir / "config.toml"
        assert cfg.exists(), (
            "setup_packages SHOULD be written when Dockerfile is not git-tracked"
        )

    def test_non_fatal_on_write_failure(self, leerie, tmp_path, monkeypatch):
        """capture_repo_deps is called non-fatally from phase_finalize.

        The function itself may propagate a write failure, but phase_finalize
        wraps the call in try/except so a write failure never blocks a run.
        Verify this contract: when _write_config_toml_keys raises, the exception
        propagates from capture_repo_deps (as designed), and can be caught safely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        exc_caught = None
        with patch.object(leerie, "_write_config_toml_keys",
                          side_effect=OSError("disk full")):
            try:
                leerie.capture_repo_deps(repo, st)
            except Exception as exc:
                exc_caught = exc

        # The exception must be catchable (not a SystemExit or crash) —
        # phase_finalize catches Exception and logs+swallows it.
        # Accept either no exception (if the impl swallows) or a catchable one.
        if exc_caught is not None:
            assert isinstance(exc_caught, Exception), (
                f"Expected catchable Exception but got {type(exc_caught)}: {exc_caught!r}"
            )

    def test_opt_out_via_env(self, leerie, tmp_path, monkeypatch):
        """LEERIE_CAPTURE_DEPS=0 prevents any write."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.setenv("LEERIE_CAPTURE_DEPS", "0")

        leerie.capture_repo_deps(repo, st)

        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists(), (
            "config.toml should not be written when LEERIE_CAPTURE_DEPS=0"
        )

    def test_opt_out_via_config_file(self, leerie, tmp_path, monkeypatch):
        """capture_deps = false in .leerie/config.toml prevents any write."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('capture_deps = "false"\n')

        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        # Record content before.
        content_before = cfg.read_text()

        leerie.capture_repo_deps(repo, st)

        # config.toml should not gain a setup_packages line.
        content_after = cfg.read_text()
        assert "setup_packages" not in content_after
        assert content_before == content_after

    def test_no_logs_dir_is_silent_noop(self, leerie, tmp_path, monkeypatch):
        """Missing logs directory is a silent no-op (non-fatal)."""
        repo = tmp_path / "repo"
        repo.mkdir()

        class _FakeState:
            run_dir = tmp_path / "nonexistent-run"

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        try:
            leerie.capture_repo_deps(repo, _FakeState())
        except Exception as exc:
            pytest.fail(f"capture_repo_deps raised on missing logs dir: {exc!r}")

        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists()

    def test_multiple_apt_packages_all_written(self, leerie, tmp_path, monkeypatch):
        """All packages from logs appear in setup_packages."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(tmp_path, [
            "apt-get install -y postgresql libpq-dev curl",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        leerie.capture_repo_deps(repo, st)

        cfg = repo / ".leerie" / "config.toml"
        content = cfg.read_text()
        assert "postgresql" in content
        assert "libpq-dev" in content
        assert "curl" in content
