"""A local service is not external (DESIGN §13 *A local service is not
external*).

Round-3 review M2: a `network` claim on `localhost:<closed port>` — a
Postgres nobody started, the commonest real blocker — probed `unreachable`
and was `verified`, settling the subtask `accepted_external` with no work.
The reviewer's driver is kept below with its assertion inverted; the real
probe is driven against real loopback addresses, and the end-to-end routing
through `_settle_subtask` is asserted.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from tests.test_external_claim_check import (  # noqa: F401  (fixtures)
    _check, _commit, _settle_with, ext)
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_reviewer_driver_loopback_service_is_not_accepted(ext):
    c = ext["leerie"]
    port = _closed_port()
    _commit(ext, "config/db.py",
            f"DATABASE_URL = 'postgres://app@localhost:{port}/app'\n")
    ext["reach"][c._network_control()] = "reachable"
    ext["reach"][("localhost", port)] = "unreachable"
    out = _check(ext, {"kind": "network", "host": "localhost", "port": port})
    assert out["verdict"] == "refuted", out
    assert out["reason"] == "local_service"
    assert out["why"].startswith("local service: start it in-container")


@pytest.mark.parametrize("host", [
    "localhost", "LOCALHOST.", "db.localhost", "127.0.0.1", "127.8.9.10",
    "::1", "10.1.2.3", "172.17.0.2", "192.168.1.5", "169.254.169.254",
    "fd00::5", "fe80::1", "0.0.0.0"])
def test_local_host_spellings(leerie, host):
    assert leerie._network_local_host(host) != ""


@pytest.mark.parametrize("host", [
    "billing.acme-test.invalid", "8.8.8.8", "2001:4860:4860::8888",
    "localhost.example.test"])
def test_external_host_spellings(leerie, host):
    assert leerie._network_local_host(host) == ""


def test_name_served_by_etc_hosts(leerie, tmp_path, monkeypatch):
    hosts = tmp_path / "hosts"
    hosts.write_text("# compose\n10.0.0.9  postgres pg.internal.test  "
                     "# the db\n203.0.113.4 partner.example.test\n")
    monkeypatch.setattr(leerie, "_ETC_HOSTS", str(hosts))
    assert "postgres" in leerie._network_local_host("postgres")
    assert leerie._network_local_host("PG.internal.test") != ""
    # every /etc/hosts name is the container's own, whatever its address
    assert leerie._network_local_host("partner.example.test") != ""
    assert leerie._network_local_host("the") == ""
    assert leerie._network_local_host("db.acme-test.invalid") == ""


def test_real_probe_reports_a_resolved_local_address(leerie):
    """The check is on the resolved address, not the spelling: the real
    probe resolves `localhost` and reports the loopback address before any
    connect, open port or closed."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        for port in (srv.getsockname()[1], _closed_port()):
            got = asyncio.run(leerie._network_reach("localhost", port))
            assert got.startswith("local "), got
            assert leerie._is_local_address(got.split()[1])
    finally:
        srv.close()


def test_a_dotted_name_resolving_local_is_refuted(ext):
    """A name that passes every static check but resolves to a local
    address (a compose service under a domain) is refuted from the probe's
    report."""
    c = ext["leerie"]
    _commit(ext, "config/cache.py", "HOST = 'cache.acme-test.invalid'\n")
    ext["reach"][c._network_control()] = "reachable"
    ext["reach"][("cache.acme-test.invalid", 6379)] = "local 172.18.0.4"
    out = _check(ext, {"kind": "network", "host": "cache.acme-test.invalid",
                       "port": 6379})
    assert out["verdict"] == "refuted"
    assert out["reason"] == "local_service"
    assert "resolves to 172.18.0.4" in out["why"]


def test_local_service_is_redriven_not_accepted(ext, monkeypatch):
    port = _closed_port()
    res = _settle_with(ext, monkeypatch, {
        "subtask_id": "t1", "status": "blocked",
        "blocker": "could not connect to the database",
        "blocker_kind": "external_precondition",
        "external_resource": "postgres",
        "external_resource_claim": {"kind": "network", "host": "localhost",
                                    "port": port}})
    st = ext["st"]
    assert res["status"] == "blocked"
    assert "local service: start it in-container" in res["blocker"]
    assert st.data["subtask_status"]["t1"] == "blocked"
    assert not st.data.get("needs_human_verification")
    rec = st.data["blocker_kinds"]["t1"]
    assert rec["kind"] == "other"
    assert rec["external_check"]["reason"] == "local_service"
