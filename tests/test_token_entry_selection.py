"""The token a run spawns with is chosen from the configured list, by a probe
that reads every window the server reports (DESIGN §6 *Multi-token
rotation*).

Two defects, measured 2026-11-26 on leerie 0.104.2, one rule:

1. Probe B required the five-hour headers. A healthy token with no
   five-hour usage answers with the 7-day headers alone (shape below, as
   measured), so the probe logged contract drift and returned None, and
   rotation read the only token with runway as unprobeable and paused the
   run on a limited one.
2. `resume` (and `--phase`) kept the persisted `active_oauth_token`: a run
   paused on token 1's weekly limit, resumed with only another token
   configured, spawned on token 1 — no longer listed — and re-paused at
   token 1's reset.

Header sets here are the measured ones: a 200 carrying only the 7-day
window, a 429 without the five-hour headers (7-day exhausted), a 429 with
them. The entry tests drive the real `_run_phases` resume branch and the
real `main()` `--phase` branch; only the probe's network call is stubbed.
"""
from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timezone

import pytest

from tests.test_main_exception_arms import repo  # noqa: F401  (fixture)
from tests.test_resume_planning_reentry import MODELS, _args, _caps

TOK_LIMITED = "tok-1111-weekly-limited"
TOK_RUNWAY = "tok-2222-runway"
SEVEN_DAY_RESET = 1796313600
LIMITED_RESET = 1796036400
FIVE_HOUR_RESET = 1795500000

# 200, healthy token, five-hour window unused: no -5h-* headers at all.
HEALTHY_7D_ONLY = {
    "anthropic-ratelimit-unified-status": "allowed",
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-7d-utilization": "0.02",
    "anthropic-ratelimit-unified-7d-reset": str(SEVEN_DAY_RESET),
    "anthropic-ratelimit-unified-reset": str(SEVEN_DAY_RESET),
    "anthropic-ratelimit-unified-representative-claim": "seven_day",
}
# 429, weekly limit reached, no five-hour headers.
LIMITED_NO_5H = {
    "anthropic-ratelimit-unified-status": "rejected",
    "anthropic-ratelimit-unified-7d-status": "rejected",
    "anthropic-ratelimit-unified-7d-utilization": "1.0",
    "anthropic-ratelimit-unified-7d-reset": str(LIMITED_RESET),
    "anthropic-ratelimit-unified-reset": str(LIMITED_RESET),
    "anthropic-ratelimit-unified-representative-claim": "seven_day",
}
# 429 carrying the five-hour window as well.
LIMITED_WITH_5H = {
    **LIMITED_NO_5H,
    "anthropic-ratelimit-unified-5h-status": "allowed",
    "anthropic-ratelimit-unified-5h-utilization": "0.4",
    "anthropic-ratelimit-unified-5h-reset": str(FIVE_HOUR_RESET),
}


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class _Resp:
    def __init__(self, headers: dict):
        self.headers = headers

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_probe(leerie, monkeypatch, answers: dict) -> list[str]:
    """`answers`: token -> (status, headers) for Probe B; Probe A always
    403s (a setup-token's scope). Returns the probed tokens, in order."""
    probed: list[str] = []
    monkeypatch.setattr(leerie, "_parse_claude_cli_version_string",
                        lambda: "2.1.999")

    def urlopen(req, timeout=None):
        token = req.headers["Authorization"].split(" ", 1)[1]
        if req.full_url.endswith("/api/oauth/usage"):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b""))
        probed.append(token)
        status, headers = answers[token]
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err",
                                         headers, io.BytesIO(b""))
        return _Resp(headers)

    monkeypatch.setattr(leerie.urllib.request, "urlopen", urlopen)
    leerie._TOKEN_PROBE_CACHE.clear()
    return probed


@pytest.fixture(autouse=True)
def _clear_probe_cache(leerie):
    leerie._TOKEN_PROBE_CACHE.clear()
    yield
    leerie._TOKEN_PROBE_CACHE.clear()


# --- the probe reads every window the server reports -------------------------

def test_a_healthy_token_with_only_the_seven_day_window_is_probed(
        leerie, monkeypatch, capsys):
    _install_probe(leerie, monkeypatch, {TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    usage = leerie._probe_token_usage(TOK_RUNWAY, cache_sec=180)
    assert usage is not None
    assert usage["seven_day_util"] == pytest.approx(0.02)
    assert usage["five_hour_util"] == 0.0
    assert usage["resets_at"] == _utc(SEVEN_DAY_RESET)
    assert leerie._TOKEN_PROBE_DRIFT_MARKER not in capsys.readouterr().out


def test_a_limited_token_without_five_hour_headers_gives_its_reset(
        leerie, monkeypatch, capsys):
    _install_probe(leerie, monkeypatch, {TOK_LIMITED: (429, LIMITED_NO_5H)})
    usage = leerie._probe_token_usage(TOK_LIMITED, cache_sec=180)
    assert usage is not None
    assert usage["seven_day_util"] == pytest.approx(1.0)
    assert usage["resets_at"] == _utc(LIMITED_RESET)
    assert leerie._TOKEN_PROBE_DRIFT_MARKER not in capsys.readouterr().out


def test_a_limited_token_with_five_hour_headers_binds_on_its_week(
        leerie, monkeypatch):
    """The server names the binding window's reset (`-reset`); the five-hour
    reset, earlier, would wake a paused run into the same weekly limit."""
    _install_probe(leerie, monkeypatch, {TOK_LIMITED: (429, LIMITED_WITH_5H)})
    usage = leerie._probe_token_usage(TOK_LIMITED, cache_sec=180)
    assert usage["five_hour_util"] == pytest.approx(0.4)
    assert usage["seven_day_util"] == pytest.approx(1.0)
    assert usage["resets_at"] == _utc(LIMITED_RESET)


def test_a_429_with_no_headers_is_no_data_not_drift(
        leerie, monkeypatch, capsys):
    _install_probe(leerie, monkeypatch, {TOK_LIMITED: (429, {})})
    assert leerie._probe_token_usage(TOK_LIMITED, cache_sec=180) is None
    assert leerie._TOKEN_PROBE_DRIFT_MARKER not in capsys.readouterr().out


def test_ranking_puts_the_healthy_token_first(leerie, monkeypatch):
    _install_probe(leerie, monkeypatch, {TOK_LIMITED: (429, LIMITED_NO_5H),
                                         TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    assert leerie._rank_tokens([TOK_LIMITED, TOK_RUNWAY], 180) == [
        TOK_RUNWAY, TOK_LIMITED]


def test_a_rejected_spawn_rotates_to_the_seven_day_only_token(
        leerie, monkeypatch, tmp_path):
    """The live incident's rotation: the active token's spawn is refused, the
    other token's probe carries only the 7-day window — the run rotates to
    it instead of pausing at the limited token's reset."""
    _install_probe(leerie, monkeypatch, {TOK_LIMITED: (429, LIMITED_NO_5H),
                                         TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS",
                       f"{TOK_LIMITED},{TOK_RUNWAY}")
    st = leerie.State(tmp_path / "state", "rot-7d")
    st.data = {"active_oauth_token": TOK_LIMITED}
    spawned: list[str] = []

    async def spawn() -> dict:
        token = st.data["active_oauth_token"]
        spawned.append(token)
        if token == TOK_LIMITED:
            raise leerie.RateLimitedExit(
                reset_at=_utc(LIMITED_RESET),
                raw_message="rate_limit_event status=rejected",
                limit_type="seven_day")
        return {"is_error": False, "result": "ok"}

    out = asyncio.run(leerie._spawn_with_token_rotation(
        st, _caps(leerie), spawn))
    assert out["result"] == "ok"
    assert spawned == [TOK_LIMITED, TOK_RUNWAY]
    assert st.data["active_oauth_token"] == TOK_RUNWAY


# --- every entry chooses from the list configured for it ---------------------

class _Reached(BaseException):
    """Raised at the containment gate, past the resume branch's selection
    and ahead of the resume's first worker."""


@pytest.fixture
def resume_run(leerie, tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir,
                   check=True)
    monkeypatch.chdir(repo_dir)
    st = leerie.State(tmp_path / "state", "resume-tok", repo_root=repo_dir)

    def _stop(*_a, **_k):
        raise _Reached()
    monkeypatch.setattr(leerie, "_enforce_and_record_cgroup_containment",
                        _stop)

    def drive(*, persisted: str | None, tokens: tuple[str, ...] | None,
              singular: str | None = None):
        st.data = {"task": "a trivial task", "current_phase": "classify",
                   "worker_count": 3}
        if persisted is not None:
            st.data["active_oauth_token"] = persisted
        st.save()
        if tokens is None:
            monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKENS", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", ",".join(tokens))
        if singular is None:
            monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", singular)
        with pytest.raises(_Reached):
            asyncio.run(leerie._run_phases(
                _args(resume=True), _caps(leerie), st.run_dir, st, "both",
                "normal", MODELS, {}))
        return json.loads(st.path.read_text())

    return drive


def test_resume_never_uses_a_persisted_token_no_longer_listed(
        leerie, monkeypatch, resume_run):
    """The barnacle resumes: paused on token 1, relaunched with only token 2
    configured — the run's spawns must be token 2's."""
    _install_probe(leerie, monkeypatch, {TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    saved = resume_run(persisted=TOK_LIMITED, tokens=(TOK_RUNWAY,))
    assert saved["active_oauth_token"] == TOK_RUNWAY


def test_resume_reranks_a_persisted_token_that_is_still_listed(
        leerie, monkeypatch, resume_run):
    """Both listed, the persisted one limited: the token with runway is the
    one chosen, before the first worker — not after eating a 429 on it."""
    probed = _install_probe(leerie, monkeypatch,
                            {TOK_LIMITED: (429, LIMITED_NO_5H),
                             TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    saved = resume_run(persisted=TOK_LIMITED,
                       tokens=(TOK_LIMITED, TOK_RUNWAY))
    assert saved["active_oauth_token"] == TOK_RUNWAY
    assert sorted(probed) == sorted([TOK_LIMITED, TOK_RUNWAY])


def test_resume_on_the_singular_variable_drops_a_persisted_token(
        leerie, monkeypatch, resume_run):
    """No list configured: the persisted token would override the singular
    variable in every spawn (`_invoke`'s active_token); it must not."""
    probed = _install_probe(leerie, monkeypatch, {})
    saved = resume_run(persisted=TOK_LIMITED, tokens=None,
                       singular=TOK_RUNWAY)
    assert "active_oauth_token" not in saved
    assert probed == []


def test_resume_on_the_singular_variable_alone_is_unchanged(
        leerie, monkeypatch, resume_run):
    probed = _install_probe(leerie, monkeypatch, {})
    saved = resume_run(persisted=None, tokens=None, singular=TOK_RUNWAY)
    assert "active_oauth_token" not in saved
    assert probed == []


class _FirstSpawn(BaseException):
    pass


def test_phase_spawns_with_a_token_chosen_from_the_list(
        leerie, monkeypatch, repo):  # noqa: F811
    run_id = "phase-tok"
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"task": "a trivial task", "run_layout": leerie.RUN_LAYOUT_VERSION,
         "active_oauth_token": TOK_LIMITED}))
    (run_dir / "calls.ndjson").write_text(json.dumps(
        {"call_id": "c1", "call_type": "planner", "system_prompt": "s",
         "user_prompt": "u", "response_content": "{}"}) + "\n")
    monkeypatch.setattr(leerie, "_require_landlock", lambda: None)
    monkeypatch.setattr(leerie, "_require_heldout_sandbox", lambda d: None)
    _install_probe(leerie, monkeypatch, {TOK_RUNWAY: (200, HEALTHY_7D_ONLY)})
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", TOK_RUNWAY)
    seen: list = []

    async def claude_p(*a, **kw):
        seen.append(json.loads((run_dir / "state.json").read_text())
                    .get("active_oauth_token"))
        raise _FirstSpawn()
    monkeypatch.setattr(leerie, "claude_p", claude_p)
    monkeypatch.setattr(sys, "argv",
                        ["leerie", "--phase", "judge", "--run-id", run_id])
    with pytest.raises(_FirstSpawn):
        leerie.main()
    assert seen == [TOK_RUNWAY]
