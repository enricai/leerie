"""Every invocation that can start a confined process passes through one
entry first (DESIGN §6 *Fail closed, on every entry*).

Review round 21 (execution H1): Landlock's check and the held-out sandbox
check — which turns isolation on, as a process flag — were preflight steps,
and only a fresh run ran preflight. `leerie resume` therefore ran every
worker and command without `--isolate`, and a runtime that could not build
a sandbox was no longer stopped. Now `main()` calls `_require_confinement`
once its run directory is held, before any dispatch.

Two halves, each catching what the other cannot:
- by rule: no statement of `main()` before the entry reaches
  `_confined_argv` through any function the module defines, and the entry
  is an unconditional statement of `main()`'s body — so every dispatch path
  after it, including one added later, is behind it;
- by execution: the real `main()` driven down each dispatch path (a fresh
  run, `resume <id>`, `--phase judge`, `--phase heal`) calls the entry
  before the first confined spawn, which carries `--isolate`.
"""
from __future__ import annotations

import ast
import json
import sys

import pytest

from tests.conftest import REPO_ROOT
from tests.test_main_exception_arms import repo  # noqa: F401  (fixture)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
ENTRY = "_require_confinement"


# --- by rule -----------------------------------------------------------------

def _module_defs(module: ast.Module) -> dict[str, ast.AST]:
    return {n.name: n for n in module.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef))}


def _names(node: ast.AST) -> set[str]:
    """Every name `node` references: a call, an argument, a callback."""
    out = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Name):
            out.add(c.id)
        elif isinstance(c, ast.Attribute):
            out.add(c.attr)
    return out


def _spawners(module: ast.Module) -> set[str]:
    """The module's definitions that reach `_confined_argv` (a confined
    spawn) by any reference, transitively."""
    defs = _module_defs(module)
    refs = {k: _names(v) for k, v in defs.items()}
    out = {"_confined_argv"}
    grew = True
    while grew:
        grew = False
        for k, r in refs.items():
            if k not in out and r & out:
                out.add(k)
                grew = True
    return out


def _entry_index(main: ast.FunctionDef) -> int:
    hits = [i for i, s in enumerate(main.body)
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
            and getattr(s.value.func, "id", None) == ENTRY]
    assert len(hits) == 1, (
        f"`{ENTRY}(...)` must be exactly one unconditional statement of "
        "main()'s body")
    return hits[0]


def _spawns_before_entry(module: ast.Module) -> list[tuple[int, list[str]]]:
    main = _module_defs(module)["main"]
    spawners = _spawners(module)
    return [(s.lineno, sorted(_names(s) & spawners))
            for s in main.body[:_entry_index(main)]
            if _names(s) & spawners]


def test_nothing_in_main_before_the_entry_spawns_a_confined_process():
    module = ast.parse(LEERIE_PY.read_text())
    spawners = _spawners(module)
    # Anti-vacuity: the graph does reach every dispatch target.
    assert {"_orchestrate", "phase_judge", "phase_heal", "preflight",
            "claude_p", "_run_streaming"} <= spawners
    assert _spawns_before_entry(module) == []


def test_the_rule_catches_a_dispatch_moved_above_the_entry():
    """The `--phase` branch placed before the entry, as a resume once was."""
    planted = ast.parse(
        "def _confined_argv(g, c): pass\n"
        "def _spawn(): _confined_argv(1, 2)\n"
        "def phase_judge(): _spawn()\n"
        "def _require_confinement(d): pass\n"
        "def main():\n"
        "    if args.phase:\n"
        "        asyncio.run(phase_judge())\n"
        "        return\n"
        "    _require_confinement(st.run_dir)\n"
        "    asyncio.run(_orchestrate())\n")
    got = _spawns_before_entry(planted)
    assert [names for _, names in got] == [["phase_judge"]]


def test_the_rule_refuses_a_conditional_entry():
    planted = ast.parse(
        "def main():\n"
        "    if not args.resume:\n"
        "        _require_confinement(st.run_dir)\n")
    with pytest.raises(AssertionError, match="unconditional"):
        _entry_index(planted.body[0])


def test_preflight_no_longer_runs_either_check(leerie, monkeypatch,
                                               tmp_path):
    """One owner: a second copy in preflight would build the sandbox twice
    on a fresh run, and hide a missing entry from a fresh-only test."""
    import asyncio

    class Stop(Exception):
        pass
    calls: list = []
    monkeypatch.setattr(leerie, "_require_landlock",
                        lambda: calls.append("landlock"))
    monkeypatch.setattr(leerie, "_require_heldout_sandbox",
                        lambda d: calls.append("sandbox"))

    def stop(*a, **k):
        raise Stop
    monkeypatch.setattr(leerie, "_disk_free_ratio", stop)
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    with pytest.raises(Stop):
        asyncio.run(leerie.preflight(run_dir))
    assert calls == []


# --- by execution: the real main(), every dispatch path ----------------------

class _Seen:
    def __init__(self) -> None:
        self.events: list = []


def _arm(leerie, monkeypatch, seen: _Seen) -> None:
    """The entry's two checks record their order and turn isolation on as
    the real sandbox check does; a dispatch target records whether the
    process is isolated and what its first confined spawn would be."""
    monkeypatch.setattr(leerie, "_require_landlock",
                        lambda: seen.events.append("landlock"))

    def sandbox(run_dir):
        seen.events.append("sandbox")
        monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    monkeypatch.setattr(leerie, "_require_heldout_sandbox", sandbox)
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)


def _dispatched(leerie, seen: _Seen, name: str) -> None:
    argv = leerie._confined_argv(
        leerie.WorkerGrants(rw=("/dev/null",), ro=("/",)), ["claude"])
    seen.events.append((name, leerie._isolated(), "--isolate" in argv))


def _main(leerie, monkeypatch, argv: list[str]) -> int | None:
    monkeypatch.setattr(sys, "argv", ["leerie", *argv])
    try:
        leerie.main()
    except SystemExit as e:
        return e.code
    return None


def _fresh(leerie, monkeypatch, seen, run_id: str) -> None:
    async def orchestrate(args, caps, run_dir, st, *a, **k):
        _dispatched(leerie, seen, "orchestrate")
        # What the real one seeds first: a run `resume` and `--phase` find.
        st.data.update(task="a trivial task", started_at="2026-11-01T00:00",
                       run_layout=leerie.RUN_LAYOUT_VERSION)
        st.save()
    monkeypatch.setattr(leerie, "_orchestrate", orchestrate)
    _main(leerie, monkeypatch, ["a trivial task", "--run-id", run_id])


def test_a_fresh_run_passes_through_the_entry(leerie, monkeypatch, repo):
    seen = _Seen()
    _arm(leerie, monkeypatch, seen)
    _fresh(leerie, monkeypatch, seen, "entry-fresh")
    assert seen.events == ["landlock", "sandbox",
                           ("orchestrate", True, True)]


def test_a_resumed_run_passes_through_the_entry(leerie, monkeypatch, repo):
    """The round-21 defect: a resume spawned everything unisolated."""
    seen = _Seen()
    _arm(leerie, monkeypatch, seen)
    _fresh(leerie, monkeypatch, seen, "entry-resume")
    seen.events.clear()
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)
    _main(leerie, monkeypatch, ["resume", "entry-resume"])
    assert seen.events == ["landlock", "sandbox",
                           ("orchestrate", True, True)]


def _seed_phase_run(leerie, monkeypatch, seen, repo, run_id: str):
    _fresh(leerie, monkeypatch, seen, run_id)
    seen.events.clear()
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)
    return repo / ".leerie" / "runs" / run_id


def test_phase_judge_passes_through_the_entry(leerie, monkeypatch, repo):
    seen = _Seen()
    _arm(leerie, monkeypatch, seen)
    _seed_phase_run(leerie, monkeypatch, seen, repo, "entry-judge")

    async def judge(*a, **k):
        _dispatched(leerie, seen, "phase_judge")
    monkeypatch.setattr(leerie, "phase_judge", judge)
    _main(leerie, monkeypatch, ["--phase", "judge", "--run-id",
                                "entry-judge"])
    assert seen.events == ["landlock", "sandbox",
                           ("phase_judge", True, True)]


def test_phase_heal_passes_through_the_entry(leerie, monkeypatch, repo):
    seen = _Seen()
    _arm(leerie, monkeypatch, seen)
    run_dir = _seed_phase_run(leerie, monkeypatch, seen, repo, "entry-heal")

    async def judge(run_dir_, judge_out_dir, *a, **k):
        _dispatched(leerie, seen, "phase_judge")
        judge_out_dir.mkdir(parents=True, exist_ok=True)
        (judge_out_dir / "INDEX.json").write_text(json.dumps(
            [{"call_id": "c1", "call_type": "planner", "passed": False}]))

    async def heal(*a, **k):
        _dispatched(leerie, seen, "phase_heal")
    monkeypatch.setattr(leerie, "phase_judge", judge)
    monkeypatch.setattr(leerie, "phase_heal", heal)
    (run_dir / "calls.ndjson").write_text(
        json.dumps({"call_id": "c1", "call_type": "planner"}) + "\n")
    _main(leerie, monkeypatch, ["--phase", "heal", "--run-id",
                                "entry-heal"])
    assert seen.events == ["landlock", "sandbox",
                           ("phase_judge", True, True),
                           ("phase_heal", True, True)]


def test_list_and_report_spawn_nothing_and_skip_the_entry(leerie,
                                                          monkeypatch, repo):
    """The two read-only verbs return before the entry: they need no
    sandbox, and must keep working where none can be built."""
    seen = _Seen()
    _arm(leerie, monkeypatch, seen)
    _main(leerie, monkeypatch, ["list"])
    _main(leerie, monkeypatch, ["--report", "nonexistent"])
    assert "landlock" not in seen.events and "sandbox" not in seen.events
