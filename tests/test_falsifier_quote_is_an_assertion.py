"""A falsifier's quoted assertion is one the test framework makes (DESIGN §8
*The quoted assertion is one the test framework makes*).

The at-assertion gate anchors on the sample's `assertion_quote`. Barnacle run
e7512406 item 8 sample 0 quoted its helper's call, `const { window,
trajectory } = await runStep(…)`, which threw before the probe's `expect`
ran; the gate rejected it only because the innermost probe frame was inside
the helper. Written with the helper's body in the case, quoting the call
that threw, the same probe passed every gate. The facts script now reports
the case's assertions (`asserts`), and only a quoted line inside one counts.

Covered here, each through the real `scripts/test-case-facts.cjs`:
- which calls are the framework's assertions, by syntax and binding;
- the e7512406 item 8 shapes, helper and inlined, through the real facts read
  (`_probe_case_facts_placed`, its held-out sandbox) and the gate's two
  functions;
- the real `_falsifier_gates` over real git and a real pytest run (facts
  stubbed, as every pytest-probe gate test stubs them: the script is TS-only);
- the real `_falsifier_gates` over real git, a real vitest, the real facts
  and coupling scripts, where a vitest is installed (`LEERIE_TEST_VITEST_DIR`).
Only `claude_p` is never reached: `_falsifier_gates` is given the sample.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.test_delivery_detection import (_facts_for, _placed, _placed_facts,
                                           _repo, _st, _ts_dir)

HAS_TS = _ts_dir() is not None
needs_ts = pytest.mark.skipif(not HAS_TS, reason="node or the typescript "
                              "package unavailable")


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "node_modules").mkdir(parents=True)
    os.symlink(_ts_dir(), tree / "node_modules" / "typescript")
    return tree


def _facts(leerie, tmp_path: Path, text: str, case: str,
           rel: str = "probe/p.test.ts") -> dict:
    src = tmp_path / "src.test.ts"
    src.write_text(text)
    facts = _placed_facts(leerie, tmp_path, _tree(tmp_path), src, rel, case)
    assert "error" not in facts, facts
    return facts


def _assert_lines(facts: dict) -> set[int]:
    return {n for a in facts["asserts"] for n in range(a["start"],
                                                       a["end"] + 1)}


# === which calls are the framework's assertions ============================

KINDS = """import { describe, it } from "vitest";
import { expect as want } from "vitest";
import { expect as fx } from "./fixtures";
import nodeAssert from "node:assert";
import { strictEqual } from "node:assert/strict";
import { runStep, act } from "../src/lib";
describe("m", () => {
  it("kinds", async () => {
    const any = expect.any(String);
    runStep(1);
    const { window } = await runStep(2);
    expect(window).toBe(1);
    want(window).not.toBe(2);
    fx(window).toBe(3);
    expect.soft(window).toBe(4);
    await expect.poll(() => act(5)).toBe(5);
    nodeAssert.equal(window, 6);
    strictEqual(window, 7);
    assert(window);
    expect(
      act(8),
    ).toEqual(any);
    {
      const expect = (v: number) => ({ toBe: (w: number) => v === w });
      expect(window).toBe(9);
    }
    expect.extend({});
  });
});
"""


@needs_ts
def test_the_facts_name_the_frameworks_assertions(leerie, tmp_path):
    """Each line's verdict, read from the probe's syntax: a global
    `expect`/`assert`, an import named `expect` from a framework or a
    fixture file, `expect.soft`/`expect.poll`, any import of Node's
    `assert`, a multi-line chain whole — and never a setup call, a call
    binding a value, an asymmetric matcher, `expect.extend`, or a local
    function that shadows `expect`."""
    facts = _facts(leerie, tmp_path, KINDS, "m kinds")
    lines = _assert_lines(facts)
    yes = {12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22}
    no = {9, 10, 11, 25, 27}
    assert yes <= lines, sorted(yes - lines)
    assert not (no & lines), sorted(no & lines)
    # The chain is one assertion, from `expect(` to its matcher's `)`.
    assert {"start": 20, "end": 22} in facts["asserts"]


# === the e7512406 item 8 shapes ============================================

I8_HELPER = """import { describe, expect, it } from "vitest";
import { executeStep } from "../flow";
async function runStep(options: string[]) {
  const doc = { text: "" };
  await executeStep({ options, doc });
  return { window: doc };
}
describe("layered dropdown", () => {
  it("drives the visible control and commits", async () => {
    const { window } = await runStep(["Canada", "Mexico"]);
    const dd = window.text;
    expect(dd).toContain("Canada");
  });
});
"""
I8_INLINE = """import { describe, expect, it } from "vitest";
import { executeStep } from "../flow";
describe("layered dropdown", () => {
  it("drives the visible control and commits", async () => {
    const doc = { text: "" };
    await executeStep({ options: ["Canada", "Mexico"], doc });
    const dd = doc.text;
    expect(dd).toContain("Canada");
  });
});
"""
I8_CASE = "layered dropdown drives the visible control and commits"
I8_REL = "src/probe/layered-dropdown.test.ts"
HELPER_CALL = "const { window } = await runStep([\"Canada\", \"Mexico\"]);"
INLINE_CALL = "await executeStep({ options: [\"Canada\", \"Mexico\"], doc });"
REAL = "expect(dd).toContain(\"Canada\");"


def _frames(*lines: int) -> list[str]:
    """A failing run's message whose stack, innermost first, holds the code
    under test's frame and then these probe lines."""
    return ["StepVerificationError: probe found no candidates on page\n"
            "    at executeStep (/t/src/flow.ts:40:11)\n"
            + "".join(f"    at f (/t/{I8_REL}:{n}:5)\n" for n in lines)]


@needs_ts
@pytest.mark.parametrize("probe,quote,frames,quoted,at", [
    # As recorded: the helper's call quoted, its step threw inside the helper
    # (innermost probe frame 5, then the call at 10).
    (I8_HELPER, HELPER_CALL, (5, 10), False, False),
    # The same probe with its real assertion quoted: a setup failure still.
    (I8_HELPER, REAL, (5, 10), True, False),
    # The helper's body in the case, the call that threw quoted: the shape
    # that passed every gate when any quoted line was the assertion.
    (I8_INLINE, INLINE_CALL, (6,), False, False),
    (I8_INLINE, REAL, (6,), True, False),
    # A failure at the assertion itself confirms, as before.
    (I8_INLINE, REAL, (8,), True, True),
    # A quote that names both the setup call and the assertion counts only
    # at the assertion's line.
    (I8_INLINE, INLINE_CALL + " " + REAL, (6,), True, False),
    (I8_INLINE, INLINE_CALL + " " + REAL, (8,), True, True),
])
def test_a_quoted_setup_call_is_not_the_assertion(leerie, tmp_path, probe,
                                                  quote, frames, quoted, at):
    facts = _facts(leerie, tmp_path, probe, I8_CASE, I8_REL)
    got = leerie._falsifier_quoted_assertion_lines(probe, quote,
                                                   facts["asserts"])
    assert bool(got) is quoted, got
    assert leerie._falsifier_failed_at_assertion(
        _frames(*frames), I8_REL, probe, quote, steps=facts["steps"],
        asserts=facts["asserts"]) is at


def test_without_assertions_no_line_is_one(leerie):
    """Facts that report no assertions name none: the quote is never
    taken on its own word."""
    assert leerie._falsifier_quoted_assertion_lines(I8_INLINE, REAL,
                                                    None) == set()
    assert leerie._falsifier_quoted_assertion_lines(I8_INLINE, REAL,
                                                    []) == set()


# === the real gate, a real pytest run ======================================

PY_PROBE_REL = "falsify_probe/test_add_probe.py"
PY_ITEM = "add(2, 3) returns 5, the sum of its arguments"
# The setup call throws inside the code under test, so the innermost probe
# frame is its own line, before the assertion: e7512406 item 8 inlined.
PY_SETUP_THROWS = ("from calc import add\n\n\n"
                   "def test_add_returns_the_sum():\n"
                   "    add(None, 3)\n"
                   "    assert add(2, 3) == 5\n")


def _py_gates(leerie, tmp_path, monkeypatch, quote: str, src: str) -> dict:
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    lane = leerie._falsifier_checkout(st, "falsifier-lane0", head)
    confirm = leerie._falsifier_checkout(st, "falsifier-confirm", head)
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", _placed(
        lambda tree, probe, case, **_kw: _facts_for(Path(probe).read_text())))
    p = lane / PY_PROBE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)
    res = {"verdict": "unmet_demonstrated", "probe_path": PY_PROBE_REL,
           "case_name": "test_add_returns_the_sum", "assertion_quote": quote,
           "item_span_quote": "add(2, 3) returns 5", "entry_point": "add",
           "reason": "add subtracts"}
    return asyncio.run(leerie._falsifier_gates(
        st, dict(leerie.DEFAULT_CAPS), res=res, lane=lane,
        confirm_tree=confirm, sha=head, item_text=PY_ITEM,
        acc_paths=set(), label="t"))


def test_the_gate_refuses_a_quoted_setup_call(leerie, tmp_path,
                                              monkeypatch):
    """The setup call that threw, quoted, fails every run at its own line:
    before, every gate passed and the sample was confirmed. It names no
    assertion, so neither the quoted assertion's gate nor the at-assertion
    gate passes."""
    out = _py_gates(leerie, tmp_path, monkeypatch, "add(None, 3)",
                    PY_SETUP_THROWS)
    g = out["gates"]
    assert g["fails_under_bstar"] is True and g["not_coupled"] is True, g
    assert out["failure_line"] == 5
    assert g["assertion_in_case"] is False
    assert g["at_assertion"] is False
    assert out["confirmed"] is False


def test_the_gate_still_refuses_that_probe_quoting_its_assertion(
        leerie, tmp_path, monkeypatch):
    out = _py_gates(leerie, tmp_path, monkeypatch, "assert add(2, 3) == 5",
                    PY_SETUP_THROWS)
    assert out["gates"]["assertion_in_case"] is True
    assert out["gates"]["at_assertion"] is False
    assert out["confirmed"] is False


def test_the_gate_confirms_a_probe_failing_at_its_quoted_assertion(
        leerie, tmp_path, monkeypatch):
    src = PY_SETUP_THROWS.replace("    add(None, 3)\n", "    add(1, 1)\n")
    out = _py_gates(leerie, tmp_path, monkeypatch, "assert add(2, 3) == 5",
                    src)
    assert out["confirmed"] is True, out


# === the real gate, a real vitest ===========================================

VITEST_DIR = os.environ.get("LEERIE_TEST_VITEST_DIR", "")
HAS_VITEST = bool(VITEST_DIR) and os.access(
    os.path.join(VITEST_DIR, "node_modules", ".bin", "vitest"), os.X_OK)

FLOW = """export async function executeStep(o: { options: string[]; doc: { text: string } }): Promise<void> {
  if (o.options.length > 1) throw new Error("probe found no candidates on page");
  o.doc.text = o.options[0] ?? "";
}

export async function pick(options: string[]): Promise<string> {
  const doc = { text: "" };
  await executeStep({ options: options.slice(0, 1), doc });
  return doc.text.toUpperCase();
}
"""
FLOW_ITEM = ("executeStep commits the chosen option of a layered dropdown "
             "to the visible control")
FAILS_AT_ASSERTION = I8_INLINE.replace(
    "await executeStep({ options: [\"Canada\", \"Mexico\"], doc });",
    "await executeStep({ options: [\"Mexico\"], doc });")


def _vitest_gates(leerie, tmp_path, monkeypatch, probe: str,
                  quote: str) -> dict:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    mods = Path(VITEST_DIR) / "node_modules"
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    (repo / "node_modules" / "vitest").symlink_to(mods / "vitest")
    (repo / "node_modules" / ".bin" / "vitest").symlink_to(
        os.path.realpath(mods / ".bin" / "vitest"))
    (repo / "node_modules" / "typescript").symlink_to(_ts_dir())
    (repo / "package.json").write_text('{"type": "module"}\n')
    (repo / "src" / "flow.ts").write_text(FLOW)
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        'test = "npx vitest run"\n'
        'test_scoped = "npx vitest run {test_files}"\n')
    (repo / ".gitignore").write_text(
        ".leerie/\nnode_modules/.vite/\nnode_modules/.vitest/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", Path(_ts_dir()))
    st = _st(leerie, tmp_path, repo, head)
    lane = leerie._falsifier_checkout(st, "falsifier-lane0", head)
    confirm = leerie._falsifier_checkout(st, "falsifier-confirm", head)
    p = lane / I8_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(probe)
    res = {"verdict": "unmet_demonstrated", "probe_path": I8_REL,
           "case_name": I8_CASE, "assertion_quote": quote,
           "item_span_quote": "commits the chosen option",
           "entry_point": "executeStep", "reason": "it throws"}
    return asyncio.run(leerie._falsifier_gates(
        st, dict(leerie.DEFAULT_CAPS), res=res, lane=lane,
        confirm_tree=confirm, sha=head, item_text=FLOW_ITEM,
        acc_paths=set(), label="t"))


@needs_ts
@pytest.mark.skipif(not HAS_VITEST, reason="no vitest installed "
                    "(LEERIE_TEST_VITEST_DIR)")
def test_a_real_vitest_probe_quoting_the_call_that_threw_is_refused(
        leerie, tmp_path, monkeypatch):
    """e7512406 item 8 inlined, end to end: the call under test throws on
    its own line every run, B* confirms the failure, the facts and the
    coupling check pass it — and the quote names no assertion."""
    out = _vitest_gates(leerie, tmp_path, monkeypatch, I8_INLINE,
                        INLINE_CALL)
    g = out["gates"]
    assert g["fails_under_bstar"] is True, out
    assert g["facts_read"] is True and g["not_coupled"] is True, out
    assert out["failure_line"] == 6, out
    assert g["assertion_in_case"] is False
    assert g["at_assertion"] is False
    assert out["confirmed"] is False


@needs_ts
@pytest.mark.skipif(not HAS_VITEST, reason="no vitest installed "
                    "(LEERIE_TEST_VITEST_DIR)")
def test_a_real_vitest_probe_failing_at_its_assertion_is_confirmed(
        leerie, tmp_path, monkeypatch):
    out = _vitest_gates(leerie, tmp_path, monkeypatch, FAILS_AT_ASSERTION,
                        REAL)
    assert out["failure_line"] == 8, out
    assert out["confirmed"] is True, out
