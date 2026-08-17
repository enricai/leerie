"""Tests for `_fingerprint_to_worker_type` and `_parse_claude_cli_version_string`.

Both are best-effort, degrade-gracefully helpers with no direct prior test:
`_fingerprint_to_worker_type` (leerie.py) is a diagnostic-naming reverse
lookup for constrained-decoding fallback logs; `_parse_claude_cli_version_string`
is a User-Agent version probe for Probe A rate-limit avoidance (DESIGN §6
*Multi-token rotation*).

Per the subtask's investigation_notes, the fingerprint test builds its
expectations from SCHEMAS / _structured_output_fingerprint's own
canonicalization directly rather than hardcoding fingerprint hashes — the
same discipline CLAUDE.md's collect-subtrees.sh drift incident argues for.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest


class TestFingerprintToWorkerType:
    def test_covers_every_worker_type_with_a_schema(self, leerie):
        mapping = leerie._fingerprint_to_worker_type()
        expected_workers = {w for w in leerie.WORKER_TYPES if w in leerie.SCHEMAS}
        assert expected_workers, "sanity: WORKER_TYPES ∩ SCHEMAS must be non-empty"
        assert set(mapping.values()) >= expected_workers

    def test_fingerprint_matches_the_real_canonicalization(self, leerie):
        # Build the expected fingerprint the same way _structured_output_fingerprint
        # does for a request's input_schema, not a hardcoded hash.
        mapping = leerie._fingerprint_to_worker_type()
        for worker, schema in leerie.SCHEMAS.items():
            canonical = json.dumps(schema, sort_keys=True)
            fp = hashlib.sha256(canonical.encode()).hexdigest()[:16]
            assert mapping[fp] == worker

    def test_round_trips_through_structured_output_fingerprint(self, leerie):
        # A request body carrying SCHEMAS[worker] as its tool's input_schema
        # must resolve back to that worker via _worker_type_for_fingerprint.
        for worker, schema in leerie.SCHEMAS.items():
            body = json.dumps({
                "tools": [{
                    "name": leerie._STRICT_OUTPUT_TOOL_NAME,
                    "input_schema": schema,
                }],
            }).encode()
            fp = leerie._structured_output_fingerprint(body)
            assert fp is not None
            assert leerie._worker_type_for_fingerprint(fp) == worker

    def test_unknown_fingerprint_resolves_to_none(self, leerie):
        assert leerie._worker_type_for_fingerprint("deadbeefdeadbeef") is None

    def test_none_fingerprint_resolves_to_none(self, leerie):
        assert leerie._worker_type_for_fingerprint(None) is None

    def test_result_is_cached(self, leerie):
        # @functools.lru_cache(maxsize=1) — same dict object across calls.
        assert leerie._fingerprint_to_worker_type() is leerie._fingerprint_to_worker_type()


class TestParseClaudeCliVersionString:
    def test_returns_version_on_success(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="2.1.210 (Claude Code)\n", stderr="")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "2.1.210"

    def test_falls_back_on_missing_binary(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("claude not found")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"

    def test_falls_back_on_nonzero_exit(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="error")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"

    def test_falls_back_on_malformed_output(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="not a version\n", stderr="")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"

    def test_falls_back_on_timeout(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["claude", "--version"], timeout=10)

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"

    def test_never_raises_on_arbitrary_oserror(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"

    def test_empty_stdout_falls_back(self, leerie, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(leerie.subprocess, "run", fake_run)
        assert leerie._parse_claude_cli_version_string() == "0.0.0"
