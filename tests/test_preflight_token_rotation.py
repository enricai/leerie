"""The preflight smoke test rotates tokens as every worker call does (DESIGN
§6 *Multi-token rotation*).

Observed on leerie 0.85.0 with three `CLAUDE_CODE_OAUTH_TOKENS`: the smoke
test hit `rate_limit_event status=rejected rateLimitType=seven_day` on the
first token and the run paused for 33 hours, while two seconds later the
`dep_capture` worker the pause runs rotated to a token with runway and succeeded.
Two causes, one shape: `_run_phases` selected the active token only AFTER
`preflight()`, so the smoke test spawned with the launcher's seed (the list's
first token), and the smoke test called `_invoke` directly, so neither
rotation surface `claude_p` has reached it.

Driven end to end: the real fresh-run branch of `_run_phases`, the real
`_select_active_oauth_token` and `preflight()`, and the smoke test through
the real `_invoke` and Landlock shim, with a stub `claude` on PATH that
answers by the token it was handed. Stubbed: the token probe's network call
(`urllib.request.urlopen`), the host checks other files own (cgroup
containment, CLI version, SIGCHLD disposition, disk headroom), and
`_ensure_run_repo_or_die`, the first step after preflight, where the run
stops.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import urllib.error
from datetime import datetime, timezone

import pytest

from tests.conftest import needs_landlock, skip_without_landlock
from tests.test_resume_planning_reentry import _args, _caps

pytestmark = [needs_landlock, skip_without_landlock]

TOK_A = "tok-aaaa-limited"
TOK_B = "tok-bbbb-runway"
TOK_C = "tok-cccc-runway"
# The rejected event's own reset, the active token's live signal.
EVENT_RESET = 1795431600
# A later probe reset for the other token, and an earlier one.
PROBE_RESET_LATE = "2026-11-30T00:00:00+00:00"
PROBE_RESET_SOON = "2026-11-22T12:00:00+00:00"

# Builtins and absolute paths only (no PATH lookups from a stub). Answers by
# the token in its environment: a token listed in REJECT gets the
# protocol-level rejection, one in QUOTA a 429 result envelope, any other a
# success. Each spawn's token is appended to `seen.txt` in its own
# configuration directory, which the smoke test may write and which outlives
# the call (its temp directory does not).
STUB = r'''#!/bin/sh
# `claude --version` (the token probe's User-Agent) has no configuration
# directory to record in.
if [ "$1" = "--version" ]; then printf '%s\n' '2.1.999 (Claude Code)'; exit 0; fi
[ -n "$CLAUDE_CONFIG_DIR" ] || exit 3
printf '%s\n' "$CLAUDE_CODE_OAUTH_TOKEN" >> "$CLAUDE_CONFIG_DIR/seen.txt"
case ",$STUB_REJECT," in
  *",$CLAUDE_CODE_OAUTH_TOKEN,"*)
    printf '%s\n' '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":"seven_day","resetsAt":__RESET__}}'
    exit 1;;
esac
case ",$STUB_QUOTA," in
  *",$CLAUDE_CODE_OAUTH_TOKEN,"*)
    printf '%s\n' '{"type":"result","subtype":"error_during_execution","is_error":true,"api_error_status":429,"result":"rate limit exceeded","session_id":"s1"}'
    exit 0;;
esac
printf '%s\n' '{"type":"result","subtype":"success","is_error":false,"result":"ok","session_id":"s1","structured_output":{"ok":true},"usage":{"input_tokens":1,"output_tokens":1}}'
'''.replace("__RESET__", str(EVENT_RESET))


class _Reached(BaseException):
    """Raised at `_ensure_run_repo_or_die`, the first step after preflight."""


class _ProbeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def run(leerie, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(STUB)
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # `_use_run_tmp` (first thing `_run_phases` does) rewrites these.
    monkeypatch.setenv("TMPDIR", os.environ.get("TMPDIR", "/tmp"))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setattr(leerie, "_SYSTEM_TEMP_DIR", {})

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    state_root = tmp_path / "state"
    st = leerie.State(state_root, "rot-001", repo_root=repo)
    # The smoke test's own configuration directory (`_confined_cli_env`).
    seen = st.run_dir / "workers" / "smoke" / "config" / "seen.txt"

    monkeypatch.setattr(leerie, "_enforce_and_record_cgroup_containment",
                        lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda _p: 0.9)

    async def _stop(*_a, **_k):
        raise _Reached()
    monkeypatch.setattr(leerie, "_ensure_run_repo_or_die", _stop)
    leerie._TOKEN_PROBE_CACHE.clear()

    def drive(*, probes: dict, reject=(), quota=(), tokens=(TOK_A, TOK_B)):
        """`probes`: token -> (five_hour %, seven_day %, resets_at) answered
        by Probe A; a token absent is unprobeable (HTTP 500)."""
        # What the launcher does: the list's first token seeds the
        # singular variable.
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", ",".join(tokens))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", tokens[0])
        def urlopen(req, timeout=None):
            token = req.headers["Authorization"].split(" ")[1]
            if token not in probes:
                raise urllib.error.HTTPError(req.full_url, 500, "err", {},
                                             io.BytesIO(b""))
            five, seven, reset = probes[token]
            return _ProbeResponse(json.dumps({
                "five_hour": {"utilization": five, "resets_at": reset},
                "seven_day": {"utilization": seven, "resets_at": reset},
                "seven_day_opus": None, "seven_day_sonnet": None,
            }).encode())

        monkeypatch.setattr(leerie.urllib.request, "urlopen", urlopen)
        monkeypatch.setenv("STUB_REJECT", ",".join(reject))
        monkeypatch.setenv("STUB_QUOTA", ",".join(quota))
        args = _args(resume=False, task="a trivial task", skip_smoke=False)
        try:
            asyncio.run(leerie._run_phases(
                # The default verbosity, the incident's: the stream event
                # raises `RateLimitedExit` only above quiet/normal.
                args, _caps(leerie), st.run_dir, st, "both", "stream",
                {"classifier": "haiku"}, {}))
        except _Reached:
            pass
        else:  # pragma: no cover - the stop is unconditional
            raise AssertionError("never reached the step after preflight")
        return seen.read_text().split() if seen.exists() else []

    try:
        yield st, drive
    finally:
        leerie._TOKEN_PROBE_CACHE.clear()
        # The smoke test's working directory, which production leaves (two
        # runs of one state root share it) under the system temp dir.
        with contextlib.suppress(OSError):
            os.rmdir(os.path.join(
                leerie._system_temp_dir(), "leerie-smoke-" + hashlib.sha256(
                    str(state_root).encode()).hexdigest()[:12]))


def test_a_limited_selected_token_rotates_rather_than_pausing(leerie, run):
    """The incident: the probe ranks A first (its view is stale), the smoke
    test is rejected on A, and B has runway — the run goes on, on B."""
    st, drive = run
    spawned = drive(probes={TOK_A: (5, 5, PROBE_RESET_LATE),
                            TOK_B: (40, 40, PROBE_RESET_LATE)},
                    reject=(TOK_A,))
    assert spawned == [TOK_A, TOK_B]
    assert st.data["active_oauth_token"] == TOK_B


def test_the_smoke_test_spawns_with_the_selected_token(leerie, run):
    """Selection comes before the run's first `claude -p` call: with A known
    exhausted, the smoke test never spends a spawn on it."""
    st, drive = run
    spawned = drive(probes={TOK_A: (100, 100, PROBE_RESET_LATE),
                            TOK_B: (10, 10, PROBE_RESET_LATE)},
                    reject=(TOK_A,))
    assert spawned == [TOK_B]
    assert st.data["active_oauth_token"] == TOK_B


def test_a_429_result_on_the_smoke_test_rotates_too(leerie, run):
    """The envelope surface: a completed 429 result, not the stream event."""
    st, drive = run
    spawned = drive(probes={TOK_A: (5, 5, PROBE_RESET_LATE),
                            TOK_B: (40, 40, PROBE_RESET_LATE)},
                    quota=(TOK_A,))
    assert spawned == [TOK_A, TOK_B]
    assert st.data["active_oauth_token"] == TOK_B


def test_every_token_limited_pauses_on_the_soonest_reset(leerie, run):
    """Only when no token has runway does the smoke test's limit reach the
    pause — and then at the soonest reset across every token, not the
    first token's."""
    st, drive = run
    with pytest.raises(leerie.RateLimitedExit) as exc:
        drive(probes={TOK_A: (5, 5, PROBE_RESET_LATE),
                      TOK_B: (100, 100, PROBE_RESET_SOON)},
              reject=(TOK_A,))
    # B's probe already shows no runway: no spawn is spent on it.
    assert (st.run_dir / "workers" / "smoke" / "config"
            / "seen.txt").read_text().split() == [TOK_A]
    assert exc.value.reset_at == datetime.fromisoformat(PROBE_RESET_SOON)
    assert exc.value.reset_at < datetime.fromtimestamp(EVENT_RESET,
                                                       tz=timezone.utc)
    assert st.data["active_oauth_token"] == TOK_B


def test_a_stale_probe_never_stops_the_search_short(leerie, run):
    """Three tokens, the incident's count: A is selected, the smoke test is
    refused on A, B ranks next on a probe as stale as A's and is refused
    too — C carries the run rather than B's refusal pausing it."""
    st, drive = run
    spawned = drive(probes={TOK_A: (5, 5, PROBE_RESET_LATE),
                            TOK_B: (20, 20, PROBE_RESET_LATE),
                            TOK_C: (40, 40, PROBE_RESET_LATE)},
                    reject=(TOK_A, TOK_B), tokens=(TOK_A, TOK_B, TOK_C))
    assert spawned == [TOK_A, TOK_B, TOK_C]
    assert st.data["active_oauth_token"] == TOK_C
