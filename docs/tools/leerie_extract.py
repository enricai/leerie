#!/usr/bin/env python3
"""Deterministic structural extractor for a Python module (stdlib only).

This is the tool that generates the facts in `docs/ANALYSIS.md`. It treats a
Python source file the way a compiler front end treats source text, in four
stages:

  1. PREPROCESS  - read source, record provenance (path, sha256, line/byte size)
  2. LEX         - tokenize, count token classes, rank identifiers/keywords/ops
  3. PARSE/AST   - ast.parse, walk the tree, extract the declared surface
                   (imports, module constants, functions, classes, methods)
  4. SEMANTIC    - intra-module call graph + per-function ordered call sequence
                   (the "grammar" of the control flow)

It is pure stdlib and has NO side effects on the analysed file, so re-running it
on the same input always yields the same output. That reproducibility is the
whole point: every number in ANALYSIS.md can be regenerated with one command.

Usage:
    python3 docs/tools/leerie_extract.py orchestrator/leerie.py [out.json]

Full JSON goes to stdout (and to out.json if given); a short human digest goes
to stderr.
"""
from __future__ import annotations

import ast
import collections
import hashlib
import io
import json
import keyword
import re
import sys
import token as token_mod
import tokenize


def first_line(s: str | None) -> str | None:
    if not s:
        return None
    for ln in s.splitlines():
        if ln.strip():
            return ln.strip()
    return None


def to_str(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparse-failed>"


def arg_to_str(a: ast.arg, default: ast.AST | None) -> str:
    s = a.arg
    ann = to_str(a.annotation)
    if ann:
        s += f": {ann}"
    if default is not None:
        s += f" = {to_str(default)}"
    return s


def signature(fn) -> str:
    a = fn.args
    parts: list[str] = []
    pos = list(getattr(a, "posonlyargs", [])) + list(a.args)
    pad = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    posonly = list(getattr(a, "posonlyargs", []))
    for i, arg in enumerate(posonly):
        parts.append(arg_to_str(arg, pad[i]))
    if posonly:
        parts.append("/")
    for j, arg in enumerate(a.args):
        parts.append(arg_to_str(arg, pad[len(posonly) + j]))
    if a.vararg:
        parts.append("*" + arg_to_str(a.vararg, None))
    elif a.kwonlyargs:
        parts.append("*")
    for k, arg in enumerate(a.kwonlyargs):
        parts.append(arg_to_str(arg, a.kw_defaults[k] if k < len(a.kw_defaults) else None))
    if a.kwarg:
        parts.append("**" + arg_to_str(a.kwarg, None))
    return "(" + ", ".join(parts) + ")"


def func_record(fn, qual: str) -> dict:
    end = getattr(fn, "end_lineno", fn.lineno)
    return {
        "name": fn.name, "qualname": qual,
        "async": isinstance(fn, ast.AsyncFunctionDef),
        "lineno": fn.lineno, "end_lineno": end, "span": end - fn.lineno + 1,
        "decorators": [to_str(d) for d in getattr(fn, "decorator_list", []) or []],
        "signature": signature(fn), "returns": to_str(fn.returns),
        "doc": first_line(ast.get_docstring(fn)),
    }


def describe_value(node) -> dict:
    if isinstance(node, ast.Dict):
        keys = [k.value if isinstance(k, ast.Constant) else (to_str(k) if k else "**spread")
                for k in node.keys]
        return {"kind": "dict", "len": len(node.keys), "keys": keys}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        vals = [e.value if isinstance(e, ast.Constant) else to_str(e) for e in node.elts]
        return {"kind": type(node).__name__.lower(), "len": len(node.elts), "values": vals}
    if isinstance(node, ast.Constant):
        return {"kind": type(node.value).__name__, "value": node.value}
    return {"kind": "expr", "expr": (to_str(node) or "")[:200]}


def main() -> None:
    path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    src = open(path, encoding="utf-8").read()
    sha = hashlib.sha256(src.encode("utf-8")).hexdigest()

    # ---- LEX ----
    tok_counts, name_counts = collections.Counter(), collections.Counter()
    kw_counts, op_counts = collections.Counter(), collections.Counter()
    strings: list[str] = []
    total, comment_chars = 0, 0
    try:
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            tok_counts[token_mod.tok_name[t.type]] += 1
            total += 1
            if t.type == token_mod.NAME:
                (kw_counts if keyword.iskeyword(t.string) else name_counts)[t.string] += 1
            elif t.type == token_mod.OP:
                op_counts[t.string] += 1
            elif t.type == token_mod.COMMENT:
                comment_chars += len(t.string)
            elif t.type == token_mod.STRING:
                strings.append(t.string)
    except (tokenize.TokenError, IndentationError) as e:
        print(f"tokenize error: {e}", file=sys.stderr)
    env_vars = sorted(set(re.findall(r"LEERIE_[A-Z0-9_]+", "\n".join(strings))))
    exit_syms = sorted(set(re.findall(r"\bEXIT_[A-Z0-9_]+\b", src)))

    # ---- PARSE / AST ----
    tree = ast.parse(src, filename=path)
    node_counts = collections.Counter(type(n).__name__ for n in ast.walk(tree))
    imports, constants, functions, classes = [], [], [], []
    func_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [a.name + (f" as {a.asname}" if a.asname else "") for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
            imports.append(f"from {mod} import {names}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(func_record(node, node.name))
            func_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            methods = [func_record(b, f"{node.name}.{b.name}") for b in node.body
                       if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
            end = getattr(node, "end_lineno", node.lineno)
            classes.append({"name": node.name, "bases": [to_str(b) for b in node.bases],
                            "lineno": node.lineno, "end_lineno": end, "span": end - node.lineno + 1,
                            "doc": first_line(ast.get_docstring(node)), "methods": methods})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    rec = {"name": t.id, "lineno": node.lineno}
                    rec.update(describe_value(node.value) if node.value else {"kind": "annotation-only"})
                    constants.append(rec)

    # ---- SEMANTIC (intra-module call graph + ordered call sequences) ----
    callgraph: dict[str, list[str]] = {}
    ordered: dict[str, list[str]] = {}

    class Scope(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def _enter(self, node, qual):
            # Collect calls with source position so the sequence reflects
            # textual order. ast.walk yields BFS order, which is meaningless
            # for reading control flow. NOTE: source order is still not
            # execution order (branches/loops), so true pipeline order must be
            # verified by reading driver bodies, not inferred from this list.
            hits = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    c = sub.func
                    nm = c.id if isinstance(c, ast.Name) else (c.attr if isinstance(c, ast.Attribute) else None)
                    if nm and nm in func_names:
                        hits.append((getattr(sub, "lineno", 0), getattr(sub, "col_offset", 0), nm))
            hits.sort()
            seq = [nm for _, _, nm in hits]
            edges = []
            for nm in seq:
                if nm not in edges:
                    edges.append(nm)
            callgraph[qual], ordered[qual] = edges, seq

        def visit_FunctionDef(self, node):
            self._enter(node, ".".join(self.stack + [node.name]) if self.stack else node.name)
        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            for b in node.body:
                self.visit(b)
            self.stack.pop()

    Scope().visit(tree)
    fan_in = collections.Counter(c for callees in callgraph.values() for c in callees)
    fan_out = {k: len(v) for k, v in callgraph.items()}

    result = {
        "preprocess": {"path": path, "sha256": sha, "lines": len(src.splitlines()),
                       "bytes": len(src.encode("utf-8")), "module_doc": first_line(ast.get_docstring(tree))},
        "lex": {"total_tokens": total, "token_class_counts": dict(tok_counts.most_common()),
                "keyword_counts": dict(kw_counts.most_common(25)),
                "top_identifiers": dict(name_counts.most_common(60)),
                "top_operators": dict(op_counts.most_common(25)),
                "comment_chars": comment_chars, "string_literal_count": len(strings),
                "env_vars": env_vars, "exit_symbols": exit_syms},
        "ast": {"node_class_counts": dict(node_counts.most_common()), "imports": imports,
                "constant_count": len(constants), "function_count": len(functions),
                "class_count": len(classes)},
        "constants": constants, "functions": functions, "classes": classes,
        "callgraph": {"fan_in_top": dict(fan_in.most_common(40)),
                      "fan_out_top": dict(sorted(fan_out.items(), key=lambda kv: -kv[1])[:40]),
                      "edges": callgraph, "ordered_calls": ordered},
    }
    payload = json.dumps(result, indent=2, default=str)
    if out_path:
        open(out_path, "w").write(payload)
    print(payload)
    e = sys.stderr
    print(f"\n=== {path} sha={sha[:12]} ===", file=e)
    print(f"lines={len(src.splitlines())} tokens={total} ast_nodes={sum(node_counts.values())} "
          f"functions={len(functions)} classes={len(classes)} constants={len(constants)}", file=e)


if __name__ == "__main__":
    main()
