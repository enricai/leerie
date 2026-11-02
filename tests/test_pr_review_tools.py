"""The pr_reviewer's prompt and tools agree (DESIGN §9 *An advisory whole-PR
review feeds the final conformer*).

The smoke defect: the prompt said the reviewer "may run the repository's
tests", while its tools added only the inferred build/lint/test verbs. On a
repo with no inferred test command the reviewer tried `python -m pytest`
twice and was refused both times. Now every spelling of the canonical
runner is granted, the user message lists exactly what may run, and with no
known runner it says that none is granted. The whole-suite deny still
covers the granted runner.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path

from tests.test_finalize_evidence import EFFORTS, MODELS, _review_env
from tests.test_run_final_conformance import env  # noqa: F401


def _pytest_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "pyrepo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    return repo


def test_every_runner_spelling_is_granted(leerie, tmp_path, monkeypatch):
    repo = _pytest_repo(tmp_path)
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _r: [])
    monkeypatch.setattr(leerie, "_canonical_test_command",
                        lambda _r, *a: "pytest")
    tools, prefixes, canonical = leerie._pr_review_tools(repo, repo)
    for form in ("pytest", "python3 -m pytest", "python -m pytest"):
        assert f"Bash({form}:*)" in tools.split(",")
    assert prefixes == ["pytest", "python3 -m pytest", "python -m pytest"]
    assert canonical == "pytest"
    assert tools.startswith(leerie.INSPECT_TOOLS)


def test_no_known_runner_grants_none(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _r: [])
    monkeypatch.setattr(leerie, "_canonical_test_command",
                        lambda _r, *a: "")
    tools, prefixes, canonical = leerie._pr_review_tools(tmp_path, tmp_path)
    assert tools == leerie.INSPECT_TOOLS
    assert prefixes == [] and canonical == ""


def test_the_whole_suite_deny_still_covers_the_granted_runner(
        leerie, tmp_path, monkeypatch):
    """#292: granting `python -m pytest` must not reopen the whole-suite
    run. Driven through the real claude_p."""
    repo = _pytest_repo(tmp_path)
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _r: [])
    monkeypatch.setattr(leerie, "_canonical_test_command",
                        lambda _r, *a: "pytest")
    tools, _, _ = leerie._pr_review_tools(repo, repo)
    seen: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        seen["deny"] = cmd[cmd.index("--disallowedTools") + 1].split(",")
        seen["stdin"] = stdin_data or ""
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"findings": []}}

    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    st = types.SimpleNamespace(
        path=run_dir / "state.json", run_dir=run_dir, repo_root=repo,
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None, add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_append_system_prompt_file_supported",
                        lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key="pr_reviewer", cwd=str(tmp_path / "staging"),
        allowed_tools=tools, max_turns=5, autonomous=False,
        caps=dict(leerie.DEFAULT_CAPS), st=st, model="opus", sid="pr"))
    for runner in ("pytest", "python -m pytest", "python3 -m pytest"):
        assert f"Bash({runner} tests)" in seen["deny"]
        assert f"Bash({runner})" in seen["deny"]
    assert "whole test suite" in seen["stdin"]


def _review_prompt(leerie, env, monkeypatch, canonical):  # noqa: F811
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _r: [])
    monkeypatch.setattr(leerie, "_canonical_test_command",
                        lambda _r, *a: canonical)
    _review_env(env)
    calls: list[dict] = []

    async def fake(**kw):
        calls.append(kw)
        return {"findings": []}
    # monkeypatch, not assignment: a module attribute left patched breaks
    # tests/test_claude_p_call_sites.py, which reads claude_p's signature.
    monkeypatch.setattr(leerie, "claude_p", fake)
    asyncio.run(leerie._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))
    (call,) = calls
    return call


def test_review_message_lists_what_it_may_run(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    call = _review_prompt(leerie, env, monkeypatch, "python3 -m pytest")
    up = call["user_prompt"]
    i = up.index("COMMANDS YOU MAY RUN")
    assert i > up.index("FULL DIFF")
    block = up[i:up.index("Review this PR")]
    assert "- `python -m pytest ...`" in block
    assert "test command is `python3 -m pytest`" in block
    assert "No test runner is granted" not in block
    assert "Bash(python -m pytest:*)" in call["allowed_tools"].split(",")


def test_review_message_without_a_runner_promises_none(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    call = _review_prompt(leerie, env, monkeypatch, "")
    up = call["user_prompt"]
    block = up[up.index("COMMANDS YOU MAY RUN"):up.index("Review this PR")]
    assert "- none:" in block
    assert "No test runner is granted to you: do not try to run tests" \
        in block
    assert call["allowed_tools"] == leerie.INSPECT_TOOLS


def test_the_system_prompt_defers_to_the_listed_commands(leerie):
    text = leerie._load_prompt("pr_reviewer")
    assert "COMMANDS YOU MAY RUN" in text
    assert "run the repository's tests" not in text
