"""The rebaser verdict travels on its own channel, never shared with the log.

`run_rebaser` calls `claude_p`, and leerie's `log()` is a bare
`print(..., flush=True)` to **stdout**. `scripts/host-finalize.sh` used to
capture the seam as `_rebaser_json="$(python3 … 2>&1)"` and feed that to `jq`,
so `jq` received several hundred lines of worker log with the JSON at the end.
It returned rc 5 every time and control fell to the case statement's `*)` arm.

Measured against a real state directory before the fix:
`rebase_disposition_status` was `"unusable"` in **9 of 9** runs that ever
reached the rebaser, and `rebase_disposition_raw_json` held log text rather
than JSON in every one. The `rebased` and `irreconcilable|failed` arms had
therefore NEVER executed, so a rebaser returning a valid
`{"status": "failed", …}` with a full conflict diagnosis had that diagnosis
silently dropped instead of folded into the PR body.

The tests here drive the REAL seam — extracted from the script rather than
reproduced — against a stand-in orchestrator module, so a future edit that
reintroduces a shared channel fails. `test_pre_fix_shared_channel_does_not_parse`
is the falsification control: it replays the old combined-stream shape and
proves `jq` chokes on it, so the passing cases above it are not vacuous.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"

# Log lines shaped like leerie's own `log()` output, which is what actually
# polluted the channel in production.
_LOG_NOISE = [
    "2026-08-14T04:26:06+00:00 [leerie] [repo]   [rebaser-abc123] spawned (pid=2841642)",
    "2026-08-14T04:26:08+00:00 [leerie] [repo]   [rebaser-abc123] starting (model=claude-sonnet-5)",
    "2026-08-14T04:26:12+00:00 [leerie] [repo]   [rebaser-abc123 bash] git status && git fetch origin main",
]

_VERDICT = {
    "status": "rebased",
    "diagnosis": "",
    "resolution_summary": "Rebased 21 commits onto origin/main with no conflicts.",
}


def _code_only(src: str) -> str:
    """Drop whole-line `#` comments before scanning for forbidden constructs.

    The region these tests guard is dense with comments that necessarily NAME
    the constructs they forbid (`2>&1`, `head -c 2000`) in order to explain why
    they are wrong — so a raw substring scan matches the prose describing the
    defect and fails on correct code. This is the same trap the zombie-reaper
    and `unreviewed_subtasks` guards document in CLAUDE.md.

    Whole-line only: a `#` mid-line may sit inside a string literal, and the
    constructs at issue always appear as their own statements.
    """
    return "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )


def _extract_rebaser_seam() -> str:
    """Pull the real `<<'PY' … PY` seam body out of host-finalize.sh.

    Extracted, never reproduced: a hand-copied seam is body-blind by
    construction, so no change to the script could reach these tests
    (see CLAUDE.md on `_resolve_ec2_knob`'s hand-copied harness).
    """
    src = HOST_FINALIZE_SH.read_text()
    m = re.search(r"cat > \"\$_rebaser_py\" <<'PY'\n(.*?)\nPY\n", src, re.S)
    assert m, "could not find the rebaser python seam heredoc in host-finalize.sh"
    return m.group(1)


def _fake_orchestrator(tmp_path: Path) -> Path:
    """A stand-in for orchestrator/leerie.py exposing `run_rebaser`.

    It prints log-shaped lines to stdout exactly as the real `log()` does, so
    the seam is exercised against the condition that broke it. Driving the real
    orchestrator here would mean spawning a live `claude -p` worker.
    """
    p = tmp_path / "fake_orch.py"
    noise = "\n".join(f'print({line!r}, flush=True)' for line in _LOG_NOISE)
    p.write_text(
        "import json\n\n\n"
        "def run_rebaser(leerie_root, repo_root, run_id, worktree,\n"
        "                run_branch, working_branch, pr_base_branch):\n"
        f"    {noise.replace(chr(10), chr(10) + '    ')}\n"
        f"    return {_VERDICT!r}\n"
    )
    return p


def _run_seam(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    seam = tmp_path / "rebaser.py"
    seam.write_text(_extract_rebaser_seam())
    out = tmp_path / "rebaser.json"
    proc = subprocess.run(
        ["python3", str(seam),
         str(tmp_path / "state"), str(tmp_path / "repo"), "run-abc123",
         str(tmp_path / "worktree"), "leerie/runs/abc123", "main", "main",
         str(_fake_orchestrator(tmp_path)), str(out)],
        capture_output=True, text=True,
    )
    return proc, out


def test_seam_writes_the_verdict_to_its_own_file(tmp_path: Path) -> None:
    proc, out = _run_seam(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert out.exists(), "the seam must write its verdict to the argv path"
    assert json.loads(out.read_text()) == _VERDICT


def test_the_verdict_never_reaches_stdout(tmp_path: Path) -> None:
    """The load-bearing separation: log on stdout, verdict in the file.

    If the seam ever prints the JSON again, a caller capturing stdout gets the
    old mixed stream back even though the file also exists.
    """
    proc, _ = _run_seam(tmp_path)
    assert '"status"' not in proc.stdout, (
        "the verdict must not be printed to stdout — that is the channel the "
        f"worker's log stream owns. stdout was:\n{proc.stdout}"
    )
    # Anti-vacuity: stdout is not simply empty, so the assertion above is
    # actually discriminating between two populated streams.
    assert _LOG_NOISE[0] in proc.stdout, (
        "the log stream must still reach stdout; an empty stdout would make "
        "the assertion above pass for the wrong reason"
    )


@pytest.mark.skipif(not HAS_JQ, reason="needs real jq, as host-finalize.sh uses")
def test_jq_parses_the_verdict_file(tmp_path: Path) -> None:
    """What host-finalize.sh actually does with the payload."""
    _, out = _run_seam(tmp_path)
    r = subprocess.run(["jq", "-r", ".status // \"\"", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "rebased"


@pytest.mark.skipif(not HAS_JQ, reason="needs real jq, as host-finalize.sh uses")
def test_pre_fix_shared_channel_does_not_parse(tmp_path: Path) -> None:
    """Falsification control for the three tests above.

    Replays the pre-fix shape — the worker's log lines followed by the JSON, as
    a `2>&1` capture produced — and proves `jq` fails on it. Without this, the
    passing cases prove only that jq parses JSON, not that the channel split is
    what makes them pass.
    """
    combined = "\n".join(_LOG_NOISE) + "\n" + json.dumps(_VERDICT)
    r = subprocess.run(["jq", "-e", ".status // \"\""],
                       input=combined, capture_output=True, text=True)
    assert r.returncode != 0, (
        "the pre-fix combined stream must NOT parse — if it does, this file's "
        "other tests are not testing the channel split"
    )


def test_capture_does_not_merge_the_log_stream_into_the_verdict() -> None:
    """Source pin: no `2>&1` capture of the seam invocation.

    Behavioural tests cannot see this — they drive the seam directly, not the
    shell's capture of it — and the capture is where the defect lived.
    """
    src = _code_only(HOST_FINALIZE_SH.read_text())
    # Anchored at the START of the invocation's line, not at `python3`. The
    # first version began the match AT `python3`, so the prefix a regression
    # adds — `_rebaser_json="$(` — fell outside the extracted block and the
    # assertions below could not see it. The `( … ) >&2` wrapper fell outside
    # it too, which is why neither was pinned.
    m = re.search(
        r'^[ \t]*[^\n]*python3 "\$_rebaser_py"[^\n]*\n'
        r'(?:[^\n]*\n){0,6}?[^\n]*_rebaser_rc=\$\?',
        src, re.M)
    assert m, "could not locate the rebaser seam invocation"
    block = m.group(0)
    assert "2>&1" not in block, (
        "the seam invocation must not merge stderr into stdout: with the "
        "verdict on its own channel this only re-pollutes the operator's view, "
        "and it is the exact construct that made 9 of 9 runs unparseable:\n"
        + block
    )
    assert '"$_rebaser_out"' in block, (
        "the invocation must pass the verdict-file path to the seam:\n" + block
    )
    assert "$(" not in block and "`" not in block, (
        "the seam's stdout must not be captured at all — capturing is the "
        "defect, and `2>/dev/null` would evade the `2>&1` check above while "
        "reinstating it:\n" + block
    )
    assert ">&2" in block, (
        "the seam's stdout carries the worker log and must be redirected to "
        "stderr, so it reaches the operator without being mistaken for a "
        "return value:\n" + block
    )
    assert block.lstrip().startswith("("), (
        "the invocation must stay wrapped in a ( … ) subshell: the original "
        "command substitution provided one, and removing it without replacement "
        "let a `set -u` abort inside the seam kill host_finalize outright "
        "(measured: 20 of 32 tests failed):\n" + block
    )


def test_fallback_arm_truncates_from_the_tail() -> None:
    """`head -c 2000` preserved log noise and dropped the JSON it exists to keep."""
    src = _code_only(HOST_FINALIZE_SH.read_text())
    assert 'tail -c 2000' in src, (
        "the `*)` arm must tail-truncate the raw payload — a malformed verdict "
        "shows its corruption at the end"
    )
    assert 'head -c 2000' not in src, (
        "`head -c 2000` is the pre-fix truncation; it preserved 2000 bytes of "
        "whatever came first"
    )
