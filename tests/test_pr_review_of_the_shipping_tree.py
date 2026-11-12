"""The review the PR shows is of the tree that ships (DESIGN §9; issue 46,
barnacle 39c4c5ad).

The whole-PR review runs before the final conformer, which — with the
held-out repair rounds and the delivery re-drive after it — commits. The
PR listed every verified finding of that review as a current lead: in
39c4c5ad, "only compares the top-level key set" quoted code the head no
longer had. Over the 27 local runs with a review, the run branch moved
after it in 24.

Replay on real git and a real run repository: the reviewer flags two
places, a later commit fixes one, and the PR is rendered as finalize
renders it. Stubbed: `claude_p` only.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_finalize_evidence import EFFORTS, MODELS, _review_env
from tests.test_run_final_conformance import env  # noqa: F401

FIXED = {"item_id": None, "kind": "bug", "path": "src.py",
         "line_start": 1, "line_end": 2, "quote": "def f():\n    pass",
         "item_span_quote": None,
         "explanation": "f only compares the top-level key set"}
KEPT = {"item_id": None, "kind": "bug", "path": "api.py",
        "line_start": 1, "line_end": 2, "quote": "def g():\n    return 2",
        "item_span_quote": None, "explanation": "g ignores its input"}


def _commit(tree: Path, name: str, text: str, msg: str) -> None:
    (tree / name).write_text(text)
    _git(tree, "add", "-A")
    _git(tree, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         msg)


def _setup(env):  # noqa: F811
    """The integrated tree has both flagged places; the review reads it."""
    _review_env(env, required=False)
    _commit(env["staging"], "api.py", "def g():\n    return 2\n", "add g")
    return env


def _stub(leerie, monkeypatch, replies: list):
    """Each pr_reviewer call takes the next reply: a findings list, or an
    exception to raise. Records the call and what its cwd held."""
    calls: list[dict] = []

    async def fake(**kw):
        assert kw["schema_key"] == "pr_reviewer", kw["schema_key"]
        cwd = Path(kw["cwd"])
        calls.append({**kw, "src": (cwd / "src.py").read_text(),
                      "head": _git(cwd, "rev-parse", "HEAD")})
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return {"findings": reply}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def _review(env):  # noqa: F811
    return asyncio.run(env["leerie"]._run_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))


def _review_shipping(env):  # noqa: F811
    return asyncio.run(env["leerie"]._run_shipping_pr_review(
        env["run_dir"], env["st"], env["caps"], MODELS, EFFORTS))


def _fix_after_review(env) -> str:  # noqa: F811
    """A later step (the final conformer, a re-drive group) fixes f."""
    _commit(env["staging"], "src.py", "def f():\n    return 1\n",
            "conformer: f returns 1")
    return env["leerie"]._shipping_commit(env["st"])


def _pr_section(env) -> str:  # noqa: F811
    """What finalize renders into the PR (`phase_finalize`: the record,
    then `pr_end_state`)."""
    leerie = env["leerie"]
    leerie._record_pr_review_shown(env["st"])
    return leerie._format_pr_end_state_section(env["st"].data)


def test_the_review_is_made_again_on_the_tree_that_ships(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _setup(env)
    calls = _stub(leerie, monkeypatch, [[FIXED, KEPT], [KEPT]])
    first = _review(env)
    shipping = _fix_after_review(env)
    assert shipping and shipping != first["head"]
    rec = _review_shipping(env)
    assert len(calls) == 2
    # The second review read the commit that ships, in its own checkout.
    assert calls[1]["head"] == shipping == rec["head"]
    assert calls[1]["src"] == "def f():\n    return 1\n"
    assert f"head {shipping}" in calls[1]["user_prompt"]
    assert "+    return 1" in calls[1]["user_prompt"]
    assert calls[1]["model"] == "opus" and calls[1]["effort"] == "high"
    assert env["st"].data["pr_review_shipped"] is rec
    # A resume on the same tree does not make it again.
    assert _review_shipping(env) is rec and len(calls) == 2
    body = _pr_section(env)
    assert ("Whole-PR review findings (reviewed on "
            f"`{shipping[:12]}`, the run branch's head") in body
    assert "g ignores its input" in body
    assert "top-level key set" not in body


def test_no_second_review_when_nothing_moved_the_tree(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    _setup(env)
    calls = _stub(leerie, monkeypatch, [[FIXED, KEPT]])
    first = _review(env)
    assert _review_shipping(env) is None
    assert len(calls) == 1 and "pr_review_shipped" not in env["st"].data
    body = _pr_section(env)
    assert ("Whole-PR review findings (reviewed on "
            f"`{first['head'][:12]}`, the run branch's head; each quote "
            "checked in the file there)") in body
    assert "f only compares the top-level key set" in body
    assert "g ignores its input" in body
    assert "`src.py:1-2`" in body


def test_a_finding_whose_code_changed_is_not_listed(env, monkeypatch):  # noqa: F811
    """The second review crashed: only the first review's findings exist.
    The one whose quoted code a later commit changed is not listed as a
    current lead; the one still in the shipped file is, under the commit
    it was reviewed on and the commit it was checked on."""
    leerie = env["leerie"]
    _setup(env)
    _stub(leerie, monkeypatch, [[FIXED, KEPT],
                                leerie.WorkerError("reviewer crashed")])
    first = _review(env)
    shipping = _fix_after_review(env)
    assert "error" in _review_shipping(env)
    body = _pr_section(env)
    assert ("Whole-PR review findings (reviewed on "
            f"`{first['head'][:12]}`; each quote checked again in the file "
            f"at `{shipping[:12]}`, the run branch's head)") in body
    assert "g ignores its input" in body
    assert "top-level key set" not in body
    assert (f"- 1 more of that review's findings quote code the file at "
            f"`{shipping[:12]}` no longer has; not listed (state.json "
            "`pr_review`)") in body
    shown = env["st"].data["pr_review_shown"]
    assert (shown["source"], shown["n_quote_gone"]) == ("pr_review", 1)


def test_the_pr_body_lists_only_what_holds_on_the_tree_that_ships(
        env, monkeypatch):  # noqa: F811
    """The composed PR body (`_compose_pr_via_llm`), as the host pushes it."""
    leerie = env["leerie"]
    _setup(env)
    _stub(leerie, monkeypatch, [[FIXED, KEPT], [KEPT]])
    _review(env)
    _fix_after_review(env)
    _review_shipping(env)
    leerie._record_pr_review_shown(env["st"])

    async def writer(**kw):
        return {"title": "t", "body": "b"}
    monkeypatch.setattr(leerie, "claude_p", writer)
    asyncio.run(leerie._compose_pr_via_llm(env["st"], env["caps"], {}, {},
                                           None))
    body = json.loads((Path(env["st"].run_dir) / "run.json").read_text()
                      )["pr_body"]
    assert "g ignores its input" in body
    assert "top-level key set" not in body


def test_the_shipping_review_never_reaches_a_fixer(env, monkeypatch):  # noqa: F811
    """Its findings are for the PR: the final conformer's section and its
    set-aside ids are built from the first review alone, and nothing that
    commits runs after it."""
    leerie = env["leerie"]
    _setup(env)
    late = {**KEPT, "explanation": "found only on the shipping tree"}
    _stub(leerie, monkeypatch, [[FIXED], [late]])
    _review(env)
    _fix_after_review(env)
    assert _review_shipping(env)["n_verified"] == 1
    section = leerie._format_pr_review_section(env["st"]) or ""
    assert "found only on the shipping tree" not in section
    assert all("shipping tree" not in f["text"] for f in
               leerie._set_aside_findings(env["st"], [], None).values())
    src = inspect.getsource(leerie._run_phases)
    at = src.index("await _run_shipping_pr_review(")
    assert src.rindex("await _run_delivery_redrive(") < at
    assert src.rindex("await _check_existing_behaviour(") < at
    assert src.index("await phase_finalize(") > at
    assert "await " not in src[src.index("\n", at):src.index(
        "await phase_finalize(")].replace("await _run_shipping_pr_review(",
                                          "")
