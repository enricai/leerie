"""The key of an external claim has the shape of its kind, and is matched as
a whole token (DESIGN §13 *An external claim is checked, not taken on the
word*).

Round-3 review M1: grounding and the verify-output check were
case-insensitive substring tests with no constraint on the key, so a
one-letter or common-word key (`E`, `export`, `FAILED`) and a
`human_action` quote lifted from the task itself came back `verified` and
settled an undone subtask `accepted_external`. The reviewer's driver is
kept below with its assertions inverted: every example now comes back
`refuted` or `unverified`. Each drives the real `_verify_external_claim`
against a real git worktree.
"""
from __future__ import annotations

import pytest

from tests.test_external_claim_check import (  # noqa: F401  (fixtures)
    _check, _commit, _verify_sub, ext, runs)
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)

NOT_ACCEPTED = ("refuted", "unverified")


# --- the reviewer's driver, inverted -----------------------------------------

@pytest.mark.parametrize("name", ["E", "export", "the", "THE", "EXPORT"])
def test_trivial_env_var_is_not_accepted(ext, monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    out = _check(ext, {"kind": "env_var", "name": name})
    assert out["verdict"] in NOT_ACCEPTED, out
    assert "is not an environment variable name" in out["why"]


@pytest.mark.parametrize("claim", [
    {"kind": "env_var", "name": "FAILED"},
    {"kind": "service_credential", "name": "export"},
])
def test_trivial_key_with_failing_verify_is_not_accepted(runs, claim):
    """The work simply isn't done: an assertion fails, nothing external."""
    code = ("import sys; print('FAILED test_export - assert 0 == 3'); "
            "sys.exit(1)")
    out = _check(runs, claim, subtask=_verify_sub(runs, code))
    assert out["verdict"] in NOT_ACCEPTED, out


@pytest.mark.parametrize("quote", ["Wire the billing export",
                                   "It reads LEERIE_T_BILLING"])
def test_human_action_quoting_the_task_is_not_accepted(runs, quote):
    code = "import sys; print('assert 0 == 3'); sys.exit(1)"
    out = _check(runs, {"kind": "human_action", "quote": quote,
                        "description": "needs a person"},
                 subtask=_verify_sub(runs, code))
    assert out["verdict"] in NOT_ACCEPTED, out
    out = _check(runs, {"kind": "human_action", "quote": quote})
    assert out["verdict"] in NOT_ACCEPTED, out


# --- shapes -----------------------------------------------------------------

@pytest.mark.parametrize("kind, key, ok", [
    ("env_var", "LEERIE_T_BILLING_TOKEN", True),
    ("env_var", "_PRIVATE_X", True),
    ("env_var", "AB", False),
    ("env_var", "lower_case", False),
    ("env_var", "1ABC", False),
    ("env_var", "ERROR", False),
    ("binary", "acmectl-leerie-t", True),
    ("binary", "g++", True),
    ("binary", "x", False),
    ("binary", "bin/acmectl", False),
    ("binary", "two words", False),
    ("binary", "export", False),
    ("file", "config/acme-billing.pem", True),
    ("file", ".env", True),
    ("file", "~/.aws/credentials", True),
    ("file", "/opt/x.key", True),
    ("file", "export", False),
    ("file", "a b/c.pem", False),
    ("file", "https://x.test/a.pem", False),
    ("file", "/", False),
    ("file", "..", False),
    ("network", "billing.acme-test.invalid", True),
    ("network", "10.0.0.5", True),
    ("network", "::1", True),
    ("network", "the", False),
    ("network", "bad_label.test", False),
    ("service_credential", "ACME_BILLING", True),
    ("service_credential", "~/.config/acme/token.json", True),
    ("service_credential", "Acme Billing", False),
    ("service_credential", "export", False),
])
def test_claim_key_shape(leerie, kind, key, ok):
    assert (leerie._claim_key_shape(kind, key) == "") is ok


# --- whole-token matching ---------------------------------------------------

@pytest.mark.parametrize("kind, key, text, hit", [
    ("env_var", "LEERIE_T_TOKEN", "os.environ['LEERIE_T_TOKEN']", True),
    ("env_var", "LEERIE_T_TOKEN", "KeyError: LEERIE_T_TOKEN.", True),
    ("env_var", "LEERIE_T_TOKEN", "LEERIE_T_TOKEN_V2 is set", False),
    ("env_var", "LEERIE_T_TOKEN", "X_LEERIE_T_TOKEN", False),
    ("env_var", "LEERIE_T_TOKEN", "leerie_t_token", False),
    ("file", "config/acme.pem", "open('/srv/app/config/acme.pem')", True),
    ("file", "config/acme.pem", "loads config/acme.pem.", True),
    ("file", "./config/acme.pem", "config/acme.pem", True),
    ("file", "config/acme.pem", "myconfig/acme.pem", False),
    ("file", "config/acme.pem", "config/acme.pem.bak", False),
    ("file", "config/acme.pem", "config/acme.pemx", False),
    ("file", ".env", "copy .env.example", False),
    ("file", ".env", "reads .env at boot", True),
    ("binary", "acmectl", "shells out to acmectl and", True),
    ("binary", "acmectl", "node_modules/.bin/acmectl", True),
    ("binary", "acmectl", "acmectl-v2", False),
    ("binary", "acmectl", "acmectl.py", False),
    ("network", "api.acme.test", "https://API.Acme.test:8443/x", True),
    ("network", "api.acme.test", "eu.api.acme.test", False),
    ("network", "api.acme.test", "api.acme.testing", False),
    ("human_action", "an admin approves the export",
     "Then an admin approves   the export.", True),
])
def test_claim_key_in_is_whole_token(leerie, kind, key, text, hit):
    assert leerie._claim_key_in(text, kind, key) is hit


def test_verify_output_must_name_the_key_as_a_token(runs, monkeypatch):
    """A failure output that merely contains the name inside a longer token
    does not name it."""
    monkeypatch.delenv("LEERIE_T_BILLING_TOKEN", raising=False)
    claim = {"kind": "env_var", "name": "LEERIE_T_BILLING_TOKEN"}
    code = ("import sys; print('LEERIE_T_BILLING_TOKEN_V2 mismatch'); "
            "sys.exit(1)")
    out = _check(runs, claim, subtask=_verify_sub(runs, code))
    assert out["verdict"] == "refuted"
    assert "never names LEERIE_T_BILLING_TOKEN" in out["why"]


def test_repository_grounding_is_whole_token(ext, monkeypatch):
    """The repository only contains a longer name: the claim is named
    nowhere."""
    monkeypatch.delenv("LEERIE_T_REPO_TOK", raising=False)
    claim = {"kind": "env_var", "name": "LEERIE_T_REPO_TOK"}
    _commit(ext, "app/settings.py",
            "TOKEN = os.environ['LEERIE_T_REPO_TOKEN']\n")
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "nor the repository names it" in out["why"]
    _commit(ext, "app/other.py", "T = os.environ['LEERIE_T_REPO_TOK']\n")
    assert _check(ext, claim)["verdict"] == "verified"


def test_path_shaped_credential_present_is_refuted(ext):
    claim = {"kind": "service_credential", "name": "config/acme-billing.pem"}
    assert _check(ext, claim)["verdict"] == "verified"
    p = ext["worktree"] / "config" / "acme-billing.pem"
    p.parent.mkdir()
    p.write_text("x")
    out = _check(ext, claim)
    assert out["verdict"] == "refuted"
    assert "the file config/acme-billing.pem" in out["why"]
