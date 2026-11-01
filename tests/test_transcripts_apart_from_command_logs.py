"""A worker's transcript and leerie's run of a command never share a path
(DESIGN §8 *What a worker ran is read from its own transcript*).

Review round 22 H4: the acceptance writer's transcript
(`_worker_log_path(run, "acceptance-<k>", "acceptance_writer")`) and its
set's validation log (`_held_out_logs(run) / "acceptance-<k>.log"`) were one
file, so `_run_streaming`'s transcript guard die()d at the first set every
run validated. The two names came from the same string in one directory.
Transcripts now live in their log directory's own `transcripts/`, where only
`_invoke` writes, and every command log beside it — so no name either side
picks can make them meet.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from tests.test_acceptance_gate import (CONTROL_TEST, DEFECT_TEST, _caps,
                                        _repo, _st)
from tests.test_held_out_relays import _Proc

MODELS = {"acceptance_writer": "sonnet"}
EFFORTS = {"acceptance_writer": "medium"}

# Names a worker sid and a command log both take in the code: the held-out
# set labels, the conformers' rounds, the integration gate's, a subtask's.
NAMES = ("acceptance-1", "acceptance-rewrite-2", "acceptance-1-a2",
         "acceptance-surrogate", "acceptance-repair", "final-conformer-r0",
         "final-conformer-r0-axes", "feat-001", "feat-001-conformer",
         "integration-feat-001", "integrator-feat-001", "coverage-feat-001",
         "settle-exec-feat-001", "falsifier-confirm-1", "falsifier-i0-s0",
         "base-baseline", "ci-parity", "smoke", "transcripts")


# --- the reviewer's proof, inverted -----------------------------------------

def test_the_writers_transcript_is_not_its_sets_validation_log(leerie,
                                                              tmp_path):
    run = tmp_path / "run"
    name = "acceptance-1"
    transcript = leerie._worker_log_path(run, name, "acceptance_writer")
    # `_write_acceptance_set`'s validation log, verbatim from its source.
    src = inspect.getsource(leerie._write_acceptance_set)
    assert 'log_path = _held_out_logs(leerie_dir) / f"{name}.log"' in src
    validation = leerie._held_out_logs(run) / f"{name}.log"
    assert transcript != validation
    leerie._register_worker_transcript(transcript, name)
    assert leerie._is_worker_transcript(transcript)
    assert not leerie._is_worker_transcript(validation)


# --- the rule: the two path families are disjoint ----------------------------

def test_every_workers_transcript_is_in_a_transcripts_directory(leerie,
                                                                tmp_path):
    """For every worker kind and every name, the transcript is in the
    transcripts directory of its log directory, and a command log of the
    same name in either log directory is another file."""
    run = tmp_path / "run"
    log_dirs = (run / "logs", leerie._held_out_logs(run))
    for kind in (*leerie.WORKER_TYPES, "smoke"):
        own = (leerie._held_out_logs(run)
               if kind in leerie.HELD_OUT_LOG_WORKERS else run / "logs")
        for name in NAMES:
            t = leerie._worker_log_path(run, name, kind)
            assert t == leerie._transcripts_dir(own) / f"{name}.log"
            assert leerie._in_transcripts_dir(t)
            for d in log_dirs:
                assert not leerie._in_transcripts_dir(d / f"{name}.log")
                assert t != d / f"{name}.log"
                assert t != d / "ci" / f"{name}.log"


def _tree(leerie) -> ast.Module:
    return ast.parse(Path(leerie.__file__).read_text())


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    out: dict[ast.AST, str] = {}

    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn)
            out[child] = name
            walk(child, name)
    walk(tree, "<module>")
    return out


# The functions that may name a transcripts directory: where a transcript is
# written (`_worker_log_path`, `_invoke`), read (`_worker_transcripts`), told
# apart (`_in_transcripts_dir`), and watched for activity. None of them makes
# a command's log path.
TRANSCRIPT_NAMERS = {"_transcripts_dir", "_in_transcripts_dir",
                     "_worker_log_path", "_invoke", "_worker_transcripts",
                     "_run_last_activity"}


def test_only_the_transcript_functions_name_a_transcripts_directory(leerie):
    """Every command-log producer builds its path from `logs/`,
    `_held_out_logs` or `_final_axes_log`; none can reach a transcripts
    directory, because nothing but these functions names one — not the
    constant, not its literal, not `_transcripts_dir`."""
    tree = _tree(leerie)
    fn_of = _enclosing_functions(tree)
    offenders = []
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Name) and node.id == "TRANSCRIPTS_DIRNAME" \
                and isinstance(node.ctx, ast.Load):
            hit = node.id
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_transcripts_dir":
            hit = "_transcripts_dir()"
        elif isinstance(node, ast.Constant) and node.value == "transcripts":
            hit = repr(node.value)
        if hit and fn_of.get(node) not in TRANSCRIPT_NAMERS:
            # The constant's own definition.
            if not (hit == "'transcripts'" and fn_of.get(node) == "<module>"):
                offenders.append((fn_of.get(node), hit, node.lineno))
    assert offenders == []
    # The converse: each namer does name it (a renamed one would leave the
    # set above stale and the rule passing on nothing).
    named = {fn_of[n] for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_transcripts_dir"}
    assert named == TRANSCRIPT_NAMERS - {"_transcripts_dir",
                                         "_in_transcripts_dir"}


def test_a_command_is_refused_any_path_in_a_transcripts_directory(
        leerie, tmp_path):
    """The guard is structural, not only the registry: a path in a
    transcripts directory is refused before the command runs, registered
    or not, and nothing is written there."""
    for logs in (tmp_path / "logs", leerie._held_out_logs(tmp_path)):
        target = leerie._transcripts_dir(logs) / "anything.log"
        with pytest.raises(SystemExit):
            asyncio.run(leerie._run_streaming(
                ["true"], grants=None, cwd=str(tmp_path), log_path=target,
                timeout=10, verbosity="quiet"))
        assert not target.exists()


# --- the writer's validation, through the real path --------------------------

def _writer_events(sid: str) -> list[str]:
    return [json.dumps(e) for e in (
        {"type": "assistant", "session_id": sid, "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Write",
             "input": {"file_path": "acc/test_defect_1.py"}}]}},
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 1, "result": "{}", "session_id": sid,
         "structured_output": {"files": [
             {"path": "acc/test_defect_1.py", "kind": "defect",
              "cases": ["sums"], "failure_mode": "assertion"},
             {"path": "acc/test_control_1.py", "kind": "control",
              "cases": ["zero"], "failure_mode": "assertion"}]},
         "usage": {"input_tokens": 1, "output_tokens": 1}})]


def test_a_written_set_is_validated_beside_its_writers_transcript(
        leerie, tmp_path, monkeypatch):
    """`_write_acceptance_set` with the real `claude_p` → `_invoke`, only
    the CLI's process faked: the writer's transcript is registered where
    `_invoke` writes it, then the set's files run through `_run_streaming`
    into the validation log. Before the fix this die()d at the first
    validation run (review round 22 H4, measured on a real pnpm + vitest
    run)."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    real_exec = asyncio.create_subprocess_exec
    spawned: list[str] = []

    async def exec_(*cmd, **kw):
        if "--output-format" not in cmd:
            return await real_exec(*cmd, **kw)
        wt = Path(kw["cwd"])
        (wt / "acc").mkdir(exist_ok=True)
        (wt / "acc" / "test_defect_1.py").write_text(DEFECT_TEST)
        (wt / "acc" / "test_control_1.py").write_text(CONTROL_TEST)
        spawned.append(str(wt))
        return _Proc(_writer_events("writer-session"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", exec_)
    res = asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps(leerie, 1), MODELS, EFFORTS, head))
    assert len(spawned) == 1
    assert res["valid"] is True, res
    assert res["defect_files"] == ["acc/test_defect_1.py"]
    transcript = leerie._worker_log_path(st.run_dir, "acceptance-1",
                                         "acceptance_writer")
    validation = leerie._held_out_logs(st.run_dir) / "acceptance-1.log"
    assert leerie._worker_transcripts(leerie._held_out_logs(st.run_dir)) \
        == [("acceptance-1", transcript)]
    # The transcript is the stream alone; the runs are in the command log.
    events = list(leerie._iter_transcript_events(transcript))
    assert [e["type"] for e in events] == ["assistant", "result"]
    assert "test_defect_1.py" in validation.read_text()
    assert "writer-session" not in validation.read_text()
    assert list(leerie._iter_transcript_events(validation)) == []
