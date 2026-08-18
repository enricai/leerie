"""Every `claude -p` argv leerie builds carries containment — derived, not listed.

#216 ("Cut MCP exposure and widen worker tool containment") widened
`DISALLOWED_TOOLS` and added `--strict-mcp-config`, auditing the invocation
sites BY HAND: it found the shell one (`scripts/remote/collect-subtrees.sh`)
and missed the Python one (`preflight`'s smoke test). That site then ran with
the CLI's full default surface — measured on run 7432b2b4, `system/init`
reported **78 tools / 4 mcp_servers**, of which 46 were `mcp__claude_ai_*`
(`send_message`, `forward`, `trash_thread`, `slack_send_message` …) and the
remaining 32 included every tool the deny list exists to remove.

Measured cost of each half, isolated on CLI 2.1.234 (identical argv otherwise,
one variable changed per run, fresh empty cwd, per-first-turn prompt tokens):

    bare (the old smoke shape)      54 tools, 27 mcp   34,108 tok
    + --strict-mcp-config           27 tools,  0 mcp   33,228 tok
    + --disallowedTools/--allowed    8 tools,  0 mcp   17,222 tok
    the same contained argv, but run inside this repo  126,022 tok

The as-shipped argv (which also denies Task and the three MCP-resource
tools, added in the same change; NotebookEdit was denied too and reverted —
see ACT_TOOLS) measures 7 tools / 0 mcp / 16,590 tokens /
$0.1063 in an empty cwd, 3 turns, is_error False — against the failing run's
78 tools / 46 mcp / 183,485 tokens / $1.8389 / refused.

So `--strict-mcp-config` is worth ~880 tokens and is a containment fix, not a
size fix; `--disallowedTools` is worth ~16,000; and the repo cwd is worth
~108,800 — which is why the smoke test now runs in an empty directory.

This file is the SINGLE OWNER of the rule. `tests/test_strict_mcp_config.py`
and two tests in `tests/test_disallowed_tools.py` previously asserted it by
source-slicing `claude_p`'s body, which structurally cannot observe any other
call site — the exact construction that let this ship. They are deleted.

SCOPE: `orchestrator/leerie.py` (AST) and `scripts/**/*.sh` (text). That was
verified to cover every production invocation — the `leerie` launcher,
`chain/*.py` and `commands/*.md` mention `claude -p` only in prose. The one
other real invocation in the tree is `tests/manual/planner_fence_probe.py`,
which `pytest.ini`'s `python_files = test_*.py` never collects and whose whole
purpose is measuring raw, UNCONTAINED prompt behaviour — containment flags
would change what it measures, so it is excluded by design, not by oversight.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"

# The builder every live `claude -p` argv must come from.
BUILDER = "_contained_claude_argv"

# The one legitimate exemption: this probe asks the CLI whether it accepts a
# flag. It passes `input=""` and exits before any session or model call
# (commander rejects the flag, or the CLI errors "Input must be provided"), so
# containment flags would only make the probe's accuracy depend on flag-parse
# order. Annotated here rather than pattern-matched, so a NEW hand-rolled argv
# cannot inherit the exemption by resembling this one.
EXEMPT_FUNCTIONS = {"_append_system_prompt_file_supported"}

REQUIRED_SHELL_FLAGS = (
    "--strict-mcp-config", "--disallowedTools", "--allowedTools",
    "--max-turns", "--model", "--output-format",
)


def _hand_rolled_argv_sites(source: str) -> list[str]:
    """Enclosing function name for every `["claude", "-p", ...]` list literal.

    Derived from the AST rather than enumerated: a new call site is caught
    with no test edit. Nested functions resolve to the outermost enclosing
    def, which is what the exemption list names.
    """
    tree = ast.parse(source)
    sites: list[str] = []

    def walk(node: ast.AST, enclosing: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            name = enclosing
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = enclosing or child.name
            if isinstance(child, ast.List) and len(child.elts) >= 2:
                first, second = child.elts[0], child.elts[1]
                if (isinstance(first, ast.Constant) and first.value == "claude"
                        and isinstance(second, ast.Constant)
                        and second.value == "-p"):
                    sites.append(name or "<module>")
            walk(child, name)

    walk(tree, None)
    return sites


def test_no_hand_rolled_claude_p_argv_in_python():
    """Only the builder (and the annotated probe) may construct one."""
    sites = _hand_rolled_argv_sites(LEERIE_PY.read_text())
    offenders = sorted(set(sites) - {BUILDER} - EXEMPT_FUNCTIONS)
    assert not offenders, (
        f"{offenders} hand-roll a `claude -p` argv instead of calling "
        f"{BUILDER}() — a site built by hand silently misses the tool denies "
        "and --strict-mcp-config, which is how the preflight smoke test came "
        "to run with 46 mcp__claude_ai_* tools"
    )


def test_the_scan_finds_the_known_sites():
    """Anti-vacuity: a walker that matches nothing would certify everything."""
    sites = _hand_rolled_argv_sites(LEERIE_PY.read_text())
    assert len(sites) >= 2, f"expected >=2 argv sites, found {sites}"
    assert BUILDER in sites, f"{BUILDER} must build one, found {sites}"
    for exempt in EXEMPT_FUNCTIONS:
        assert exempt in sites, (
            f"{exempt} is exempted but the scan no longer finds an argv in "
            "it — the exemption is now stale and hides nothing"
        )


def test_the_scan_can_find_a_reproduction():
    """Guard-the-guard: plant a hand-rolled site, prove the walker fires."""
    planted = (
        "def some_new_worker():\n"
        "    cmd = ['claude', '-p', 'do a thing', '--output-format', 'json']\n"
        "    return cmd\n"
    )
    assert "some_new_worker" in _hand_rolled_argv_sites(planted)
    # …and that a legitimate non-argv list is not mistaken for one.
    assert _hand_rolled_argv_sites("x = ['claude', 'other']\n") == []


def _shell_claude_p_invocations() -> list[tuple[str, str]]:
    """(path, spliced command) for every `claude -p` in scripts/**/*.sh."""
    out: list[tuple[str, str]] = []
    for sh in sorted((REPO_ROOT / "scripts").rglob("*.sh")):
        text = sh.read_text()
        # Splice backslash continuations so a multi-line invocation is one
        # string, then drop comment-only lines.
        spliced = re.sub(r"\\\n\s*", " ", text)
        for line in spliced.splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = re.search(r"\bclaude\s+-p\b", line)
            if not match:
                continue
            # Discriminate an INVOCATION from a MENTION. `scripts/install.sh`
            # says "Leerie shells out to `claude -p` for every unit of LLM
            # work" inside an err() string; matching that would make this
            # test unsatisfiable and invite weakening it. An odd number of
            # unescaped double quotes before the match means we are inside a
            # quoted string, i.e. prose.
            before = line[:match.start()]
            if len(re.findall(r'(?<!\\)"', before)) % 2 == 1:
                continue
            out.append((str(sh.relative_to(REPO_ROOT)), line))
    return out


def test_shell_claude_p_sites_are_contained():
    for path, cmd in _shell_claude_p_invocations():
        missing = [f for f in REQUIRED_SHELL_FLAGS if f not in cmd]
        assert not missing, f"{path}: `claude -p` missing {missing}"


def test_the_shell_scan_finds_the_known_site():
    """Anti-vacuity for the shell half."""
    found = _shell_claude_p_invocations()
    assert found, "expected at least one `claude -p` invocation under scripts/"
    assert any("collect-subtrees.sh" in p for p, _ in found), (
        f"collect-subtrees.sh must be among the scanned sites, got {found}"
    )


# --- substance: execute the builder, assert VALUES sit beside their flags ---

def _adjacent(argv: list[str], flag: str) -> str:
    assert flag in argv, f"{flag} missing from {argv}"
    return argv[argv.index(flag) + 1]


def test_builder_places_each_value_beside_its_flag(leerie):
    """Adjacency, not membership: a value moved away from its flag passes a
    membership check and changes what the CLI actually receives."""
    argv = leerie._contained_claude_argv(
        schema='{"type":"object"}', allowed_tools="Read", max_turns=7,
        model="sonnet")
    assert _adjacent(argv, "--disallowedTools") == leerie.DISALLOWED_TOOLS
    assert _adjacent(argv, "--allowedTools") == "Read"
    assert _adjacent(argv, "--max-turns") == "7"
    assert _adjacent(argv, "--model") == "sonnet"
    assert _adjacent(argv, "--json-schema") == '{"type":"object"}'
    assert _adjacent(argv, "--output-format") == "stream-json"
    assert "--strict-mcp-config" in argv
    assert "--mcp-config" not in argv, (
        "--mcp-config alongside --strict-mcp-config would strand the caller "
        "with server config it cannot use as strictly as intended"
    )


def test_builder_omits_the_positional_prompt_by_default(leerie):
    """A positional prompt silently wins over stdin, and claude_p routes the
    user prompt over stdin because one argv element cannot exceed
    MAX_ARG_STRLEN."""
    argv = leerie._contained_claude_argv(
        schema="{}", allowed_tools="Read", max_turns=1, model="sonnet")
    assert argv[:2] == ["claude", "-p"]
    assert argv[2].startswith("--"), (
        f"expected a flag immediately after -p, got positional {argv[2]!r}")


def test_builder_emits_the_positional_only_when_asked(leerie):
    argv = leerie._contained_claude_argv(
        schema="{}", allowed_tools="Read", max_turns=1, model="sonnet",
        prompt="hello")
    assert argv[2] == "hello"


def test_model_goes_through_model_arg_under_the_strict_proxy(leerie,
                                                            monkeypatch):
    """The one call site that most needs the 1M restoration had no --model to
    rewrite. `[1m]` must appear when the proxy owns ANTHROPIC_BASE_URL."""
    monkeypatch.setattr(leerie, "_STRICT_PROXY", None)
    argv = leerie._contained_claude_argv(
        schema="{}", allowed_tools="Read", max_turns=1, model="sonnet")
    assert _adjacent(argv, "--model") == "sonnet"

    monkeypatch.setattr(leerie, "_STRICT_PROXY", object())
    argv = leerie._contained_claude_argv(
        schema="{}", allowed_tools="Read", max_turns=1, model="sonnet")
    assert _adjacent(argv, "--model") == "sonnet[1m]", (
        "under the strict proxy the CLI treats the session as gateway-routed "
        "and lowers its client-side context ceiling; [1m] restores it")


def _worker_argv(leerie, monkeypatch) -> list[str]:
    """The argv `claude_p`'s build() closure produces (it is a local closure,
    so it cannot be imported — same technique as
    tests/test_prompt_over_stdin.py::_build_cmd)."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = SimpleNamespace(
        path=Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=Path("/tmp/leerie-test-nonexistent"),
        data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None,
    )
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    # Without this, claude_p's capability probe shells out to the real
    # `claude` binary and memoizes the answer into a module global that the
    # session-scoped `leerie` fixture shares with every other test.
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key="classifier", cwd="/work", allowed_tools="Read",
        max_turns=40, autonomous=False, caps=dict(leerie.DEFAULT_CAPS),
        st=st, model="sonnet", sid="argv-test"))
    return captured["cmd"]


def _smoke_argv(leerie, monkeypatch, tmp_path) -> list[str]:
    """The argv `preflight`'s smoke test produces."""
    captured: dict = {}

    async def fake_invoke(cmd, **_kw):
        captured["cmd"] = list(cmd)
        return {"type": "result", "is_error": False, "result": "ok"}

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda _p: 0.9)
    asyncio.run(leerie.preflight(tmp_path, skip_smoke=False, model="sonnet"))
    return captured["cmd"]


def test_both_sites_share_the_builder(leerie, monkeypatch, tmp_path):
    """The shared-owner proof, in both directions.

    An earlier version of this test only crippled the builder and asserted the
    flag was ABSENT from the smoke argv — which the pre-fix hand-rolled argv
    also satisfied, because missing that flag *was* the bug. A negative
    assertion the defect already satisfies proves nothing, so the positive
    control below is the load-bearing half: with the builder intact the flag
    must be present on BOTH argvs, and it must disappear from BOTH when the
    single owner is broken.
    """
    # (a) positive control — the flag really is on both argvs.
    worker = _worker_argv(leerie, monkeypatch)
    smoke = _smoke_argv(leerie, monkeypatch, tmp_path)
    assert "--strict-mcp-config" in worker, worker
    assert "--strict-mcp-config" in smoke, smoke
    for argv in (worker, smoke):
        assert argv[argv.index("--disallowedTools") + 1] == \
            leerie.DISALLOWED_TOOLS

    # (b) break the single owner — BOTH must lose it. A site that rebuilt its
    #     own argv would keep the flag here and fail.
    real = leerie._contained_claude_argv

    def crippled(**kw):
        return [a for a in real(**kw) if a != "--strict-mcp-config"]

    monkeypatch.setattr(leerie, "_contained_claude_argv", crippled)
    worker_c = _worker_argv(leerie, monkeypatch)
    smoke_c = _smoke_argv(leerie, monkeypatch, tmp_path)
    assert "--strict-mcp-config" not in worker_c, (
        "claude_p built its own argv instead of going through the builder")
    assert "--strict-mcp-config" not in smoke_c, (
        "preflight built its own argv instead of going through the builder")


# --- the smoke test's own argv, cwd, and error path ---

def _run_preflight(leerie, monkeypatch, tmp_path, envelope=None, model="sonnet"):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **_kw):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["listdir"] = os.listdir(cwd)
        captured["isdir"] = os.path.isdir(cwd)
        return envelope or {"type": "result", "is_error": False,
                            "result": "ok"}

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda _p: 0.9)
    asyncio.run(leerie.preflight(tmp_path, skip_smoke=False, model=model))
    return captured


def test_smoke_argv_equals_the_derived_builder_output(leerie, monkeypatch,
                                                      tmp_path):
    """Compared against the builder's own output, never a written-down list —
    a hard-coded expectation would have to be edited in lockstep and would
    stop failing the moment someone edited it."""
    cap = _run_preflight(leerie, monkeypatch, tmp_path)
    expected = leerie._contained_claude_argv(
        schema=leerie.SMOKE_SCHEMA, allowed_tools=leerie.SMOKE_TOOLS,
        max_turns=leerie.SMOKE_MAX_TURNS, model="sonnet",
        prompt=leerie.SMOKE_PROMPT)
    assert cap["cmd"] == expected


def test_smoke_runs_in_an_empty_directory(leerie, monkeypatch, tmp_path):
    """Empty is necessary but NOT sufficient — see the outside-the-repo test
    below, which is the property that actually bounds the prompt."""
    cap = _run_preflight(leerie, monkeypatch, tmp_path)
    assert cap["cwd"] != os.getcwd()
    assert cap["listdir"] == [], (
        f"smoke cwd must be empty, held {cap['listdir']}")
    assert cap["isdir"] is True


def test_smoke_cwd_has_no_repository_ancestor(leerie, monkeypatch, tmp_path):
    """THE load-bearing property. Claude Code resolves project context by
    walking UP from cwd, so emptiness buys nothing if the directory sits
    inside a checkout — the repo's CLAUDE.md/skills load anyway.

    The state root is not a safe home for it: `resolve_leerie_root` falls back
    to `<repo>/.leerie` whenever LEERIE_STATE_DIR is unset, so a cwd derived
    from it lands INSIDE the repo on exactly the direct-Python path that
    fallback exists for — while still being empty and still differing from
    `os.getcwd()`, so every other assertion here would keep passing.
    """
    leerie_dir = tmp_path / "repo" / ".leerie" / "runs" / "run-1"
    leerie_dir.mkdir(parents=True)
    cwd = Path(_run_preflight(leerie, monkeypatch, leerie_dir)["cwd"]).resolve()
    repo = (tmp_path / "repo").resolve()
    assert repo not in cwd.parents and cwd != repo, (
        f"smoke cwd {cwd} sits inside the repo at {repo} — the CLI walks up "
        "from cwd, so its CLAUDE.md and skills load and the prompt bound this "
        "change exists for is lost")


def test_smoke_cwd_is_stable_across_runs_of_one_state_root(leerie, monkeypatch,
                                                           tmp_path):
    """Two DIFFERENT run dirs under ONE state root must share the cwd.

    Passing the same `leerie_dir` twice would make this hold for any pure
    function of it — including a per-run directory, which is the orphaned
    `~/.claude/projects` entry this pins against.
    """
    root = tmp_path / "state"
    a = root / "runs" / "run-a"
    b = root / "runs" / "run-b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    first = _run_preflight(leerie, monkeypatch, a)["cwd"]
    second = _run_preflight(leerie, monkeypatch, b)["cwd"]
    assert first == second, (
        f"two runs of one state root must share the smoke cwd, got {first!r} "
        f"then {second!r} — a per-run path leaves one orphaned CLI session "
        "directory per run")


def test_smoke_cwd_differs_across_state_roots(leerie, monkeypatch, tmp_path):
    """...and two different state roots must NOT share it, or concurrent runs
    of two repos would collide on one directory."""
    one = tmp_path / "s1" / "runs" / "r"
    two = tmp_path / "s2" / "runs" / "r"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    assert (_run_preflight(leerie, monkeypatch, one)["cwd"]
            != _run_preflight(leerie, monkeypatch, two)["cwd"])


def test_smoke_max_turns_exceeds_the_measured_happy_path(leerie):
    """Measured: 3 turns (text answer, the CLI's synthetic
    [structured-output-enforce] turn, the StructuredOutput call). A cap of 2
    would die() on every healthy preflight."""
    assert leerie.SMOKE_MAX_TURNS > 3


# The verbatim result envelope from run 7432b2b4's logs/smoke.log.
INCIDENT_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": True,
    "terminal_reason": "blocking_limit", "stop_reason": "stop_sequence",
    "api_error_status": None, "result": "Prompt is too long", "num_turns": 2,
}


def test_context_overflow_is_classified_not_printed_bare(leerie, monkeypatch,
                                                         tmp_path):
    with pytest.raises(leerie.ContextOverflow):
        _run_preflight(leerie, monkeypatch, tmp_path,
                       envelope=INCIDENT_ENVELOPE)


def test_a_generic_error_still_dies_with_the_log_path(leerie, monkeypatch,
                                                      tmp_path):
    env = {"type": "result", "is_error": True, "api_error_status": 401,
           "result": "Invalid authentication", "terminal_reason": None}
    messages: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: messages.append(m))
    real_die = leerie.die

    def capturing_die(msg, code=1):
        messages.append(msg)
        real_die(msg, code)

    monkeypatch.setattr(leerie, "die", capturing_die)
    with pytest.raises(SystemExit):
        _run_preflight(leerie, monkeypatch, tmp_path, envelope=env)
    joined = "\n".join(messages)
    # The name of this test is the assertion: without these, deleting the
    # interpolation leaves it green.
    assert "smoke.log" in joined, joined
    assert "terminal_reason" in joined, joined


def test_max_turns_refusal_is_not_treated_as_context_overflow(leerie,
                                                              monkeypatch,
                                                              tmp_path):
    """Disjointness. Keying on the result text alone would misroute this —
    the trap `_is_context_overflow`'s own docstring records."""
    env = dict(INCIDENT_ENVELOPE, terminal_reason="max_turns")
    with pytest.raises(SystemExit):
        _run_preflight(leerie, monkeypatch, tmp_path, envelope=env)


def test_call_site_passes_the_resolved_classifier_model():
    """A defaulted parameter nothing passes is an inert knob — the shape
    tests/test_no_dead_resolutions.py exists for."""
    src = LEERIE_PY.read_text()
    start = src.index("await preflight(")
    # Balanced-paren scan: the call contains nested calls
    # (getattr(args, "no_push", False)), so stopping at the first ")"
    # truncates mid-argument and the assertion fails against correct code.
    depth = 0
    for i in range(src.index("(", start), len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                break
    call = src[start:i + 1]
    assert 'model=models["classifier"]' in call, (
        "preflight must be handed the tier the run's first worker uses; "
        f"got: {call!r}")
