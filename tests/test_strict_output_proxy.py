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
def test_upstream_errors_are_logged_at_every_verbosity(leerie, error_upstream,
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
    assert p.upstream_errors == 1


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
    assert p.upstream_errors == n


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
    assert "body, desc = swap" in src
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
