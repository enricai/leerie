"""`_make_stub_timeout` and `_stub_timeout` each have exactly one
definition, in tests/stub_helpers.py.

Six test files previously each defined a byte-identical no-op `timeout`
bash-stub builder (`_make_stub_timeout`). Nothing stops a future edit from
reintroducing a copy in one of the consumer files — this is a structural
regression guard against exactly that, mirroring the single-owner
discipline in `tests/test_no_duplicate_seed_shell_helpers.py` and
`tests/test_no_duplicate_launcher_blocks.py`.

`_stub_timeout` is the distinct, intentionally different killing variant
(it asserts the cap actually *fires*, whereas `_make_stub_timeout` is a
no-op passthrough) that was previously duplicated byte-for-byte between
tests/test_ec2_ssm.py and tests/test_ec2_transport.py — this file's guard
originally declared that variant out of scope; it is now covered by the
same definition-count + anti-vacuity discipline below.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

_MARKER_RE = re.compile(r"^def _make_stub_timeout\(", re.MULTILINE)
_KILLING_MARKER_RE = re.compile(r"^def _stub_timeout\(", re.MULTILINE)


def _definition_sites() -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.glob("*.py")):
        text = path.read_text(errors="replace")
        n = len(_MARKER_RE.findall(text))
        if n:
            hits[path.name] = n
    return hits


def _killing_definition_sites() -> dict[str, int]:
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.glob("*.py")):
        text = path.read_text(errors="replace")
        n = len(_KILLING_MARKER_RE.findall(text))
        if n:
            hits[path.name] = n
    return hits


def test_defined_exactly_once_repo_wide() -> None:
    sites = _definition_sites()
    total = sum(sites.values())
    assert total == 1, (
        f"`_make_stub_timeout` should be defined exactly once across "
        f"tests/*.py, found {total} definition(s): {sites} — a duplicate "
        f"has been reintroduced outside tests/stub_helpers.py"
    )


def test_defined_only_in_stub_helpers() -> None:
    sites = _definition_sites()
    assert sites == {"stub_helpers.py": 1}, (
        f"`_make_stub_timeout` is expected to be defined only in "
        f"tests/stub_helpers.py, found: {sites}"
    )


def test_anti_vacuity_the_scan_can_find_a_real_definition() -> None:
    """A scan matching nothing would certify 'no duplicates' forever."""
    sites = _definition_sites()
    assert sites, (
        "expected to find at least one definition of `_make_stub_timeout` "
        "in tests/*.py — the scan's marker is stale and its guards above "
        "are vacuous"
    )


def test_killing_variant_defined_exactly_once_repo_wide() -> None:
    sites = _killing_definition_sites()
    total = sum(sites.values())
    assert total == 1, (
        f"`_stub_timeout` (the group-killing variant) should be defined "
        f"exactly once across tests/*.py, found {total} definition(s): "
        f"{sites} — a duplicate has been reintroduced outside "
        f"tests/stub_helpers.py"
    )


def test_killing_variant_defined_only_in_stub_helpers() -> None:
    sites = _killing_definition_sites()
    assert sites == {"stub_helpers.py": 1}, (
        f"`_stub_timeout` (the group-killing variant) is expected to be "
        f"defined only in tests/stub_helpers.py, found: {sites}"
    )


def test_killing_variant_anti_vacuity_the_scan_can_find_a_real_definition() -> None:
    """A scan matching nothing would certify 'no duplicates' forever."""
    sites = _killing_definition_sites()
    assert sites, (
        "expected to find at least one definition of `_stub_timeout` "
        "in tests/*.py — the scan's marker is stale and its guards above "
        "are vacuous"
    )
