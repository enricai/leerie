"""The finalize workers whose whole input is their payload never run in the
user's checkout (DESIGN §8 *A worker that does not commit never runs in a
tree leerie measures*).

`pr_writer` and `dep_capture` ran with `cwd` = the repo root — inside the
container, `/work`, the user's real checkout bind-mounted (`leerie`:
`-v "$USER_REPO:/work"`, `-w /work`) — holding INSPECT_TOOLS, whose
prefix-matched verbs include `find` (`find . -delete`) and `git branch`
(`git branch -D <the user's branch>`). Neither reads anything its payload
does not carry. They now run in an empty run-private directory with only
`Read`. The flag `--dangerously-skip-permissions` reaches neither (both
are outside PLANNING_WORKER_TYPES, so no widening, and not autonomous, so
no skip flag): pinned here too.

Driven through the real `claude_p` with `_invoke` stubbed, so the argv and
the cwd the CLI would get are read, not a call site's source.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_dep_capture_worker import (_CAPS, _DEP_CAPTURE_ENVELOPE,  # noqa: E402
                                     _make_fake_state)
from test_source_of_truth_delivery import _state  # noqa: E402

_JUDGE_ENVELOPE_FOR_REPLAY = {**_DEP_CAPTURE_ENVELOPE, "structured_output": {
    "categories": ["bug-fixing"]}}
_PR_ENVELOPE = {**_DEP_CAPTURE_ENVELOPE,
                "structured_output": {"title": "t", "body": "b",
                                      "used_template": None}}


def _capture_invokes(leerie, monkeypatch, envelope: dict) -> list[dict]:
    seen: list[dict] = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        seen.append({"cmd": list(cmd), "cwd": cwd,
                     "listing": sorted(os.listdir(cwd))})
        return envelope
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    return seen


def _tools(cmd: list[str]) -> str:
    return cmd[cmd.index("--allowedTools") + 1]


@pytest.mark.parametrize("skip_perms", [False, True])
def test_dep_capture_runs_outside_the_checkout(leerie, tmp_path,
                                               monkeypatch, skip_perms):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name": "x"}\n')
    st = _make_fake_state(leerie, tmp_path,
                          ["sudo -n apt-get install -y libpq-dev"])
    st.repo_root = str(repo)
    st.data["dangerously_skip_permissions"] = skip_perms
    monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
    seen = _capture_invokes(leerie, monkeypatch, _DEP_CAPTURE_ENVELOPE)
    asyncio.run(leerie.capture_repo_deps(
        repo, st, caps=_CAPS, models={"dep_capture": "sonnet"},
        efforts={"dep_capture": None}))
    assert seen, "dep_capture never ran; this test proves nothing"
    call = seen[0]
    assert Path(call["cwd"]) == Path(st.run_dir) / "payload-only"
    assert call["listing"] == []
    assert _tools(call["cmd"]) == "Read"
    assert "--dangerously-skip-permissions" not in call["cmd"]
    # Its input still reached it: the manifest is in the payload.
    assert (repo / ".leerie" / "config.toml").is_file()


@pytest.mark.parametrize("skip_perms", [False, True])
def test_pr_writer_runs_outside_the_checkout(leerie, tmp_path, monkeypatch,
                                             skip_perms):
    st = _state(leerie, tmp_path)
    st.data["dangerously_skip_permissions"] = skip_perms
    repo = tmp_path / "repo"
    repo.mkdir()
    seen = _capture_invokes(leerie, monkeypatch, _PR_ENVELOPE)
    try:
        asyncio.run(leerie._compose_pr_via_llm(
            st, dict(leerie.DEFAULT_CAPS), {"pr_writer": "sonnet"},
            {"pr_writer": "medium"}, repo, None))
    except Exception:
        pass        # git plumbing after the call may fail in a bare repo
    assert seen, "pr_writer never ran; this test proves nothing"
    call = seen[0]
    assert Path(call["cwd"]) == Path(st.run_dir) / "payload-only"
    assert Path(call["cwd"]) != repo and call["listing"] == []
    assert _tools(call["cmd"]) == "Read"
    assert "--dangerously-skip-permissions" not in call["cmd"]


# --- the post-run skills ---------------------------------------------------
#
# `judge`, `patch_generator` and the replay call ran with `cwd=os.getcwd()`
# — the user's checkout — holding INSPECT_TOOLS. The judge scores a record
# against the inputs the record carries and the patch generator edits the
# prompt text it is handed: payload-only. A replay re-runs a captured call
# that may have read the repository, so it reads a disposable checkout of
# HEAD, never the checkout itself.

def _judge_state(leerie, run_dir: Path):
    from test_phase_judge import _make_state as make
    return make(leerie, run_dir)


def test_judge_runs_payload_only(leerie, tmp_path, monkeypatch):
    from test_phase_judge import _JUDGE_ENVELOPE, _make_records
    monkeypatch.chdir(tmp_path)                   # a cwd it must not use
    seen = _capture_invokes(leerie, monkeypatch, _JUDGE_ENVELOPE)
    st = _judge_state(leerie, tmp_path / "run")
    asyncio.run(leerie._judge_capture(
        _make_records(1)[0], {"judge": "sonnet"}, {"judge": None},
        dict(leerie.DEFAULT_CAPS), st))
    assert seen, "the judge never ran"
    assert Path(seen[0]["cwd"]) == Path(st.run_dir) / "payload-only"
    assert seen[0]["listing"] == []
    assert _tools(seen[0]["cmd"]) == "Read"


def test_patch_generator_runs_payload_only(leerie, tmp_path, monkeypatch):
    _, body, _ = leerie.resolve_prompt("classifier")
    anchor = body.split("\n")[0].strip()[:50]
    env = {**_DEP_CAPTURE_ENVELOPE, "structured_output": {
        "anchor": anchor, "replacement": anchor + " (clarified)",
        "strategy": "clarify", "pivot_reason": None}}
    monkeypatch.chdir(tmp_path)
    seen = _capture_invokes(leerie, monkeypatch, env)
    st = _judge_state(leerie, tmp_path / "run")
    hs = leerie.HealState(tmp_path / "heal", "classifier")
    hs.failing_samples = [{"call_id": "c1", "call_type": "classifier",
                           "response_content": "x"}]
    hs.history = []
    asyncio.run(leerie._request_patch(hs, 1, st, dict(leerie.DEFAULT_CAPS),
                                      {"heal": "sonnet"}, {}))
    assert seen, "the patch generator never ran"
    assert Path(seen[0]["cwd"]) == Path(st.run_dir) / "payload-only"
    assert seen[0]["listing"] == []
    assert _tools(seen[0]["cmd"]) == "Read"


def test_a_replay_reads_a_checkout_of_head_never_the_checkout(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"],):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "committed.txt").write_text("in HEAD\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True)
    (repo / "uncommitted.txt").write_text("the user's own work\n")
    seen = _capture_invokes(leerie, monkeypatch, _JUDGE_ENVELOPE_FOR_REPLAY)
    record = {"call_type": "classifier", "system_prompt": "s",
              "user_content": "u", "model": "sonnet"}
    asyncio.run(leerie._replay_capture(record, cwd=str(repo)))
    assert seen, "the replay never ran"
    cwd = Path(seen[0]["cwd"])
    assert cwd != repo and repo not in cwd.parents
    assert seen[0]["listing"] == [".git", "committed.txt"]
    assert _tools(seen[0]["cmd"]) == leerie.INSPECT_TOOLS
    assert "--dangerously-skip-permissions" not in seen[0]["cmd"]
    # The checkout is gone, its registration with it; the user's tree is
    # as it was.
    assert not cwd.exists()
    assert subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True,
        text=True, check=True).stdout.count("\n") == 1
    assert (repo / "uncommitted.txt").read_text() == "the user's own work\n"
