"""No two `tests/*.py` files may define a body-identical `_extract*`/
`extract_*` helper without one importing the other.

This generalizes `tests/test_no_duplicate_launcher_blocks.py`'s single-owner
discipline from bash markers to python function bodies. That file's own
docstring records the shape: five bash-harness files each hand-copied a
launcher block into a string literal, converting the five fixed those
instances, and three MORE reproductions were found afterwards outside the
original list. `_extract*`-named python helpers are the same recurring
pattern (CLAUDE.md's "Testing" section documents a run of these being found
and deduplicated one at a time — `_extract_bedrock_functions`,
`_extract_run_argv`, `_extract_config_arm`, `_extract_block`, ... — each
fixed individually, never as a class). This guard is the standing check that
stops the *next* one landing unnoticed, rather than another one-off fix.

Two same-named `_extract*`/`extract_*` functions in different files with
STRUCTURALLY IDENTICAL bodies (ignoring docstrings — a comment-only diff is
not a real divergence) are a reproduction unless one file imports the other's
definition. A genuinely distinct pair — e.g. `_extract_guard` in one file
walking totally different source text than `_extract_guard` in another — is
not flagged; only same-body pairs are.

Scoped to `_extract`/`extract_` prefixes specifically (not every test
helper) to avoid false positives on unrelated same-named functions that
happen to share a name for other reasons; this is the exact recurring
reproduction class named in CLAUDE.md's "Testing" section and in
`tests/test_no_duplicate_launcher_blocks.py`'s own docstring.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"


def _iter_test_files() -> list[Path]:
    return sorted(
        p for p in TESTS_DIR.glob("*.py")
        if p.name != Path(__file__).name
    )


def _strip_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


def _normalized_body_dump(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """AST dump of the function's body with docstring and line/col
    metadata stripped, so two textually-reformatted-but-identical bodies
    still compare equal, and a docstring-only diff never counts as a real
    divergence."""
    body = _strip_docstring(node)
    return "\n".join(ast.dump(stmt, annotate_fields=True, include_attributes=False)
                      for stmt in body)


def _extractor_defs(path: Path) -> list[tuple[str, str]]:
    """Return (function_name, normalized_body_dump) for every top-level
    `_extract*`/`extract_*` function defined in `path`. Deliberately
    module-level only (not nested/class methods) — every real reproduction
    in this repo's history is a module-level harness function."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if not (name.startswith("_extract") or name.startswith("extract_")):
            continue
        dump = _normalized_body_dump(node)
        if not dump:
            continue
        out.append((name, dump))
    return out


def _imports_name(path: Path, name: str) -> bool:
    """True when `path` imports `name` from elsewhere (the exemption for a
    file that legitimately reuses another file's extractor rather than
    reproducing it — e.g. `from tests.launcher_blocks import
    launch_env_blocks`)."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == name or alias.asname == name:
                    return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.asname or alias.name
                if imported == name:
                    return True
    return False


def _find_duplicate_extractor_pairs(
    files: list[Path],
) -> list[tuple[str, Path, Path]]:
    """Return (name, file_a, file_b) for every pair of files that each
    define a same-named `_extract*`/`extract_*` function with a
    structurally identical body, where neither file imports the name from
    the other (or from a shared owner)."""
    by_name: dict[str, list[tuple[Path, str]]] = {}
    for f in files:
        for name, dump in _extractor_defs(f):
            by_name.setdefault(name, []).append((f, dump))

    dupes = []
    for name, defs in by_name.items():
        for i in range(len(defs)):
            for j in range(i + 1, len(defs)):
                file_a, dump_a = defs[i]
                file_b, dump_b = defs[j]
                if dump_a != dump_b:
                    continue
                if _imports_name(file_a, name) or _imports_name(file_b, name):
                    continue
                dupes.append((name, file_a, file_b))
    return dupes


def test_no_duplicate_extractor_function_bodies():
    """The real guard: on the current tree, no two test files define a
    body-identical `_extract*`/`extract_*` helper without one importing the
    other."""
    dupes = _find_duplicate_extractor_pairs(_iter_test_files())
    assert not dupes, (
        "Duplicate extractor-helper bodies found (reproduction, not reuse) "
        "— move the shared body into one module and have both files "
        "import it, following tests/launcher_blocks.py's pattern:\n"
        + "\n".join(f"  {name}: {a.name} == {b.name}" for name, a, b in dupes)
    )


def test_scan_finds_files_at_all():
    """Anti-vacuity: a scan that finds zero test files, or zero
    `_extract*`/`extract_*` definitions anywhere, would certify 'no
    duplicates' forever without checking anything."""
    files = _iter_test_files()
    assert len(files) > 20

    total_defs = sum(len(_extractor_defs(f)) for f in files)
    assert total_defs > 10


def test_planted_duplicate_is_detected(tmp_path):
    """Anti-vacuity: two files each defining a body-identical
    `_extract_planted_thing` (no import relationship between them) must be
    flagged. Without this, a broken comparison (e.g. one that always
    returns unequal, or an import-detector that always exempts) would pass
    the real-tree test vacuously."""
    body = (
        "def _extract_planted_thing():\n"
        "    x = 1 + 2\n"
        "    return x\n"
    )
    file_a = tmp_path / "test_planted_a.py"
    file_b = tmp_path / "test_planted_b.py"
    file_a.write_text(body)
    file_b.write_text(body)

    dupes = _find_duplicate_extractor_pairs([file_a, file_b])
    names = [d[0] for d in dupes]
    assert "_extract_planted_thing" in names


def test_known_good_import_is_exempt(tmp_path):
    """Anti-vacuity partner: a file that legitimately imports another
    file's extractor (rather than redefining it) must NOT be flagged, even
    though the imported function is textually present in the importing
    file's `ast.parse` in the trivial sense of appearing in its import
    statement. This proves the guard distinguishes reuse from
    reproduction."""
    owner = tmp_path / "shared_extract.py"
    owner.write_text(
        "def extract_shared_thing():\n"
        "    return 1 + 2\n"
    )
    consumer = tmp_path / "test_consumer.py"
    consumer.write_text(
        "from shared_extract import extract_shared_thing\n"
        "\n"
        "\n"
        "def extract_shared_thing_wrapper():\n"
        "    return extract_shared_thing()\n"
    )
    # A second, genuinely independent file redefining the SAME name with
    # the SAME body as the owner would still be a reproduction even though
    # a sibling file imports it correctly — the exemption is per-file.
    reproducer = tmp_path / "test_reproducer.py"
    reproducer.write_text(
        "def extract_shared_thing():\n"
        "    return 1 + 2\n"
    )

    dupes = _find_duplicate_extractor_pairs([owner, consumer, reproducer])
    names = [d[0] for d in dupes]
    # owner vs reproducer: same name, same body, neither imports the other
    # from the other's perspective -> flagged.
    assert "extract_shared_thing" in names
    # consumer defines no such function at all (only imports it), so it
    # cannot appear as either side of a flagged pair.
    flagged_files = {p.name for _, a, b in dupes for p in (a, b)}
    assert consumer.name not in flagged_files


def test_docstring_only_diff_is_still_a_duplicate(tmp_path):
    """Two bodies differing only in their docstring text are the same
    reproduction under a fresh comment — the guard must not be fooled into
    treating a docstring rewrite as a real divergence."""
    file_a = tmp_path / "test_doc_a.py"
    file_a.write_text(
        "def _extract_doc_thing():\n"
        "    \"\"\"First copy's docstring.\"\"\"\n"
        "    return 1 + 2\n"
    )
    file_b = tmp_path / "test_doc_b.py"
    file_b.write_text(
        "def _extract_doc_thing():\n"
        "    \"\"\"A completely different docstring here.\"\"\"\n"
        "    return 1 + 2\n"
    )

    dupes = _find_duplicate_extractor_pairs([file_a, file_b])
    names = [d[0] for d in dupes]
    assert "_extract_doc_thing" in names


def test_genuinely_different_bodies_are_not_flagged(tmp_path):
    """The negative control: two same-named extractors with different
    bodies (the common, legitimate case — e.g. this repo's
    `_extract_fn` in test_auto_detect_run_runtime.py vs
    test_resolve_repo_image_tag.py, confirmed to differ) must not be
    flagged."""
    file_a = tmp_path / "test_diff_a.py"
    file_a.write_text(
        "def _extract_fn(name):\n"
        "    return name + '_a'\n"
    )
    file_b = tmp_path / "test_diff_b.py"
    file_b.write_text(
        "def _extract_fn(name):\n"
        "    return name.upper()\n"
    )

    dupes = _find_duplicate_extractor_pairs([file_a, file_b])
    assert dupes == []
