"""Regression pins for /home/leerie/.local/bin on the image's PATH.

`pip install --user` lands console scripts (e.g. `pre-commit`) in
/home/leerie/.local/bin. Without that dir on PATH they are unreachable
whenever a caller doesn't resolve them through an absolute path — the
observed case being a git hook whose embedded interpreter path points at
a Python that doesn't have the package installed.

Position is load-bearing, not incidental: the entry sits *after* $PATH so
image-baked tooling (the mise shims and the LTS Node bin that carry
`claude` itself — see the PATH comment in the Dockerfile) always wins
over anything a repo's setup step later `pip install --user`s. Moving it
ahead of $PATH would let a user-installed package silently shadow a baked-in
binary, which is why the ordering is pinned here rather than just membership.

Source-coupling tests (mirroring tests/test_tmp_cache_writable.py and
tests/test_home_leerie_ownership.py): the real container behavior needs a
built image, which the plain pytest suite doesn't exercise.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _env_path_line() -> str:
    """The image's `ENV PATH=...` line (the one that sets the base PATH).

    Matched on the `ENV PATH=` prefix rather than any single entry so the
    extractor doesn't bake in the very value the tests below assert on.
    """
    for line in DOCKERFILE.read_text().splitlines():
        if line.startswith("ENV PATH="):
            return line
    raise AssertionError("no `ENV PATH=` line found in Dockerfile")


def test_user_local_bin_is_on_path():
    """`pip install --user` console scripts must be reachable by name."""
    assert "/home/leerie/.local/bin" in _env_path_line()


def test_user_local_bin_comes_after_inherited_path():
    """Image-baked tooling must win over user-installed packages.

    A `pip install --user` package that happens to ship a binary named
    like a baked-in one (or like `claude` itself) must not shadow it, so
    the user bin dir has to sit after $PATH, never before.
    """
    line = _env_path_line()
    assert line.index("$PATH") < line.index("/home/leerie/.local/bin")


def test_user_local_bin_is_last_entry():
    """Pin the strongest form of the ordering above: nothing follows it."""
    value = _env_path_line().split("=", 1)[1]
    assert value.split(":")[-1] == "/home/leerie/.local/bin"
