"""No host-side seam may read a machine value off the orchestrator's stdout.

**stdout is the log channel, by design.** On the remote runtime `sys.stdout`
IS `<run_dir>/orchestrator.log` — the launcher redirects fd1 there and
`_install_run_log_tee` deliberately skips its guarded tee in that case — so
`log()`'s `print(..., flush=True)` is the intended writer. Moving `log()` to
stderr to "free up" stdout would therefore break log capture on every remote
run; the architecture is not the defect.

The defect is a *consumer* treating that channel as a return path. Before this
guard, `scripts/host-finalize.sh` captured the rebaser seam as
`_rebaser_json="$(python3 … 2>&1)"` and fed the result to `jq`, which received a
few hundred lines of worker log with the JSON appended. `jq` returned rc 5 and
control fell to the `*)` fallback arm on **9 of 9** runs that ever reached the
rebaser — so the `rebased` and `irreconcilable|failed` arms had never once
executed, and a rebaser returning a valid `{"status":"failed", …}` with a full
conflict diagnosis had that diagnosis silently discarded rather than folded into
the PR body. See docs/POSTMORTEM-2026-08-14.md, F1.

The rule this file enforces: a seam that imports `orchestrator/leerie.py` may
have its stdout flow to the operator, and may have its exit code consumed, but
its stdout must never be captured for parsing. A seam that needs to *return*
something takes an explicit output-path argument.

Sweep at the time of writing: exactly two seams import the orchestrator from
shell — `scripts/host-finalize.sh`'s rebaser (needs a verdict → writes it to an
argv-supplied file) and `leerie`'s `config --recapture` (needs only an exit code
→ stdout flows to the terminal, uncaptured). The second is the counter-example
that shows the rule is about *capturing*, not about printing.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The marker every shell-hosted seam uses to load the orchestrator by path.
_SEAM_MARKER = 'spec_from_file_location("leerie_orch"'


def _shell_files() -> list[Path]:
    files = [REPO_ROOT / "leerie"]
    files.extend(sorted((REPO_ROOT / "scripts").rglob("*.sh")))
    return [p for p in files if p.is_file()]


_PY3 = re.compile(r"(^|[^\w-])python3\b")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _seam_invocations() -> list[tuple[Path, int, str]]:
    """Every shell invocation that RUNS the orchestrator-loading seam.

    Returns (path, 1-indexed line number, invocation line).

    A seam's marker line always sits inside a heredoc, and the invocation is on
    one of two sides of it:

      * `python3 - <<'PY'` — the heredoc IS the program, so the invocation is
        the opener, above the marker;
      * `cat > "$var" <<'PY'` … `PY` … `python3 "$var"` — the heredoc only
        writes a script file, so the invocation is *below* the terminator.

    Resolving that is the whole job. The first version of this helper walked
    backwards to the nearest line matching `python3` and, for
    `scripts/host-finalize.sh` — the one file this guard exists for — landed on
    a **comment** reading "Write the python3 seam to a script file rather than a
    heredoc so the …". The real invocation is ~35 lines below, past the `PY`
    terminator, and was never examined: the rule below saw comment prose, found
    no `$(`, and passed unconditionally. Restoring the exact capture that cost
    9 of 9 runs their rebase verdict left this file green.
    """
    found: list[tuple[Path, int, str]] = []
    for path in _shell_files():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if _SEAM_MARKER not in line:
                continue
            # Walk back to the heredoc opener that contains this marker.
            opener = next((j for j in range(i, -1, -1)
                           if "<<" in lines[j] and not _is_comment(lines[j])),
                          None)
            assert opener is not None, (
                f"{path}:{i+1} loads the orchestrator outside any heredoc")
            # The opener may be a backslash-continuation of the command that
            # actually runs python3 (`leerie`'s recapture seam spreads its
            # invocation over four lines and puts `<<'PY'` on the last). Walk
            # back over continuations to the logical start before deciding.
            start = opener
            while start > 0 and lines[start - 1].rstrip().endswith("\\"):
                start -= 1
            logical = "\n".join(lines[start:opener + 1])
            if _PY3.search(logical):
                inv = next(k for k in range(start, opener + 1)
                           if _PY3.search(lines[k]))
                # Report the logical line's FIRST line: a capture prefix
                # (`VAR="$(`) sits there, not on the continuation carrying
                # `python3`.
                found.append((path, start + 1, logical if start != inv
                              else lines[inv]))
                continue
            # `cat > "$var" <<'PY'` — find the later line that runs "$var".
            m = re.search(r">\s*\"?\$\{?(\w+)\}?\"?", lines[opener])
            assert m, (
                f"{path}:{opener+1} opens a heredoc that neither invokes "
                f"python3 nor writes to a variable path: {lines[opener]!r}")
            var = m.group(1)
            run = next((k for k in range(i, len(lines))
                        if _PY3.search(lines[k]) and var in lines[k]
                        and not _is_comment(lines[k])), None)
            assert run is not None, (
                f"{path}:{i+1} writes a seam script to ${var} but nothing "
                "below invokes it with python3")
            found.append((path, run + 1, lines[run]))
    return found


def test_the_scan_finds_the_known_seams() -> None:
    """Anti-vacuity: a scan that finds nothing would pass every assertion below."""
    seams = _seam_invocations()
    names = {p.name for p, _, _ in seams}
    assert len(seams) >= 2, f"expected at least 2 orchestrator seams, got {seams}"
    assert "host-finalize.sh" in names, names
    assert "leerie" in names, names


def test_every_match_is_an_invocation_not_prose() -> None:
    """The control the original version lacked, and the reason it was blind.

    Asserting only that the scan found `host-finalize.sh` is satisfied by
    matching *any* line in it — including the comment that happens to contain
    the words "python3 seam". So assert the shape of the matched line: it must
    actually run python3, and must not be a comment.
    """
    for path, lineno, line in _seam_invocations():
        assert not _is_comment(line), (
            f"{path.name}:{lineno} matched a COMMENT, not an invocation — the "
            f"rule below then inspects prose and passes vacuously: {line!r}")
        assert _PY3.search(line), f"{path.name}:{lineno}: {line!r}"


def test_the_rebaser_invocation_is_the_one_that_runs_the_script_file() -> None:
    """`host-finalize.sh` writes its seam to a file, so the invocation is BELOW
    the marker rather than above it. Pinned by name because this file's whole
    purpose is that seam, and resolving it to anything else disables the guard.
    """
    match = [(ln, line) for p, ln, line in _seam_invocations()
             if p.name == "host-finalize.sh"]
    assert len(match) == 1, match
    _lineno, line = match[0]
    assert "$_rebaser_py" in line, (
        f"expected the line invoking the seam script, got {line!r}")


def test_no_seam_captures_the_orchestrator_stdout() -> None:
    """The load-bearing rule.

    `VAR="$(python3 … )"` on a seam is the exact construct that made the rebase
    verdict unparseable for every run. Note this deliberately does NOT forbid
    printing to stdout, redirecting it, or consuming the exit code — only
    capturing it for parsing.
    """
    offenders = []
    for path, lineno, line in _seam_invocations():
        # A capture puts `$(` before `python3` on the same logical line.
        head = line.split("python3", 1)[0]
        if "$(" in head or "`" in head:
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "these seams capture the orchestrator's stdout, which carries the log "
        "stream and not a return value — give the seam an explicit output-path "
        "argument instead (see docs/POSTMORTEM-2026-08-14.md, F1):\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_would_catch_a_capturing_seam(tmp_path: Path) -> None:
    """Falsification control for the rule above.

    Without this, `test_no_seam_captures_the_orchestrator_stdout` passes on a
    tree where the scan simply never matches a capture.
    """
    fake = tmp_path / "bad.sh"
    fake.write_text(
        '#!/usr/bin/env bash\n'
        'out="$(python3 - "$repo" <<\'PY\'\n'
        f'spec = {_SEAM_MARKER}, orch_path)\n'
        'PY\n'
        ')"\n'
    )
    lines = fake.read_text().splitlines()
    marker_idx = next(i for i, l in enumerate(lines) if _SEAM_MARKER in l)
    inv = next(lines[j] for j in range(marker_idx, -1, -1)
               if re.search(r"(^|[^\w-])python3\b", lines[j]))
    head = inv.split("python3", 1)[0]
    assert "$(" in head, (
        "the capture predicate must fire on a seam whose stdout is captured; "
        f"it did not match {inv!r}"
    )


def test_the_rebaser_seam_takes_an_explicit_output_path() -> None:
    """The positive half: a seam that needs a value gets its own channel."""
    src = (REPO_ROOT / "scripts" / "host-finalize.sh").read_text()
    assert "_rebaser_out" in src, (
        "the rebaser seam must take an explicit verdict-file path rather than "
        "returning its value on stdout"
    )
    # The seam writes there instead of printing.
    assert "out_path.write_text(json.dumps(result))" in src, (
        "the rebaser seam must WRITE its verdict to the output path; printing "
        "it puts the value back on the log channel"
    )
    assert "print(json.dumps(result))" not in src, (
        "the rebaser verdict must not be printed to stdout"
    )
