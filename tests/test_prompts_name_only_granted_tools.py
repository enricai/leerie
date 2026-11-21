"""A shipped prompt names only the tools every worker it is sent to can call.

A worker told to use a tool it lacks spends a turn calling it and is refused:
`No such tool available: Grep` when the tool is absent from its context,
"requires approval" when it is present but not granted. Then it falls back to
what it does have. Since the judgment profiles' read set became
`_JUDGE_READ_BASE` (`WebSearch,WebFetch`), the CLI has left `Grep` and `Glob`
out of the context of every worker whose grant names neither (the
transcript `init` event lists `Read` but neither of them; CLI
2.1.327-2.1.328). Thirteen prompt files (twelve worker prompts and one
include) still told them to use one or both. The field logs counted 114
`Grep`/`Glob` refusals on the delivery judge, 47 on the delivery ratchet, 20
on the satisfied probe and 16 more on the planner and the task-coverage judge
(lane TR, MAP §4).

The rule is derived, not listed. Every `claude_p` call is paired, through the
AST, with the prompts its `system_prompt` is built from (`_load_prompt`
literals, followed through local names, closures and the parameters of the
helpers that forward them; `_load_prompt` itself expands includes) and with
the tools its `allowed_tools` grants. A prompt sent to several workers must
fit the narrowest of them: the delivery ratchet is sent `delivery_judge.md`
on `SATISFIED_PROBE_TOOLS`, while the delivery judge gets it on
`TREE_READER_TOOLS`.

A worker can call the bare tool names of its grant (`_bare_tool_names`) and
the tools the CLI runs without a grant (`CLI_UNGATED`): `Read` inside the
working directories, and the injected `StructuredOutput`. In lane TR-D2's
replay, 34 of 34 `Read` calls succeeded under `SATISFIED_PROBE_TOOLS`, which
grants no `Read`; a tool `DISALLOWED_TOOLS` names is not callable, granted
or not. The vocabulary is `KNOWN_TOOLS`. A name counts when it is backticked
or a whole word. A name that is also an English word (`_ALSO_A_WORD`, such as
`Read`, `Write` or `Task`) is not counted where it begins a sentence, because
its capital may be just grammar; the blind spot is such a name opening a
sentence, including after a colon ("Tools: Read"), an abbreviation's period
("e.g. Read"), inside quotes, after a plural possessive or in a table's first
cell. A hyphenated compound of a name that is also a word (`Read-only`,
`Task-Context`) is not counted unless backticked; of any other name (`Grep`,
`Glob`) it still counts. An augmented `tools += ...` counts as a replacement,
which can only narrow a grant (a false failure, never a miss). A grant that
refers to itself (`t = t + ",..."`, `t = f(t)`) or falls back on `None` (`tools or X`)
is reported; give each alternative its own name.

The derivation follows direct calls. A `claude_p` or forwarding helper
referenced otherwise (an alias, a `functools.partial`, a callback), a helper
call whose arguments are spread (`*args`, `**kw`) and a binding it does not
follow (a `for` or `with` target, an unpaired tuple) are reported, not
skipped. A tools string a helper builds is taken to grant the narrowest
`*_TOOLS` constant the helper names and of any tools string it is passed,
which holds while such helpers only append `Bash(...)` verbs.

What this does not cover: a CLI tool outside `KNOWN_TOOLS`, shell verbs a
prompt names inside `Bash` (a prompt may name a command to forbid it),
user-prompt text leerie.py builds, the Fly integrator's own argv
(`scripts/remote/collect-subtrees.sh`, on the acting list), and `leerie
replay`, which replays any recorded prompt under `INSPECT_TOOLS`.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_LEERIE = _REPO / "orchestrator" / "leerie.py"

CLI_UNGATED = frozenset({"Read", "StructuredOutput"})

# Prompts no `claude_p` call sends: `config_chat.md` is the host-side
# `leerie config --chat` session's, which the launcher starts with the full
# interactive tool set; `_`-prefixed files are includes, checked inside every
# prompt that includes them.
_NOT_A_WORKER_PROMPT = frozenset({"config_chat"})

# Functions whose `claude_p` call sends no shipped prompt: `leerie replay`
# re-sends a recorded call's own system prompt. Every other direct call must
# resolve to at least one prompt, so that no call drops out of the check
# unseen.
_SENDS_NO_SHIPPED_PROMPT = frozenset({"_replay_capture"})

# Tool names that are also ordinary English words, so a capital at a
# sentence's start may be just grammar ("Read the file."). Any other name
# (`Grep`, `Glob`, `WebFetch`) counts wherever it stands; a new CLI tool left
# out of this set errs toward a failure, never a miss.
_ALSO_A_WORD = frozenset({"Agent", "Edit", "Monitor", "Read", "Skill",
                          "Task", "Workflow", "Write"})

# Text a call adds to its prompts inside leerie.py, keyed
# `leerie.py:<line>`. `_recipients` fills it; each piece is joined with
# " and " because it follows a prompt, so its first word does not open a
# sentence.
_INLINE: dict[str, str] = {}


def _tree() -> ast.Module:
    return ast.parse(_LEERIE.read_text())


class _Flow:
    """The literal values that can reach an expression in leerie.py."""

    def __init__(self, tree: ast.Module) -> None:
        self.parents = {c: n for n in ast.walk(tree)
                        for c in ast.iter_child_nodes(n)}
        self.calls: dict[str, list[ast.Call]] = {}
        self.defs: dict[str, list[ast.AST]] = {}
        # Names also used otherwise than as a call's target (passed as a
        # callback, wrapped in `partial`): their calls are not all seen.
        self.escapes: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                self.calls.setdefault(n.func.id, []).append(n)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.defs.setdefault(n.name, []).append(n)
            elif (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                  and not (isinstance(self.parents.get(n), ast.Call)
                           and self.parents[n].func is n)):
                self.escapes.add(n.id)
            elif isinstance(n, ast.Attribute):  # `self.f(...)`, `m.f`
                self.escapes.add(n.attr)

    def scopes(self, node: ast.AST) -> list[ast.AST]:
        out = []
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.Lambda)):
                out.append(node)
        return out

    def _bindings(self, f: ast.AST, name: str):
        """`(node, value)` for each binding of `name` in `f`'s own scope (a
        nested function's binding counts only under `nonlocal`). `value` is
        None for a binding whose value this does not follow (a `for` or
        `with` target, an unpaired tuple), so the caller reports it."""
        for m in ast.walk(f):
            if isinstance(m, ast.Assign):
                pairs = [(t, m.value) for t in m.targets]
            elif isinstance(m, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                pairs = [(m.target, m.value)] if m.value is not None else []
            elif isinstance(m, (ast.For, ast.AsyncFor, ast.comprehension)):
                pairs = [(m.target, None)]
            elif isinstance(m, ast.withitem) and m.optional_vars is not None:
                pairs = [(m.optional_vars, None)]
            else:
                continue
            if not any(isinstance(n, ast.Name) and n.id == name
                       for t, _ in pairs for n in ast.walk(t)):
                continue
            owner = next((g for g in self.scopes(m) if g is f or not any(
                isinstance(n, ast.Nonlocal) and name in n.names
                for n in ast.walk(g))), None)
            if owner is not f:
                continue
            while pairs:
                t, v = pairs.pop()
                if isinstance(t, ast.Name):
                    if t.id == name:
                        yield (m, v)
                elif isinstance(t, (ast.Subscript, ast.Attribute)):
                    base = t
                    while isinstance(base, (ast.Subscript, ast.Attribute)):
                        base = base.value
                    if isinstance(base, ast.Name) and base.id == name:
                        yield (m, None)  # `common["allowed_tools"] = ...`
                elif isinstance(t, (ast.Tuple, ast.List)):
                    if (isinstance(v, (ast.Tuple, ast.List))
                            and len(v.elts) == len(t.elts)
                            and not any(isinstance(e, ast.Starred)
                                        for e in t.elts + v.elts)):
                        pairs += zip(t.elts, v.elts)
                    elif any(isinstance(e, ast.Name) and e.id == name
                             for e in ast.walk(t)):
                        # `sp, tools = _pick()`: the call, which the caller
                        # resolves (a `_load_prompt`) or reports.
                        yield (m, v if isinstance(v, ast.Call) else None)

    def leaves(self, expr: ast.AST, scopes: list[ast.AST],
               depth: int = 0, seen: frozenset = frozenset(),
               flatten: bool = True) -> list[ast.AST]:
        """`expr`'s terminal nodes: each local name is replaced by every
        value assigned to it, and each parameter by every argument a caller
        passes for it. A name already being followed (`p = p + extra`)
        contributes nothing more. A conditional gives both branches. With
        `flatten`, a concatenation or f-string gives its pieces; without, it
        is a leaf itself (a tools string's pieces add up, not alternate)."""
        if depth > 8:
            raise AssertionError(f"flow too deep at line {expr.lineno}")
        if isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load):
            for f in scopes:
                if isinstance(f, ast.Lambda):
                    if expr.id in {x.arg for x in ast.walk(f.args)
                                   if isinstance(x, ast.arg)}:
                        return [expr]  # its callers are not followed
                    continue
                key = (id(f), expr.id)
                if key in seen:
                    return []
                inner = seen | {key}
                found = []
                for m, value in self._bindings(f, expr.id):
                    if value is None:  # a binding this does not model
                        found.append(m)
                    else:
                        found += self.leaves(value, self.scopes(m),
                                             depth + 1, inner, flatten)
                a = f.args
                pos = a.posonlyargs + a.args
                params = pos + a.kwonlyargs
                defaults = dict(zip(pos[len(pos) - len(a.defaults):],
                                    a.defaults))
                defaults.update((p, d) for p, d in zip(a.kwonlyargs,
                                                       a.kw_defaults) if d)
                param = next((p for p in params if p.arg == expr.id), None)
                callers = self.calls.get(f.name, [])
                spread = any(isinstance(x, ast.Starred) for c in callers
                             for x in c.args) or any(
                    k.arg is None for c in callers for k in c.keywords)
                if param is not None and (f.name in self.escapes or spread):
                    # A call this cannot pair with the parameter: reported.
                    found.append(expr)
                elif param is not None:
                    omitted = not callers
                    for c in callers:
                        arg = next((k.value for k in c.keywords
                                    if k.arg == expr.id), None)
                        if (arg is None and param in pos
                                and pos.index(param) < len(c.args)):
                            arg = c.args[pos.index(param)]
                        if arg is not None:
                            found += self.leaves(arg, self.scopes(c),
                                                 depth + 1, inner, flatten)
                        else:
                            omitted = True
                    if omitted and param in defaults:
                        found += self.leaves(defaults[param],
                                             scopes[scopes.index(f) + 1:],
                                             depth + 1, inner, flatten)
                    elif omitted or not found:
                        found.append(expr)
                if found:
                    return found
            return [expr]
        if isinstance(expr, ast.IfExp):
            parts = [expr.body, expr.orelse]
        elif isinstance(expr, ast.BoolOp):
            parts = list(expr.values)
        elif flatten and isinstance(expr, (ast.BinOp, ast.JoinedStr,
                                           ast.FormattedValue)):
            parts = [c for c in ast.iter_child_nodes(expr)
                     if isinstance(c, ast.expr)]
        else:
            return [expr]
        return [x for c in parts
                for x in self.leaves(c, scopes, depth, seen, flatten)]


def _used_otherwise(flow: _Flow, call: ast.Call, name: str) -> bool:
    """Whether `name`, spread into `call`, is read anywhere in the function
    that binds it (closures included) other than as a `**` spread: a read
    can change the dict (`.update`, an item store) or pass it on."""
    owner = next((f for f in flow.scopes(call)
                  if any(True for _ in flow._bindings(f, name))), None)
    if owner is None:
        return True
    return any(isinstance(n, ast.Name) and n.id == name
               and isinstance(n.ctx, ast.Load)
               and not (isinstance(flow.parents.get(n), ast.keyword)
                        and flow.parents[n].arg is None)
               for n in ast.walk(owner))


def _kwargs(flow: _Flow, call: ast.Call) -> dict[str, list[ast.AST]]:
    """A `claude_p` call's keyword arguments, `**dict(...)` and `**{...}`
    spreads included. A spread of anything else is listed under `"**"`, so
    the call is reported rather than read in part."""
    out: dict[str, list[ast.AST]] = {}
    for k in call.keywords:
        if k.arg:
            out.setdefault(k.arg, []).append(k.value)
            continue
        if isinstance(k.value, ast.Name) and _used_otherwise(flow, call,
                                                              k.value.id):
            # `common.update(...)`, `common["k"] = ...`: what it spreads is
            # not its literal.
            out.setdefault("**", []).append(k.value)
            continue
        for d in flow.leaves(k.value, flow.scopes(call)):
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id == "dict" and not d.args
                    and all(kk.arg for kk in d.keywords)):
                for kk in d.keywords:
                    out.setdefault(kk.arg, []).append(kk.value)
            elif isinstance(d, ast.Dict) and all(
                    isinstance(kk, ast.Constant) for kk in d.keys):
                for kk, vv in zip(d.keys, d.values):
                    out.setdefault(kk.value, []).append(vv)
            else:
                out.setdefault("**", []).append(d)
    return out


def _granted(leerie, flow: _Flow, node: ast.AST, scopes: list[ast.AST],
             seen: frozenset = frozenset()) -> set[str] | None:
    """The bare tool names a tools expression grants, or None when it cannot
    be told. Concatenated pieces grant their union. A tools string built at
    run time (`_pr_review_tools`) is followed into the function that builds
    it: its grant is the narrowest of the leerie constants it names, since
    what it appends is `Bash(...)` verbs."""
    if isinstance(node, (ast.BinOp, ast.JoinedStr)):
        if id(node) in seen:  # `t = t + ","...`: its own piece again
            return None
        seen = seen | {id(node)}
        total: set[str] = set()
        for c in ast.iter_child_nodes(node):
            if not isinstance(c, ast.expr):
                continue
            piece = c.value if isinstance(c, ast.FormattedValue) else c
            alts = [_granted(leerie, flow, x, scopes, seen)
                    for x in flow.leaves(piece, scopes, flatten=False)]
            if not alts or any(a is None for a in alts):
                return None
            total |= set.intersection(*alts)
        return total
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return leerie._bare_tool_names(node.value) - {""}
    if isinstance(node, ast.Name) and isinstance(
            getattr(leerie, node.id, None), str):
        return leerie._bare_tool_names(getattr(leerie, node.id))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if id(node) in seen:  # `t = f(t)`: the call feeds its own argument
            return None
        seen = seen | {id(node)}
        consts = {n.id for f in flow.defs.get(node.func.id, [])
                  for n in ast.walk(f)
                  if isinstance(n, ast.Name) and n.id.endswith("_TOOLS")
                  and isinstance(getattr(leerie, n.id, None), str)}
        if consts:
            # And by the tools strings it is passed (`_with(base)`).
            passed = [g for a in node.args + [k.value for k in node.keywords]
                      for x in flow.leaves(a, scopes, flatten=False)
                      if (g := _granted(leerie, flow, x, scopes, seen))
                      is not None]
            return set.intersection(*(leerie._bare_tool_names(
                getattr(leerie, c)) for c in consts), *passed)
    return None


def _recipients(leerie) -> tuple[dict[str, list[tuple[str, int, set[str]]]],
                                 list[str]]:
    """prompt name -> [(schema key, line, callable tools)] for every
    `claude_p` call, and the calls the derivation could not resolve. Text a
    call adds to its prompts in leerie.py itself is keyed `leerie.py:<line>`
    and holds that text (`_INLINE`)."""
    _INLINE.clear()
    tree = _tree()
    flow = _Flow(tree)
    out: dict[str, list[tuple[str, int, set[str]]]] = {}
    unresolved: list[str] = []
    if "claude_p" in flow.escapes:
        unresolved.append("claude_p is referenced other than as a call "
                          "(an alias, a partial): those calls are unseen")
    for call in flow.calls.get("claude_p", []):
        kw = _kwargs(flow, call)
        scopes = flow.scopes(call)
        where = (getattr(scopes[0], "name", "<lambda>") if scopes
                 else "<module>")
        if where in _SENDS_NO_SHIPPED_PROMPT:
            continue
        if kw.get("**"):
            unresolved.append(f"line {call.lineno} in {where}: spread "
                              f"{ast.unparse(kw['**'][0])[:60]!r}")
            continue
        # Every piece of the system prompt is a literal `_load_prompt` or a
        # string constant; a piece that is neither could carry a prompt the
        # check never sees.
        prompts: set[str] = set()
        inline: list[str] = []
        leaves = [leaf for e in kw.get("system_prompt", [])
                  for leaf in flow.leaves(e, scopes)]
        for leaf in leaves:
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                inline.append(leaf.value)
                continue
            if (isinstance(leaf, ast.Call)
                    and getattr(leaf.func, "id", "") == "_load_prompt"
                    and len(leaf.args) == 1 and not leaf.keywords
                    and isinstance(leaf.args[0], ast.Constant)):
                prompts.add(leaf.args[0].value)
                continue
            unresolved.append(f"line {call.lineno} in {where}: system "
                              f"prompt piece {ast.unparse(leaf)[:60]!r}")
        if not prompts:
            unresolved.append(f"line {call.lineno} in {where}: no shipped "
                              "prompt resolved")
            continue
        keys = sorted({leaf.value for e in kw.get("schema_key", [])
                       for leaf in flow.leaves(e, scopes)
                       if isinstance(leaf, ast.Constant)}) or ["?"]
        grants = [_granted(leerie, flow, leaf, scopes)
                  for e in kw.get("allowed_tools", [])
                  for leaf in flow.leaves(e, scopes, flatten=False)]
        if not grants or any(g is None for g in grants):
            unresolved.append(f"line {call.lineno} ({', '.join(keys)}): "
                              "tools not resolved")
            continue
        have = ((set.intersection(*grants)
                 - leerie._bare_tool_names(leerie.DISALLOWED_TOOLS))
                | CLI_UNGATED)
        if inline:
            name = f"leerie.py:{call.lineno}"
            _INLINE[name] = "".join(" and " + piece for piece in inline)
            prompts = prompts | {name}
        for p in prompts:
            for k in keys:
                out.setdefault(p, []).append((k, call.lineno, have))
    return out, unresolved


def _named_tools(text: str, vocabulary: set[str]) -> dict[str, list[int]]:
    """Tool name -> the lines that name it as a tool (see the module
    docstring for what counts)."""
    hits: dict[str, list[int]] = {}
    for name in vocabulary:
        for m in re.finditer(r"(?<![-\w])" + re.escape(name) + r"(?!\w)",
                             text):
            i = m.start()
            backticked = i > 0 and text[i - 1] == "`"
            if (text[m.end():m.end() + 1] == "-" and not backticked
                    and name in _ALSO_A_WORD):  # `Read-only`, `Task-Context`
                continue
            # A wrapped line continues its sentence; a sentence starts after
            # terminal punctuation or an opening quote, after a blank line,
            # or as a line's first word behind a heading, list, quote or
            # table marker.
            line = text[text.rfind("\n", 0, i) + 1:i]
            gap = text[:i][len(text[:i].rstrip()):]
            before = text[:i].rstrip()
            initial = (not before or before[-1] in ".!?:\"'"
                       or re.search(r"\n\s*\n", gap) is not None
                       or re.fullmatch(r"\s*(#+|>|\||[-*+]|\d+\.)\s*[*_]*",
                                       line) is not None)
            if backticked or not initial or name not in _ALSO_A_WORD:
                hits.setdefault(name, []).append(text.count("\n", 0, i) + 1)
    return hits


@pytest.fixture(scope="module")
def recipients(leerie):
    return _recipients(leerie)


def test_every_worker_prompt_is_paired_with_its_calls(recipients):
    """Anti-vacuity: a prompt the derivation loses is a prompt nothing
    checks, so every shipped worker prompt must reach at least one
    `claude_p` call, and every call must resolve to its prompts (unless it
    sends none: `_SENDS_NO_SHIPPED_PROMPT`) and its tools."""
    paired, unresolved = recipients
    assert not unresolved, f"calls the derivation cannot check: {unresolved}"
    shipped = {p.stem for p in (_REPO / "prompts").glob("*.md")
               if not p.stem.startswith("_")} - _NOT_A_WORKER_PROMPT
    assert shipped - set(paired) == set(), (
        "no claude_p call found for these prompts -- the pairing broke, so "
        f"their tools are unchecked: {sorted(shipped - set(paired))}")
    # The shared prompt is paired with both of its workers.
    assert {k for k, _, _ in paired["delivery_judge"]} >= {
        "delivery_judge", "delivery_ratchet"}


def test_a_prompt_names_only_tools_its_workers_can_call(leerie, recipients):
    paired, _ = recipients
    vocabulary = set(leerie.KNOWN_TOOLS)
    failures = []
    for prompt, sites in sorted(paired.items()):
        inline = prompt in _INLINE
        text = _INLINE[prompt] if inline else leerie._load_prompt(prompt)
        named = _named_tools(text, vocabulary)
        for key, line, have in sites:
            for tool, lines in sorted(named.items()):
                if tool not in have:
                    failures.append(
                        (f"text the call at {prompt} adds to its prompt "
                         f"names {tool}" if inline else
                         f"prompts/{prompt}.md names {tool} at line(s) "
                         f"{lines} of its include-expanded text")
                        + f"; {key} (leerie.py:{line}) cannot call it")
    assert not failures, "\n".join(sorted(set(failures)))


def test_the_matcher_counts_a_tool_and_skips_prose():
    vocab = {"Grep", "Glob", "Read", "Task", "Write"}
    assert _named_tools("Use `Read`, `Grep`, `Glob`-style here.", vocab) == {
        "Read": [1], "Grep": [1], "Glob": [1]}
    # `Grep-based` names the tool; `Read-only` is a word in a compound.
    assert _named_tools("a Grep-based Read-only scan", vocab) == {"Grep": [1]}
    assert _named_tools("search with Grep and\nGlob, then", vocab) == {
        "Grep": [1], "Glob": [2]}
    assert _named_tools("Read the file. Write a plan.\n"
                        "the P1 Task-Context Fit\n"
                        '"Write captured envelopes"\n'
                        "- Read files on disk\n"
                        "  1. **Read its contract.**", vocab) == {}
    # A name that is not also a word counts at a sentence's start too.
    assert _named_tools("Grep before you write it. Tools: Glob", vocab) == {
        "Grep": [1], "Glob": [1]}
