"""DESIGN §12 L1, extended to acting workers via a path-scoped deny rule.

Judgment workers are held by the absence of `--dangerously-skip-permissions`
(#228). Acting workers cannot be: they must write files unprompted, so
`claude_p` gives them the flag on `autonomous` alone — and that flag erases the
CLI's working-directory boundary. Measured consequence, parsed out of the run
corpus: **146 `Edit`/`Write` tool calls whose `file_path` was under the user's
real checkout, across 36 runs and 4 repos, spanning 13 leerie versions** —
8.8% of the 411 runs then present, leerie's own checkout included. Measured
2026-08-20; the corpus grows, so re-derive rather than carrying this forward.

A deny rule is the one permission control that survives the flag. Probed live
(claude 2.1.237, cwd a worktree, flag ON, ground truth from the filesystem):

| configuration                       | write outside cwd | write inside cwd |
|-------------------------------------|-------------------|------------------|
| no path deny (today)                | SUCCEEDED         | succeeded        |
| `Edit(//<root>/**)` denied          | blocked           | succeeded        |

The first row reproduces the production failure; the second is this feature.
The inside-cwd column is the half that matters second: containment that also
broke a worker's own worktree would be useless.

The same probe measured the residual precisely. With the rule in place,
`sed -i` was blocked (the CLI models it as an edit), but a shell redirect,
`rm -f`, `touch` and `mkdir -p` all still wrote. That is why
`_assert_repo_unchanged` now also runs during execute — see
tests/test_work_sentinel.py.
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import types

import pytest


# The production layout: run state lives outside the checkout
# (LEERIE_STATE_DIR=/leerie-state under the launcher). See
# TestStateRootInsideTheCheckout for the fallback layout.
_OUT = "/leerie-state/runs/r1"


# ---------------------------------------------------------------------------
# the rule itself
# ---------------------------------------------------------------------------

class TestRendering:
    def test_absolute_path_gets_the_double_slash_anchor(self, leerie):
        """`//` is the CLI's anchor for an absolute filesystem path, so the
        checkout at /work becomes `//work/**`. Verified live: this exact
        string blocked the write, and its absence allowed it."""
        assert leerie._repo_write_denials("/work", _OUT) == "Edit(//work/**)"

    def test_derived_from_the_root_not_hard_coded(self, leerie):
        """A hard-coded `/work` would be wrong the moment the bind-mount
        path moves, and silently so — the rule would still be *present*
        while guarding nothing. This is the assertion that fails on a
        hard-coded constant."""
        assert leerie._repo_write_denials("/srv/checkout", _OUT) == \
            "Edit(//srv/checkout/**)"
        assert leerie._repo_write_denials("/work", _OUT) != \
            leerie._repo_write_denials("/srv/checkout", _OUT)

    def test_trailing_slash_does_not_double(self, leerie):
        assert leerie._repo_write_denials("/work/", _OUT) == "Edit(//work/**)"

    def test_accepts_a_path_object(self, leerie):
        assert leerie._repo_write_denials(pathlib.Path("/work"), _OUT) == \
            "Edit(//work/**)"

    def test_edit_subsumes_the_other_writers(self, leerie):
        """`Edit(...)` covers Write/NotebookEdit/MultiEdit per the CLI's
        documented rule syntax, so naming them separately would be noise.
        All 146 corpus mutations were Edit or Write."""
        rule = leerie._repo_write_denials("/work", _OUT)
        assert rule.startswith("Edit(")
        for redundant in ("Write(", "NotebookEdit(", "MultiEdit("):
            assert redundant not in rule


# ---------------------------------------------------------------------------
# it reaches the argv — for BOTH worker classes
# ---------------------------------------------------------------------------

def _argv_for(leerie, monkeypatch, *, schema_key, autonomous, repo_root):
    """The argv claude_p's build() closure produces (a local closure, so it
    cannot be imported — same technique as test_prompt_over_stdin.py)."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        repo_root=repo_root,
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key=schema_key,
        cwd="/leerie-state/runs/r1/worktrees/w",
        allowed_tools="Read", max_turns=40, autonomous=autonomous,
        caps=dict(leerie.DEFAULT_CAPS), st=st, model="sonnet", sid="t"))
    return captured["cmd"]


def _deny_value(argv):
    return argv[argv.index("--disallowedTools") + 1]


class TestReachesTheArgv:
    @pytest.mark.parametrize("schema_key,autonomous", [
        ("classifier", False),     # judgment: already held by L1
        ("implementer", True),     # acting: L1 unavailable — the real target
    ])
    def test_denial_is_present(self, leerie, monkeypatch,
                               schema_key, autonomous):
        argv = _argv_for(leerie, monkeypatch, schema_key=schema_key,
                         autonomous=autonomous, repo_root="/work")
        assert _deny_value(argv).endswith(",Edit(//work/**)")

    def test_the_acting_worker_really_does_carry_the_bypass_flag(
            self, leerie, monkeypatch):
        """Anti-vacuity control. If acting workers stopped carrying the flag,
        the deny rule would still pass the test above while guarding a case
        that no longer exists — and the reader would draw the wrong
        conclusion about why this feature is needed."""
        argv = _argv_for(leerie, monkeypatch, schema_key="implementer",
                         autonomous=True, repo_root="/work")
        assert "--dangerously-skip-permissions" in argv

    def test_denial_tracks_the_actual_repo_root(self, leerie, monkeypatch):
        """The load-bearing derivation test at the argv layer: a hard-coded
        `/work` passes every assertion above and fails this one."""
        argv = _argv_for(leerie, monkeypatch, schema_key="implementer",
                         autonomous=True, repo_root="/srv/other")
        assert _deny_value(argv).endswith(",Edit(//srv/other/**)")
        assert "//work/**" not in _deny_value(argv)

    def test_base_denials_are_preserved(self, leerie, monkeypatch):
        """The path rule is appended, never a replacement — dropping the
        bare-name denials would restore subagent spawning (CLAUDE.md's
        no-subagent invariant rides on this same string)."""
        argv = _argv_for(leerie, monkeypatch, schema_key="implementer",
                         autonomous=True, repo_root="/work")
        assert _deny_value(argv).startswith(leerie.DISALLOWED_TOOLS)
        assert "Task" in _deny_value(argv)


class TestDoesNotBreakTheAllowlist:
    def test_edit_is_still_allowed_wholesale(self, leerie):
        """The rule is path-scoped, not a wholesale deny. `Edit` must stay in
        ACT_TOOLS or acting workers lose file editing in their own worktree —
        the case the live probe's inside-cwd column checks."""
        assert "Edit" in leerie._bare_tool_names(leerie.ACT_TOOLS)

    def test_disjointness_invariant_still_holds(self, leerie):
        """`test_disallowed_tools.py::test_the_two_lists_are_disjoint`
        compares bare allow names against raw deny entries. A path-scoped
        entry is not a bare name, so it does not collide — pinned here
        deliberately rather than left to pass by accident of that
        asymmetry."""
        allowed = leerie._bare_tool_names(leerie.ACT_TOOLS)
        denied = {e.strip() for e in leerie.DISALLOWED_TOOLS.split(",")}
        assert not (allowed & denied)
        assert "Edit(//work/**)" not in denied, (
            "the path rule belongs on the per-call argv, not in the global "
            "constant — it is derived from each run's repo_root")


class TestWiring:
    def test_claude_p_passes_the_denial_through_the_shared_builder(
            self, leerie):
        """Not a second hand-rolled argv: containment is inherited from
        `_contained_claude_argv` by construction, which is the whole reason
        that builder has a single owner."""
        src = inspect.getsource(leerie.claude_p)
        # Whitespace-normalised: the call wraps across lines, and re-wrapping
        # it is not a regression. Both arguments are asserted — passing only
        # repo_root would restore the self-blocking bug that
        # TestStateRootInsideTheCheckout covers.
        flat = " ".join(src.split())
        assert "_repo_write_denials(st.repo_root, st.run_dir)" in flat
        assert "deny_extra=" in flat

    def test_builder_appends_rather_than_replaces(self, leerie):
        src = inspect.getsource(leerie._contained_claude_argv)
        assert "DISALLOWED_TOOLS + (" in src


class TestStateRootInsideTheCheckout:
    """`resolve_leerie_root` falls back to `repo_root / ".leerie"` whenever
    `LEERIE_STATE_DIR` is unset — the direct-invocation and test path. Worker
    worktrees then live *inside* the checkout, so a blanket deny under it
    would deny each worker its own worktree and every implementer would fail
    silently. A deny rule cannot carry a carve-out, so the denial is skipped
    and announced.
    """

    def test_no_denial_when_run_state_is_inside_the_checkout(self, leerie):
        assert leerie._repo_write_denials(
            "/repo", "/repo/.leerie/runs/r1") == ""

    def test_no_denial_when_run_dir_equals_the_checkout(self, leerie):
        assert leerie._repo_write_denials("/repo", "/repo") == ""

    def test_the_normal_layout_still_gets_one(self, leerie):
        """Anti-vacuity partner. Without this the skip could be implemented as
        `return ""` and every test above would still pass."""
        assert leerie._repo_write_denials(
            "/repo", "/leerie-state/runs/r1") == "Edit(//repo/**)"

    def test_a_sibling_directory_is_not_inside(self, leerie):
        """`/repo-state` shares a string prefix with `/repo` but is not under
        it. A prefix comparison instead of a path one would skip the denial
        here and quietly disable containment."""
        assert leerie._repo_write_denials(
            "/repo", "/repo-state/runs/r1") == "Edit(//repo/**)"

    def test_the_skip_is_announced_not_silent(self, leerie, monkeypatch):
        """Silent would leave the operator believing acting workers are
        confined when they are not."""
        monkeypatch.setattr(leerie, "_denial_skipped_warned", False)
        msgs = []
        monkeypatch.setattr(leerie, "log", lambda m: msgs.append(m))
        leerie._repo_write_denials("/repo", "/repo/.leerie/runs/r1")
        assert any("NOT confined" in m for m in msgs), msgs
        assert any("LEERIE_STATE_DIR" in m for m in msgs), msgs

    def test_the_warning_fires_at_most_once_per_process(
            self, leerie, monkeypatch):
        monkeypatch.setattr(leerie, "_denial_skipped_warned", False)
        msgs = []
        monkeypatch.setattr(leerie, "log", lambda m: msgs.append(m))
        for _ in range(3):
            leerie._repo_write_denials("/repo", "/repo/.leerie/runs/r1")
        assert len(msgs) == 1, msgs
