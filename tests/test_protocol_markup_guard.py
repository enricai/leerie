"""Leaked tool-call markup in structured output, and the strict-output CLI floor.

DESIGN §7 *Forcing constrained decoding*: on CLI 2.1.280 with `strict: true`
forced, the model's own tool-call markup was decoded INTO a string field
(`…contract.</defect_shape><parameter name="sites">[…`) and the remaining
fields came out empty — schema-valid, content destroyed. A recorded
v0.36.0 `defect_scope_auditor` call leaked 4/4 with the proxy, 0/4 without,
and 0/4 with the proxy on CLI 2.1.289.

Two layers, both pinned here:
  - `claude_p` treats leaked protocol markup in ANY worker's structured output
    as a schema miss: one corrective re-prompt, then the ordinary
    `WorkerError`. The token set is wire syntax, not prose (DESIGN §12
    *Language-to-JSON*).
  - `--dangerously-force-strict-output` refuses a CLI below
    `MIN_CLAUDE_CLI_STRICT_OUTPUT` at startup.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from tests.conftest import init_git_repo


# The shape the v0.36.0 auditor actually emitted (field names are the
# auditor's own schema properties; the content is synthetic).
_LEAKED_SHAPE = (
    "The contract: a declared field must reach the request body."
    "</defect_shape>\n<parameter name=\"sites\">[{\"file\": \"src/example.py\"}]")


def _auditor_props(leerie):
    return leerie._schema_property_names(leerie.SCHEMAS["defect_scope_auditor"])


# --- the detector ----------------------------------------------------------

@pytest.mark.parametrize("token", [
    "antml:parameter", '<parameter name="sites">', "</parameter>",
    '<invoke name="StructuredOutput">', "</invoke>",
])
def test_each_fixed_protocol_token_is_detected(leerie, token):
    s = f"some value {token} more"
    assert leerie._find_protocol_markup(s) == s


def test_closing_tag_named_after_own_schema_property_is_detected(leerie):
    props = _auditor_props(leerie)
    assert "defect_shape" in props and "sites" in props
    assert leerie._find_protocol_markup(_LEAKED_SHAPE, props) == _LEAKED_SHAPE
    # Without the `<parameter` half, the bare closing tag alone still hits.
    bare = "text that ends</defect_shape>"
    assert leerie._find_protocol_markup(bare, props) == bare


def test_closing_tag_for_an_unrelated_name_is_not_a_hit(leerie):
    """`</div>` in an HTML-ish snippet is not a schema property of the worker
    — only the worker's own field names identify the leak."""
    props = _auditor_props(leerie)
    assert leerie._find_protocol_markup("render <b>x</b> in a </div>", props) is None


def test_backtick_quoted_protocol_text_is_not_a_hit(leerie):
    """A worker on a repo that is itself about this protocol quotes it as
    code; the one legitimate hit in the corpus audit had exactly this shape."""
    s = "the file has no `antml:` corruption detection and no `</sites>` tag"
    assert leerie._find_protocol_markup(s, _auditor_props(leerie)) is None


def test_ordinary_markup_named_like_a_schema_field_is_not_a_hit(leerie):
    """Round-1 M5: `summary`, `title`, `name` are schema properties AND
    ordinary markup. A closing tag counts only where leaked syntax sits — at
    the end of the value or before another tool/schema tag — and fenced code
    is ignored. Measured 2026-10-04, that dropped none of the 2,395 hits in
    26,663 recorded responses."""
    pr = leerie._schema_property_names(leerie.SCHEMAS["pr_writer"])
    impl = leerie._schema_property_names(leerie.SCHEMAS["implementer"])
    body = "Adds a page:\n```html\n<title>Orders</title>\n```\nand tests."
    assert leerie._find_protocol_markup({"title": "t", "body": body}, pr) is None
    assert leerie._find_protocol_markup(
        "see <details><summary>Log</summary> for more", impl) is None
    assert leerie._find_protocol_markup(
        "fenced:\n```\n<parameter name=\"x\">y</parameter>\n```", impl) is None
    # A fence must start a line: an unpaired backtick or a mid-line ```
    # cannot swallow a leak (round-2 review).
    assert leerie._find_protocol_markup(
        'x ``` leaked</summary>\n<parameter name="files">[{"note": "see ```y```"}]',
        impl)
    # The leaked shapes still hit.
    assert leerie._find_protocol_markup("done</summary>\n", impl)
    assert leerie._find_protocol_markup(
        'done</summary>\n<parameter name="files">', impl)


def test_detection_recurses_into_nested_structures(leerie):
    props = _auditor_props(leerie)
    out = {"applicable": True, "sites": [{"note": "fine"},
                                         {"note": _LEAKED_SHAPE}]}
    assert leerie._find_protocol_markup(out, props) == _LEAKED_SHAPE
    assert leerie._find_protocol_markup(
        {"applicable": True, "sites": [{"note": "clean"}]}, props) is None
    assert leerie._find_protocol_markup(None, props) is None
    assert leerie._find_protocol_markup(7, props) is None


def test_schema_property_names_walks_nested_items(leerie):
    props = _auditor_props(leerie)
    # nested in sites[].items and ground_truth.inputs[].items
    assert {"file", "symbol", "role", "resolved_path"} <= props


def test_validate_result_uses_the_shared_detector(leerie):
    """The implementer's `corrupted_envelope` arm now delegates to the shared
    helper, so it also catches the `<parameter name=` shape, not only
    `antml:`."""
    res = {"subtask_id": "bugfix-001", "status": "complete",
           "summary": 'done</summary>\n<parameter name="files_touched">[]'}
    assert leerie._validate_result(res)[0] == "corrupted_envelope"


# --- claude_p: a leak is a schema miss --------------------------------------

class _FakeState:
    def __init__(self, tmp_path):
        self.path = tmp_path / "runs" / "r1" / "state.json"
        self.run_dir = self.path.parent
        self.repo_root = "/leerie-test-user-repo"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "r1"
        self.data = {"verbosity": "quiet"}

    def bump_workers(self, *a, **k):
        pass

    def add_telemetry(self, *a, **k):
        pass


def _envelope(structured):
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": "{}", "structured_output": structured}


def _run_auditor_call(leerie, monkeypatch, tmp_path, outputs):
    prompts: list[str] = []
    seq = [_envelope(o) for o in outputs]

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **kwargs):
        prompts.append(stdin_data or "")
        return seq.pop(0)

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)

    async def run():
        return await leerie.claude_p(
            "audit this defect", "you are the auditor",
            schema_key="defect_scope_auditor", cwd="/work",
            allowed_tools="Read", max_turns=40, autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS), st=_FakeState(tmp_path),
            model="sonnet", sid="defect_scope_auditor")

    try:
        return asyncio.run(run()), prompts
    except leerie.WorkerError as e:
        return e, prompts


_CLEAN = {"applicable": True, "defect_shape": "a contract",
          "sites": [{"file": "src/example.py", "symbol": "f", "role": "decision_site"}]}
_LEAKED = {"applicable": True, "defect_shape": _LEAKED_SHAPE, "sites": []}


def test_claude_p_reprompts_a_leaked_answer_and_returns_the_clean_retry(
        leerie, monkeypatch, tmp_path):
    out, prompts = _run_auditor_call(leerie, monkeypatch, tmp_path,
                                     [_LEAKED, _CLEAN])
    assert out == _CLEAN
    assert len(prompts) == 2
    assert "YOUR PREVIOUS ATTEMPT FAILED" in prompts[1]
    assert "leaked tool-call markup" in prompts[1]


def test_claude_p_raises_worker_error_when_both_attempts_leak(
        leerie, monkeypatch, tmp_path):
    out, prompts = _run_auditor_call(leerie, monkeypatch, tmp_path,
                                     [_LEAKED, _LEAKED])
    assert isinstance(out, leerie.WorkerError)
    assert "leaked tool-call markup" in str(out)
    assert len(prompts) == 2


def test_claude_p_returns_a_clean_answer_on_the_first_attempt(
        leerie, monkeypatch, tmp_path):
    out, prompts = _run_auditor_call(leerie, monkeypatch, tmp_path, [_CLEAN])
    assert out == _CLEAN and len(prompts) == 1


# --- strict-output CLI floor -------------------------------------------------

def _stub_version(tmp_path, monkeypatch, version_line):
    bindir = tmp_path / "verbin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version_line}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_strict_floor_value(leerie):
    assert leerie.MIN_CLAUDE_CLI_STRICT_OUTPUT == (2, 1, 289)
    # Deliberately not folded into the general floor.
    assert leerie.MIN_CLAUDE_CLI < leerie.MIN_CLAUDE_CLI_STRICT_OUTPUT


def test_strict_floor_dies_below(leerie, tmp_path, monkeypatch, capsys):
    _stub_version(tmp_path, monkeypatch, "2.1.280 (Claude Code)")
    with pytest.raises(SystemExit):
        leerie._check_strict_output_cli_version()
    err = capsys.readouterr().err
    assert "2.1.280" in err and "2.1.289" in err


def test_strict_floor_passes_at_floor(leerie, tmp_path, monkeypatch):
    _stub_version(tmp_path, monkeypatch, "2.1.289 (Claude Code)")
    leerie._check_strict_output_cli_version()


def test_strict_floor_defers_on_unrecognized_output(leerie, tmp_path, monkeypatch):
    _stub_version(tmp_path, monkeypatch, "not a version")
    leerie._check_strict_output_cli_version()


def test_main_refuses_strict_output_on_an_old_cli(leerie, monkeypatch, tmp_path,
                                                 capsys):
    """Behavioural: real `main()` with the flag on and a 2.1.280 CLI dies at
    the floor (not at some unrelated earlier gate)."""
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    repo = tmp_path / "repo"
    init_git_repo(repo)
    _stub_version(tmp_path, monkeypatch, "2.1.280 (Claude Code)")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.setattr(sys, "argv", ["leerie", "--run-id", "run-strict-old",
                                      "a task", "--dangerously-force-strict-output"])
    with pytest.raises(SystemExit):
        leerie.main()
    assert "needs claude CLI >= 2.1.289" in capsys.readouterr().err
