"""No message of the launcher or a host-side script prints a value it read
from a repository's configuration, a run's record or git raw (DESIGN §6
*No text leerie did not write reaches the operator's terminal as a control
sequence*; review round 32, execution X3).

A static rule over the shell, not a pattern over prose: a value is *read*
when a variable is assigned the output of a command substitution that
reads `leerie.toml`, `.leerie/config.toml` or `run.json` (by name, through
a variable holding such a path, or through a function whose body does), or
that runs a git command printing names or text (`log`, `branch`,
`symbolic-ref`, `status`, `config`...; not a count or an object id), or the
value of another read variable. A *message* is a call of the launcher's
loggers (`remote_log`, `_runtime_log`...) or an `echo`/`printf` to stderr.
A message may expand a read variable only inside `$(_terminal_safe ...)`
or on a line piped through `_terminal_relay`. The rule is flow-insensitive
(a variable read anywhere in a file is read everywhere in it), which errs
towards escaping.

The rule is pinned on synthetic shell both ways, so a rule that stopped
finding anything fails here too."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The host side: the launcher and every script it runs or sources on the
# host. Scripts run in the container print to the orchestrator's pipes,
# which `log()` escapes.
HOST_FILES = (["leerie", "scripts/host-finalize.sh"]
              + sorted(str(p.relative_to(REPO_ROOT))
                       for p in (REPO_ROOT / "scripts" / "remote")
                       .glob("*.sh")))

_FILES = re.compile(r"leerie\.toml|config\.toml|run\.json")
_GIT_TEXT = re.compile(
    r"\bgit\b(\s+-[Cc]\s+\S+|\s+--\S+)*\s+(log|show|for-each-ref|branch|"
    r"symbolic-ref|ls-files|ls-tree|diff|status|describe|tag|name-rev|"
    r"cat-file|config|rev-parse\s+--abbrev-ref|"
    r"rev-parse\s+--symbolic-full-name|ls-remote)\b")
# A substitution whose output is a number or an object id.
_MECHANICAL = re.compile(
    r"\|\s*wc\s+-[lc]\s*\)$|\bgrep\s+-c\b|\bgit\b.*\b(rev-list\s+--count|"
    r"merge-base|rev-parse\s+(--verify|-q|--quiet|HEAD|\"))")
_ASSIGN = re.compile(r"^\s*(?:(?:local|export|readonly|declare)\s+"
                     r"(?:-\w+\s+)?)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_FUNC = re.compile(r"(?:^|[;{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")
_LOGGER = re.compile(r"^\s*(?:if\s+!?\s*|then\s+|else\s+|\|\|\s*|&&\s*)?"
                     r"(remote_log|_runtime_log|_runtime_err|"
                     r"_aws_die_with_hint|_repo_build_fail|die|warn|_warn)\b")
_TO_STDERR = re.compile(r"^\s*(echo|printf)\b.*>&2")
_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    out, buf, start = [], "", None
    for n, line in enumerate(text.split("\n"), 1):
        if start is None:
            start = n
        if line.rstrip().endswith("\\") and not line.lstrip().startswith("#"):
            buf += line.rstrip()[:-1] + " "
            continue
        out.append((start, buf + line))
        buf, start = "", None
    return out


def _substitutions(s: str) -> list[tuple[int, int]]:
    """The outermost `$(...)` spans of `s`."""
    spans, i = [], 0
    while True:
        i = s.find("$(", i)
        if i < 0:
            return spans
        depth, j = 0, i + 1
        while j < len(s):
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        spans.append((i, j + 1))
        i = j + 1


def _without(s: str, keep) -> str:
    for a, b in reversed(_substitutions(s)):
        if keep(s[a:b]):
            s = s[:a] + s[b:]
    return s


def raw_messages(text: str) -> list[tuple[int, list[str], str]]:
    """(line, read variables, line text) of each message in `text` that
    expands a read variable outside `_terminal_safe`/`_terminal_relay`."""
    lines = [(n, l) for n, l in _logical_lines(text)
             if not l.lstrip().startswith("#")]
    readers: set[str] = set()
    current: list[str] = []
    for _n, l in lines:
        for m in _FUNC.finditer(l):
            if l.startswith(m.group(1)) or not current:
                current = [m.group(1)]
            else:
                current.append(m.group(1))
        if current and (_FILES.search(l) or _GIT_TEXT.search(l)):
            readers.update(current)
        if l.startswith("}"):
            current = []
    paths = {m.group(1) for _n, l in lines
             if (m := _ASSIGN.match(l)) and _FILES.search(m.group(2))
             and "$(" not in m.group(2)}
    reader_rx = [re.compile(r"(^|[\s(;|])" + re.escape(r) + r"\b")
                 for r in readers]
    path_rx = [re.compile(r"\$\{?" + re.escape(p) + r"\b") for p in paths]

    def reads(sub: str, tainted: set[str]) -> bool:
        if "_terminal_safe" in sub or _MECHANICAL.search(sub):
            return False
        return bool(_FILES.search(sub) or _GIT_TEXT.search(sub)
                    or any(r.search(sub) for r in reader_rx + path_rx)
                    or set(_REF.findall(sub)) & tainted)

    tainted: set[str] = set()
    changed = True
    while changed:
        changed = False
        for _n, l in lines:
            m = _ASSIGN.match(l)
            if not m or m.group(1) in tainted:
                continue
            rhs = m.group(2)
            subs = [rhs[a:b] for a, b in _substitutions(rhs)]
            outside = _without(rhs, lambda _s: True)
            if (any(reads(s, tainted) for s in subs)
                    or set(_REF.findall(outside)) & tainted):
                tainted.add(m.group(1))
                changed = True
    found = []
    for n, l in lines:
        if not (_LOGGER.match(l) or _TO_STDERR.match(l)):
            continue
        if "_terminal_relay" in l:
            continue
        bare = _without(l, lambda s: s.startswith("$(_terminal_safe"))
        refs = sorted(set(_REF.findall(bare)) & tainted)
        if refs:
            found.append((n, refs, l.strip()))
    return found


def test_no_host_message_prints_a_read_value_raw():
    found = [f"{rel}:{n}: {refs}: {line[:120]}"
             for rel in HOST_FILES
             for n, refs, line in raw_messages(
                 (REPO_ROOT / rel).read_text(errors="replace"))]
    assert not found, ("print these through `$(_terminal_safe ...)` or "
                       "`| _terminal_relay`:\n" + "\n".join(found))


def test_every_script_that_logs_on_the_host_is_scanned():
    """A host-side script sources `_log.sh` for its logger; each one is in
    the scanned set."""
    logging = sorted(str(p.relative_to(REPO_ROOT))
                     for p in (REPO_ROOT / "scripts").rglob("*.sh")
                     if "_log.sh" in p.read_text(errors="replace")
                     and p.name != "_log.sh")
    assert logging and set(logging) <= set(HOST_FILES), logging


RAW = [
    # A leerie.toml value, read in place.
    'v="$(grep ^x= "$USER_REPO/leerie.toml")"\nremote_log "bad x=$v"\n',
    # Through a variable holding the path.
    'cfg="$USER_REPO/.leerie/config.toml"\nv="$(sed -n 1p "$cfg")"\n'
    'echo "bad: $v" >&2\n',
    # Through a function that reads run.json.
    'get() { jq -r .push_error "$1/run.json"; }\nv="$(get "$d")"\n'
    'remote_log "push failed: ${v:-none}"\n',
    # Git text, and a value built from it.
    'b="$(git -C "$r" symbolic-ref --short HEAD)"\nmsg="on $b"\n'
    'printf "%s\\n" "$msg" >&2\n',
]

SAFE = [
    'v="$(grep ^x= "$USER_REPO/leerie.toml")"\n'
    'remote_log "bad x=$(_terminal_safe "$v")"\n',
    'v="$(git -C "$r" log -1 --format=%s)"\n'
    'printf "%s\\n" "$v" | _terminal_relay >&2\n',
    # A count and an object id are not text.
    'n="$(git -C "$r" rev-list --count a..b)"\n'
    'h="$(git -C "$r" rev-parse HEAD)"\nremote_log "$n behind at $h"\n',
    # A read value that is never printed.
    'v="$(grep ^x= "$USER_REPO/leerie.toml")"\nexport V="$v"\n',
]


def test_the_rule_finds_a_raw_read_value():
    for snippet in RAW:
        assert raw_messages(snippet), snippet


def test_the_rule_passes_an_escaped_or_mechanical_value():
    for snippet in SAFE:
        assert not raw_messages(snippet), snippet
