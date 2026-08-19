"""The three seed-helper shell functions have exactly one definition each.

`refactor-001` moved `_seed_timeout_prefix`, `_seed_use_shallow`, and
`_seed_branch_shallow_safe` out of their duplicated homes in
`scripts/remote/seed-repo.sh` / `scripts/remote/seed-auth.sh` /
`scripts/remote/ec2-seed-repo.sh` / `scripts/remote/ec2-seed-auth.sh` into a
single shared `scripts/remote/seed-common.sh`. Nothing stops a future edit
from reintroducing a copy in one of the consumer scripts — this is a
structural regression guard against exactly that, mirroring the single-owner
discipline in `tests/test_no_duplicate_launcher_blocks.py` and
`tests/test_no_duplicate_ec2_knob.py`.

Each definition marker is anchored to the START OF A LINE. A real
`name() {` definition opens at column 0; a reference to the name (a call
site, a comment, an extractor string) is always mid-line. Matching the bare
token instead would flag every caller, which is the false-positive class
`test_no_duplicate_launcher_blocks.py` documents for the same reason.
"""
from __future__ import annotations

import re
from pathlib import Path

REMOTE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "remote"

_HELPER_NAMES = (
    "_seed_timeout_prefix",
    "_seed_use_shallow",
    "_seed_branch_shallow_safe",
)


def _marker_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(name)}\(\) \{{", re.MULTILINE)


def _definition_sites(name: str) -> dict[str, int]:
    """Every scripts/remote/*.sh file DEFINING `name`, and its count there."""
    pattern = _marker_re(name)
    hits: dict[str, int] = {}
    for path in sorted(REMOTE_DIR.glob("*.sh")):
        text = path.read_text(errors="replace")
        n = len(pattern.findall(text))
        if n:
            hits[path.name] = n
    return hits


def test_each_helper_is_defined_exactly_once_repo_wide() -> None:
    for name in _HELPER_NAMES:
        sites = _definition_sites(name)
        total = sum(sites.values())
        assert total == 1, (
            f"`{name}` should be defined exactly once across "
            f"scripts/remote/*.sh, found {total} definition(s): {sites} — "
            f"a duplicate has been reintroduced outside seed-common.sh"
        )


def test_each_helper_is_defined_in_seed_common_sh() -> None:
    for name in _HELPER_NAMES:
        sites = _definition_sites(name)
        assert sites == {"seed-common.sh": 1}, (
            f"`{name}` is expected to be defined only in seed-common.sh, "
            f"found: {sites}"
        )


def test_anti_vacuity_the_scan_can_find_a_real_definition() -> None:
    """A scan matching nothing would certify 'no duplicates' forever.

    Pin that each marker actually matches the real, live definition site so
    a rename/refactor that breaks the scan fails loudly here instead of
    silently passing the tests above.
    """
    for name in _HELPER_NAMES:
        sites = _definition_sites(name)
        assert sites, (
            f"expected to find at least one definition of `{name}` in "
            f"scripts/remote/*.sh — the scan's marker is stale and its "
            f"'exactly once' result above is meaningless"
        )
