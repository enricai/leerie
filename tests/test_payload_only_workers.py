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
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_dep_capture_worker import (_CAPS, _DEP_CAPTURE_ENVELOPE,  # noqa: E402
                                     _make_fake_state)
from test_source_of_truth_delivery import _state  # noqa: E402

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
