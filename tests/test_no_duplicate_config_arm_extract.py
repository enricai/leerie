"""The `config)` case-arm derivation lives in exactly one place.

`tests/config_arm_extract.py` is the single derivation of the launcher's
`config)` case arm (see its own module docstring). `test_config_verb.py` and
`test_config_recapture.py` both used to define a byte-identical
`_extract_config_arm()` locally — two copies of the same rule, which drift
the same way two copies of a list do (see `tests/launcher_blocks.py` and
`tests/test_no_duplicate_launcher_splitters.py` for the established
precedent this guard mirrors). This test makes a third local copy impossible
to add quietly.
"""
from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = "config_arm_extract.py"

# The load-bearing token: an actual local reproduction defines the function.
# Consumers only ever reference the name (as an import or an alias), never
# redefine it — matching on `def ` is what distinguishes a reproduction from
# a reference.
_MARKER = "def _extract_config_arm("


def _files_defining_marker() -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        # Skip this file: it necessarily names the marker to check for it.
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = text.count(_MARKER)
        if n:
            hits[path.name] = n
    return hits


def test_no_file_locally_redefines_extract_config_arm() -> None:
    hits = _files_defining_marker()
    assert not hits, (
        f"{sorted(hits)} locally define `_extract_config_arm`, reintroducing "
        f"the duplicate this dedup removed. Import `extract_config_arm` from "
        f"tests/{OWNER} instead (aliased as `_extract_config_arm` if needed)."
    )


def test_owner_actually_defines_it() -> None:
    text = (TESTS_DIR / OWNER).read_text()
    assert "def extract_config_arm(" in text, (
        f"tests/{OWNER} no longer defines extract_config_arm — "
        "the derivation this guard protects has moved or been removed."
    )


def test_both_consumers_import_from_the_owner() -> None:
    for consumer in ("test_config_verb.py", "test_config_recapture.py"):
        text = (TESTS_DIR / consumer).read_text()
        assert "from tests.config_arm_extract import extract_config_arm" in text, (
            f"{consumer} no longer imports extract_config_arm from the "
            f"shared module tests/{OWNER}."
        )
