"""`--dangerously-force-strict-output` — the transform and the proxy.

`claude -p --json-schema` is *validated*, not constrained. The CLI injects the
schema as a synthetic `StructuredOutput` tool with **no `strict: true`** and no
`output_config` — confirmed from a captured outbound request (2026-08-04), where
`tools[18].name == "StructuredOutput"` and `strict` was absent. Measured across
the run corpus, **2,861 of 9,924 submissions (28.8%)** are malformed as a direct
result, in exactly the shapes the vendor documents as the cost of omitting
strict mode.

Setting `strict: true` compiles the schema into a sampling grammar, making those
shapes unrepresentable. Two live experiments established that this works on
subscription auth: injecting `strict` alone returned 400 with a *schema*
complaint (not a permissions one), and adding `additionalProperties: false`
returned 200 with valid output.

The two properties this file exists to pin:

**Fail-open on the transform.** The tool name and shape are a private,
unversioned interface. Anything unexpected must forward the request
byte-identical, so an upstream rename costs the guarantee rather than the run.

**Fail-closed on the listener.** If the proxy cannot bind, the run must die
rather than quietly proceeding unconstrained — an operator who asked for the
guarantee must never be silently given the old behaviour.
"""
from __future__ import annotations

import copy
import json
import re

import pytest


def _wrap(schema: dict, name: str = "StructuredOutput") -> bytes:
    """A minimal Messages body carrying one injected tool."""
    return json.dumps({
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"name": name, "description": "d", "input_schema": schema}],
    }).encode()


def _tool(body: bytes) -> dict:
    return json.loads(body)["tools"][0]


# ----- the transform ---------------------------------------------------------

def test_sets_strict_on_the_injected_tool(leerie):
    out = leerie._strictify_request(_wrap({"type": "object", "properties": {}}))
    assert out is not None
    assert _tool(out[0])["strict"] is True


def test_hardens_objects_nested_inside_array_items(leerie):
    """leerie nests objects inside arrays (`collisions[]`, `subtasks[]`,
    `wiring_defects[]`). A properties-only pass would leave those unhardened and
    the API would reject the request naming that exact path."""
    out = leerie._strictify_request(_wrap({
        "type": "object",
        "properties": {
            "rows": {"type": "array",
                     "items": {"type": "object",
                               "properties": {"a": {"type": "string"}}}},
        },
    }))
    schema = _tool(out[0])["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["rows"]["items"]["additionalProperties"] is False


def test_strips_keywords_grammar_compilation_cannot_express(leerie):
    out = leerie._strictify_request(_wrap({
        "type": "object",
        "properties": {
            "s": {"type": "string", "minLength": 1, "maxLength": 200},
            "n": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }))
    props = _tool(out[0])["input_schema"]["properties"]
    for gone in ("minLength", "maxLength"):
        assert gone not in props["s"]
    for gone in ("minimum", "maximum"):
        assert gone not in props["n"]


def test_minitems_survives_at_one_but_is_clamped_above(leerie):
    """`minItems` is supported only at 0 or 1, so a larger value must be
    clamped rather than dropped — dropping it would silently widen the
    contract more than necessary."""
    out = leerie._strictify_request(_wrap({
        "type": "object",
        "properties": {"a": {"type": "array", "minItems": 1},
                       "b": {"type": "array", "minItems": 5}},
    }))
    props = _tool(out[0])["input_schema"]["properties"]
    assert props["a"]["minItems"] == 1
    assert props["b"]["minItems"] == 1


def test_every_real_schema_survives_the_transform(leerie):
    """All 23 shipped schemas come out clean under the transform's own rules.

    **What this does and does not establish.** It walks the hardened output
    looking for residuals using `_STRICT_UNSUPPORTED_KEYWORDS` — the same
    constant `_strictify_schema` consults — so it proves the transform is
    self-consistent, *not* that the API accepts the result. It shares any blind
    spot the transform has, and it did: the union-type bug pinned in
    `test_every_object_shape_is_hardened` passed this test while the API
    rejected `implementer` outright.

    The independent checks are its siblings: `test_no_schema_has_an_unhardened_object_shape`
    (spells out "is an object" separately) and, decisively, the live sweep in
    `<scratchpad>/verify_strict_live.py`, which sends every schema to the real
    API. `reconciler` (11 object nodes) and `conformer` (10) are the stress
    cases here.
    """
    unsupported = leerie._STRICT_UNSUPPORTED_KEYWORDS

    def residual(node, path="$"):
        bad = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k in unsupported:
                    bad.append(f"{path}.{k}")
                if k == "minItems" and v not in (0, 1):
                    bad.append(f"{path}.minItems={v}")
                bad += residual(v, f"{path}.{k}")
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                bad.append(f"{path} missing additionalProperties:false")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                bad += residual(v, f"{path}[{i}]")
        return bad

    offenders = {}
    for name, schema in leerie.SCHEMAS.items():
        out = leerie._strictify_request(_wrap(copy.deepcopy(schema)))
        assert out is not None, name
        bad = residual(_tool(out[0])["input_schema"])
        if bad:
            offenders[name] = bad[:3]
    assert not offenders, f"schemas still violating the strict subset: {offenders}"


@pytest.mark.parametrize("node, why", [
    ({"type": ["object", "null"], "properties": {"a": {"type": "string"}}},
     "union type — leerie's own implementer.clarification_question"),
    ({"type": ["null", "object"], "properties": {"a": {"type": "string"}}},
     "union type, object not first"),
    ({"properties": {"a": {"type": "string"}}},
     "properties with no declared type"),
    ({"type": "object", "properties": {"a": {"type": "string"}}},
     "the plain case (control)"),
])
def test_every_object_shape_is_hardened(leerie, node, why):
    """`additionalProperties: false` must land on every node the API treats as
    an object — not only on `{"type": "object"}`.

    Regression, found by a live sweep of all 23 schemas against the real API
    (2026-08-04): the shipped check was `node.get("type") == "object"`, which is
    False for the *union* `["object", "null"]`. leerie's own
    `implementer.clarification_question` is exactly that shape, so the API
    rejected the implementer schema outright — "tools.0.custom: For 'object'
    type, 'additionalProperties' must be explicitly set to false".

    That would have 400'd every call by the most-used worker in the system,
    surfacing only as the retry storm this flag exists to eliminate. No unit
    test could have caught it: the sweep that was supposed to
    (`test_every_real_schema_survives_the_transform`) checked the transform's
    output against the transform's own rule set, so it shared the blind spot.
    """
    import copy
    n = copy.deepcopy(node)
    leerie._strictify_schema(n)
    assert n.get("additionalProperties") is False, f"unhardened: {why}"


def test_no_schema_has_an_unhardened_object_shape(leerie):
    """The whole-corpus form of the test above, using an *independent*
    object-detection rule rather than re-reading `_strictify_schema`'s own.

    This is the check `test_every_real_schema_survives_the_transform` could not
    perform: that one walks the result looking for `_STRICT_UNSUPPORTED_KEYWORDS`
    — the same constant the transform consults — so it can only prove
    self-consistency. Here the notion of "is an object" is spelled out
    separately, so a narrowing of the transform's own test fails this.
    """
    import copy

    def unhardened(node, path="$"):
        bad = []
        if isinstance(node, dict):
            declared = node.get("type")
            objish = (declared == "object"
                      or (isinstance(declared, list) and "object" in declared)
                      or (declared is None and "properties" in node))
            if objish and node.get("additionalProperties") is not False:
                bad.append(path)
            for k, v in node.items():
                bad += unhardened(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                bad += unhardened(v, f"{path}[{i}]")
        return bad

    offenders = {}
    for name, schema in leerie.SCHEMAS.items():
        s = copy.deepcopy(schema)
        leerie._strictify_schema(s)
        bad = unhardened(s)
        if bad:
            offenders[name] = bad[:3]
    assert not offenders, (
        f"the API rejects these outright: {offenders}")


def test_union_type_detection_is_not_a_substring_match(leerie):
    """A type that merely *contains* the letters of "object" is not an object.
    Guards a lazy `"object" in str(declared)` implementation."""
    import copy
    n = copy.deepcopy({"type": "string", "description": "an object, sort of"})
    leerie._strictify_schema(n)
    assert "additionalProperties" not in n, "a string node was hardened"


def test_the_real_schema_sweep_is_not_vacuous(leerie):
    """Anti-vacuity: the sweep above only means something if the shipped
    schemas genuinely need fixing. Measured: 21 unsupported keywords across 8
    schemas, and almost every object node lacks additionalProperties."""
    unsupported = leerie._STRICT_UNSUPPORTED_KEYWORDS
    needed = 0
    for schema in leerie.SCHEMAS.values():
        blob = json.dumps(schema)
        needed += sum(blob.count(f'"{k}"') for k in unsupported)
    assert needed >= 15, f"expected the shipped schemas to need real fixing, found {needed}"


# ----- fail-open -------------------------------------------------------------

@pytest.mark.parametrize("label, payload", [
    ("tool renamed", {"tools": [{"name": "Other", "input_schema": {"type": "object"}}]}),
    ("tool absent", {"tools": []}),
    ("tool duplicated", {"tools": [{"name": "StructuredOutput", "input_schema": {}},
                                   {"name": "StructuredOutput", "input_schema": {}}]}),
    ("no input_schema", {"tools": [{"name": "StructuredOutput"}]}),
    ("input_schema not an object", {"tools": [{"name": "StructuredOutput",
                                               "input_schema": "nope"}]}),
    ("no tools key", {"messages": []}),
    ("tools not a list", {"tools": "nope"}),
])
def test_unexpected_shapes_forward_unmodified(leerie, label, payload):
    """An upstream rename must cost the guarantee, never the run."""
    assert leerie._strictify_request(json.dumps(payload).encode()) is None, label


def test_non_json_body_forwards_unmodified(leerie):
    assert leerie._strictify_request(b"<html>not json</html>") is None


def test_transform_does_not_touch_other_tools(leerie):
    """The captured request carried 29 tools. Only ours may change."""
    body = json.dumps({"tools": [
        {"name": "Bash", "input_schema": {"type": "object",
                                          "properties": {"command": {"type": "string",
                                                                     "minLength": 1}}}},
        {"name": "StructuredOutput", "input_schema": {"type": "object", "properties": {}}},
    ]}).encode()
    out = leerie._strictify_request(body)
    assert out is not None
    bash = json.loads(out[0])["tools"][0]
    assert bash == json.loads(body)["tools"][0], "a non-target tool was modified"


def test_strictify_schema_reports_what_it_changed(leerie):
    """The counts feed the log line; a silent transform would make an upstream
    change undiagnosable."""
    schema = {"type": "object", "properties": {"s": {"type": "string", "minLength": 1}}}
    hardened, stripped = leerie._strictify_schema(schema)
    assert hardened == 1 and stripped == 1


# ----- the proxy -------------------------------------------------------------
#
# Exercised against a mock upstream, so these never touch the network. The
# hardening below is not defensive styling: a naive version of this proxy was
# load-tested and failed two ways — 34 of 40 concurrent connections survived
# (default executor saturation), and the port was not rebindable after
# shutdown. Both are pinned here.

import asyncio
import socket
import threading
import http.server
import socketserver


class _MockUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        # Echo back whether the request arrived with strict set, so the test
        # can prove the transform survived the whole round trip.
        strict = b'"strict": true' in body or b'"strict":true' in body
        payload = json.dumps({"saw_strict": strict}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Pool(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    request_queue_size = 256


@pytest.fixture()
def mock_upstream():
    srv = _Pool(("127.0.0.1", 0), _MockUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def _post(port: int, body: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"POST /v1/messages HTTP/1.1\r\nhost: x\r\n"
                 b"content-length: %d\r\n\r\n" % len(body) + body)
    await writer.drain()
    data = await reader.read(-1)
    writer.close()
    return data


def test_proxy_binds_an_ephemeral_port_and_releases_it(leerie):
    """Port 0 lets the OS pick, so concurrent leerie runs never collide. The
    release half is the regression pin: the naive version left the port
    unbindable, which breaks a second run in the same container."""
    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        assert port > 0
        await p.stop()
        return port
    port = asyncio.run(go())
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_proxy_rewrites_the_request_end_to_end(leerie, mock_upstream, monkeypatch):
    """The whole point: `strict` must actually reach the upstream."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", mock_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            data = await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
        return data, p
    data, p = asyncio.run(go())
    assert b'"saw_strict": true' in data
    assert p.rewritten == 1 and p.passed_through == 0


def test_proxy_forwards_unmodified_when_the_tool_is_renamed(leerie, mock_upstream,
                                                            monkeypatch):
    """Fail-open, end to end — the upstream must see no strict."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", mock_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            data = await _post(port, _wrap({"type": "object"}, name="SomethingElse"))
        finally:
            await p.stop()
        return data, p
    data, p = asyncio.run(go())
    assert b'"saw_strict": false' in data
    assert p.rewritten == 0 and p.passed_through == 1


@pytest.mark.parametrize("n", [5, 40])
def test_proxy_survives_concurrent_workers(leerie, mock_upstream, monkeypatch, n):
    """`max_parallel` defaults to 5 and is user-raisable, so the proxy sits in
    the path of every concurrent worker. 40 is the width at which the naive
    version lost 6 connections to executor saturation."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", mock_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=n)
        port = await p.start()
        try:
            body = _wrap({"type": "object", "properties": {}})
            results = await asyncio.gather(
                *[_post(port, body) for _ in range(n)], return_exceptions=True)
        finally:
            await p.stop()
        return results
    results = asyncio.run(go())
    ok = [r for r in results if isinstance(r, bytes) and b'"saw_strict": true' in r]
    assert len(ok) == n, f"only {len(ok)}/{n} concurrent connections completed"


def test_one_aborted_connection_does_not_kill_the_listener(leerie, mock_upstream,
                                                           monkeypatch):
    """A worker being reaped mid-request must not take down the proxy every
    other worker in the wave depends on."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", mock_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            # Connect, send a partial request, then hang up.
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"POST /v1/messages HTTP/1.1\r\ncontent-length: 999\r\n\r\n{")
            await w.drain()
            w.close()
            await asyncio.sleep(0.05)
            # The listener must still serve a healthy request.
            return await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
    assert b'"saw_strict": true' in asyncio.run(go())


# ----- flag resolution, collision guard, and the numeric bounds --------------

def test_flag_defaults_off(leerie, tmp_path):
    """Nothing about this feature may engage unless explicitly asked for."""
    assert leerie.resolve_dangerously_force_strict_output(tmp_path, False) is False


def test_flag_resolves_from_all_three_tiers(leerie, tmp_path, monkeypatch):
    assert leerie.resolve_dangerously_force_strict_output(tmp_path, True) is True
    monkeypatch.setenv(leerie.DANGEROUS_FORCE_STRICT_OUTPUT_ENV, "1")
    assert leerie.resolve_dangerously_force_strict_output(tmp_path, False) is True
    monkeypatch.delenv(leerie.DANGEROUS_FORCE_STRICT_OUTPUT_ENV)
    (tmp_path / "leerie.toml").write_text("dangerously_force_strict_output = true\n")
    assert leerie.resolve_dangerously_force_strict_output(tmp_path, False) is True


def test_help_text_discloses_all_four_risks(leerie):
    """The flag is where the risk is disclosed, so the disclosure is part of
    the contract — not decoration that can be trimmed later."""
    import inspect
    src = inspect.getsource(leerie)
    i = src.index('"--dangerously-force-strict-output"')
    block = src[i:i + 2600]
    # Rejoin adjacent string literals: the help text is written as many
    # concatenated fragments, so phrases straddle source lines.
    block = re.sub(r'"\s*\n\s*(f?)"', "", block)
    for required in ("not documented for subscription auth",   # terms
                     "no compatibility",                        # unversioned iface
                     "fails OPEN",                              # silent loss
                     "minLength",                               # stripped constraints
                     "ANTHROPIC_BASE_URL",                      # side effects
                     "Default is off"):
        assert required in block, f"help text no longer discloses: {required}"


def test_help_string_is_percent_escaped(leerie):
    """argparse %-expands help strings, so a bare `%` raises 'badly formed
    help string' and breaks `--help` for EVERY flag, not just this one. This
    bit during development."""
    import argparse
    ap = argparse.ArgumentParser()
    import inspect
    src = inspect.getsource(leerie)
    i = src.index('"--dangerously-force-strict-output"')
    block = src[i:i + 2600]
    assert "28.8%%" in block, "the percentage must be escaped as %% for argparse"


def test_collision_with_operator_base_url_is_fatal(leerie):
    """The flag owns ANTHROPIC_BASE_URL. Overriding a user's gateway silently
    and silently skipping the requested guarantee are both wrong, so leerie
    refuses and lets the operator choose."""
    import inspect
    src = inspect.getsource(leerie.main)
    assert 'os.environ.get("ANTHROPIC_BASE_URL")' in src
    assert "will not override" in src


@pytest.mark.parametrize("value, expected", [
    (0.8, 0.8),            # in range, untouched
    (0.0, 0.0),
    (1.0, 1.0),
    (5.0, 0.0),            # impossible on a 0-1 axis -> conservative
    (-2.0, 0.0),
    (None, 0.0),
    ("eight", 0.0),
    (float("nan"), 0.0),
    (float("inf"), 0.0),
])
def test_out_of_range_values_are_untrusted_not_clamped(leerie, value, expected):
    """Clamping was tried and rejected: on a 0-1 axis it maps 5.0 to 1.0, which
    still clears every threshold and preserves the permissive failure this
    exists to prevent. The corpus shows the confusion is real — fit_judge
    emitted `"fit": 8.5` on an axis declared 0-1."""
    assert leerie._bounded_or_conservative(value, 0.0, 1.0, 0.0, "probe") == expected


def test_a_bad_score_cannot_clear_the_fit_threshold(leerie):
    """The property that matters, stated directly against the real cap."""
    threshold = leerie.DEFAULT_CAPS["decompose_fit_threshold"]
    bogus = leerie._bounded_or_conservative(8.5, 0.0, 1.0, 0.0, "probe")
    assert bogus < threshold, "an uninterpretable score cleared the fit gate"


def test_every_documented_range_guard_is_wired(leerie):
    """Source-coupling: the helper is inert if a call site reads the raw value
    again. There are exactly three numeric bounds in `SCHEMAS`, and the docs
    name all three — this asserts all three, not a subset.

    Regression: the earlier version of this test asserted two of the three, and
    `provision.recipe[].timeout_s` was documented as guarded while being wired
    nowhere. A test that covers a subset of a documented contract is how that
    survived seven audit passes.
    """
    import inspect
    for fn in (leerie._recursive_decompose,        # fit_judge.score
               leerie.phase_adherence_gate,        # instruction_adherence
               leerie._recipe_timeout_s):          # provision timeout_s
        assert "_bounded_or_conservative(" in inspect.getsource(fn), (
            f"{fn.__name__} does not use the guard")


def test_the_three_guards_match_the_schemas_numeric_bounds(leerie):
    """Anti-drift: if a new `minimum`/`maximum` is added to any schema, strict
    mode will strip it and it needs a guard too. Fails when the count moves."""
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            if "minimum" in node or "maximum" in node:
                found.append(path)
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for v in node:
                walk(v, path)

    for name, schema in leerie.SCHEMAS.items():
        walk(schema, name)
    assert len(found) == 3, (
        f"schemas now carry {len(found)} range-bounded fields, not 3: {found}. "
        "Strict mode strips these — each needs a _bounded_or_conservative guard "
        "and a line in DESIGN §7 / IMPLEMENTATION.md.")


def test_recipe_timeout_rejects_a_negative_but_keeps_a_sane_value(leerie):
    """`minimum: 1` is stripped under the flag. `0` was already absorbed by the
    old `or 1800` fallback, but a negative is *truthy* and would have reached
    `wait_for(timeout=-5.0)`, firing instantly — a provisioning step failing for
    no visible reason."""
    default = leerie._PROVISION_TIMEOUT_DEFAULT_S
    assert leerie._recipe_timeout_s({"timeout_s": 600, "command": ["x"]}) == 600
    assert leerie._recipe_timeout_s({"timeout_s": -5, "command": ["x"]}) == default
    assert leerie._recipe_timeout_s({"timeout_s": 0, "command": ["x"]}) == default
    assert leerie._recipe_timeout_s({"command": ["x"]}) == default
    # Above the worker wall-clock cap: a step cannot outlive the worker.
    over = float(leerie.DEFAULT_CAPS["worker_timeout_sec"]) + 1
    assert leerie._recipe_timeout_s({"timeout_s": over, "command": ["x"]}) == default
    # An explicit ceiling is honoured over the default cap.
    assert leerie._recipe_timeout_s({"timeout_s": 3000, "command": ["x"]}, 100) == default


def test_both_recipe_timeout_consumers_go_through_the_guard(leerie):
    """The guard is worthless if either consumer still reads the raw field."""
    import inspect
    for fn in (leerie._format_provision_recipe_section,
               leerie._capture_conformance_baseline):
        src = inspect.getsource(fn)
        assert "_recipe_timeout_s(" in src, f"{fn.__name__} bypasses the guard"
        assert 'get("timeout_s")' not in src, (
            f"{fn.__name__} still reads timeout_s directly")


def test_worker_env_gets_the_base_url_only_when_the_proxy_is_running(leerie):
    """Default-off proof at the injection seam: no proxy, no variable."""
    import inspect
    src = inspect.getsource(leerie)
    i = src.index('worker_env["ANTHROPIC_BASE_URL"]')
    window = src[max(0, i - 400):i]
    assert "_STRICT_PROXY is not None" in window


# --------------------------------------------------------------------------
# Transport: every verb and every body framing must survive the hop.
#
# Only POST bodies are ever rewritten, so everything below is about the proxy
# not corrupting what it was never supposed to touch. Both defects these pin
# were live in the first implementation.
# --------------------------------------------------------------------------


class _RecordingUpstream(http.server.BaseHTTPRequestHandler):
    """Echoes back the method, path and body it actually received."""

    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):
        pass

    def _reply(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        type(self).seen.append({
            "method": self.command, "path": self.path, "body": body,
            # Lowercased names only: the point of recording these is to prove a
            # header did not survive, and the survivor's casing is the variable.
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })
        payload = json.dumps({"method": self.command, "len": len(body)}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = _reply


@pytest.fixture()
def recording_upstream():
    _RecordingUpstream.seen = []
    srv = _Pool(("127.0.0.1", 0), _RecordingUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _RecordingUpstream.seen
    srv.shutdown()


async def _raw(port: int, wire: bytes) -> bytes:
    """Send a hand-built request so the framing is under the test's control."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(wire)
    await writer.drain()
    data = await reader.read(-1)
    writer.close()
    return data


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_non_post_verbs_reach_upstream_as_themselves(leerie, recording_upstream,
                                                     monkeypatch, method):
    """Regression: `_upstream` hardcoded `method="POST"`, so any other verb the
    CLI issues (model listing, token counting, telemetry) was replayed upstream
    as a POST. The transform already branched on the method; the hop did not."""
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            return await _raw(port, b"%s /v1/models HTTP/1.1\r\nhost: x\r\n\r\n"
                              % method.encode())
        finally:
            await p.stop()
    data = asyncio.run(go())
    assert seen and seen[0]["method"] == method, (
        f"upstream saw {seen[0]['method'] if seen else 'nothing'}, not {method}")
    assert seen[0]["body"] == b"", "a bodyless verb acquired a body"
    assert b'"method": "%s"' % method.encode() in data


def test_post_still_reaches_upstream_as_post(leerie, recording_upstream,
                                             monkeypatch):
    """Anti-vacuity control for the parametrized test above: threading the
    method through must not have broken the one verb that always worked."""
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            return await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
    asyncio.run(go())
    assert seen[0]["method"] == "POST"
    assert b'"strict"' in seen[0]["body"]


def _chunked_wire(path: bytes, body: bytes, chunk: int) -> bytes:
    """A request framed with `Transfer-Encoding: chunked` and no length."""
    out = bytearray(b"POST " + path + b" HTTP/1.1\r\nhost: x\r\n"
                    b"transfer-encoding: chunked\r\n\r\n")
    for i in range(0, len(body), chunk):
        piece = body[i:i + chunk]
        out += b"%X\r\n" % len(piece) + piece + b"\r\n"
    out += b"0\r\n\r\n"
    return bytes(out)


@pytest.mark.parametrize("chunk", [7, 4096])
def test_chunked_request_body_arrives_whole(leerie, recording_upstream,
                                            monkeypatch, chunk):
    """Regression: body length came solely from `content-length`, so a chunked
    request read as length 0 and forwarded whatever landed in the first packet —
    a silently truncated request, which surfaces downstream as a model error
    rather than a proxy bug. Today's CLI always sends `content-length`, but that
    is an observation about one client version, not a guarantee.

    Two chunk sizes: 7 forces many chunks and a mid-chunk read boundary; 4096
    covers the single-chunk case.
    """
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)
    body = json.dumps({"filler": "x" * 20_000}).encode()

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            return await _raw(port, _chunked_wire(b"/v1/messages", body, chunk))
        finally:
            await p.stop()
    asyncio.run(go())
    assert seen, "the chunked request never reached upstream"
    assert seen[0]["body"] == body, (
        f"upstream saw {len(seen[0]['body'])} of {len(body)} bytes")


def test_chunked_body_is_not_forwarded_with_its_framing(leerie, recording_upstream,
                                                        monkeypatch):
    """The de-chunk is load-bearing, not decorative: urllib recomputes
    `content-length` for the upstream hop, so forwarding the raw framing would
    describe a body that is not what it says it is."""
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)
    body = b'{"a": 1}'

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _raw(port, _chunked_wire(b"/v1/messages", body, 3))
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert seen[0]["body"] == body
    assert b"\r\n" not in seen[0]["body"], "chunk framing leaked into the body"
    assert json.loads(seen[0]["body"]) == {"a": 1}
    # Never rewritten: this path is untested against the real CLI, so the
    # fail-open discipline applies to it too.
    assert p.rewritten == 0 and p.passed_through == 1


# --------------------------------------------------------------------------
# Logging: the proxy shares the orchestrator's log stream.
# --------------------------------------------------------------------------


class _ErrorUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        payload = json.dumps({"error": {
            "message": "tools.18.custom.input_schema: additionalProperties "
                       "must be false at #/properties/collisions"}}).encode()
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def error_upstream():
    srv = _Pool(("127.0.0.1", 0), _ErrorUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _run_against(leerie, port_url, monkeypatch, n: int, verbosity: str):
    """Drive `n` requests through a proxy and return the captured log lines."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", port_url)
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m: lines.append(m))

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5, verbosity=verbosity)
        port = await p.start()
        try:
            body = _wrap({"type": "object", "properties": {}})
            for _ in range(n):
                await _post(port, body)
        finally:
            await p.stop()
        return p
    return asyncio.run(go()), lines


@pytest.mark.parametrize("verbosity", ["quiet", "normal", "stream", "debug"])
def test_schema_errors_are_logged_at_every_verbosity(leerie, error_upstream,
                                                       monkeypatch, verbosity):
    """This proxy is the only thing in the path that rewrites a request, so a
    4xx here is most likely leerie's own edit being rejected — and the response
    names the offending schema path. Suppressing it at low verbosity would leave
    the operator watching workers retry, which is exactly the misattribution the
    flag's failure mode consists of."""
    p, lines = _run_against(leerie, error_upstream, monkeypatch, 1, verbosity)
    hits = [l for l in lines if "upstream 400" in l]
    assert len(hits) == 1, f"no upstream-error line at verbosity={verbosity}"
    assert "additionalProperties must be false" in hits[0], (
        "the response body carries the offending schema path — it must survive")
    assert p.schema_errors == 1


def test_upstream_error_echoes_are_budgeted(leerie, error_upstream, monkeypatch):
    """A rejected rewrite is systematic: every worker call fails the same way.
    The first few carry all the diagnostic value; echoing the rest would bury
    the run log the proxy shares with every other leerie line."""
    n = leerie._STRICT_PROXY_ERROR_LOG_MAX + 4
    p, lines = _run_against(leerie, error_upstream, monkeypatch, n, "stream")
    echoed = [l for l in lines if "upstream 400" in l]
    assert len(echoed) == leerie._STRICT_PROXY_ERROR_LOG_MAX
    assert any("counted, not echoed" in l for l in lines)
    # Counted in full even when not echoed — the summary must not under-report.
    # `schema_errors`, not a merged total: these are 400s on requests that were
    # never rewritten (the fixture's tool is unnamed), so the fallback does not
    # absorb them and they are genuinely ours to raise.
    assert p.schema_errors == n


def test_per_request_lines_are_debug_only(leerie, mock_upstream, monkeypatch):
    """One line per worker API call is far too much for normal output, and the
    end-of-run summary already covers the aggregate."""
    _, debug = _run_against(leerie, mock_upstream, monkeypatch, 2, "debug")
    _, stream = _run_against(leerie, mock_upstream, monkeypatch, 2, "stream")
    assert len([l for l in debug if "-> 200" in l]) == 2
    assert [l for l in stream if "-> 200" in l] == []
    assert any("strict:true" in l for l in debug), (
        "the debug line must say what the transform did, not just that it ran")


def test_the_transform_description_is_not_discarded(leerie):
    """`_strictify_request` builds a description of exactly what it changed and
    the first implementation threw it away into `_desc`, contradicting its own
    docstring. It must reach the log."""
    import inspect
    src = inspect.getsource(leerie._StrictOutputProxy._handle)
    # Matched loosely on purpose: the unpacking form has already changed once
    # (the fallback path needed the pre-transform body kept alongside), and
    # what must hold is that `desc` is bound from the swap and reaches the log
    # — not the exact spelling of one assignment.
    assert "desc) = body, swap" in src or "body, desc = swap" in src
    assert "_log_exchange" in src


def test_claude_md_documents_the_flag_with_its_mirrors(leerie):
    """CLAUDE.md's Quick start is where the user-facing flag surface is
    enumerated, and it is loaded into context every session. Both sibling
    dangerous flags are documented there."""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text()
    assert "--dangerously-force-strict-output" in text
    assert leerie.DANGEROUS_FORCE_STRICT_OUTPUT_ENV in text
    assert "dangerously_force_strict_output = true" in text
    assert "DANGEROUS" in text.split("--dangerously-force-strict-output")[0][-1200:], (
        "the Quick start entry must carry the risk, not just the flag name")


# --------------------------------------------------------------------------
# Bedrock: the second collision, same contract as ANTHROPIC_BASE_URL.
# --------------------------------------------------------------------------


def _main_guard_src(leerie) -> str:
    import inspect
    return inspect.getsource(leerie.main)


def test_bedrock_collision_is_fatal(leerie):
    """`ANTHROPIC_BASE_URL` is the *first-party* endpoint override and the
    proxy's upstream is hardcoded to api.anthropic.com. Under Bedrock the flag
    is either inert — the CLI never contacts the proxy and the operator is
    silently handed the post-hoc validation they asked to replace — or it
    misroutes every worker call. Neither is distinguishable from a healthy run
    in the log, so it must die() rather than warn."""
    src = _main_guard_src(leerie)
    assert "AWS_BEARER_TOKEN_BEDROCK" in src
    assert "CLAUDE_CODE_USE_BEDROCK" in src
    # It must be a die(), not a log/warn: silently proceeding is the failure.
    i = src.index("AWS_BEARER_TOKEN_BEDROCK")
    assert "die(" in src[i:i + 1600], "the Bedrock collision does not die()"


def test_bedrock_guard_accepts_the_launchers_truthy_spellings(leerie):
    """`detect_bedrock_mode()` in the launcher accepts 1/true/yes/on. A guard
    that only matched `"1"` would miss a settings.json-driven Bedrock run."""
    src = _main_guard_src(leerie)
    i = src.index("CLAUDE_CODE_USE_BEDROCK")
    window = src[i:i + 400]
    for spelling in ('"1"', '"true"', '"yes"', '"on"'):
        assert spelling in window, f"truthy spelling {spelling} not accepted"
    assert ".lower()" in window, "the match is case-sensitive"


def test_bedrock_guard_is_gated_on_the_flag(leerie):
    """Bedrock alone is a fully supported configuration (CLAUDE.md documents
    both auth paths). Only the *combination* is refused."""
    src = _main_guard_src(leerie)
    i = src.index("bedrock_via")
    assert 'caps["force_strict_output"] and bedrock_via' in src[i:], (
        "the guard must fire only when the flag is also on")


def test_both_collision_guards_name_the_same_contract(leerie):
    """Both refusals exist for one reason — the operator asked for a guarantee
    and must never be silently denied it. The wording carries that."""
    src = _main_guard_src(leerie)
    assert src.count("will not silently run without the constrained") == 2


# --------------------------------------------------------------------------
# Transport, continued: counters and header casing.
# --------------------------------------------------------------------------


def test_chunked_non_post_does_not_count_as_passed_through(leerie,
                                                           recording_upstream,
                                                           monkeypatch):
    """`passed_through` drives the operator warning that constrained decoding
    was lost. A GET was never a candidate for it, so counting one raises a false
    alarm about the flag's single most important failure mode."""
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _raw(port, _chunked_wire(b"/v1/models", b"", 8).replace(
                b"POST ", b"GET ", 1))
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert seen and seen[0]["method"] == "GET"
    assert p.passed_through == 0, "a chunked GET was counted as passed-through"
    assert p.rewritten == 0


def test_chunked_post_still_counts_as_passed_through(leerie, recording_upstream,
                                                     monkeypatch):
    """Anti-vacuity control: the POST case must still count, or the assertion
    above would pass against a counter that never increments at all."""
    url, _ = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _raw(port, _chunked_wire(b"/v1/messages", b'{"a":1}', 3))
        finally:
            await p.stop()
        return p
    assert asyncio.run(go()).passed_through == 1


@pytest.mark.parametrize("casing", [
    b"Transfer-Encoding", b"transfer-encoding", b"TRANSFER-ENCODING",
    b"Transfer-encoding",
])
def test_transfer_encoding_is_stripped_case_insensitively(leerie,
                                                          recording_upstream,
                                                          monkeypatch, casing):
    """Header names are case-insensitive on the wire. A casing that survived
    would reach upstream beside urllib's computed content-length, and a request
    carrying both framing headers is a smuggling shape servers reject.

    The earlier fix popped two literal spellings, so `TRANSFER-ENCODING` got
    through; the exclusion list two lines above was already case-insensitive.
    """
    url, seen = recording_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)
    body = b'{"a": 1}'
    wire = _chunked_wire(b"/v1/messages", body, 3).replace(
        b"transfer-encoding: chunked", casing + b": chunked", 1)
    assert casing + b": chunked" in wire, "the casing rewrite did not apply"

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _raw(port, wire)
        finally:
            await p.stop()
    asyncio.run(go())
    assert seen, f"the {casing.decode()} request never reached upstream"
    assert seen[0]["body"] == body, "body truncated or framing leaked through"
    # The load-bearing assertion. Checking only the body is vacuous: http.server
    # honours content-length regardless, so the body arrives intact even when a
    # stray transfer-encoding rides along. Verified by reintroducing the
    # literal-spelling pops — the body assertion alone passes for all casings.
    assert "transfer-encoding" not in seen[0]["headers"], (
        f"{casing.decode()} survived to upstream beside content-length "
        f"({seen[0]['headers'].get('transfer-encoding')!r}) — a request with "
        "both framing headers is an RFC 7230 smuggling shape")


def test_no_transfer_encoding_header_survives_to_upstream(leerie):
    """Structural pin for the mechanism, not just the outcome: the exclusion
    happens in the case-insensitive list rather than via literal pops."""
    import inspect
    src = inspect.getsource(leerie._StrictOutputProxy._handle)
    assert '"transfer-encoding"' in src
    assert 'headers.pop("Transfer-Encoding"' not in src, (
        "literal-spelling pops are case-sensitive; use the lower()ed list")


@pytest.mark.parametrize("env, expected", [
    ({}, ""),
    ({"AWS_BEARER_TOKEN_BEDROCK": "tok"}, "AWS_BEARER_TOKEN_BEDROCK"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "CLAUDE_CODE_USE_BEDROCK"),
    ({"CLAUDE_CODE_USE_BEDROCK": "TRUE"}, "CLAUDE_CODE_USE_BEDROCK"),
    ({"CLAUDE_CODE_USE_BEDROCK": " yes "}, "CLAUDE_CODE_USE_BEDROCK"),
    ({"CLAUDE_CODE_USE_BEDROCK": "on"}, "CLAUDE_CODE_USE_BEDROCK"),
    ({"CLAUDE_CODE_USE_BEDROCK": "0"}, ""),
    ({"CLAUDE_CODE_USE_BEDROCK": "false"}, ""),
    ({"CLAUDE_CODE_USE_BEDROCK": ""}, ""),
    ({"AWS_BEARER_TOKEN_BEDROCK": "", "CLAUDE_CODE_USE_BEDROCK": "1"},
     "CLAUDE_CODE_USE_BEDROCK"),
])
def test_bedrock_detection_behaviour(leerie, env, expected):
    """Behavioural coverage of the classifier itself, not just its source text.

    `main()` cannot be driven here (it parses argv, resolves a repo, and dies),
    so the detection block is extracted verbatim and executed against a
    controlled environment — the same technique
    `tests/test_launcher_env_forwarding.py` uses for launcher fragments.

    An empty `AWS_BEARER_TOKEN_BEDROCK` must not shadow a truthy
    `CLAUDE_CODE_USE_BEDROCK`: the launcher exports the former unconditionally
    on some paths, and a guard that reported the wrong variable would send the
    operator to unset something that was never set.
    """
    import inspect
    src = inspect.getsource(leerie.main)
    start = src.index("    bedrock_via = (")
    end = src.index('    if caps["force_strict_output"] and bedrock_via:')
    block = src[start:end].strip()
    assert "bedrock_via" in block and len(block) < 500, "extraction drifted"
    ns = {"os": type("_O", (), {"environ": dict(env)})}
    exec(block, ns)  # noqa: S102 - executing our own extracted source
    assert ns["bedrock_via"] == expected


# --------------------------------------------------------------------------
# Response-side fail-open: some schemas cannot be compiled at all.
#
# Measured live against the real API (2026-08-04), 2 of leerie's 23 are
# rejected outright: `planner` ("Schema is too complex.") and `reconciler`
# ("The compiled grammar is too large"). Both carry 12 optional properties
# inside array items — strict mode must accept every subset in any order, so
# grammar size multiplies per element. `conformer` has 41 properties but only
# 2 optionals and compiles fine, which is why raw size is the wrong intuition.
#
# Without a fallback the flag 400s every call by those workers, and a run that
# cannot plan cannot do anything.
# --------------------------------------------------------------------------


class _RejectsHardened(http.server.BaseHTTPRequestHandler):
    """Rejects a request carrying `strict`, accepts the same one without it."""

    protocol_version = "HTTP/1.1"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        hardened = b'"strict"' in body
        type(self).seen.append({"hardened": hardened, "body": body})
        if hardened:
            payload = json.dumps({"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "Schema is too complex."}}).encode()
            self.send_response(400)
        else:
            payload = b'{"ok": true}'
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def rejecting_upstream():
    _RejectsHardened.seen = []
    srv = _Pool(("127.0.0.1", 0), _RejectsHardened)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", _RejectsHardened.seen
    srv.shutdown()


def test_uncompilable_schema_falls_back_instead_of_failing(leerie,
                                                           rejecting_upstream,
                                                           monkeypatch):
    """The worker must get a 200, not the 400 the hardened request earned."""
    url, seen = rejecting_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            data = await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
        return data, p
    data, p = asyncio.run(go())
    assert b'"ok": true' in data, "the caller saw the rejection instead of a retry"
    assert [s["hardened"] for s in seen] == [True, False], (
        "expected exactly one hardened attempt then one clean retry")
    assert p.fell_back == 1
    # The guarantee was lost for this schema, so it must NOT be counted as
    # rewritten — that number is what tells an operator what they actually got.
    assert p.rewritten == 0 and p.passed_through == 1


def test_fallback_is_remembered_so_the_doomed_attempt_is_paid_once(
        leerie, rejecting_upstream, monkeypatch):
    """A rejected schema is systematic — every call by that worker repeats it.
    Re-hardening each time would double every request for the whole run."""
    url, seen = rejecting_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)
    body = _wrap({"type": "object", "properties": {}})

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            for _ in range(4):
                await _post(port, body)
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert sum(1 for s in seen if s["hardened"]) == 1, (
        "the un-compilable schema was hardened more than once")
    assert len(seen) == 5, f"expected 1 doomed + 4 clean, got {len(seen)}"
    assert p.fell_back == 1


def test_a_different_schema_is_still_hardened_after_a_fallback(
        leerie, mock_upstream, monkeypatch):
    """The memo is keyed per schema. One worker's un-compilable schema must not
    disable constrained decoding for every other worker in the run."""
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", mock_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            p._unhardenable.add(leerie._structured_output_fingerprint(
                _wrap({"type": "object", "properties": {"a": {"type": "string"}}})))
            skipped = await _post(port, _wrap(
                {"type": "object", "properties": {"a": {"type": "string"}}}))
            other = await _post(port, _wrap(
                {"type": "object", "properties": {"b": {"type": "string"}}}))
        finally:
            await p.stop()
        return skipped, other, p
    skipped, other, p = asyncio.run(go())
    assert b'"saw_strict": false' in skipped, "memoized schema was hardened anyway"
    assert b'"saw_strict": true' in other, "an unrelated schema lost its hardening"
    assert p.rewritten == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_only_400_triggers_the_fallback(leerie, monkeypatch, status):
    """401/403/429/500 are not schema problems — the original would fail the
    same way, so retrying just doubles the damage."""
    calls = []

    def fake_upstream(self, method, path, body, headers):
        calls.append(body)
        return status, [], b'{"error": {"message": "nope"}}'

    monkeypatch.setattr(leerie._StrictOutputProxy, "_upstream", fake_upstream)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert len(calls) == 1, f"status {status} should not retry"
    assert p.fell_back == 0


def test_fallback_is_logged_at_every_verbosity(leerie, rejecting_upstream,
                                               monkeypatch):
    """Losing the guarantee silently is the failure this whole flag is built to
    avoid. The operator has to be told which worker dropped to post-hoc
    validation, and the API's own reason is the diagnostic."""
    url, _ = rejecting_upstream
    p, lines = _run_against(leerie, url, monkeypatch, 1, "quiet")
    hits = [l for l in lines if "retrying WITHOUT" in l]
    assert len(hits) == 1, "the fallback was not surfaced"
    assert "Schema is too complex" in hits[0], (
        "the API's own reason must survive into the log")
    assert p.fell_back == 1


def test_api_error_head_reads_the_message_and_survives_junk(leerie):
    """Operates on the API's machine-generated envelope, so it must not explode
    on a body that is not the shape it expects."""
    good = json.dumps({"error": {"message": "Schema is  too\n complex."}}).encode()
    assert leerie._api_error_head(good) == "Schema is too complex."
    assert leerie._api_error_head(b"not json at all")  # no exception, some text
    assert leerie._api_error_head(b"") == ""
    assert len(leerie._api_error_head(b'{"error":{"message":"' + b"x" * 500)) <= 160


def test_fingerprint_is_stable_and_discriminating(leerie):
    """Same schema -> same id regardless of key order; different schema -> not."""
    a = _wrap({"type": "object", "properties": {"a": {"type": "string"}}})
    b = _wrap({"properties": {"a": {"type": "string"}}, "type": "object"})
    c = _wrap({"type": "object", "properties": {"z": {"type": "string"}}})
    fa, fb, fc = (leerie._structured_output_fingerprint(x) for x in (a, b, c))
    assert fa == fb, "key order changed the fingerprint"
    assert fa != fc, "different schemas collided"
    assert leerie._structured_output_fingerprint(b"garbage") is None
    assert leerie._structured_output_fingerprint(
        _wrap({"type": "object"}, name="Other")) is None


# --------------------------------------------------------------------------
# All-required: collapsing optional-subset combinatorics.
#
# Strict mode admits every SUBSET of a node's optional properties in any order,
# so a node with k optionals costs ~2^k grammar paths, multiplied per array
# element. Measured live (2026-08-04): `planner` — 11 optionals in one
# `subtasks[]` item — was rejected with "Schema is too complex for
# compilation"; requiring them takes it to 200.
#
# A controlled type experiment established what is expensive per path: 20
# free-form string properties are rejected, while 20 enums, 20 booleans, 20
# integers and 20 arrays all compile. Strings are the cost; optionals multiply
# it.
# --------------------------------------------------------------------------


def test_no_optional_properties_survive_the_transform(leerie):
    """The property that makes `planner` compile: zero optionals on the wire."""
    import copy
    leftover = {}
    for name, schema in leerie.SCHEMAS.items():
        s = copy.deepcopy(schema)
        leerie._strictify_schema(s)

        def walk(node, path="$"):
            if isinstance(node, dict):
                props = node.get("properties")
                if isinstance(props, dict):
                    req = set(node.get("required") or [])
                    opt = [k for k in props if k not in req]
                    if opt:
                        leftover.setdefault(name, []).append(f"{path}: {opt}")
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
        walk(s)
    assert not leftover, f"optional properties left on the wire: {leftover}"


def test_original_schemas_are_not_mutated(leerie):
    """`_strictify_schema` edits in place, so the transform must run on a COPY.

    If it ever touched `SCHEMAS` itself, the CLI's own validation copy would
    gain the same `required` list — and then a worker omitting an optional
    field really would be rejected, which is exactly the PR #153 regression
    this design claims not to repeat.
    """
    import copy
    before = copy.deepcopy(leerie.SCHEMAS)
    for name in leerie.SCHEMAS:
        leerie._strictify_request(_wrap(copy.deepcopy(leerie.SCHEMAS[name])))
    assert leerie.SCHEMAS == before, "the live SCHEMAS dict was mutated"


def test_forcing_a_field_never_makes_a_trivial_value_illegal(leerie):
    """Every forced field must accept some value the ORIGINAL schema allows.

    An array with `minItems: 0` accepts `[]`; a string with `minLength: 2`
    does not accept `""`. If a forced field falls in the second class, the
    grammar would let the model emit a value the CLI then rejects — trading a
    compile error for a validation error.
    """
    offenders = []
    for name, schema in leerie.SCHEMAS.items():
        def walk(node, path="$"):
            if isinstance(node, dict):
                props = node.get("properties")
                if isinstance(props, dict):
                    req = set(node.get("required") or [])
                    for k, v in props.items():
                        if k in req or not isinstance(v, dict):
                            continue
                        t = v.get("type")
                        if t == "string" and (v.get("minLength") or 0) > 0:
                            offenders.append(f"{name}{path}.{k} minLength")
                        if t == "array" and (v.get("minItems") or 0) > 0:
                            offenders.append(f"{name}{path}.{k} minItems")
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
        walk(schema)
    assert not offenders, (
        "forcing these would let the grammar produce a value the CLI rejects: "
        f"{offenders}")


def test_all_required_output_still_validates_against_the_original(leerie):
    """The seam the whole design rests on: the CLI validates against leerie's
    ORIGINAL schema, so an output carrying every field must still pass.

    `synth` honours `minLength` / `minItems` rather than emitting degenerate
    values. An earlier version emitted `""` everywhere and exempted the
    resulting failures by matching the phrase "too short" in the exception —
    which broke on a newer jsonschema that words the same error
    "should be non-empty". Matching a library's error prose is brittle by
    construction; building an instance that genuinely satisfies the schema
    tests the real claim and depends on no wording at all.
    """
    jsonschema = pytest.importorskip("jsonschema")

    def synth(node):
        t = node.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            t = non_null[0] if non_null else "null"
        if t == "object" or (t is None and "properties" in node):
            return {k: synth(v) for k, v in (node.get("properties") or {}).items()}
        if t == "array":
            n = node.get("minItems") or 0
            item = node.get("items")
            return [synth(item) for _ in range(n)] if isinstance(item, dict) else []
        if t == "string":
            if node.get("enum"):
                return node["enum"][0]
            return "x" * max(int(node.get("minLength") or 0), 1)
        if t in ("number", "integer"):
            return node.get("minimum", 0)
        if t == "boolean":
            return False
        return None

    bad = []
    for name, schema in leerie.SCHEMAS.items():
        try:
            jsonschema.validate(synth(schema), schema)
        except jsonschema.ValidationError as e:
            bad.append((name, str(e).splitlines()[0][:90]))
    assert not bad, f"all-fields-present output rejected by the original: {bad}"


# --------------------------------------------------------------------------
# Reconciler: flattened wire shape + the fan-out adapter.
#
# The nine-array shape was refused outright ("The compiled grammar is too
# large", 0.6 s) because it nested an array-of-objects inside an
# array-of-objects — the only three-deep path in any leerie schema — and
# repeated the isomorphic {sid, tag, reason} object four times. Seven in-place
# reductions were each measured and each still refused; only the restructure
# worked (51.8 s, and a full end-to-end run with `structured_output` present).
# --------------------------------------------------------------------------


def test_reconciler_wire_shape_is_flat(leerie):
    """No array-of-objects nested inside another array-of-objects, which is
    what put the old schema over the ceiling."""
    def depth_of_object_arrays(node, inside_items=False):
        worst = 0
        if isinstance(node, dict):
            is_obj_array = (node.get("type") == "array"
                            and isinstance(node.get("items"), dict)
                            and "properties" in node["items"])
            if is_obj_array and inside_items:
                worst = max(worst, 2)
            for k, v in node.items():
                worst = max(worst, depth_of_object_arrays(
                    v, inside_items or is_obj_array))
        elif isinstance(node, list):
            for v in node:
                worst = max(worst, depth_of_object_arrays(v, inside_items))
        return worst
    assert depth_of_object_arrays(leerie.SCHEMAS["reconciler"]) == 0, (
        "an array-of-objects is nested inside another array-of-objects again")


def test_reconciler_has_no_repeated_object_shape(leerie):
    """`add_provide`/`drop_require`/`unresolvable`/`conditional_drop` collapsed
    into one enum-discriminated array. Enums are cheap; four identical object
    shapes were not."""
    props = leerie.SCHEMAS["reconciler"]["properties"]
    assert "tag_ops" in props
    ops = props["tag_ops"]["items"]["properties"]["op"]["enum"]
    assert set(ops) == {"add_provide", "drop_require", "unresolvable",
                        "conditional_drop"}
    for gone in ("added_provides", "dropped_requires", "conditional_drops",
                 "unresolvable"):
        assert gone not in props, f"{gone} should be a tag_ops op now"


def test_expand_fans_tag_ops_into_the_internal_shape(leerie):
    """Everything downstream still consumes the nine arrays."""
    out = leerie._expand_reconciler_output({
        "added_subtasks": [], "added_requires": [], "renames": [],
        "dependency_edges": [], "merged_subtasks": [], "confidence": {},
        "tag_ops": [
            {"op": "add_provide", "sid": "a", "tag": "t1", "reason": "r"},
            {"op": "drop_require", "sid": "b", "tag": "t2", "reason": "r"},
            {"op": "unresolvable", "sid": "c", "tag": "t3", "reason": "r"},
            {"op": "conditional_drop", "sid": "d", "tag": "", "reason": "r"},
        ],
    })
    assert [x["sid"] for x in out["added_provides"]] == ["a"]
    assert [x["sid"] for x in out["dropped_requires"]] == ["b"]
    assert [x["sid"] for x in out["unresolvable"]] == ["c"]
    assert [x["sid"] for x in out["conditional_drops"]] == ["d"]
    # conditional_drops has no `tag` in the internal shape — its consumer
    # never reads one, and inventing an empty one would be a silent lie.
    assert "tag" not in out["conditional_drops"][0]


def test_expand_is_case_insensitive_on_enums(leerie):
    """Constrained decoding does not guarantee enum capitalisation, and it
    drifts silently — no error, no special stop reason."""
    out = leerie._expand_reconciler_output({
        "added_subtasks": [{"id": "s1", "title": "t",
                            "success_criteria_seed": "c"}],
        "added_requires": [{"sid": "s1", "tag": "x", "extent": "IN_PLAN"}],
        "tag_ops": [{"op": "ADD_PROVIDE", "sid": "a", "tag": "t",
                     "reason": "r"}],
        "renames": [], "dependency_edges": [], "merged_subtasks": [],
        "confidence": {},
    })
    assert len(out["added_provides"]) == 1, "uppercase op was dropped"
    assert out["added_subtasks"][0]["requires"][0]["extent"] == "in_plan"


def test_expand_drops_an_unknown_op_rather_than_guessing(leerie):
    """Misfiling a resolution action would mutate the plan invisibly."""
    out = leerie._expand_reconciler_output({
        "added_subtasks": [], "added_requires": [],
        "tag_ops": [{"op": "delete_everything", "sid": "a", "reason": "r"}],
        "renames": [], "dependency_edges": [], "merged_subtasks": [],
        "confidence": {},
    })
    assert all(not out[k] for k in ("added_provides", "dropped_requires",
                                    "unresolvable", "conditional_drops"))


def test_expand_rebinds_requires_and_reports_danglers(leerie):
    """The subtask<->requires binding is no longer structural, so it is
    re-bound by sid here and a miss is surfaced rather than silently lost."""
    out = leerie._expand_reconciler_output({
        "added_subtasks": [{"id": "feat-008", "title": "t",
                            "success_criteria_seed": "c"}],
        "added_requires": [
            {"sid": "feat-008", "tag": "cap-y", "extent": "in_plan"},
            {"sid": "ghost", "tag": "cap-z", "extent": "in_plan"},
        ],
        "tag_ops": [], "renames": [], "dependency_edges": [],
        "merged_subtasks": [], "confidence": {},
    })
    assert out["added_subtasks"][0]["requires"] == [
        {"tag": "cap-y", "extent": "in_plan"}]
    assert len(out["_dangling_requires"]) == 1


def test_expand_does_not_mutate_the_worker_output(leerie):
    """The raw envelope is persisted as telemetry and must stay as-emitted."""
    raw = {"added_subtasks": [{"id": "s", "title": "t",
                               "success_criteria_seed": "c"}],
           "added_requires": [{"sid": "s", "tag": "x", "extent": "in_plan"}],
           "tag_ops": [], "renames": [], "dependency_edges": [],
           "merged_subtasks": [], "confidence": {}}
    import copy
    before = copy.deepcopy(raw)
    leerie._expand_reconciler_output(raw)
    assert raw == before


def test_spawn_reconciler_routes_through_the_adapter(leerie):
    """Source-coupling: all three call sites (first attempt, size retry, cycle
    retry) go through `_spawn_reconciler`, so the adapter must sit there."""
    import inspect
    src = inspect.getsource(leerie.phase_reconcile)
    assert "_expand_reconciler_output(raw)" in src
    assert "_dangling_requires" in src


# --------------------------------------------------------------------------
# Retry prompts must speak the vocabulary the schema accepts.
#
# leerie builds its reconciler retry prompts in Python. When the wire shape was
# restructured (four {sid, tag, reason} arrays -> one op-discriminated
# `tag_ops`; `requires` lifted into `added_requires`), `prompts/reconciler.md`
# was rewritten but these Python-side prompts were not — so a retry told the
# model to emit arrays its own schema no longer has. Under
# `--dangerously-force-strict-output` the grammar makes those keys
# unrepresentable, so the model *cannot* comply; without the flag it produces
# output that fails validation. Either way the retry is wasted and the run then
# dies claiming the model "defied a structural constraint" when leerie asked
# for the wrong thing.
#
# No existing test caught it: every reconciler test stubs the worker's return
# value, so none reads these strings.
# --------------------------------------------------------------------------


def _reconciler_vocabulary(leerie) -> set[str]:
    """Every field/op name the reconciler schema actually accepts."""
    schema = leerie.SCHEMAS["reconciler"]
    vocab = set(schema["properties"])
    vocab |= set(schema["properties"]["tag_ops"]["items"]["properties"]["op"]["enum"])
    return vocab


def test_retry_prompts_only_name_ops_the_schema_accepts(leerie):
    """Checks the RENDERED prompt text, not the source.

    A source scan cannot tell a model-facing string from leerie's own internal
    op tag (`rec["op"] == "dropped_requires"` is an internal value the
    recommendation dict legitimately still uses) or from a docstring. Only what
    is actually emitted matters, so the builders are invoked and their output
    inspected.

    The accepted vocabulary is derived from `SCHEMAS["reconciler"]` rather than
    hand-listed, so the next rename is caught too.
    """
    vocab = _reconciler_vocabulary(leerie)
    retired = {"added_provides", "dropped_requires", "conditional_drops"}
    assert not (retired & vocab), "fixture is stale — these are live again"

    rendered: dict[str, str] = {}

    rec_drop = {"op": "dropped_requires", "sid": "config-001",
                "tag": "some-cap", "reason": "over-specified"}
    rendered["_format_recommendation"] = leerie._format_recommendation(rec_drop)

    edges = [{"from": "feat-001", "to": "config-001",
              "mutation": "rename: 'a' -> 'b'", "source": "requires:b"}]
    attempt1 = {"renames": [{"sid": "config-001", "from": "a", "to": "b"}]}
    rendered["_format_must_include"] = "\n".join(
        leerie._format_must_include(["feat-001", "config-001"], edges, attempt1))

    rendered["_build_unresolved_retry_prompt"] = (
        leerie._build_unresolved_retry_prompt(
            [{"sid": "config-001", "tag": "some-cap", "domain": "config"}],
            {"other-cap": ["feat-001"]},
            {("config-001", "some-cap"): None},
            attempt1,
            "orig",
        ))

    offenders = {}
    for name, text in rendered.items():
        hits = sorted(n for n in retired if n in text)
        if hits:
            offenders[name] = hits
    assert not offenders, (
        "retry prompts instruct the model to emit arrays the schema no longer "
        f"has: {offenders}")


def test_recommendation_marker_survives_the_vocabulary_change(leerie):
    """`_matches_recommendation` compares against `_format_must_include`'s
    rendering. If the two drift, no option is ever marked recommended — and
    nothing fails, so the drift is silent."""
    edges = [{"from": "feat-001", "to": "config-001",
              "mutation": "rename: 'a' -> 'b'", "source": "requires:b"}]
    attempt1 = {"renames": [{"sid": "config-001", "from": "a", "to": "b"}]}
    options = leerie._format_must_include(
        ["feat-001", "config-001"], edges, attempt1)
    rec = {"op": "dropped_requires", "sid": "config-001", "tag": "a",
           "reason": "r"}
    assert any(leerie._matches_recommendation(o, rec) for o in options), (
        "no rendered option matched the recommendation — _format_must_include "
        "and _matches_recommendation have drifted apart")


# --------------------------------------------------------------------------
# Self-reporting: say what happened, not the worst thing it could mean.
#
# The first real run rewrote 395 requests with zero 400s and zero fallbacks,
# and reported itself as: "173 passed through — ... the injected tool may have
# changed upstream, 14 upstream error(s) — the rewrite itself may be being
# rejected; re-run without the flag to confirm."
#
# Every clause was false. One root cause, three symptoms: counters merged
# distinct categories and the summary then described the merged total using
# the alarming interpretation. A summary that cries wolf on a healthy run is
# worse than no summary — it sends the operator chasing a problem that is not
# there, and it devalues the warning for when it is real.
# --------------------------------------------------------------------------


def test_a_request_with_no_tool_is_not_alarming(leerie):
    """Measured against a real 4-turn worker: one of its four /v1/messages
    POSTs arrives with NO tools array at all, because the CLI injects
    StructuredOutput only on turns that want structured output. 173 of these
    in the real run. Ordinary traffic."""
    body = json.dumps({"model": "m", "messages": [],
                       "tools": [{"name": "Bash", "input_schema": {}}]}).encode()
    assert leerie._unexpected_structured_output_shape(body) is False
    assert leerie._unexpected_structured_output_shape(b'{"model":"m"}') is False
    assert leerie._unexpected_structured_output_shape(b"not json") is False


@pytest.mark.parametrize("tools, why", [
    ([{"name": "StructuredOutput", "input_schema": {}},
      {"name": "StructuredOutput", "input_schema": {}}], "duplicated"),
    ([{"name": "StructuredOutput"}], "no input_schema"),
    ([{"name": "StructuredOutput", "input_schema": "not-a-dict"}],
     "input_schema is not an object"),
])
def test_a_present_but_unusable_tool_is_alarming(leerie, tools, why):
    """THIS is what the warning was written for — the private, unversioned
    interface changing shape under us."""
    body = json.dumps({"model": "m", "messages": [], "tools": tools}).encode()
    assert leerie._unexpected_structured_output_shape(body) is True, why


def test_transient_errors_do_not_starve_the_schema_echo_budget(leerie,
                                                               monkeypatch):
    """The regression the real run demonstrated: three 529s consumed the whole
    allowance, so a later genuine 400 — carrying the API's own message naming
    the offending schema path — would have been counted, not shown."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m: lines.append(m))
    p = leerie._StrictOutputProxy(max_parallel=2)

    over = json.dumps({"error": {"message": "Overloaded"}}).encode()
    for _ in range(leerie._STRICT_PROXY_ERROR_LOG_MAX + 2):
        p.transient_errors += 1
        p._log_exchange("POST", "/v1/messages", 529, "", over)

    lines.clear()
    body = json.dumps({"error": {"message":
                                 "tools.0.custom: additionalProperties "
                                 "must be false at #/properties/x"}}).encode()
    p.schema_errors += 1
    p._log_exchange("POST", "/v1/messages", 400, "strict:true", body)

    hits = [l for l in lines if "upstream 400" in l]
    assert len(hits) == 1, "the 400 was swallowed by the transient budget"
    assert "additionalProperties must be false at #/properties/x" in hits[0], (
        "the API's own diagnostic must survive — it names the offending path")


def test_a_transient_error_is_not_described_as_a_rejection(leerie, monkeypatch):
    """A 529 has nothing to do with the rewrite; saying it might be sends the
    operator to re-run without the flag to 'confirm' an unrelated outage."""
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m: lines.append(m))
    p = leerie._StrictOutputProxy(max_parallel=2)
    p.transient_errors += 1
    p._log_exchange("POST", "/v1/messages", 529, "",
                    json.dumps({"error": {"message": "Overloaded"}}).encode())
    assert any("transient (not a schema rejection)" in l for l in lines)
    assert not any("rejected" in l and "not a schema rejection" not in l
                   for l in lines)


def test_the_healthy_run_summary_makes_no_accusation(leerie):
    """Replays the real run's exact shape — 395 rewrites, 173 tool-free
    requests, 14 transient errors, 0 schema errors, 0 fallbacks — and asserts
    the summary reads as the clean run it was."""
    import inspect
    src = inspect.getsource(leerie._orchestrate)
    start = src.index("_p = _STRICT_PROXY")
    end = src.index('log("; ".join(parts))') + len('log("; ".join(parts))')
    block = "\n".join(l[12:] if l.startswith(" " * 12) else l
                      for l in src[start:end].splitlines())

    class Fake:
        rewritten, passed_through = 395, 173
        unexpected_tool_shape = fell_back = schema_errors = 0
        transient_errors = 14
    out: list[str] = []
    exec(block, {"_STRICT_PROXY": Fake, "log": out.append})
    summary = out[0]

    assert "395 request(s) rewritten" in summary
    assert "may have changed upstream" not in summary, (
        "accused the upstream interface of changing on a healthy run")
    assert "may be being rejected" not in summary, (
        "accused the rewrite of being rejected with zero schema errors")
    assert "re-run without the flag" not in summary, (
        "sent the operator to re-run to confirm a problem that did not exist")
    assert "transient" in summary and "unrelated to the rewrite" in summary


def test_the_unhealthy_run_summary_still_accuses(leerie):
    """Anti-vacuity: the warnings must still fire when they are true, or the
    fix is just deletion."""
    import inspect
    src = inspect.getsource(leerie._orchestrate)
    start = src.index("_p = _STRICT_PROXY")
    end = src.index('log("; ".join(parts))') + len('log("; ".join(parts))')
    block = "\n".join(l[12:] if l.startswith(" " * 12) else l
                      for l in src[start:end].splitlines())

    class Fake:
        rewritten, passed_through = 10, 3
        unexpected_tool_shape, fell_back = 4, 1
        schema_errors, transient_errors = 2, 0
    out: list[str] = []
    exec(block, {"_STRICT_PROXY": Fake, "log": out.append})
    summary = out[0]

    assert "may have changed upstream" in summary
    # Deliberately definite, not hedged: with schema_errors > 0 the rewrite IS
    # being rejected. The hedge belongs on the upstream-shape claim (inferred
    # from a private interface), not on a count of observed 400s.
    assert "the rewrite itself is being rejected" in summary
    assert "re-run without the flag" in summary
    assert "fell back to post-hoc validation" in summary


def test_an_absorbed_fallback_is_not_also_an_unhandled_rejection(leerie,
                                                                 rejecting_upstream,
                                                                 monkeypatch):
    """A 400 the fallback resolves is reported ONCE, as `fell_back`.

    Counting it again as a schema rejection produces:

        1 schema(s) fell back to post-hoc validation …;
        1 schema rejection(s) — the rewrite itself is being rejected;
        re-run without the flag to confirm

    Both clauses describe the same event, and the second sends the operator to
    chase a problem the proxy already resolved. That is the exact failure this
    whole change set exists to remove, reintroduced one layer down — the
    classification therefore keys on the FINAL status, after any fallback.
    """
    url, _ = rejecting_upstream
    monkeypatch.setattr(leerie._StrictOutputProxy, "_UPSTREAM", url)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert p.fell_back == 1, "the fallback did not fire"
    assert p.schema_errors == 0, (
        "an absorbed 400 was also counted as an unhandled schema rejection")


def test_a_fallback_that_does_not_help_is_still_raised(leerie, monkeypatch):
    """Anti-vacuity for the test above: if the retried original ALSO fails, the
    fallback did not save the run and the operator must still hear about it."""
    calls = []

    def always_400(self, method, path, body, headers):
        calls.append(body)
        return 400, [], json.dumps(
            {"error": {"message": "Schema is too complex."}}).encode()

    monkeypatch.setattr(leerie._StrictOutputProxy, "_upstream", always_400)

    async def go():
        p = leerie._StrictOutputProxy(max_parallel=5)
        port = await p.start()
        try:
            await _post(port, _wrap({"type": "object", "properties": {}}))
        finally:
            await p.stop()
        return p
    p = asyncio.run(go())
    assert len(calls) == 2, "expected the hardened attempt plus the retry"
    assert p.fell_back == 1
    assert p.schema_errors == 1, (
        "a 400 that survived the fallback must still be raised")


def test_no_write_only_error_counter_survives(leerie):
    """`upstream_errors` was a merged total nothing read once the classes were
    split — state kept alive only by the tests asserting on it. The file's
    stated goal is that the control flow reads top-to-bottom in one sitting."""
    import inspect
    import pathlib
    # `inspect.getsource` on the class fails when leerie is imported from a
    # path the linecache cannot resolve; read the module file directly.
    src = pathlib.Path(inspect.getfile(leerie)).read_text()
    assert "upstream_errors" not in src, (
        "the merged total is back — nothing reads it once the classes split")
    p = leerie._StrictOutputProxy(max_parallel=1)
    assert not hasattr(p, "upstream_errors")


def _summary_for(leerie, **counters) -> str:
    """Render the end-of-run summary for a given set of counters."""
    import inspect
    src = inspect.getsource(leerie._orchestrate)
    start = src.index("_p = _STRICT_PROXY")
    end = src.index('log("; ".join(parts))') + len('log("; ".join(parts))')
    block = "\n".join(l[12:] if l.startswith(" " * 12) else l
                      for l in src[start:end].splitlines())
    base = {"rewritten": 0, "passed_through": 0, "unexpected_tool_shape": 0,
            "fell_back": 0, "schema_errors": 0, "transient_errors": 0}
    base.update(counters)
    out: list[str] = []
    exec(block, {"_STRICT_PROXY": type("F", (), base), "log": out.append,
                 "_STRICT_OUTPUT_TOOL_NAME": leerie._STRICT_OUTPUT_TOOL_NAME})
    return out[0]


def test_a_renamed_tool_is_indistinguishable_per_request(leerie):
    """Pinned deliberately, so the two mechanisms are not confused later.

    A renamed tool yields no matching hit — exactly like an ordinary turn that
    never asked for structured output. There is no per-request signal to
    separate them, which is why the rename check lives at run level instead.
    """
    renamed = json.dumps({"model": "m", "messages": [], "tools": [
        {"name": "StructuredOutputV2", "description": "d",
         "input_schema": {"type": "object", "properties": {}}}]}).encode()
    ordinary = json.dumps({"model": "m", "messages": [], "tools": [
        {"name": "Bash", "input_schema": {}}]}).encode()
    assert leerie._unexpected_structured_output_shape(renamed) is False
    assert leerie._unexpected_structured_output_shape(ordinary) is False


def test_a_run_that_rewrote_nothing_reports_a_probable_rename(leerie):
    """DESIGN §7 requires a renamed tool to be reported — "the dangerous
    failure here is a silent one". Suppressing the per-request false positives
    removed that signal, so it is restored at run level: leerie passes a schema
    to every worker, so if anything was proxied, something must have carried
    the tool. Nothing rewritten means the flag silently did nothing all run."""
    summary = _summary_for(leerie, rewritten=0, passed_through=50)
    assert "NOTHING was rewritten" in summary
    assert "renamed or removed" in summary
    assert leerie._STRICT_OUTPUT_TOOL_NAME in summary


def test_a_healthy_run_never_reports_a_rename(leerie):
    """Anti-vacuity: the whole point of the change was to stop warning on
    ordinary pass-throughs, so a run that rewrote anything must stay quiet no
    matter how many tool-free requests it saw."""
    summary = _summary_for(leerie, rewritten=395, passed_through=173,
                           transient_errors=14)
    assert "NOTHING was rewritten" not in summary
    assert "renamed or removed" not in summary


def test_no_proxied_requests_at_all_is_not_a_rename(leerie):
    """A run where the proxy saw nothing proves nothing about the tool — the
    warning must key on `passed_through`, not fire on an empty run."""
    assert "NOTHING was rewritten" not in _summary_for(leerie)
