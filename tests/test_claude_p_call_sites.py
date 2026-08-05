"""Every `claude_p` call site in the module, checked against the real signature.

`phase_planning_coverage_gate` shipped in 0.10.0 with a call that raised
`TypeError` on **every** invocation — two positionals where all-keyword was
required, plus `allowed_tools` and `max_turns` (both REQUIRED) omitted — and a
broad `except Exception` reported it as a clean advisory degrade. The judge
never ran once, and the log line read like a healthy degrade path.

Nothing caught it because **every test stubs `claude_p`, and a stub accepts any
signature**. The real signature is exercised only by a live run.

`tests/test_phase_planning_coverage_gate.py::TestCallSignature` guards that one
gate behaviorally. This file is the root-level guard: a static sweep over all
27 call sites, so the NEXT one cannot ship broken either. It needs no stub, no
event loop, and no LLM — it reads the AST.

Prior art: `tests/test_recursive_decompose.py`'s C0 guard binds one call site's
kwargs against the real signature; this generalizes that to the whole module.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

MODULE = (pathlib.Path(__file__).resolve().parents[1] /
          "orchestrator" / "leerie.py")


def _call_sites() -> list[ast.Call]:
    tree = ast.parse(MODULE.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (f.id if isinstance(f, ast.Name)
                else f.attr if isinstance(f, ast.Attribute) else None)
        if name == "claude_p":
            out.append(node)
    return out


@pytest.fixture(scope="module")
def sites() -> list[ast.Call]:
    found = _call_sites()
    assert found, "no claude_p call sites found — the scan is broken"
    return found


def _required(leerie) -> set[str]:
    return {n for n, p in inspect.signature(leerie.claude_p).parameters.items()
            if p.default is inspect.Parameter.empty}


class TestEveryCallSiteIsWellFormed:
    def test_no_call_site_passes_positional_arguments(self, sites):
        """The shipped bug. `claude_p`'s first two params are
        (user_prompt, system_prompt), so a positional worker-name binds to
        `user_prompt` and collides with an explicit `system_prompt=`."""
        bad = [(n.lineno, len(n.args)) for n in sites if n.args]
        assert not bad, (
            f"claude_p called with positional args at lines {bad}; every "
            "call site must be all-keyword")

    def test_no_call_site_omits_a_required_parameter(self, sites, leerie):
        """Computed from the live signature, so adding a required parameter
        to `claude_p` fails here rather than at runtime in one unlucky phase."""
        req = _required(leerie)
        bad = []
        for n in sites:
            if any(k.arg is None for k in n.keywords):  # **kwargs splat
                continue
            missing = req - {k.arg for k in n.keywords if k.arg}
            if missing:
                bad.append((n.lineno, sorted(missing)))
        assert not bad, f"claude_p call sites missing required params: {bad}"

    def test_no_call_site_passes_an_unknown_keyword(self, sites, leerie):
        known = set(inspect.signature(leerie.claude_p).parameters)
        bad = [(n.lineno, sorted({k.arg for k in n.keywords if k.arg} - known))
               for n in sites]
        bad = [b for b in bad if b[1]]
        assert not bad, f"claude_p call sites passing unknown kwargs: {bad}"

    def test_model_is_never_a_defaultless_dict_get(self, sites):
        """`models.get("worker")` yields None for any worker absent from
        `MODEL_DEFAULT_PER_WORKER` — which is MOST of them, since CLAUDE.md
        requires a new worker to be absent from that dict and fall through to
        `MODEL_DEFAULT`. None then reaches the argv builder. The coverage
        gate shipped this too, hidden behind the TypeError."""
        bad = []
        for n in sites:
            for k in n.keywords:
                v = k.value
                if (k.arg == "model" and isinstance(v, ast.Call)
                        and isinstance(v.func, ast.Attribute)
                        and v.func.attr == "get" and len(v.args) == 1):
                    bad.append((n.lineno, ast.unparse(v)))
        assert not bad, (
            f"model=<dict>.get(k) with no fallback at {bad}; use "
            "`.get(k, MODEL_DEFAULT)`")


class TestTheScanItselfWorks:
    """ANTI-VACUITY: a scan that finds nothing passes every assertion above."""

    def test_scan_finds_the_known_call_sites(self, sites):
        assert len(sites) >= 20, (
            f"only {len(sites)} claude_p call sites found; the scan is likely "
            "missing an alias or the module moved")

    def test_a_synthetic_bad_call_would_be_caught(self, leerie, tmp_path):
        """Proves the assertions can fail — they are checked against a
        deliberately broken call, not merely against clean code."""
        broken = tmp_path / "broken.py"
        broken.write_text(
            'claude_p("task_coverage_judge", "prompt", '
            'system_prompt=s, schema_key="k")\n')
        tree = ast.parse(broken.read_text())
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert call.args, "the synthetic bad call must have positionals"
        missing = _required(leerie) - {k.arg for k in call.keywords if k.arg}
        assert missing, "the synthetic bad call must omit required params"
