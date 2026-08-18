"""Behavioural probe for `prompts/planner.md`'s `extent` decision rules.

**Not a test module.** `pytest.ini` sets `python_files = test_*.py`, so this is
never collected — same arrangement as
`tests/fixtures/incident_2026_07_19/generate.py`. It spawns real `claude -p`
workers and costs money; run it by hand.

## Why this exists

`prompts/planner.md` is advisory (DESIGN §12). `tests/test_planner_extent_out_of_scope.py`
can prove the words are present and correctly ordered; it cannot prove a planner
obeys them. A rewrite that keeps every phrase those tests assert can still stop
working — measured: a draft patch scored 6/6, and the first re-validation of the
actually-shipped wording came back 5/6 before a larger sample settled at 17/18.

So CLAUDE.md instructs re-running this before trusting an edit to that section.
That instruction is only followable if the harness ships with the repo.

## What it measures

One decision, in isolation: given a task that fences off a surface and an
acceptance criterion whose only implementation site sits on it, does the
`testing`-domain planner declare `extent: external` (or decline to emit the
subtask), or does it declare `extent: in_plan` — the classification that reaches
`phase_reconcile` with no provider and aborts the run?

`in_plan` is the failure. Both `external` and "no subtask" are safe.

## Method notes that took two contaminated attempts to get right

- The extent rules are extracted from the **live** `prompts/planner.md`, never
  reproduced here. A reproduction is blind to the file it is meant to validate.
- Run against a **sandbox copy** of the target repo with its planning docs
  removed. Twice the model read a task doc off disk that had been corrected
  *after* the failing run, and answered from that instead of the task text under
  test. Instructing it not to read them did not work.
- The task text should come from a failing run's `run.json`, not from the file
  on disk, for the same reason.

## Usage

    python3 tests/manual/planner_fence_probe.py \\
        --repo /path/to/sandbox-copy-without-planning-docs \\
        --task /path/to/as-run-task.md \\
        --criterion "<the acceptance criterion, quoted from the task>" \\
        -n 6

Compare against a baseline by pointing `--planner-md` at a checkout of the
previous `prompts/planner.md`.

## The recorded numbers, and how to reproduce them

The figures CLAUDE.md cites came from this configuration:

- repo: a copy of the target repo containing only `src/`, `prisma/`, `docs/`
  and `package.json` — its `audit/` planning docs **removed**, because they had
  been corrected after the failing run and the model answered from them.
- task: the as-run text recovered from that run's `run.json`, not the file on
  disk.
- criterion: *"Fix the two known config values: replace the reserved
  `555-01xx` fictional support phone with the intended real number (or make it
  env-configurable and documented)"*.
- `-n 18`, `--model sonnet`.

Results: the pre-fix `prompts/planner.md` scored **1/6 safe**; the as-shipped
wording **17/18** (Wilson 95% CI 0.74–0.99), Fisher one-sided **p = 0.00081**.

`--criterion` exists so those numbers are reproducible. An earlier version of
this file asked the model to locate the criterion itself, which is a *different
and unmeasured* instrument — citing 17/18 beside it would have been a number
borrowed from another command.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

# Anchors bounding the `requires`/`extent` rules plus the test-wiring mandate.
# Deliberately phrase-anchored rather than line-numbered: line numbers rot, and
# a silently-empty extraction would make every run look safe.
_START = "**`requires` is an array of objects"
_END = "silent middle"

SCHEMA = json.dumps({
    "type": "object",
    "required": ["emit_subtask", "extent", "reason"],
    "properties": {
        "emit_subtask": {"type": "boolean"},
        "extent": {"type": "string", "enum": ["in_plan", "external", "n/a"]},
        "reason": {"type": "string"},
    },
})


def extract_rules(planner_md: pathlib.Path) -> str:
    text = planner_md.read_text()
    i = text.find(_START)
    j = text.find(_END, i + 1)
    if i == -1 or j == -1:
        raise SystemExit(
            f"could not locate the extent rules in {planner_md} "
            f"(start={i}, end={j}). Update the anchors rather than shipping an "
            "empty extraction — an empty prompt scores 'safe' for the wrong "
            "reason.")
    end = text.find("\n", j)
    # `find` returns -1 on a final line with no trailing newline, and -1 + 1
    # is 0 — an empty slice, which would score every sample "safe" for the
    # wrong reason. Same hazard the guard above exists for.
    return text[i:] if end == -1 else text[i:end + 1]


def build_prompt(rules: str, task: str, criterion: str) -> str:
    ctx = json.dumps({"task": task, "source_of_truth": "codebase"}, indent=2)
    return f"""You are the leerie `testing`-domain planner, decomposing ONE domain of a larger
task. You run read-only. Other planner domains are planning in parallel and you
CANNOT see their subtasks.

These are your governing rules for the `requires` field:

{rules}

CONTEXT:
{ctx}

YOUR TASK RIGHT NOW: decide the single subtask covering this acceptance criterion:
"{criterion}"

Investigate the source code (you are in the repo; use Read/Grep/Glob), then answer:
- emit_subtask: do you emit a `testing`-domain subtask validating that criterion?
- extent: if it declares a `requires` on the capability that satisfies the
  criterion, what `extent` do you declare? "n/a" only if you emit no requires
  entry at all.
- reason: one sentence.

Answer ONLY with the JSON object."""


def run_once(prompt: str, cwd: str, model: str) -> dict:
    """One sample. Fails CLOSED — see `main`.

    Anything that is not a parsed verdict is an error, never a pass. The
    scoring in `main` keys on `extent == "in_plan"` for failure, so a
    crash, a non-zero exit or a timeout would otherwise be counted as
    "safe" and a completely broken probe would print a perfect score.
    """
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", model, "--json-schema", SCHEMA,
             "--allowedTools", "Read", "Grep", "Glob"],
            input=prompt, capture_output=True, text=True, cwd=cwd, timeout=600)
    except subprocess.TimeoutExpired:
        return {"_error": "timeout"}
    if r.returncode != 0:
        return {"_error": f"exit {r.returncode}", "_stderr": r.stderr[:200]}
    try:
        j = json.loads(r.stdout.strip())
    except Exception:
        return {"_error": "unparseable", "_raw": r.stdout.strip()[:300]}
    if j.get("extent") not in ("in_plan", "external", "n/a"):
        return {"_error": "no verdict", "_raw": str(j)[:300]}
    return j


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True,
                    help="sandbox copy of the target repo, planning docs removed")
    ap.add_argument("--task", required=True,
                    help="as-run task text (recover it from run.json)")
    ap.add_argument("--criterion", required=True,
                    help="the acceptance criterion to probe, quoted verbatim "
                         "from the task (see the docstring for the exact "
                         "invocation that produced the recorded 17/18)")
    ap.add_argument("--planner-md", default=str(REPO / "prompts" / "planner.md"))
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()

    rules = extract_rules(pathlib.Path(args.planner_md))
    prompt = build_prompt(rules, pathlib.Path(args.task).read_text(),
                          args.criterion)

    results = []
    for i in range(args.n):
        j = run_once(prompt, args.repo, args.model)
        results.append(j)
        print(f"#{i}: emit={j.get('emit_subtask')} extent={j.get('extent')} "
              f":: {str(j.get('reason'))[:90]}", flush=True)

    errors = [x for x in results if "_error" in x]
    unsafe = [x for x in results if x.get("extent") == "in_plan"]
    safe = len(results) - len(unsafe) - len(errors)
    print(f"\nSAFE {safe}/{len(results)}  "
          f"(in_plan failures: {len(unsafe)}; errors: {len(errors)})")
    if errors:
        print("ERRORS ARE NOT PASSES — the run is inconclusive until they are "
              "resolved:")
        for e in errors:
            print("   ", e)
    print(json.dumps(results, indent=1))
    return 1 if (unsafe or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
