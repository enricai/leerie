"""Regression pins for the image's `ENV PATH=...` construction.

/home/leerie/.local/bin: `pip install --user` lands console scripts (e.g.
`pre-commit`) here. Without that dir on PATH they are unreachable whenever
a caller doesn't resolve them through an absolute path — the observed case
being a git hook whose embedded interpreter path points at a Python that
doesn't have the package installed.

Position is load-bearing, not incidental: the entry sits *after* $PATH so
image-baked tooling (the mise shims and the LTS Node bin that carry
`claude` itself — see the PATH comment in the Dockerfile) always wins
over anything a repo's setup step later `pip install --user`s. Moving it
ahead of $PATH would let a user-installed package silently shadow a baked-in
binary, which is why the ordering is pinned here rather than just membership.

/home/leerie/.local/share/mise/shims (`MISE_DATA_DIR/shims`): populated at
runtime by `mise install` reading a repo's .tool-versions / .ruby-version /
rust-toolchain.toml (DESIGN §6½ "Worker-driven install"). Without this dir
on PATH, a worker's own ad-hoc Bash commands (e.g. `bin/rails test`) fail
with `env: 'ruby': No such file or directory` on their first invocation
every single session, even though `phase_provision` already installed the
pinned runtime — the worker then has to rediscover `mise exec --` from
scratch. Position is load-bearing here too, in the opposite direction from
`.local/bin`: it sits *before* $PATH (so a repo's pinned runtime is actually
reachable) but *after* the system mise shims and the LTS Node bin, so a
repo pinning its own Node/Python version can never shadow the baked LTS
Node that hosts `claude` itself.

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


def test_mise_user_shims_is_on_path():
    """Per-repo mise-installed runtimes must be reachable by name."""
    assert "/home/leerie/.local/share/mise/shims" in _env_path_line()


def test_mise_user_shims_comes_after_baked_tooling():
    """A repo's pinned Node/Python must never shadow the LTS Node that
    hosts `claude` itself."""
    line = _env_path_line()
    assert (
        line.index("/usr/local/share/mise/shims")
        < line.index("/usr/local/share/mise/installs/node/lts-current/bin")
        < line.index("/home/leerie/.local/share/mise/shims")
    )


def test_mise_user_shims_comes_before_inherited_path():
    """A repo's pinned runtime must win over the generic inherited PATH —
    otherwise it's unreachable without an explicit `mise exec --`."""
    line = _env_path_line()
    assert line.index("/home/leerie/.local/share/mise/shims") < line.index(
        "$PATH"
    )


def test_the_system_has_a_python_for_the_unpinned_shim():
    """A mise `python` shim with no pinned version falls through to the
    system `python`. Debian ships only `python3`, so the base apt layer
    installs python-is-python3; without it, a planner verify argv such as
    `python -m pytest` failed in every run ("No version is set for shim:
    python"), and a sibling-delivered no-op could not settle by execution.
    Measured: with /usr/bin/python present, `python --version` through the
    shim resolves to the system 3.13."""
    text = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text()
    apt = text[text.index("RUN apt-get update && apt-get install"):]
    apt = apt[:apt.index("rm -rf /var/lib/apt/lists/*")]
    assert "python-is-python3" in apt.split()
