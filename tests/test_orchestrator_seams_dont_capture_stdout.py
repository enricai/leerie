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
import subprocess
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


# ===========================================================================
# The same rule over BASH'S OWN PARSE
# ===========================================================================
#
# Everything above resolves shell structure out of source TEXT, and four
# generations of it were each still green on a form that reinstates F1. The
# verified misses were all resolution failures rather than predicate failures:
# a multi-line `$(` with no backslash (the joiner follows backslashes only), a
# pre-opened fd (`>&3`), a capture at the CALL SITE of a wrapper function,
# `eval` with single quotes, `coproc`, a `|` on the line after a brace group,
# and a second capturing invocation (the resolver takes the first match only).
# In the other direction it failed correct code twice: a trailing comment
# containing `>` or `|`, and `2>logfile` — redirecting the seam's own log is
# not capturing its stdout.
#
# `source <script>; declare -f` re-renders every function from bash's internal
# parse tree, which removes all of that at once rather than one form at a
# time: comments are gone, continuations are joined, a multi-line `$( )`
# collapses onto one line, and `>&2` normalises to `1>&2` so stdout-vs-stderr
# is explicit instead of guessed.
#
# SCOPE, stated honestly: `declare -f` reaches functions. That is where the F1
# defect lived, and it is `host-finalize.sh`'s whole surface. The launcher's
# `config --recapture` seam sits in a `case` arm at top level, so bash cannot
# render it and the textual rule above remains its only check.

_SINKS = frozenset({"cat", "tee"})


def _canonical_functions(script: Path) -> dict[str, str]:
    """{function name: bash's own re-rendering of its body}."""
    out = subprocess.run(
        # `cut`, not `awk "{print $3}"`: inside double quotes BASH expands
        # `$3` (empty) before awk ever sees it, so awk prints the whole
        # `declare -f <name>` line and word-splitting invents two extra
        # "functions" called `declare` and `-f`.
        ["bash", "-c", f'source "{script}" 2>/dev/null; '
                       'for f in $(declare -F | cut -d" " -f3); do '
                       'echo "@@FN $f"; declare -f "$f"; done'],
        capture_output=True, text=True, check=False).stdout
    fns: dict[str, list[str]] = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("@@FN "):
            cur = line[5:]
            fns[cur] = []
        elif cur is not None:
            fns[cur].append(line)
    return {k: "\n".join(v) for k, v in fns.items()}


def _canonical_captures(stmt: str, needle: str) -> str:
    """Why this canonical statement consumes `needle`'s stdout, or "".

    Two passes over the quoting, for the same reason `_captures_stdout` needs
    two: only SINGLE quotes suppress `$( )`, while a pipe or a redirect is
    inert inside either kind. Scanning for pipes without blanking quotes first
    flags `python3 "$seam" --re "a|b"` — a correct tree failing on an argument.
    """
    subst = _blank(stmt, "'")
    for opener, why in (("$(", "command substitution"),
                        ("<(", "process substitution")):
        for span in _spans(subst, opener, ")"):
            if needle in span:
                return why
    if "`" in subst:
        parts = subst.split("`")
        if any(needle in parts[k] for k in range(1, len(parts), 2)):
            return "backticks"
    # `coproc` reads the command's stdout over a pipe pair with no `|` token.
    if re.search(r"\bcoproc\b", stmt) and needle in stmt:
        return "coproc"
    stmt = _blank(subst, '"')
    # bash writes `>&2` as `1>&2` but leaves a plain `> file` as `>`, so both
    # spellings of "stdout goes to a descriptor that is not fd 2" must count.
    if re.search(r"(?:\b1)?>\s*&\s*(?!2\b)\d", stmt):
        return "stdout to another fd"
    if re.search(r"(?<![0-9])>\s*(?!&)", stmt) or re.search(r"\b1>\s*(?!&)", stmt):
        return "stdout to a file"
    for seg in re.split(r"\|\|", stmt):
        stages = re.findall(r"\|\s*([\w./-]+)", seg)
        # EVERY downstream stage must be a sink. Taking only the first let
        # `| tee /dev/stderr | jq` through — the natural way to keep the log
        # and read the verdict, i.e. F1 with one extra stage.
        bad = next((st for st in stages if st not in _SINKS), None)
        if bad is not None:
            return f"piped into `{bad}`"
    return ""


def _canonical_offenders(script: Path, needle: str,
                         expect: tuple[str, ...] = ()) -> list[str]:
    fns = _canonical_functions(script)
    # FAIL CLOSED. If the script does not source, `declare -F` is empty and
    # every check below passes vacuously — the same fail-open defect that made
    # the prune scan silently useless. Measured: a mutant with a dangling `)`
    # lost `host_finalize` entirely and the first draft of this audit reported
    # clean.
    missing = [f for f in expect if f not in fns]
    if missing:
        raise AssertionError(
            f"{script}: bash did not parse {missing} — the audit cannot run. "
            "Reporting clean here is indistinguishable from a pass.")
    bad: list[str] = []
    for name, body in fns.items():
        lines = body.splitlines()
        for i, line in enumerate(lines):
            # The needle alone is not enough: `cat > "$_rebaser_py"` WRITES the
            # seam script and mentions it, but runs nothing. Only a statement
            # that actually invokes the interpreter can capture its stdout.
            if needle not in line or not _PY3.search(line):
                continue
            why = _canonical_captures(line, needle)
            if why:
                bad.append(f"{name}: [{why}] {line.strip()[:70]}")
                continue
            # `coproc NAME {` opens a block and the invocation lands inside it,
            # so the keyword is on an EARLIER line — the mirror of the
            # group-pipe case below.
            for k in range(max(0, i - 5), i):
                if re.search(r"\bcoproc\b", lines[k]):
                    bad.append(f"{name}: [coproc] {lines[k].strip()[:60]}")
                    break
            # A brace/subshell group piped on a LATER line.
            for k in range(i + 1, min(i + 6, len(lines))):
                if re.match(r"\s*[})]\s*\|", lines[k]):
                    bad.append(f"{name}: [group piped] {lines[k].strip()[:60]}")
                    break
        # A helper defined INSIDE this function: `declare -f` renders the
        # nested definition inline, so the definition and the capturing call
        # site are both in this body.
        for m in re.finditer(r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*$", body, re.M):
            inner = m.group(1)
            # `declare -f` renders the function's OWN header as `name () ` on
            # the first line; matching it made every mention of the enclosing
            # function look like a captured helper call.
            if inner == name:
                continue
            tail = body[m.end():]
            block = tail[:tail.find("\n    }")] if "\n    }" in tail else tail
            if needle not in block:
                continue
            for line in lines:
                if (re.search(rf"\b{re.escape(inner)}\b", line)
                        and _canonical_captures(line, inner)):
                    bad.append(f"{name}: [call-site capture of {inner}()] "
                               f"{line.strip()[:56]}")
                    break
        # The seam lives in this function; the CAPTURE is at its call site.
        if needle in body:
            for other, obody in fns.items():
                if other == name:
                    continue
                for line in obody.splitlines():
                    if (re.search(rf"\b{re.escape(name)}\b", line)
                            and _canonical_captures(line, name)):
                        bad.append(f"{other}: [call-site capture of {name}()] "
                                   f"{line.strip()[:60]}")
    return bad


def test_bash_own_parse_sees_no_capture_in_host_finalize() -> None:
    """The load-bearing rule again, resolved by bash rather than by regex."""
    offenders = _canonical_offenders(
        REPO_ROOT / "scripts" / "host-finalize.sh", "rebaser",
        expect=("host_finalize",))
    assert not offenders, (
        "these statements consume the rebaser seam's stdout (see "
        "docs/POSTMORTEM-2026-08-14.md, F1):\n  " + "\n  ".join(offenders))


def test_the_canonical_audit_fails_closed_on_an_unparseable_script(tmp_path) -> None:
    """A scanner that reports clean when it could not read the script is worse
    than no scanner: it is a green tick with no coverage behind it."""
    bad = tmp_path / "broken.sh"
    bad.write_text("host_finalize() {\n  python3 x )\n}\n")
    with pytest.raises(AssertionError, match="did not parse"):
        _canonical_offenders(bad, "x", expect=("host_finalize",))


@pytest.mark.parametrize("body,why", [
    # Every form verified to slip past the text-scanning generations.
    ('host_finalize() {\n  v=$(\n    python3 "$_rebaser_py" "$X"\n  )\n}',
     "multi-line $( ) with NO backslash — the joiner follows backslashes only"),
    ('host_finalize() {\n  python3 "$_rebaser_py" "$X" >&3\n}',
     "capture on a pre-opened fd"),
    ('host_finalize() {\n  _run() {\n    python3 "$_rebaser_py" "$X"\n  }\n'
     '  out=$(_run)\n}',
     "capture at the CALL SITE of a nested wrapper"),
    ('host_finalize() {\n  coproc CO { python3 "$_rebaser_py" "$X"; }\n}',
     "coproc — a pipe pair with no | token"),
    ('host_finalize() {\n  {\n    python3 "$_rebaser_py" "$X"\n  } | jq .\n}',
     "a group piped on the closing-brace line — the invocation and the pipe "
     "are never on the same line, so a per-line predicate cannot see it"),
    ('host_finalize() {\n  python3 "$_rebaser_py" a >&2\n'
     '  out=$(python3 "$_rebaser_py" b)\n}',
     "a SECOND invocation — the text resolver took the first match only"),
    ('host_finalize() {\n  python3 "$_rebaser_py" "$X" | tee /dev/stderr | jq .\n}',
     "keep the log AND read the verdict"),
])
def test_the_canonical_audit_sees_what_text_scanning_missed(tmp_path, body, why):
    f = tmp_path / "c.sh"
    f.write_text(body + "\n")
    assert _canonical_offenders(f, "_rebaser_py", expect=("host_finalize",)), why


@pytest.mark.parametrize("body,why", [
    ('host_finalize() {\n  python3 "$_rebaser_py" "$X" "$out" >&2 || rc=$?\n}',
     "the shipped form: stdout to stderr, verdict in a file, rc consumed"),
    ('host_finalize() {\n  python3 "$_rebaser_py" "$X" # writes > and | too\n}',
     "a TRAILING comment — a false positive under the text scan, and bash "
     "strips it outright"),
    ('host_finalize() {\n  python3 "$_rebaser_py" "$X" 2>"$log"\n}',
     "redirecting the seam's own LOG is not capturing its stdout"),
    ('host_finalize() {\n  cat > "$_rebaser_py" <<\'PY\'\nx = 1\nPY\n}',
     "writing the seam script mentions it but runs nothing"),
    ('host_finalize() {\n  python3 "$_rebaser_py" --re "a|b" >&2\n}',
     "a pipe inside a quoted argument is not a pipe"),
])
def test_the_canonical_audit_leaves_legitimate_shapes_alone(tmp_path, body, why):
    f = tmp_path / "c.sh"
    f.write_text(body + "\n")
    got = _canonical_offenders(f, "_rebaser_py", expect=("host_finalize",))
    assert not got, f"{why}: {got}"


def test_eval_with_single_quotes_defeats_both_checks(tmp_path) -> None:
    """A known gap, pinned so it is a decision rather than a surprise.

    `eval 'out=$(python3 "$seam")'` hides the capture from BOTH mechanisms:
    the text scan sees a quoted argument, and bash's parse tree records an
    `eval` with a string operand — the substitution does not exist until the
    string is evaluated at runtime, so `declare -f` cannot show it.

    Measured A/B on planted seam files, full pipelines: the text scan misses
    7 of 7 capture forms; the canonical audit misses 1 — this one. It stays
    documented rather than "handled", because the only way to catch it is to
    parse the eval operand, i.e. to go back to scanning text.
    """
    f = tmp_path / "e.sh"
    f.write_text('host_finalize() {\n  eval \'out=$(python3 "$s" "$X")\'\n}\n')
    assert not _canonical_offenders(f, "$s", expect=("host_finalize",)), (
        "if this now fires, the gap closed — delete this test and add the "
        "shape to the caught list above")


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
