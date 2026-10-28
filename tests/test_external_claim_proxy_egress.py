"""The network probe takes the worker's way out, and an environment that
cannot check a claim does not burn the re-drive budget (DESIGN §13).

Round-3 review L4: behind proxy-only egress the control was a direct TCP
connect, which fails, so every network claim was `unverified` — re-driven
until the budget was spent, a full implementer session each time. The probe
now goes through `HTTPS_PROXY`/`HTTP_PROXY` with an HTTP CONNECT, driven
here against a real in-test proxy on loopback; and a claim unverified for an
environmental reason twice in a row settles `unconverged` with the typed
reason instead of being re-driven again.
"""
from __future__ import annotations

import asyncio
import base64
import socket
import threading

import pytest

from tests.test_external_claim_check import (  # noqa: F401  (fixtures)
    _check, _settle_with, ext)
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)

PROXY_ANSWERS = {
    b"api.anthropic.com:443": b"200 Connection established",
    b"reach.leerie-proxy.invalid:443": b"200 Connection established",
    b"billing.acme-test.invalid:8443": b"502 Bad Gateway",
    b"policy.leerie-proxy.invalid:443": b"403 Forbidden",
}


@pytest.fixture
def proxy(monkeypatch):
    """A CONNECT proxy on loopback answering from `PROXY_ANSWERS`, set as
    the only egress (`HTTPS_PROXY`, with credentials; `NO_PROXY` unset)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    seen: list[bytes] = []
    stop = threading.Event()

    def _serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                seen.append(data)
                target = data.split(b"\r\n", 1)[0].split()[1:2]
                ans = PROXY_ANSWERS.get(target[0] if target else b"",
                                        b"403 Forbidden")
                conn.sendall(b"HTTP/1.1 " + ans + b"\r\n\r\n")
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    for v in ("https_proxy", "http_proxy", "HTTP_PROXY", "no_proxy",
              "NO_PROXY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HTTPS_PROXY",
                       f"http://probe%40u:s3cr%3At@127.0.0.1:"
                       f"{srv.getsockname()[1]}")
    yield {"seen": seen, "port": srv.getsockname()[1]}
    stop.set()
    t.join(2)
    srv.close()


@pytest.fixture
def real_reach(leerie):
    """The real probe, captured before `ext` stubs it (fixture order)."""
    return leerie._network_reach


def test_probe_goes_through_the_proxy(leerie, proxy):
    run = lambda h, p: asyncio.run(leerie._network_reach(h, p))  # noqa: E731
    assert run("reach.leerie-proxy.invalid", 443) == "reachable"
    assert run("billing.acme-test.invalid", 8443) == "unreachable"
    assert run("policy.leerie-proxy.invalid", 443) == "unknown"
    first = proxy["seen"][0].decode()
    assert first.startswith("CONNECT reach.leerie-proxy.invalid:443 HTTP/1.1")
    auth = base64.b64encode(b"probe@u:s3cr:t").decode()
    assert f"Proxy-Authorization: Basic {auth}" in first


def test_the_proxy_credential_never_reaches_argv(leerie, proxy, monkeypatch):
    seen: list = []
    real = leerie.run_proc

    async def _spy(cmd, **kw):
        seen.append((list(cmd), kw.get("env")))
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie, "run_proc", _spy)
    assert asyncio.run(leerie._network_reach(
        "reach.leerie-proxy.invalid", 443)) == "reachable"
    (argv, penv), = seen
    assert not any("s3cr" in a for a in argv)
    assert argv[-2:] == ["127.0.0.1", str(proxy["port"])]
    assert penv["LEERIE_PROBE_PROXY_AUTH"] == base64.b64encode(
        b"probe@u:s3cr:t").decode()


def test_no_proxy_and_local_hosts_go_direct(leerie, proxy, monkeypatch):
    assert leerie._egress_proxy("reach.leerie-proxy.invalid") is not None
    assert leerie._egress_proxy("localhost") is None
    assert leerie._egress_proxy("127.0.0.1") is None
    monkeypatch.setenv("NO_PROXY", ".leerie-proxy.invalid")
    assert leerie._egress_proxy("reach.leerie-proxy.invalid") is None
    assert leerie._egress_proxy("billing.acme-test.invalid") is not None


def test_proxy_unset_is_direct(leerie, monkeypatch):
    for v in leerie._PROXY_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    assert leerie._egress_proxy("billing.acme-test.invalid") is None


def test_proxy_only_egress_verifies_a_real_external_block(
        real_reach, ext, proxy, monkeypatch):
    """The reviewer's L4 shape: no direct egress, only the proxy. The
    control and the claimed host are both probed through it; a host the
    proxy cannot reach (502) is now backed instead of `unverified`
    forever."""
    c = ext["leerie"]
    monkeypatch.setattr(c, "_network_reach", real_reach)
    out = _check(ext, {"kind": "network", "host": "billing.acme-test.invalid",
                       "port": 8443})
    assert out["verdict"] == "verified", out
    targets = [d.split(b"\r\n", 1)[0] for d in proxy["seen"]]
    assert targets == [b"CONNECT billing.acme-test.invalid:8443 HTTP/1.1",
                       b"CONNECT api.anthropic.com:443 HTTP/1.1"]


# --- the bound ----------------------------------------------------------------

def _env_unverified_result(host="billing.acme-test.invalid"):
    return {"subtask_id": "t1", "status": "blocked",
            "blocker": "billing API unreachable",
            "blocker_kind": "external_precondition",
            "external_resource": "billing API",
            "external_resource_claim": {"kind": "network", "host": host,
                                        "port": 8443}}


def test_environment_unverified_twice_settles_unconverged(ext, monkeypatch):
    """The control is down: the first attempt is re-driven; the re-driven
    attempt with the same claim settles `unconverged`, naming what could
    not be checked."""
    ext["reach"][("billing.acme-test.invalid", 8443)] = "unreachable"
    st = ext["st"]
    res = _settle_with(ext, monkeypatch, _env_unverified_result())
    assert res["status"] == "blocked"
    assert st.data["blocker_kinds"]["t1"]["external_check"]["reason"] == \
        "environment"
    res = _settle_with(ext, monkeypatch, _env_unverified_result())
    assert res["status"] == "unconverged"
    assert st.data["subtask_status"]["t1"] == "unconverged"
    rec = st.data["unconverged_subtasks"]["t1"]
    assert rec["kind"] == "external_unverified"
    assert rec["reason"].startswith(
        "external claim could not be checked in this environment: network "
        "billing.acme-test.invalid:8443: the network probe cannot run here")
    assert not st.data.get("needs_human_verification")


def test_a_different_claim_or_a_worker_fault_is_redriven(ext, monkeypatch):
    ext["reach"][("billing.acme-test.invalid", 8443)] = "unreachable"
    ext["reach"][("other.acme-test.invalid", 8443)] = "unreachable"
    st = ext["st"]
    assert _settle_with(ext, monkeypatch, _env_unverified_result()
                        )["status"] == "blocked"
    # a different claim the second time: not the same unverifiable check
    assert _settle_with(ext, monkeypatch, _env_unverified_result(
        "other.acme-test.invalid"))["status"] == "blocked"
    # an untyped claim (the worker's fault, not the environment's)
    untyped = _env_unverified_result()
    del untyped["external_resource_claim"]
    assert _settle_with(ext, monkeypatch, untyped)["status"] == "blocked"
    assert _settle_with(ext, monkeypatch, untyped)["status"] == "blocked"
    assert st.data["subtask_status"]["t1"] == "blocked"
    assert "t1" not in (st.data.get("unconverged_subtasks") or {})
