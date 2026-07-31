"""Secrets-hygiene tests for multi-token OAuth rotation (DESIGN §6
*Multi-token rotation*).

The raw token value must never appear in `calls.ndjson`, `run.json`, or
any log line — only its fingerprint (`_token_fingerprint`). `state.json`
is the one sanctioned exception (local-orchestrator-owned, never
published) via the single `active_oauth_token` field.

Drives both the start-of-run selection path (`select_active_oauth_token`)
and the mid-run failover path (`claude_p`'s rotation arm) against real
on-disk state.json / calls.ndjson, plus captured log output, and greps
all three for the literal token strings used in the scenario.
"""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest

_RAW_TOKEN_A = "sk-ant-oat01-SUPER-SECRET-TOKEN-A-do-not-leak"
_RAW_TOKEN_B = "sk-ant-oat01-SUPER-SECRET-TOKEN-B-do-not-leak"


def _probe_response_body(five_hour_util, seven_day_util):
    return json.dumps({
        "five_hour": {"utilization": five_hour_util,
                      "resets_at": "2026-08-01T05:00:00.000000+00:00"},
        "seven_day": {"utilization": seven_day_util,
                      "resets_at": "2026-08-05T05:00:00.000000+00:00"},
        "seven_day_opus": None,
        "seven_day_sonnet": None,
    }).encode()


class _FakeProbeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(utils_by_token: dict):
    def fake_urlopen(req, timeout=None):
        token = req.headers["Authorization"].split(" ")[1]
        utils = utils_by_token.get(token)
        if utils is None:
            raise urllib.error.HTTPError(
                req.full_url, 500, "x", {}, io.BytesIO(b""))
        return _FakeProbeResponse(_probe_response_body(*utils))
    return fake_urlopen


def _grep_file_for_token(path, token: str) -> bool:
    if not path.exists():
        return False
    return token in path.read_text()


class TestStartOfRunSelectionHygiene:
    def test_no_raw_token_in_state_json_logs_or_ndjson(
            self, leerie, monkeypatch, tmp_path, capsys):
        run_id = "test-run-hygiene-001"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)

        st = leerie.State(tmp_path, run_id)
        st.data = {"task": "test", "started_at": "2026-08-01T00:00:00Z"}
        st.save()

        monkeypatch.setattr(
            leerie.urllib.request, "urlopen",
            _fake_urlopen_factory({_RAW_TOKEN_A: (5.0, 5.0),
                                   _RAW_TOKEN_B: (50.0, 50.0)}))
        monkeypatch.setenv(
            "CLAUDE_CODE_OAUTH_TOKENS", f"{_RAW_TOKEN_A},{_RAW_TOKEN_B}")
        leerie._TOKEN_PROBE_CACHE.clear()

        asyncio.run(leerie.select_active_oauth_token(
            st, dict(leerie.DEFAULT_CAPS)))

        # active_oauth_token IS allowed to hold the raw token in state.json
        # (the one sanctioned exception) — but nowhere else.
        assert st.data["active_oauth_token"] == _RAW_TOKEN_A

        state_path = run_dir / "state.json"
        assert state_path.exists()
        state_text = state_path.read_text()
        assert _RAW_TOKEN_A in state_text  # the sanctioned exception

        ndjson_path = run_dir / "calls.ndjson"
        assert not _grep_file_for_token(ndjson_path, _RAW_TOKEN_A)
        assert not _grep_file_for_token(ndjson_path, _RAW_TOKEN_B)

        run_json_path = run_dir / "run.json"
        assert not _grep_file_for_token(run_json_path, _RAW_TOKEN_A)
        assert not _grep_file_for_token(run_json_path, _RAW_TOKEN_B)

        log_output = capsys.readouterr().out
        assert _RAW_TOKEN_A not in log_output
        assert _RAW_TOKEN_B not in log_output


class TestMidRunFailoverHygiene:
    def test_no_raw_token_in_calls_ndjson_or_logs_during_rotation(
            self, leerie, monkeypatch, tmp_path, capsys):
        run_id = "test-run-hygiene-002"
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True)

        st = leerie.State(tmp_path, run_id)
        st.data = {
            "task": "test",
            "started_at": "2026-08-01T00:00:00Z",
            "verbosity": "quiet",
            "active_oauth_token": _RAW_TOKEN_A,
        }
        st.save()
        st.bump_workers = lambda *a, **k: None
        st.add_telemetry = lambda *a, **k: None

        rate_limited = {
            "type": "result", "subtype": "error_during_execution",
            "is_error": True, "api_error_status": 429,
            "result": "rate limit exceeded", "structured_output": None,
        }
        success = {
            "type": "result", "subtype": "success", "is_error": False,
            "result": "{}",
            "structured_output": {"categories": ["feature-implementation"]},
        }
        seq = [dict(rate_limited), dict(success)]

        async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                              **kwargs):
            return seq.pop(0)

        monkeypatch.setattr(leerie, "_invoke", fake_invoke)
        monkeypatch.setattr(
            leerie.urllib.request, "urlopen",
            _fake_urlopen_factory({_RAW_TOKEN_B: (5.0, 5.0)}))
        monkeypatch.setenv(
            "CLAUDE_CODE_OAUTH_TOKENS", f"{_RAW_TOKEN_A},{_RAW_TOKEN_B}")
        leerie._TOKEN_PROBE_CACHE.clear()

        async def run():
            return await leerie.claude_p(
                "do the task", "you are a fit judge", schema_key="fit_judge",
                cwd="/work", allowed_tools="Read", max_turns=60,
                autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
                model="opus", sid="fit-judge-hygiene",
            )

        asyncio.run(run())

        assert st.data["active_oauth_token"] == _RAW_TOKEN_B

        ndjson_path = run_dir / "calls.ndjson"
        assert not _grep_file_for_token(ndjson_path, _RAW_TOKEN_A)
        assert not _grep_file_for_token(ndjson_path, _RAW_TOKEN_B)

        run_json_path = run_dir / "run.json"
        assert not _grep_file_for_token(run_json_path, _RAW_TOKEN_A)
        assert not _grep_file_for_token(run_json_path, _RAW_TOKEN_B)

        log_output = capsys.readouterr().out
        assert _RAW_TOKEN_A not in log_output
        assert _RAW_TOKEN_B not in log_output
        # The fingerprint IS expected to appear in the rotation log line.
        assert leerie._token_fingerprint(_RAW_TOKEN_B) in log_output
