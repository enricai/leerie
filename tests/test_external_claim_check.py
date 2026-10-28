"""An external claim is checked, not taken on the word (DESIGN §13).

Before this check `_route_blocker_kind` settled any `external_precondition`
that named a resource `accepted_external` — a made-up "staging API key nobody
named" included (the lane-C suspicion-4 driver, kept below as
`test_made_up_resource_is_not_accepted`). Every test drives the real
`_verify_external_claim` (or the real `_settle_subtask`) against a real git
worktree and asserts the verdict and the routed state, per kind, for a true
claim and a false one. Only the network probe's reachability and, for the
execution cross-check, the cgroup broker and host-pressure gate are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


@pytest.fixture
def ext(env, monkeypatch, tmp_path):  # noqa: F811
    """`env` with an isolated HOME, a task naming the test resources, and
    the network probe stubbed to a per-test reachability table."""
    c = env["leerie"]
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    reach: dict[tuple[str, int], str] = {}

    async def _reach(host, port):
        return reach.get((host, port), "unknown")
    monkeypatch.setattr(c, "_network_reach", _reach)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    env["st"].data["task"] = (
        "Wire the billing export. It reads LEERIE_T_BILLING_TOKEN, calls "
        "billing.acme-test.invalid on port 8443, loads "
        "config/acme-billing.pem, shells out to acmectl-leerie-t and uses "
        "the ACME_BILLING service credential. Then an admin must approve "
        "the export in the Acme billing dashboard.")
    env["st"].save()
    env["home"] = home
    env["reach"] = reach
    return env


def _check(env, claim, *, subtask=None):  # noqa: F811
    c = env["leerie"]
    res = {"status": "blocked", "blocker_kind": "external_precondition",
           "external_resource": "x", "blocker": "b"}
    if claim is not None:
        res["external_resource_claim"] = claim
    return asyncio.run(c._verify_external_claim(
        "t1", res, subtask if subtask is not None else env["subtask"],
        str(env["worktree"]), env["st"], dict(env["caps"])))


def _commit(env, rel, text):  # noqa: F811
    """Commit `rel` on the run branch (the tree grounding searches)."""
    repo = env["repo"]
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo, check=True)


# --- the typed claim ----------------------------------------------------------

@pytest.mark.parametrize("claim", [
    None,
    {"kind": "made_up", "name": "X"},
    {"kind": "env_var"},
    {"kind": "env_var", "name": "  "},
    {"kind": "network", "host": "h"},
    {"kind": "network", "host": "h", "port": 0},
    {"kind": "network", "host": "h", "port": True},
    {"kind": "human_action", "description": "approve it"},
])
def test_an_untyped_or_incomplete_claim_is_unverified(ext, claim):
    out = _check(ext, claim)
    assert out["verdict"] == "unverified"
    assert out["claim"] is None


def test_schema_types_the_claim_through_defs(leerie):
    sch = leerie.SCHEMAS["implementer"]
    ref = sch["properties"]["external_resource_claim"]["$ref"]
    target = sch["$defs"][ref.rsplit("/", 1)[1]]
    assert target["required"] == ["kind"]
    assert target["properties"]["kind"]["enum"] == list(
        leerie.EXTERNAL_RESOURCE_KINDS)
    assert set(leerie._EXTERNAL_RESOURCE_KEY) == set(
        leerie.EXTERNAL_RESOURCE_KINDS)
    for field in leerie._EXTERNAL_RESOURCE_KEY.values():
        assert field in target["properties"]


# --- per kind: a true claim and a false one ----------------------------------

def test_env_var(ext, monkeypatch):
    claim = {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"}
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    assert _check(ext, claim)["verdict"] == "verified"
    monkeypatch.setenv("LEERIE_T_BILLING_TOKEN", "")
    assert _check(ext, claim)["verdict"] == "verified", "empty is missing"
    monkeypatch.setenv("LEERIE_T_BILLING_TOKEN", "tok")
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert out["why"] == ("claimed missing env var LEERIE_T_BILLING_TOKEN, "
                          "but it is set in the worker's environment")


def test_file(ext):
    claim = {"kind": "file", "path": "config/acme-billing.pem"}
    assert _check(ext, claim)["verdict"] == "verified"
    p = ext["worktree"] / "config" / "acme-billing.pem"
    p.parent.mkdir()
    p.write_text("x")
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "claimed missing file config/acme-billing.pem" in out["why"]


def test_binary(ext, monkeypatch):
    c = ext["leerie"]
    claim = {"kind": "binary", "name": "acmectl-leerie-t"}
    assert _check(ext, claim)["verdict"] == "verified"
    monkeypatch.setattr(c.shutil, "which",
                        lambda n: "/usr/bin/" + n if n == "acmectl-leerie-t"
                        else None)
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "/usr/bin/acmectl-leerie-t" in out["why"]


@pytest.mark.parametrize("plant, source", [
    (lambda wt: (wt / "node_modules/.bin").mkdir(parents=True)
     or (wt / "node_modules/.bin/acmectl-leerie-t").write_text(""),
     "node_modules/.bin/acmectl-leerie-t"),
    (lambda wt: (wt / "package-lock.json").write_text(json.dumps(
        {"packages": {"node_modules/acme-cli": {
            "bin": {"acmectl-leerie-t": "bin/x"}}}})),
     "package-lock.json (node_modules/acme-cli)"),
    (lambda wt: (wt / "package.json").write_text(json.dumps(
        {"devDependencies": {"acmectl-leerie-t": "^1"}})),
     "package.json devDependencies"),
    (lambda wt: (wt / "requirements-dev.txt").write_text(
        "# tools\nAcmectl_Leerie.T>=2.0 ; python_version > '3'\n"),
     "requirements-dev.txt"),
    (lambda wt: (wt / "pyproject.toml").write_text(
        '[project]\ndependencies = ["acmectl-leerie-t>=1", "x"]\n'),
     "pyproject.toml"),
    (lambda wt: (wt / ".tool-versions").write_text(
        "acmectl-leerie-t 1.2.3\n"), ".tool-versions"),
])
def test_binary_installable_from_the_repo_is_refuted(ext, plant, source):
    c = ext["leerie"]
    plant(ext["worktree"])
    assert c._binary_install_source("acmectl-leerie-t", str(ext["worktree"]),
                                    ext["st"]) == source
    assert _check(ext, {"kind": "binary", "name": "acmectl-leerie-t"}
                  )["verdict"] == "refuted"


def test_binary_in_the_provision_recipe_is_refuted(ext):
    ext["st"].data["provision"] = {"recipe": [
        {"kind": "install", "command": ["pip", "install", "acmectl-leerie-t"],
         "working_dir": "."}]}
    out = _check(ext, {"kind": "binary", "name": "acmectl-leerie-t"})
    assert out["verdict"] == "refuted"
    assert "the provision recipe" in out["why"]


def test_service_credential(ext, monkeypatch):
    claim = {"kind": "service_credential", "name": "ACME_BILLING"}
    for k in list(os.environ):
        if k.upper().startswith("ACME_BILLING"):
            monkeypatch.delenv(k)
    assert _check(ext, claim)["verdict"] == "verified"
    monkeypatch.setenv("ACME_BILLING_API_KEY", "k")
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "environment variable ACME_BILLING_API_KEY" in out["why"]


def test_service_credential_held_in_a_credential_file(ext):
    c = ext["leerie"]
    (ext["home"] / ".netrc").write_text(
        "machine acme_billing login u password p\n")
    assert c._service_credential_source(
        "ACME_BILLING", {}, ext["home"]) == "credential file ~/.netrc"
    assert c._service_credential_source("aws", {}, ext["home"]) == ""
    (ext["home"] / ".aws").mkdir()
    (ext["home"] / ".aws" / "credentials").write_text("[default]\n")
    assert c._service_credential_source(
        "aws", {}, ext["home"]) == "credential file ~/.aws/credentials"
    assert _check(ext, {"kind": "service_credential", "name": "ACME_BILLING"}
                  )["verdict"] == "refuted"


def test_network(ext):
    claim = {"kind": "network", "host": "billing.acme-test.invalid",
             "port": 8443}
    ctl = ext["leerie"]._NETWORK_PROBE_CONTROL
    ext["reach"][ctl] = "reachable"
    ext["reach"][("billing.acme-test.invalid", 8443)] = "unreachable"
    assert _check(ext, claim)["verdict"] == "verified"
    ext["reach"][("billing.acme-test.invalid", 8443)] = "reachable"
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "a TCP connect from the container succeeds" in out["why"]
    ext["reach"][("billing.acme-test.invalid", 8443)] = "unknown"
    assert _check(ext, claim)["verdict"] == "unverified"


def test_network_without_a_reachable_control_never_accepts(ext):
    """A container with no network makes every host unreachable; the probe
    then proves nothing, so the claim is unverified, never accepted."""
    ext["reach"][("billing.acme-test.invalid", 8443)] = "unreachable"
    out = _check(ext, {"kind": "network", "host": "billing.acme-test.invalid",
                       "port": 8443})
    assert out["verdict"] == "unverified"
    assert "control host api.anthropic.com:443" in out["why"]


def test_network_control_follows_anthropic_base_url(leerie, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:4001/x")
    assert leerie._network_control() == ("127.0.0.1", 4001)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gw.example.test")
    assert leerie._network_control() == ("gw.example.test", 443)
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    assert leerie._network_control() == ("api.anthropic.com", 443)


def test_human_action(ext):
    """The quote must come from a typed external item; the task names the
    same step, but the task alone never backs a human_action (round-3
    review M1)."""
    claim = {"kind": "human_action", "description": "dashboard approval",
             "quote": "an admin must approve the export in the Acme billing "
                      "dashboard"}
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "the task text alone does not declare a human step" in out["why"]
    ext["st"].data["required_items"] = [
        {"item": "Then an admin must approve the export in the Acme billing "
                 "dashboard.", "span": "S3", "verifiability": "external"}]
    assert _check(ext, claim)["verdict"] == "verified"
    out = _check(ext, {**claim, "quote": "someone has to sign the release "
                                         "form"})
    assert out["verdict"] == "refuted"
    assert "quote is in none of the run's typed external items" in out["why"]
    # too short to be a quote at all
    assert _check(ext, {**claim, "quote": "approve"})["verdict"] == "refuted"


def test_human_action_quoted_from_a_typed_external_item(ext):
    ext["st"].data["task"] = "Wire the billing export."
    claim = {"kind": "human_action", "description": "sign-off",
             "quote": "legal signs off on the data retention wording"}
    assert _check(ext, claim)["verdict"] == "refuted"
    ext["st"].data["required_items"] = [
        {"item": "Before release, legal signs off on the data retention "
                 "wording",
         "span": "S1", "verifiability": "external"}]
    assert _check(ext, claim)["verdict"] == "verified"


# --- the real network probe ---------------------------------------------------

def test_network_probe_reaches_a_listener_and_not_a_closed_port(leerie):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    open_port = srv.getsockname()[1]
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    try:
        assert asyncio.run(leerie._network_reach(
            "127.0.0.1", open_port)) == "reachable"
        assert asyncio.run(leerie._network_reach(
            "127.0.0.1", closed_port)) == "unreachable"
    finally:
        srv.close()


def test_network_probe_never_hangs(leerie, monkeypatch):
    """A probe that never answers is killed at the hard timeout and read as
    `unknown` (unverified), never as unreachable."""
    monkeypatch.setattr(leerie, "_NETWORK_PROBE_SCRIPT",
                        "import time\ntime.sleep(60)\n")
    monkeypatch.setattr(leerie, "_NETWORK_PROBE_HARD_SEC", 1.0)
    t0 = time.monotonic()
    assert asyncio.run(leerie._network_reach("127.0.0.1", 1)) == "unknown"
    assert time.monotonic() - t0 < 10


# --- grounding and consistency -----------------------------------------------

def test_made_up_resource_is_not_accepted(ext, monkeypatch):
    """The suspicion-4 driver: a resource nobody named, typed as each kind
    whose probe trivially backs absence, is refuted by grounding."""
    monkeypatch.delenv("LEERIE_T_NOBODY_NAMED", raising=False)
    for claim in ({"kind": "env_var", "name": "LEERIE_T_NOBODY_NAMED"},
                  {"kind": "service_credential",
                   "name": "LEERIE_T_STAGING_KEY_NOBODY_NAMED"},
                  {"kind": "file", "path": "/opt/nobody-named.key"},
                  {"kind": "binary", "name": "nobody-named-ctl"}):
        out = _check(ext, claim)
        assert out["verdict"] == "refuted", claim
        assert "nor the repository names it" in out["why"]


def test_grounded_by_the_repository(ext, monkeypatch):
    monkeypatch.delenv("LEERIE_T_FROM_REPO", raising=False)
    claim = {"kind": "env_var", "name": "LEERIE_T_FROM_REPO"}
    assert _check(ext, claim)["verdict"] == "refuted"
    _commit(ext, "app/settings.py",
            "TOKEN = os.environ['LEERIE_T_FROM_REPO']\n")
    assert _check(ext, claim)["verdict"] == "verified"


def test_grounding_reads_the_run_branch_not_the_workers_commits(
        ext, monkeypatch):
    """A worker that writes the name into its own commit does not ground
    its own claim."""
    monkeypatch.delenv("LEERIE_T_SELF_MADE", raising=False)
    wt = ext["worktree"]
    (wt / "s.py").write_text("X = os.environ['LEERIE_T_SELF_MADE']\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "w"], cwd=wt, check=True)
    assert _check(ext, {"kind": "env_var", "name": "LEERIE_T_SELF_MADE"}
                  )["verdict"] == "refuted"


def test_consistent_with_the_planners_declared_externals(ext, monkeypatch):
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    claim = {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"}
    ext["st"].data["external_preconditions"] = [
        {"tag": "ledger-db", "reasons": [
            {"sid": "t1", "reason": "needs the production ledger DSN"}],
         "originating_subtasks": ["t1"]}]
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "name other resources" in out["why"]
    # declared for another subtask only: no constraint on this one
    ext["st"].data["external_preconditions"][0]["originating_subtasks"] = \
        ["t2"]
    assert _check(ext, claim)["verdict"] == "verified"
    ext["st"].data["external_preconditions"] = [
        {"tag": "billing-token", "reasons": [
            {"sid": "t1", "reason": "LEERIE_T_BILLING_TOKEN from ops"}],
         "originating_subtasks": ["t1"]}]
    assert _check(ext, claim)["verdict"] == "verified"


def test_consistent_with_a_claimed_classifier_external_item(ext, monkeypatch):
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    claim = {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"}
    ext["st"].data["required_items"] = [
        {"item": "confirm the export lands in the warehouse",
         "span": "S2", "verifiability": "external"}]
    # an external item this subtask does not claim does not constrain it
    assert _check(ext, claim)["verdict"] == "verified"
    sub = {**ext["subtask"], "claims": {"sites": [], "items": [
        {"item_index": 0, "kind": "deliverable"}]}}
    assert _check(ext, claim, subtask=sub)["verdict"] == "refuted"


# --- the verify argvs must fail, naming the resource -------------------------

@pytest.fixture
def runs(ext, monkeypatch):
    c = ext["leerie"]
    monkeypatch.setattr(c, "_cgroup_create", lambda *a, **k: None)

    async def _gate(caps, **_k):
        return {"waited_sec": 0.0, "timed_out": False, "psi": 1.0,
                "readable": True}
    monkeypatch.setattr(c, "_confirm_gate", _gate)
    monkeypatch.setattr(c, "resolve_blt", lambda root: {})
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    return ext


def _verify_sub(env, code):  # noqa: F811
    return {**env["subtask"], "verify": [
        {"criterion": "exports", "argv": [sys.executable, "-c", code]}]}


@pytest.mark.parametrize("code, verdict", [
    ("import sys; print('KeyError: LEERIE_T_BILLING_TOKEN'); sys.exit(1)",
     "verified"),
    ("import sys; print('assertion failed: rows == 3'); sys.exit(1)",
     "refuted"),
    ("pass", "refuted"),
])
def test_verify_argvs_must_fail_naming_the_resource(runs, code, verdict):
    out = _check(runs, {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"},
                 subtask=_verify_sub(runs, code))
    assert out["verdict"] == verdict, out["why"]


def test_verify_argvs_that_cannot_run_leave_it_unverified(runs):
    """A dirty worktree: execution returns `none`, which never accepts."""
    (runs["worktree"] / "README.md").write_text("dirty\n")
    out = _check(runs, {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"},
                 subtask=_verify_sub(runs, "import sys; sys.exit(1)"))
    assert out["verdict"] == "unverified"


# --- routing, end to end through _settle_subtask ----------------------------

def _settle_with(env, monkeypatch, result, subtask=None):  # noqa: F811
    c = env["leerie"]
    if subtask is not None:
        (env["run_dir"] / "subtasks" / "t1.json").write_text(
            json.dumps(subtask))
    notes = []

    async def _stub(sid, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        notes.append(note)
        return dict(result)
    monkeypatch.setattr(c, "_run_implementer", _stub)
    out = asyncio.run(c._settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"], env["models"],
        env["efforts"]))
    return out


def test_a_refuted_claim_is_redriven_with_its_typed_reason(ext, monkeypatch):
    monkeypatch.setenv("LEERIE_T_BILLING_TOKEN", "set")
    res = _settle_with(ext, monkeypatch, {
        "subtask_id": "t1", "status": "blocked", "blocker": "no token",
        "blocker_kind": "external_precondition",
        "external_resource": "billing token",
        "external_resource_claim": {"kind": "env_var",
                                    "name": "LEERIE_T_BILLING_TOKEN"}})
    st = ext["st"]
    assert res["status"] == "blocked"
    assert res["blocker"] == (
        "[other] no token\nexternal_precondition not accepted (refuted): "
        "claimed missing env var LEERIE_T_BILLING_TOKEN, but it is set in "
        "the worker's environment")
    assert st.data["subtask_status"]["t1"] == "blocked"
    assert not st.data.get("needs_human_verification")
    rec = st.data["blocker_kinds"]["t1"]
    assert rec["kind"] == "other"
    assert rec["claimed"] == "external_precondition"
    assert rec["external_check"]["verdict"] == "refuted"


def test_a_verified_claim_is_accepted(ext, monkeypatch):
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    res = _settle_with(ext, monkeypatch, {
        "subtask_id": "t1", "status": "blocked", "blocker": "no token",
        "blocker_kind": "external_precondition",
        "external_resource": "billing token",
        "external_resource_claim": {"kind": "env_var",
                                    "name": "LEERIE_T_BILLING_TOKEN"}})
    st = ext["st"]
    assert res["status"] == "accepted_external"
    assert st.data["needs_human_verification"] == [
        {"source": "blocker", "subtask": "t1", "resource": "billing token",
         "blocker": "no token"}]
    rec = st.data["blocker_kinds"]["t1"]
    assert rec["kind"] == "external_precondition"
    assert rec["external_check"]["verdict"] == "verified"


def test_suspicion_4_driver_no_longer_accepts(ext, monkeypatch):
    """The original driver's result: no typed claim, a resource nobody
    named. Accepted before this check; now `other`, re-driven."""
    res = _settle_with(ext, monkeypatch, {
        "subtask_id": "t1", "status": "blocked",
        "blocker_kind": "external_precondition",
        "external_resource": "a staging API key nobody named",
        "blocker": "I could not be bothered"})
    assert res["status"] == "blocked"
    assert res["blocker"].startswith("[other] I could not be bothered\n"
                                     "external_precondition not accepted "
                                     "(unverified)")
    assert ext["st"].data["subtask_status"]["t1"] == "blocked"
    assert not ext["st"].data.get("needs_human_verification")
