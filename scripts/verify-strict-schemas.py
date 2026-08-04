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

Exit codes: 0 all compile, 1 at least one rejected, 2 the control failed (the
probe cannot detect a rejection, so a pass would mean nothing), 3 rate-limited
before a verdict.
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


def send(tool: dict, tok: str) -> tuple[int, str]:
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
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


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
    status, resp = send(tool_for(names[0], harden=False), tok)
    print(f"control (strict, un-hardened): {status}  {message_of(resp)[:140]}")
    if status == 429:
        print("rate-limited before establishing anything — re-run later.")
        return 3
    if status == 200:
        print("\nCONTROL PASSED — the probe cannot detect a rejection. "
              "Something changed upstream; fix this before trusting a sweep.")
        return 2

    print(f"\nprobing {len(names)} schemas, one request each")
    print("-" * 72)
    rejected: dict[str, str] = {}
    for name in names:
        status, resp = send(tool_for(name, harden=True), tok)
        if status == 429:
            print(f"  RATE {name} — inconclusive; re-run later.")
            return 3
        if status == 200:
            print(f"  ok   {name}")
        else:
            rejected[name] = message_of(resp)
            print(f"  FAIL {name}  {rejected[name][:180]}")
        time.sleep(1.0)

    print("-" * 72)
    if rejected:
        print(f"\n{len(rejected)} schema(s) REJECTED — `_strictify_schema` does "
              "not cover something the API requires:")
        for name, why in rejected.items():
            print(f"  {name}: {why}")
        return 1
    print(f"\nall {len(names)} schemas compile under strict mode (model={MODEL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
