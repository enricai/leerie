"""The launch-block derivation lives in exactly one place.

`tests/launcher_blocks.py` derives the launcher's orchestrator launch blocks
(the `child_env = dict(os.environ)` regions inside each unquoted `<<PY`
heredoc). Several guards consume it, and every one of them must derive rather
than enumerate — a hard-coded list of runtimes stops covering reality the moment
one is added, which is how `--runtime ec2` shipped recording `leerie_commit` as
null while a test *named* `..._fly_ec2_path_too` passed.

The derivation was then written twice, once per consuming test file, replicating
the block marker, the heredoc terminator, and the preamble window. Two copies of
a rule drift exactly the way two copies of a list do: change the launcher's
heredoc structure and one copy gets fixed while the other keeps passing. This
guard makes a third copy impossible to add quietly.
"""
from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = "launcher_blocks.py"

# The load-bearing token of the derivation, in its ESCAPED regex form. Prose
# describing the concept writes the marker plainly (`child_env = dict(...)`);
# only an actual implementation escapes the paren for `re`. Matching the plain
# substring instead would report a file that merely *documents* why the
# derivation is shared as if it re-implemented it — and the natural response to
# a false-positive guard is to weaken it, which is how guards die.
_MARKER = r"child_env = dict\("


def _files_matching_marker() -> dict[str, int]:
    """Every file under tests/ containing the block marker, and its count."""
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        # Skip this file: it necessarily names the marker to check for it.
        if path.name == Path(__file__).name:
            continue
        n = text.count(_MARKER)
        if n:
            hits[path.name] = n
    return hits


def test_only_the_shared_module_derives_launch_blocks() -> None:
    hits = _files_matching_marker()
    strays = {name: n for name, n in hits.items() if name != OWNER}
    assert not strays, (
        f"{sorted(strays)} re-implement the launch-block derivation that "
        f"tests/{OWNER} owns. Import `launch_env_blocks` from it instead — two "
        f"copies of this rule drift the same way two copies of a runtime list "
        f"do, which is the defect class PRs #180-#183 exist to close."
    )


def test_the_owner_actually_contains_it() -> None:
    """Anti-vacuity: a scan that matches nothing passes the test above.

    If the marker string ever stops appearing in the shared module — renamed,
    refactored, or the scan simply broken — this fails as a broken scan rather
    than silently certifying "no duplicates found".
    """
    hits = _files_matching_marker()
    assert OWNER in hits, (
        f"tests/{OWNER} does not contain {_MARKER!r} — the duplicate scan is "
        f"matching nothing, so its result above is meaningless")


def test_the_shared_module_is_actually_used() -> None:
    """A single owner nobody imports is dead code, not deduplication."""
    importers = [
        p.name for p in sorted(TESTS_DIR.rglob("*.py"))
        if p.name not in (OWNER, Path(__file__).name)
        and "launch_env_blocks" in p.read_text(errors="replace")
    ]
    assert len(importers) >= 2, (
        f"expected at least the two known consumers to import "
        f"`launch_env_blocks`, found {importers}")
