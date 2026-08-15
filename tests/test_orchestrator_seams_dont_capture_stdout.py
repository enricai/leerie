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

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The marker every shell-hosted seam uses to load the orchestrator by path.
_SEAM_MARKER = 'spec_from_file_location("leerie_orch"'


def _shell_files(root: Path | None = None) -> list[Path]:
    root = root or REPO_ROOT
    files = [root / "leerie"]
    files.extend(sorted((root / "scripts").rglob("*.sh")))
    return [p for p in files if p.is_file()]


_PY3 = re.compile(r"(^|[^\w-])python3\b")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")






def _spans(cmd: str, opener: str, closer: str) -> list[str]:
    """Every balanced `opener … closer` span, innermost content included."""
    out, i = [], 0
    while True:
        i = cmd.find(opener, i)
        if i < 0:
            return out
        depth, j = 1, i + len(opener)
        while j < len(cmd) and depth:
            if cmd.startswith(opener, j):
                depth += 1; j += len(opener); continue
            if cmd[j] == closer:
                depth -= 1
            j += 1
        out.append(cmd[i + len(opener):j - 1])
        i = j


def _blank(cmd: str, which: str) -> str:
    """Blank spans quoted with any char in `which`.

    TWO passes are needed downstream, and that is load-bearing: the quote types
    suppress different things. Single quotes suppress everything; double quotes
    still expand `$( )` and backticks. A first draft blanked both before
    looking for command substitution and therefore could not see
    `_rebaser_json="$(python3 …)"` — the original F1 defect.
    """
    out, i, n = [], 0, len(cmd)
    while i < n:
        c = cmd[i]
        if c in which:
            q = c
            i += 1
            while i < n and cmd[i] != q:
                if q == '"' and cmd[i] == "\\":
                    i += 1
                    if i < n:
                        out.append(" ")
                i += 1
                out.append(" ")
            i += 1
            out.append("  ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _captures_stdout(cmd: str) -> str:
    """Why `cmd` consumes the seam's stdout as a value, or "" if it does not.

    An ALLOWLIST, not a blacklist. The permitted contract is narrow and stable:
    the seam's stdout goes to stderr or the terminal, its exit code may be
    consumed, and nothing else. Anything outside that fails, so a capture form
    nobody has thought of yet is a failure rather than a pass.

    That inversion is the point. Three successive blacklists enumerated ways to
    fail and each was still green on F1's own mechanism — verified misses
    included `| tee /dev/stderr | jq`, `| cat | jq`, `|& jq`, `> file && jq
    file` and `jq < <(python3 …)`, while a `|` inside a quoted argument
    produced a false positive on correct code.
    """
    joined = cmd.replace("\\\n", " ")
    # Pass 1: only SINGLE quotes suppress substitution. A substitution matters
    # only if it WRAPS THE SEAM — `LEERIE_NOW="$(date -u +%s)" python3 …`
    # captures `date`, not the seam, and flagging it fails a correct tree.
    subst = _blank(joined, "'")
    for opener, closer, why in (("$(", ")", "command substitution"),
                                ("<(", ")", "process substitution")):
        for span in _spans(subst, opener, closer):
            if _PY3.search(span):
                return why
    if "`" in subst:
        # Backticks do not nest, so the span is between consecutive pairs.
        parts = subst.split("`")
        if any(_PY3.search(parts[k]) for k in range(1, len(parts), 2)):
            return "backtick substitution"
    # Pass 2: pipes and redirections are inert inside either quote type.
    bare = _blank(subst, '"')
    for pat, why in ((r"\|&", "stdout+stderr piped"),
                     (r"(?<!\|)\|(?!\|)", "piped into another command"),
                     (r"(?<![0-9&])>(?!&)", "stdout redirected to a file"),
                     (r"\d>(?!&)", "fd redirected to a file")):
        if re.search(pat, bare):
            return why
    return ""


def _logical_command(lines: list[str], idx: int) -> str:
    """The whole shell command containing `lines[idx]`.

    Backslash-continuations in both directions, so a capture prefix on an
    earlier line and a `| jq` on a later one are both inside the returned text.
    Resolving only the single line is how the first version of this guard
    missed a newline-separated `VAR="$(` and a trailing pipe.
    """
    start = idx
    # `not _is_comment`: a wrapped comment above the invocation ends in a
    # backslash too, and swallowing it made a correct tree fail the
    # matched-an-invocation control.
    while (start > 0 and lines[start - 1].rstrip().endswith("\\")
           and not _is_comment(lines[start - 1])):
        start -= 1
    end = idx
    while end < len(lines) - 1 and lines[end].rstrip().endswith("\\"):
        end += 1
    return "\n".join(lines[start:end + 1])


def _seam_invocations(root: Path | None = None) -> list[tuple[Path, int, str]]:
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
    for path in _shell_files(root):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if _SEAM_MARKER not in line:
                continue
            # Walk back to the heredoc opener that contains this marker.
            opener = next((j for j in range(i, -1, -1)
                           if "<<" in lines[j] and not _is_comment(lines[j])),
                          None)
            if opener is None:
                continue
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
                found.append((path, start + 1, _logical_command(lines, inv)))
                continue
            # `cat > "$dest" <<'PY'` — the heredoc only WRITES the program;
            # the invocation is below the terminator. `dest` may be a variable
            # or a literal path, and the search is by basename so a refactor
            # from `"$_rebaser_py"` to `"$scratch/rebaser.py"` still resolves.
            m = re.search(r">\s*\"?([^\s\"'<>|]+)", lines[opener])
            if m is None:
                continue
            dest = m.group(1)
            key = dest.rsplit("/", 1)[-1].strip('"').lstrip("$").strip("{}")
            run = next((k for k in range(i, len(lines))
                        if _PY3.search(lines[k]) and key in lines[k]
                        and not _is_comment(lines[k])), None)
            if run is None:
                continue
            found.append((path, run + 1, _logical_command(lines, run)))
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
    assert "rebaser" in line, (
        "expected the line invoking the seam script (matched by basename, so a "
        f"refactor to a literal path still resolves), got {line!r}")


def test_no_seam_captures_the_orchestrator_stdout() -> None:
    """The load-bearing rule.

    `VAR="$(python3 … )"` on a seam is the exact construct that made the rebase
    verdict unparseable for every run. Note this deliberately does NOT forbid
    printing to stdout, redirecting it, or consuming the exit code — only
    capturing it for parsing.
    """
    offenders = []
    for path, lineno, line in _seam_invocations():
        why = _captures_stdout(line)
        if why:
            offenders.append(f"{path.name}:{lineno}: [{why}] {line.strip()}")
    assert not offenders, (
        "these seams capture the orchestrator's stdout, which carries the log "
        "stream and not a return value — give the seam an explicit output-path "
        "argument instead (see docs/POSTMORTEM-2026-08-14.md, F1):\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_would_catch_a_capturing_seam(tmp_path: Path) -> None:
    """Falsification control, driving the REAL helper and the REAL predicate.

    Planted under `tmp_path`, never in the repo. The previous version wrote
    `scripts/zz-capture-canary.sh` into the working tree and removed it in a
    `finally` — which does not run on SIGKILL, and a survivor was reproduced
    breaking a sibling file: `_PRUNE_SURFACE` in
    tests/test_worktree_prune_scoping.py globs `scripts/` at COLLECTION time,
    this file unlinks the canary first, and that test then dies on
    FileNotFoundError. Green again next run, because the first run destroyed
    the evidence — a heisenbug in a suite whose whole thesis is falsifiability.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "canary.sh").write_text(
        '#!/usr/bin/env bash\n'
        'out="$(python3 - "$repo" <<\'PY\'\n'
        f'spec = {_SEAM_MARKER}, orch_path)\n'
        'PY\n'
        ')"\n'
    )
    (tmp_path / "leerie").write_text("#!/usr/bin/env bash\n")
    seams = {p.name: line for p, _, line in _seam_invocations(tmp_path)}
    assert "canary.sh" in seams, (
        "the resolver must find a planted seam; if it cannot, the rule above "
        "is scanning nothing")
    assert _captures_stdout(seams["canary.sh"]), (
        "the real predicate must flag a captured seam")


def test_the_canary_never_touches_the_repo() -> None:
    """Guard the guard: the control above must not be able to dirty the tree."""
    src = Path(__file__).read_text()
    body = src[src.index("def test_the_scan_would_catch_a_capturing_seam("):]
    body = body[:body.index("\ndef ")]
    assert "REPO_ROOT" not in body, (
        "the canary must be planted under tmp_path — a survivor breaks a "
        "sibling test file and can reach the Docker build context")


@pytest.mark.parametrize("shape,why", [
    # Every form verified to slip past the three preceding blacklists. The
    # first four are F1's own mechanism — "feed the seam's stdout to jq" —
    # written the ways someone would naturally write it.
    ('  python3 "$_rebaser_py" "$X" | jq -r ".status"', "naive pipe"),
    ('  python3 "$_rebaser_py" "$X" | tee /dev/stderr | jq -r ".status"',
     "the natural rewrite: keep the log AND read the verdict"),
    ('  python3 "$_rebaser_py" "$X" | cat | jq -r ".status"', "via a sink"),
    ('  python3 "$_rebaser_py" "$X" |& jq -r ".status"', "|& form"),
    ('  python3 "$_rebaser_py" "$X" > /tmp/x', "redirect, read later"),
    ('  jq -r ".status" < <(python3 "$_rebaser_py" "$X")',
     "process substitution"),
    ('  mapfile -t v < <(python3 "$_rebaser_py" "$X")', "mapfile"),
    ('  _rebaser_json="$(python3 "$_rebaser_py" "$X")"', "the original F1"),
    ('  _rebaser_json="$( \\\n    python3 "$_rebaser_py" "$X")"',
     "continued command substitution"),
    ('  out=`python3 "$_rebaser_py" "$X"`', "backticks"),
])
def test_the_predicate_sees_every_capture_mechanism(shape, why) -> None:
    assert _captures_stdout(shape), f"{why}: {shape!r}"


@pytest.mark.parametrize("shape", [
    # The shipped form: stdout to stderr, verdict in a file, exit code read.
    '      ( python3 "$_rebaser_py" "$X" "$_rebaser_out" \\\n        >&2 ) || rc=$?',
    # A `|` inside a quoted argument is not a pipe. This one FAILED a correct
    # tree under the previous predicate.
    '  python3 - "$repo" --skip-pattern "vendor|node_modules" <<\'PY\'',
    "  python3 - \"$r\" -E 'foo|bar' <<'PY'",
    # An env-var prefix computed by command substitution captures `date`, not
    # the seam — which is why a substitution only counts when it WRAPS it.
    '  LEERIE_NOW="$(date -u +%s)" \\\n    python3 - "$repo" <<\'PY\'',
    '  python3 "$_rebaser_py" "$X" || rc=$?',
    '  ( python3 "$x" 2>&1 >&2 ) || true',
])
def test_the_predicate_leaves_legitimate_shapes_alone(shape) -> None:
    """The converse. An over-broad rule fails a correct tree, which is the
    failure mode that gets a guard deleted rather than fixed."""
    assert not _captures_stdout(shape), _captures_stdout(shape)


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
