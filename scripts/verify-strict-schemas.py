#!/usr/bin/env python3
"""Check every schema in `SCHEMAS` against the real API's strict mode.

Run this after editing any entry in `SCHEMAS`, or after touching
`_strictify_schema`, when `--dangerously-force-strict-output` matters. It sends
each hardened schema to `api.anthropic.com` and reports which ones grammar
compilation accepts.

    python3 scripts/verify-strict-schemas.py

**Not part of the test suite, deliberately.** `pytest.ini` sets
`testpaths = tests` and `python_files = test_*.py`, so nothing here is
collected — the suite is LLM-free and stays that way. Do not "tidy" this into
`tests/`: it needs live credentials and network, and it would turn every CI run
into a billable API call.

**Why it exists.** `tests/test_strict_output_proxy.py::test_every_real_schema_survives_the_transform`
walks the transform's output looking for residuals — using
`_STRICT_UNSUPPORTED_KEYWORDS`, the same constant `_strictify_schema` consults.
It can only establish that the transform agrees with itself, so it shares every
blind spot the transform has. It did: `_strictify_schema` once tested
`type == "object"`, which is False for the union `["object", "null"]` that
`implementer.clarification_question` declares, and the API rejected the whole
implementer schema while that test passed happily. Under
`--dangerously-force-strict-output` that 400s every implementer call, visible
only as the retry storm the flag exists to remove. A transform written against
an external contract has to be checked against that contract.

Two things about the API that are easy to get wrong here, both of which cost a
wrong conclusion when this script was first written:

* **A subscription OAuth token requires the Claude Code system-prompt
  identity.** Without it the API answers a bare `429 {"message":"Error"}`,
  which reads exactly like quota exhaustion and is not. leerie never trips this
  because it shells out to the real `claude` CLI, which always sends it; only a
  hand-rolled request like the one below can get the shape wrong.
* **Two limits are per-REQUEST aggregates, not per-schema:** at most 20
  `strict` tools, and at most 24 optional parameters summed across every schema
  in the request. Batching schemas to save calls trips both and establishes
  nothing about any individual schema — hence one request each below. Neither
  limit constrains leerie, which sends exactly one tool per request; its
  largest single schema carries 14 optional parameters.

Exit codes: 0 every schema compiles, 1 at least one was rejected, 2 the control
failed (the probe cannot detect a rejection, so a pass would mean nothing),
3 inconclusive — at least one schema never got a verdict, because it was
throttled or the request timed out. 3 is not a pass: a schema with no verdict
is unchecked, and the summary names which ones so a re-run can settle them.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "orchestrator"))

import leerie  # noqa: E402

CREDS = pathlib.Path.home() / ".claude" / ".credentials.json"
URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5-20250929"
# Generous: grammar compilation for a large schema is slow, and a premature
# timeout costs a verdict rather than saving time. Measured, `planner` blew
# past 120 s.
READ_TIMEOUT_S = 600
# The identity a subscription OAuth token is scoped for. See the module
# docstring — omitting this yields a 429 that looks like quota exhaustion.
CC_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."


def token() -> str:
    if not CREDS.exists():
        sys.exit(f"no credentials at {CREDS} — run `claude /login`")
    oauth = json.loads(CREDS.read_text())["claudeAiOauth"]
    left = oauth["expiresAt"] / 1000 - time.time()
    if left <= 0:
        sys.exit(f"credential expired {-left / 3600:.1f}h ago — run `claude /login`")
    print(f"credential ok, {left / 3600:.1f}h remaining")
    return oauth["accessToken"]


def tool_for(name: str, harden: bool) -> dict:
    """One schema as a strict tool. `strict` is ALWAYS on; only hardening varies.

    Gating `strict` on `harden` makes the control toothless: with no `strict`
    there is no grammar compilation and nothing to reject, so it returns 200
    whatever the schema looks like. A control that cannot fail proves nothing
    about the probe beside it.
    """
    schema = copy.deepcopy(leerie.SCHEMAS[name])
    if harden:
        leerie._strictify_schema(schema)
    return {"name": f"probe_{name}", "description": "schema compilation probe",
            "input_schema": schema, "strict": True}


def send(tool: dict, tok: str) -> tuple[int, str, float]:
    """Returns `(status, body, elapsed_seconds)`. Status 0 means no verdict.

    A transport failure must NOT propagate. Compiling a large schema into a
    grammar is slow — measured, `planner` exceeded a 120 s read timeout — and
    an exception here would abandon the sweep and discard every schema already
    checked. A timeout is "no verdict for this one", not "this schema is
    broken", and the two must not be conflated: reporting a slow schema as
    rejected would send someone hunting a bug that is not there.
    """
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 1,  # tool schemas are validated before generation
        "system": [{"type": "text", "text": CC_SYSTEM}],
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [tool],
    }).encode()
    req = urllib.request.Request(URL, data=payload, method="POST", headers={
        "authorization": f"Bearer {tok}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=READ_TIMEOUT_S) as r:
            return r.status, r.read().decode("utf-8", "replace"), \
                time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), \
            time.monotonic() - started
    except Exception as e:  # noqa: BLE001 - see docstring
        return 0, f"transport failure: {type(e).__name__}: {e}", \
            time.monotonic() - started


def message_of(resp: str) -> str:
    try:
        return json.loads(resp)["error"]["message"]
    except Exception:
        return " ".join(resp.split())[:300]


def main() -> int:
    tok = token()
    names = sorted(leerie.SCHEMAS)

    # Control: a real schema, strict, deliberately NOT hardened. It must be
    # rejected, or this probe cannot tell a compiling schema from a broken one
    # and every "ok" below is worthless.
    status, resp, _ = send(tool_for(names[0], harden=False), tok)
    print(f"control (strict, un-hardened): {status}  {message_of(resp)[:140]}")
    if status == 429 or status == 0:
        print("no verdict on the control — re-run later.")
        return 3
    if status == 200:
        print("\nCONTROL PASSED — the probe cannot detect a rejection. "
              "Something changed upstream; fix this before trusting a sweep.")
        return 2

    print(f"\nprobing {len(names)} schemas, one request each")
    print("-" * 72)
    rejected: dict[str, str] = {}
    no_verdict: dict[str, str] = {}
    for name in names:
        status, resp, secs = send(tool_for(name, harden=True), tok)
        if status == 200:
            print(f"  ok   {name:24} {secs:6.1f}s")
        elif status in (0, 429):
            # Throttled or a transport failure: no information about this
            # schema either way. Keep going — the remaining schemas are still
            # worth checking, and abandoning the run would discard every
            # verdict already earned.
            no_verdict[name] = message_of(resp)
            print(f"  ??   {name:24} {secs:6.1f}s  {no_verdict[name][:120]}")
        else:
            rejected[name] = message_of(resp)
            print(f"  FAIL {name:24} {secs:6.1f}s  {rejected[name][:160]}")
        time.sleep(1.0)

    print("-" * 72)
    if rejected:
        print(f"\n{len(rejected)} schema(s) REJECTED — `_strictify_schema` does "
              "not cover something the API requires:")
        for name, why in rejected.items():
            print(f"  {name}: {why}")
        return 1
    if no_verdict:
        print(f"\nINCONCLUSIVE — {len(no_verdict)} schema(s) never got a "
              f"verdict: {', '.join(sorted(no_verdict))}")
        print(f"  {len(names) - len(no_verdict)} of {len(names)} confirmed "
              "compiling. Re-run to settle the rest.")
        return 3
    print(f"\nall {len(names)} schemas compile under strict mode (model={MODEL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
