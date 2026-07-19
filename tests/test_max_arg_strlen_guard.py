"""Pins the MAX_ARG_STRLEN ceiling constant and the size-retry-prompt
guard (incident 2026-07-19, root cause B's two secondary sites).

Distinct from `tests/test_argv_stdin_transport.py` (the incident-shaped
end-to-end transport pin) and `tests/test_prompt_over_stdin.py` (which
already covers `_build_size_retry_prompt`'s argv-length property) — this
file's job is narrower: pin the ceiling *value* everywhere it's named in
a comment, and pin that no comment still mislabels it as the aggregate
ARG_MAX. A change to the transport seam and a change to this ceiling
constant/comment must fail independently (test-002 vs. this file).

Mirrors test_pr_writer_payload_cap.py::test_pr_writer_byte_budgets_defined.
"""
from __future__ import annotations

import inspect

# Linux's real per-argument ceiling (PAGE_SIZE * 32 on a 4KB-page kernel),
# measured directly in the incident's Colima VM. Not raisable, and
# distinct from the much larger aggregate ARG_MAX (2,097,152) that the
# stale comments this subtask guards against used to cite instead.
MAX_ARG_STRLEN = 131_071

# The aggregate ARG_MAX the pre-fix comments incorrectly cited as the
# limit. Used only to assert it is NOT what the module's comments claim
# bounds a single argv element.
ARG_MAX = 2_097_152


def test_max_arg_strlen_matches_measured_linux_value():
    """Pin the ceiling this whole guard exists to defend: 131,071 bytes,
    not the 2,097,152-byte aggregate ARG_MAX."""
    assert MAX_ARG_STRLEN == 131_071
    assert MAX_ARG_STRLEN != ARG_MAX
    assert ARG_MAX == 2_097_152


def test_build_comment_names_max_arg_strlen_value(leerie):
    """claude_p's build() — the site that removed the positional argv
    prompt — must cite the true per-argument ceiling (131,071) by value,
    not just by name, so the number itself can't silently drift."""
    src = inspect.getsource(leerie.claude_p)
    assert "131,071" in src or "131071" in src, (
        "build()'s comment must state the measured MAX_ARG_STRLEN value "
        "(131,071 bytes), not just reference the constant by name")
    assert "MAX_ARG_STRLEN" in src


def test_depcap_comments_name_max_arg_strlen_not_arg_max(leerie):
    """The _DEPCAP_TOTAL_BUDGET / _DEPCAP_MANIFEST_TOTAL_BUDGET region
    (incident note's ':18772', now corrected) must cite MAX_ARG_STRLEN
    (the real per-argument ceiling) and must not describe the budget as
    bound by the aggregate ARG_MAX."""
    src = inspect.getsource(leerie)
    idx = src.index("_DEPCAP_TOTAL_BUDGET = 307200")
    comment_block = src[max(0, idx - 700):idx]
    assert "MAX_ARG_STRLEN" in comment_block
    assert "131,071" in comment_block or "131071" in comment_block
    assert "argv" in comment_block
    assert "stdin" in comment_block, (
        "the comment must state the payload is stdin-transported, so a "
        "reader doesn't assume this budget defends an argv ceiling")


def test_pr_writer_comments_name_max_arg_strlen_not_arg_max(leerie):
    """The PR_WRITER_* byte-budget region must likewise cite
    MAX_ARG_STRLEN by value and state the stdin transport, not the
    aggregate ARG_MAX."""
    src = inspect.getsource(leerie)
    idx = src.index("PR_WRITER_COMMIT_LOG_MAX_BYTES = 80_000")
    comment_block = src[max(0, idx - 700):idx]
    assert "MAX_ARG_STRLEN" in comment_block
    assert "131,071" in comment_block or "131071" in comment_block
    assert "stdin" in comment_block


def test_depcap_budget_constants_are_pinned(leerie):
    """Pin the fix-defined per-argument byte-budget constants so a
    silent bump is caught. Since bugfix-001 moved these payloads onto
    stdin transport, they are no longer individually required to sum
    under the 131,071-byte MAX_ARG_STRLEN ceiling -- that invariant is
    obsolete now that neither payload lands on argv at all -- but a
    generous sanity ceiling still catches a runaway budget."""
    assert leerie._DEPCAP_TOTAL_BUDGET == 307_200
    assert leerie._DEPCAP_MANIFEST_TOTAL_BUDGET == 131_072
    combined = (leerie._DEPCAP_TOTAL_BUDGET
                + leerie._DEPCAP_MANIFEST_TOTAL_BUDGET)
    # Documents the incident's specific claim: this combined figure is
    # already over MAX_ARG_STRLEN on its own, which is fine *only*
    # because it's stdin-transported, never argv-bound.
    assert combined > MAX_ARG_STRLEN


def test_pr_writer_budget_constants_are_pinned(leerie):
    """Pin the PR_WRITER_* per-argument byte-budget constants."""
    assert leerie.PR_WRITER_COMMIT_LOG_MAX_BYTES == 80_000
    assert leerie.PR_WRITER_TEMPLATE_MAX_BYTES == 32_000
    assert leerie.PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES == 8_000
    combined = (leerie.PR_WRITER_COMMIT_LOG_MAX_BYTES
                + leerie.PR_WRITER_TEMPLATE_MAX_BYTES
                + leerie.PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES)
    assert combined < 128_000, (
        f"pr_writer byte budgets sum to {combined} bytes — sanity-check "
        "threshold for LLM context size. Reduce one of the caps."
    )


def test_no_argv_ceiling_constant_defined_in_module(leerie):
    """The fix's comments cite MAX_ARG_STRLEN as an external Linux fact
    (like test_prompt_over_stdin.py and test_argv_stdin_transport.py
    do), not as a leerie-owned module constant — since bugfix-001
    removed every positional-argv prompt, there is no longer any argv
    budget left in the codebase for a constant to bound. Guards against
    a future change reintroducing an argv-bound budget without also
    reintroducing this test file's ceiling checks."""
    assert not hasattr(leerie, "MAX_ARG_STRLEN")
    assert not hasattr(leerie, "_MAX_ARG_STRLEN")


def test_build_size_retry_prompt_reaches_stdin_not_argv(leerie):
    """_build_size_retry_prompt (orchestrator/leerie.py) re-appends
    original_user_prompt verbatim on top of per-offender sections --
    strictly larger than the payload that already overflowed. Assert
    its output, when fed through claude_p's build() the same way
    _spawn_reconciler feeds any other user_prompt, never lands on argv
    -- it must reach the child exclusively via stdin_data."""
    import asyncio
    import pathlib
    import types
    import unittest.mock as mock

    original_user_prompt = "z" * 150_063  # incident's exact overflow size
    assert len(original_user_prompt.encode()) > MAX_ARG_STRLEN

    oversized = [{
        "id": "feat-200",
        "title": "Bundled foundation",
        "intent": "bundle 3 capabilities",
        "provides": ["cap-x", "cap-y", "cap-z"],
        "requires": [{"tag": "some-dep", "extent": "in_plan"}],
        "depends_on": ["feat-001"],
        "size": "large",
    }]
    size_retry_prompt = leerie._build_size_retry_prompt(
        oversized, original_user_prompt)
    assert len(size_retry_prompt.encode()) > len(
        original_user_prompt.encode()), (
        "sanity: the retry prompt re-appends the original prompt on top "
        "of per-offender sections, so it must be strictly larger")

    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                           stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin_data"] = stdin_data
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )

    with mock.patch.object(leerie, "_invoke", fake_invoke), \
         mock.patch.object(leerie, "_capture_call", lambda *a, **k: None):
        asyncio.run(leerie.claude_p(
            size_retry_prompt, "system prompt here",
            schema_key="reconciler", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=st, model="opus",
            sid="size-retry-ceiling-test",
        ))

    for i, elem in enumerate(captured["cmd"]):
        assert len(elem.encode()) <= MAX_ARG_STRLEN, (
            f"argv element {i} ({elem[:80]!r}...) is "
            f"{len(elem.encode())} bytes, exceeding MAX_ARG_STRLEN "
            f"({MAX_ARG_STRLEN}) — the size-retry prompt must never "
            "land on argv")
    assert captured["stdin_data"] == size_retry_prompt
    assert not any(
        original_user_prompt in elem for elem in captured["cmd"]), (
        "the re-appended original_user_prompt must not appear anywhere "
        "in argv")
