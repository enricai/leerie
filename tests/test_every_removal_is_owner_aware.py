"""Every removal goes through the one owner-aware removal (DESIGN §6 *Every
deleter of a run's directories deletes them*; review round 19 claims M1).

A plain `rmtree(..., ignore_errors=True)` cannot unlink the entries of a
read-only directory and says nothing; 25 plain `rmtree` calls were left in
the orchestrator after the deleters of run directories were fixed, many under
the run directory (helper checkouts, `repo-settings/`, the measured
coverage directory, which repository code writes). The rule is derived, not
listed: no removal anywhere but the owner-aware one, so a new plain one
fails here whatever path it names.
"""
from __future__ import annotations

import ast
import re

from tests.conftest import REPO_ROOT


def _rmtree_calls(tree: ast.AST) -> list[int]:
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, (ast.Attribute, ast.Name))
            and (n.func.attr if isinstance(n.func, ast.Attribute)
                 else n.func.id) == "rmtree"]


def test_the_orchestrator_removes_only_through_rmtree_owned():
    """`rmtree` is called in one place: inside `_rmtree_owned`, after it
    restored the bits."""
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    own = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "_rmtree_owned")
    inside = set(_rmtree_calls(own))
    assert len(inside) == 1
    assert set(_rmtree_calls(tree)) == inside


def test_the_launchers_prune_removes_only_through_rm_owned():
    """The launcher's host Python has its own copy (`_rm_owned`); a plain
    `shutil.rmtree` is allowed only inside it, after it restored the
    bits."""
    text = (REPO_ROOT / "leerie").read_text()
    start = text.index("def _rm_owned(")
    end = text.index("\ndef ", start + 1)
    for m in re.finditer(r"shutil\.rmtree\(", text):
        assert start < m.start() < end, text[m.start() - 200:m.start() + 60]


def test_the_scripts_remove_only_through_leerie_rm_owned():
    """In the scripts leerie runs, an `rm -r` is the shell's owner-aware
    removal itself (`leerie_rm_owned`) or of a `mktemp` directory the same
    script made; anything else in a run or state directory goes through
    `leerie_rm_owned`."""
    for path in sorted((REPO_ROOT / "scripts").glob("*.sh")):
        text = path.read_text()
        own = None
        if "leerie_rm_owned() {" in text:
            a = text.index("leerie_rm_owned() {")
            own = (a, text.index("\n}\n", a))
        for m in re.finditer(r"^[^#\n]*\brm -(?:rf|fr)\b[^\n]*", text, re.M):
            line = m.group(0)
            if line.lstrip().startswith("echo"):
                continue
            if own and own[0] < m.start() < own[1]:
                continue
            var = re.search(r'"\$\{?(\w+)\}?"', line)
            assert var and re.search(
                rf'\b{var.group(1)}="?\$\(mktemp\b', text), (path.name, line)
