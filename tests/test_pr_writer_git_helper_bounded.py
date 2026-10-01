"""`_compose_pr_via_llm`'s nested `_git` helper (leerie.py:32170-32187) must
not hang phase_finalize forever on a stalled git process (e.g. lock
contention on the shared bind-mounted .git across concurrent worktree
operations). It wraps `proc.communicate()` in `asyncio.wait_for` bounded by
`PR_WRITER_GIT_TIMEOUT_SEC` and swallows the timeout via the function's
existing fail-open `except Exception` contract.
"""
from __future__ import annotations

import asyncio
import json
import subprocess

import pytest


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    (root / "README.md").write_text("hi\n")
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-qm", "init"], root)
    return root


@pytest.fixture
def st(leerie, repo, tmp_path):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "r1").mkdir(parents=True)
    s = leerie.State(leerie_root, "r1", repo_root=repo)
    yield s
    s.release_lock()


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_total_workers"] = 100
    return caps


class _HangingProc:
    """Stand-in for `asyncio.subprocess.Process` whose `communicate()`
    never resolves, mimicking a git process stalled on lock contention."""

    returncode = None

    async def communicate(self):
        await asyncio.Event().wait()  # never set: blocks forever

    async def wait(self):
        await asyncio.Event().wait()

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15

    @property
    def pid(self):
        return 999999


def test_git_helper_bounded_by_timeout_not_unbounded_hang(
        leerie, monkeypatch, st, repo):
    st.data["working_branch"] = "main"
    monkeypatch.setattr(leerie, "PR_WRITER_GIT_TIMEOUT_SEC", 0.05)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _HangingProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    terminated = []

    async def _fake_terminate_proc_tree(proc):
        terminated.append(proc)

    monkeypatch.setattr(
        leerie, "_terminate_proc_tree", _fake_terminate_proc_tree)

    async def _fake_claude_p(**kwargs):
        raise AssertionError(
            "claude_p must not be reached if git context collection hangs")

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)

    async def _bounded():
        return await asyncio.wait_for(
            leerie._compose_pr_via_llm(
                st, _caps(leerie), {}, {}, repo, None),
            timeout=5.0)

    # The whole call must return well within the outer 5s ceiling instead of
    # hanging forever — proves the fix, not just that a timeout is *possible*.
    asyncio.run(_bounded())

    # Fail-open: no PR title/body written, so the launcher's bash fallback
    # takes over exactly as it does for any other _compose_pr_via_llm error.
    assert not (st.run_dir / "run.json").exists() or \
        "pr_title" not in json.loads(
            (st.run_dir / "run.json").read_text() or "{}")
    # The stalled git subprocess must be reaped, not orphaned.
    assert len(terminated) >= 1


def test_risk_section_is_appended_to_the_llm_body(leerie, monkeypatch, st, repo):
    """DESIGN §6 *The residual-risk section is appended by code on every
    composition path*: the pr_writer path's append happens BEFORE the
    run.json write, so the handed-off pr_body already carries the section
    verbatim — the launcher needs no risk logic on this path (and must not
    add any, or the section renders twice)."""
    st.data["working_branch"] = "main"
    st.data["risk_register"] = {"feat-001": [
        {"kind": "external_contract_assumption",
         "detail": "matches the provider's error-message wording",
         "source": "implementer", "addressed": False}]}

    async def _fake_claude_p(**kwargs):
        return {"title": "add a feature", "body": "## Summary\n\nworker body"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))

    run_json = json.loads((st.run_dir / "run.json").read_text())
    body = run_json["pr_body"]
    assert body.startswith("## Summary\n\nworker body"), body
    expected = leerie._format_risk_register_section(st.data)
    assert expected and expected in body, (
        f"risk section missing from LLM pr_body.\n---got---\n{body!r}")


def test_no_risk_section_appended_when_register_empty(leerie, monkeypatch,
                                                      st, repo):
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        return {"title": "add a feature", "body": "## Summary\n\nworker body"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))
    body = json.loads((st.run_dir / "run.json").read_text())["pr_body"]
    assert body == "## Summary\n\nworker body"
    assert "Residual risks" not in body


def test_worker_emitted_risk_section_is_stripped_not_doubled(
        leerie, monkeypatch, st, repo):
    """The only pre-existing defense against a doubled section was
    the prompt; code must enforce (§12 — the _strip_leerie_prefix
    precedent). A worker body carrying its own section gets it stripped,
    and the final body carries exactly one."""
    st.data["working_branch"] = "main"
    st.data["risk_register"] = {"feat-001": [
        {"kind": "untested_change", "detail": "no test covers the branch",
         "source": "implementer", "addressed": False}]}

    async def _fake_claude_p(**kwargs):
        return {"title": "t",
                "body": "## Summary\n\nwork\n\n## ⚠ Residual risks\n\n"
                        "- worker-invented entry"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))
    body = json.loads((st.run_dir / "run.json").read_text())["pr_body"]
    assert body.count("Residual risks") == 1, body
    assert "worker-invented entry" not in body
    assert "no test covers the branch" in body


def test_mid_body_worker_section_strip_preserves_trailing_content(
        leerie, monkeypatch, st, repo):
    """The strip is span-limited to the section — a
    worker-emitted copy mid-body must not take the legitimate sections
    after it down with it (the naive truncate-at-heading did; a merge-
    ordering section was silently lost)."""
    st.data["working_branch"] = "main"
    st.data["risk_register"] = {"feat-001": [
        {"kind": "untested_change", "detail": "real register entry",
         "source": "implementer", "addressed": False}]}

    async def _fake_claude_p(**kwargs):
        return {"title": "t",
                "body": "## Summary\n\nwork\n\n## ⚠ Residual risks\n\n"
                        "- worker-invented entry\n\n"
                        "## Deploy order\n\nmerge the parent PR first"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))
    body = json.loads((st.run_dir / "run.json").read_text())["pr_body"]
    assert "worker-invented entry" not in body
    assert "merge the parent PR first" in body, body
    assert body.count("Residual risks") == 1, body
    assert "real register entry" in body


def test_worker_section_is_stripped_even_with_empty_register(
        leerie, monkeypatch, st, repo):
    """With an EMPTY register, a worker-fabricated section
    is the one shape where the section would be worker-owned — strip
    unconditionally, append nothing."""
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        return {"title": "t",
                "body": "## Summary\n\nwork\n\n## ⚠ Residual risks\n\n"
                        "- hallucinated: prod DB may be dropped"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))
    body = json.loads((st.run_dir / "run.json").read_text())["pr_body"]
    assert "Residual risks" not in body, body
    assert "hallucinated" not in body
    assert "## Summary" in body


def test_strip_skips_heading_quoted_inside_code_fence(leerie):
    """A heading inside an open ``` fence is a quote, not a section —
    stripping it left the fence unclosed and the rest of the body
    rendered as a literal code block (round-3 finding, executed)."""
    body = ("A\n```\n## ⚠ Residual risks\nfenced example\n```\n\n"
            "## Tail\ntail prose")
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert out == body
    assert stripped is False


def test_strip_matches_variation_selector_heading(leerie):
    """`⚠️` (U+FE0F) and `⚠` are glyph-identical; a worker echoing the
    heading plausibly emits either — both must strip."""
    body = ("## Summary\n\nwork\n\n## ⚠️ Residual risks\n\n"
            "- worker entry")
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert stripped is True
    assert "Residual risks" not in out
    assert "## Summary" in out


def test_strip_fence_detection_survives_inline_backticks_and_tildes(leerie):
    """Fence state is per fence LINE: an inline ``` in prose must not
    flip parity (it let a real section ship doubled), and a heading
    quoted inside a ~~~ fence must be protected like a ``` one."""
    prose = ("Use ``` to open a fence in markdown.\n\n"
             "## ⚠ Residual risks\n\n- worker entry\n")
    out, stripped = leerie._strip_worker_risk_sections(prose)
    assert stripped is True and "worker entry" not in out

    tilde = ("A\n~~~\n## ⚠ Residual risks\nfenced example\n~~~\n\n"
             "## Tail\ntail prose")
    out, stripped = leerie._strip_worker_risk_sections(tilde)
    assert out == tilde and stripped is False


def test_strip_section_end_is_fence_aware(leerie):
    """A `## ` line inside a code fence WITHIN the worker's section is
    not the section's end: stopping there kept worker-fabricated prose
    and left the fence's opener unclosed (the measured broken-fence
    incident, from the other side)."""
    body = ("A\n\n## ⚠ Residual risks\n\nworker entry\n```\n"
            "## fenced pseudo-heading\nstill worker content\n```\n"
            "more worker content\n\n## Tail\ntail prose")
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert stripped is True
    assert "still worker content" not in out
    assert "more worker content" not in out
    assert "## Tail" in out and "tail prose" in out
    assert out.count("```") % 2 == 0
