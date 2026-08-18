"""Tests for _parse_memory_size, _auto_worker_memory_max, and
resolve_worker_memory_max — the resolver chain for the per-worker
cgroup memory cap.

Covers:
- Memory-size parsing: K/M/G/T suffixes, bare bytes, garbage rejected.
- Auto-derivation from /proc/meminfo (mocked) — splits VM ram across
  max_parallel+1 slots, floored at 8 GiB (build + resident claude
  measured peak ~6.3 GiB).
- Resolution order: CLI > env > leerie.toml > auto.
- die() paths for invalid env / file values.
"""
from __future__ import annotations

import pytest


# ---- _parse_memory_size ---------------------------------------------------

def test_parse_memory_size_bytes(leerie):
    assert leerie._parse_memory_size("1024", "ctx") == 1024


def test_parse_memory_size_kib(leerie):
    assert leerie._parse_memory_size("4K", "ctx") == 4 * 1024


def test_parse_memory_size_mib(leerie):
    assert leerie._parse_memory_size("512M", "ctx") == 512 * 1024**2


def test_parse_memory_size_gib(leerie):
    assert leerie._parse_memory_size("4G", "ctx") == 4 * 1024**3


def test_parse_memory_size_tib(leerie):
    assert leerie._parse_memory_size("1T", "ctx") == 1024**4


def test_parse_memory_size_lowercase_suffix(leerie):
    assert leerie._parse_memory_size("2g", "ctx") == 2 * 1024**3


def test_parse_memory_size_whitespace_tolerated(leerie):
    assert leerie._parse_memory_size("  256M  ", "ctx") == 256 * 1024**2


def test_parse_memory_size_empty_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("", "ctx")


def test_parse_memory_size_garbage_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("4XYZ", "ctx")


def test_parse_memory_size_negative_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("-4G", "ctx")


def test_parse_memory_size_zero_dies(leerie):
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("0", "ctx")


def test_parse_memory_size_fractional_rejected(leerie):
    """We reject '1.5G' rather than rounding silently. The user can
    write '1536M' if they need fractional values."""
    with pytest.raises(SystemExit):
        leerie._parse_memory_size("1.5G", "ctx")


# ---- _auto_worker_memory_max ---------------------------------------------

def test_auto_splits_meminfo_across_slots(leerie, monkeypatch, tmp_path):
    """Synthesize a /proc/meminfo with 128 GiB total. With max_parallel=4
    the per-worker share is 128 / 5 = 25.6 GiB; that's above the 8 GiB
    floor, so the even split wins.

    _auto_worker_memory_max tries the shared-slice basis
    (_cgroup_slice_info) first and only falls back to this
    /proc/meminfo-derived legacy basis when that's unavailable — force
    the fallback so this test exercises the legacy path it documents,
    independent of whether a real cgroup broker happens to be reachable
    in the host running the suite (e.g. inside a leerie worker
    container, where /run/leerie-cgroup.sock is live)."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {128 * 1024 * 1024} kB\n")
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            return real_open(meminfo, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    result = leerie._auto_worker_memory_max(max_parallel=4)
    expected = (128 * 1024**3) // 5
    assert result == expected


def test_auto_floors_at_8gib(leerie, monkeypatch, tmp_path):
    """With a 16 GiB VM / max_parallel=5, the even split (16 / 6 ≈
    2.67 GiB) is well under the measured 6.3 GiB build+claude peak
    (see docstring on _auto_worker_memory_max), so the 8 GiB floor
    wins — the fix for build-running workers cgroup-OOMing under the
    old 4 GiB clamp.

    Forces the shared-slice basis unavailable so the legacy
    /proc/meminfo path under test actually runs — see the comment on
    test_auto_splits_meminfo_across_slots."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal:       {16 * 1024 * 1024} kB\n")
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            return real_open(meminfo, *a, **kw)
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    assert leerie._auto_worker_memory_max(max_parallel=5) == 8 * 1024**3


def test_auto_fallback_when_meminfo_missing(leerie, monkeypatch):
    """No /proc/meminfo on macOS host (where the test suite usually
    runs). Auto returns the 2 GiB fallback.

    Forces the shared-slice basis unavailable so the legacy
    /proc/meminfo path under test actually runs — see the comment on
    test_auto_splits_meminfo_across_slots."""
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    import builtins
    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/meminfo":
            raise FileNotFoundError(2, "No such file", "/proc/meminfo")
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)
    assert leerie._auto_worker_memory_max(max_parallel=4) == 2 * 1024**3


# ---- resolve_worker_memory_max --------------------------------------------

@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    return tmp_path


def test_cli_value_wins(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "2G")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "2G")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4) == 2 * 1024**3


def test_file_used_when_cli_and_env_unset(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 1G\n")
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4) == 1024**3


def test_auto_fallback_when_nothing_set(leerie, repo_root):
    """No CLI, no env, no file — auto-derive from /proc/meminfo (or
    its 2 GiB fallback on macOS)."""
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result > 0


def test_garbage_env_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "garbage")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_garbage_file_dies(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = garbage\n")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


# ---- _is_node_repo (P9 node-repo detection) --------------------------------

def test_is_node_repo_package_json(leerie, tmp_path):
    (tmp_path / "package.json").write_text("{}\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_pnpm_lock(leerie, tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_package_lock(leerie, tmp_path):
    (tmp_path / "package-lock.json").write_text("{}\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_yarn_lock(leerie, tmp_path):
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_false_for_non_node_repo(leerie, tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n")
    assert leerie._is_node_repo(str(tmp_path)) is False


def test_is_node_repo_false_for_empty_dir(leerie, tmp_path):
    assert leerie._is_node_repo(str(tmp_path)) is False


# ---- NODE_OPTIONS derivation (P9) ------------------------------------------
#
# The derivation itself (inside `_invoke`) is:
#     reserve_mb = _NODE_HEAP_HEADROOM_BYTES // (1024*1024)
#     cap_mb = max(worker_memory_max_bytes // (1024*1024) - reserve_mb, 256)
#     worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
# gated on `worker_memory_max_bytes is not None and _is_node_repo(cwd)`.
#
# The reserve is READ FROM THE MODULE here, never re-typed. It is the same
# quantity `resolve_worker_memory_max` adds to a declared heap — Node's
# non-heap RSS plus the resident claude process — and the two sites were
# once out of step (injection on 2048 while the constant said 2432), which
# handed Node a heap 384 MiB larger than its cgroup could hold. Hard-coding
# it in these tests is what would let that recur silently.
# `_invoke` builds the whole subprocess environment inline rather than
# through a standalone helper, so these tests pin the arithmetic and the
# gating condition directly (mirroring the derivation's own inline
# formula) rather than invoking the full async subprocess-spawning
# function, which the rest of this file's tests never do either.

def _reserve_mb(leerie) -> int:
    return leerie._NODE_HEAP_HEADROOM_BYTES // (1024 * 1024)


def test_node_heap_headroom_is_2432_mib(leerie):
    """Pin the VALUE, not just that everything derives from it.

    Every other assertion about the reserve in this suite is now expressed
    as `_reserve_mb(leerie)`, and `_invoke`'s injection reads the same
    constant — which is the coupling we want, but it means all of them move
    together. Setting the constant to `243 * 1024 * 1024` left the ENTIRE
    suite green: the derivations agreed with each other about a reserve an
    order of magnitude too small, and nothing said where they should agree.

    The two anchors that used to hold the number down — a literal
    `--max-old-space-size=6144` assertion and a `"- 2048"` source pin — were
    both removed when those sites were converted to derive from the
    constant, so this replaces them. Same reasoning as
    `test_orphan_min_age_is_one_hour` and
    `test_destroy_drain_budget_is_ten_seconds` in the broker suite.

    The decomposition (docs/DESIGN.md, and the comment at the definition):

        Node's own non-heap RSS   ~2.08 GiB   derived
        the resident `claude -p`  ~0.32 GiB   measured live
                                  ---------
                                  ~2.40 GiB   -> rounded up to 2432 MiB

    Under-sizing it hands Node a heap larger than fits the cage and the
    worker OOMs; over-sizing it wastes the difference on every worker.
    """
    assert leerie._NODE_HEAP_HEADROOM_BYTES == 2432 * 1024 * 1024


def _node_options_value(leerie, cap_bytes: int) -> str:
    cap_mb = max(cap_bytes // (1024 * 1024) - _reserve_mb(leerie), 256)
    return f"--max-old-space-size={cap_mb}"


def test_node_options_arithmetic_8gib_floor(leerie):
    """8 GiB floor (the common auto-derived case) minus the reserve."""
    cap_bytes = 8 * 1024**3
    expected = 8192 - _reserve_mb(leerie)
    assert _node_options_value(leerie, cap_bytes) == \
        f"--max-old-space-size={expected}"


def test_node_options_arithmetic_larger_auto_derived_cap(leerie):
    """A larger auto-derived cap (e.g. 25.6 GiB from a well-resourced
    host splitting across max_parallel=4) still subtracts exactly the
    reserve, not a percentage or other scaling."""
    cap_bytes = (128 * 1024**3) // 5  # matches test_auto_splits_meminfo_across_slots
    cap_mb = cap_bytes // (1024 * 1024)
    assert _node_options_value(leerie, cap_bytes) == \
        f"--max-old-space-size={cap_mb - _reserve_mb(leerie)}"


def test_node_options_clamped_when_cap_below_reserve(leerie):
    """An operator-set cap below the reserve (e.g. via
    --worker-memory-max/LEERIE_WORKER_MEMORY_MAX/leerie.toml) must not
    produce a non-positive or degenerately small V8 heap ceiling."""
    cap_bytes = 512 * 1024**2  # 512 MiB, well under the reserve
    assert _node_options_value(leerie, cap_bytes) == "--max-old-space-size=256"


def test_node_options_present_for_node_repo(leerie, tmp_path):
    """End-to-end: replicate `_invoke`'s gating + derivation inline
    against a real Node-repo fixture (package.json present)."""
    (tmp_path / "package.json").write_text("{}\n")
    worker_memory_max_bytes = 8 * 1024**3
    worker_env = None
    if worker_memory_max_bytes is not None and leerie._is_node_repo(str(tmp_path)):
        import os as _os
        worker_env = _os.environ.copy()
        cap_mb = max(worker_memory_max_bytes // (1024 * 1024)
                     - _reserve_mb(leerie), 256)
        worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
    assert worker_env is not None
    assert worker_env["NODE_OPTIONS"] == \
        f"--max-old-space-size={8192 - _reserve_mb(leerie)}"


def test_node_options_absent_for_non_node_repo(leerie, tmp_path):
    """The same gating logic against a non-Node repo fixture: no
    package.json/lockfile present, so NODE_OPTIONS is never set."""
    (tmp_path / "requirements.txt").write_text("pytest\n")
    worker_memory_max_bytes = 8 * 1024**3
    worker_env = None
    if worker_memory_max_bytes is not None and leerie._is_node_repo(str(tmp_path)):
        import os as _os
        worker_env = _os.environ.copy()
        cap_mb = max(worker_memory_max_bytes // (1024 * 1024)
                     - _reserve_mb(leerie), 256)
        worker_env["NODE_OPTIONS"] = f"--max-old-space-size={cap_mb}"
    assert worker_env is None


# ---- Source-coupling guard: the tests above vs. _invoke's REAL code -------
#
# Every test above replicates `_invoke`'s NODE_OPTIONS block by hand rather
# than calling `_invoke` (a real async subprocess-spawning function this
# file's fixtures never exercise). That means none of them would notice if
# the real block's reserve constant, clamp floor, or gating condition
# changed. This class extracts the actual source of `_invoke` and asserts,
# structurally, that the constants/condition the tests assume are still
# what the shipping code does — so a drift in the real formula/gate fails
# here even though nothing above calls `_invoke` itself.

import ast
import inspect


def _invoke_node_options_source(leerie) -> str:
    src = inspect.getsource(leerie._invoke)
    # Isolate the `if worker_memory_max_bytes is not None and _is_node_repo(...)`
    # block specifically, not the whole (much larger) function body.
    marker = "if worker_memory_max_bytes is not None and _is_node_repo(cwd):"
    assert marker in src, (
        "_invoke's NODE_OPTIONS gate condition text changed — the tests in "
        "this file assume `worker_memory_max_bytes is not None and "
        "_is_node_repo(cwd)` verbatim; update both the gate and this guard "
        "together if the condition is intentionally changing."
    )
    start = src.index(marker)
    # Grab a generous window after the marker covering the block body.
    return src[start:start + 400]


def test_invoke_node_options_gate_condition_unchanged(leerie):
    """The gate must require BOTH a resolved memory cap AND a Node repo —
    an `or` here would set NODE_OPTIONS on non-Node repos (nonsensical) or
    skip it for Node repos with no memory cap resolved (defeats the fix)."""
    block = _invoke_node_options_source(leerie)
    assert "worker_memory_max_bytes is not None and _is_node_repo(cwd)" in block


def test_invoke_node_options_reserve_constant_unchanged(leerie):
    """The reserve must be READ from `_NODE_HEAP_HEADROOM_BYTES`, not
    re-typed as a literal.

    This pin used to assert the literal `- 2048`, which is a weaker
    property: it kept the injection frozen while
    `resolve_worker_memory_max` moved its own copy of the same quantity to
    2432, and the two silently disagreed — leerie granting Node a heap
    384 MiB bigger than the cgroup it had to live in. Coupling to the
    shared constant makes that state unrepresentable: change the constant
    and both the reconciliation floor and the injected heap move together,
    or neither does."""
    block = _invoke_node_options_source(leerie)
    assert "_NODE_HEAP_HEADROOM_BYTES" in block, (
        "_invoke's NODE_OPTIONS derivation must reference "
        "_NODE_HEAP_HEADROOM_BYTES, not a re-typed literal — that is how "
        "it drifted out of step with resolve_worker_memory_max before."
    )
    assert "- 2048" not in block, (
        "a literal reserve reappeared in _invoke's NODE_OPTIONS derivation"
    )


def test_invoke_node_options_clamp_floor_unchanged(leerie):
    """The clamp floor (256 MiB) prevents a non-positive or degenerate V8
    heap ceiling when an operator sets a cap below the reserve."""
    block = _invoke_node_options_source(leerie)
    assert ", 256)" in block, (
        "The 256 MiB clamp floor in _invoke's NODE_OPTIONS derivation "
        "changed — update test_node_options_clamped_when_cap_below_reserve "
        "to match."
    )


def test_invoke_node_options_env_key_and_format_unchanged(leerie):
    """The env var name and --max-old-space-size flag format must match
    what every test above asserts on."""
    block = _invoke_node_options_source(leerie)
    assert '"NODE_OPTIONS"' in block
    assert "--max-old-space-size={cap_mb}" in block


def test_invoke_node_options_formula_ast_matches_helper(leerie):
    """Parse `_invoke`'s real NODE_OPTIONS assignment as an AST and confirm
    it computes `max(worker_memory_max_bytes // (1024*1024) - reserve_mb, 256)` —
    structurally, not by substring — so a reformulation that keeps the
    substrings above (e.g. reordering the max() args, or changing the
    divisor) still gets caught."""
    src = inspect.getsource(leerie._invoke)
    tree = ast.parse(src)

    found = []

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "cap_mb"
            ):
                found.append(node.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert found, "no `cap_mb = ...` assignment found inside _invoke"
    call = found[0]
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "max"
    assert len(call.args) == 2
    floor_arg = call.args[1]
    assert isinstance(floor_arg, ast.Constant) and floor_arg.value == 256

    sub_expr = call.args[0]
    assert isinstance(sub_expr, ast.BinOp) and isinstance(sub_expr.op, ast.Sub)
    # The subtrahend must be a NAME (the reserve derived from
    # `_NODE_HEAP_HEADROOM_BYTES`), never an `ast.Constant`. A constant here
    # is exactly the drift that let the injection and the reconciliation
    # disagree about the same quantity.
    assert not isinstance(sub_expr.right, ast.Constant), (
        "the reserve is a hard-coded literal again; it must derive from "
        "_NODE_HEAP_HEADROOM_BYTES"
    )
    assert isinstance(sub_expr.right, ast.Name), (
        f"expected a name for the reserve, got {ast.dump(sub_expr.right)}"
    )
    # ...and that name must actually BE derived from the constant. Asserting
    # only "it is a Name" accepts any local, so
    #     reserve_mb = 2432          # _NODE_HEAP_HEADROOM_BYTES
    #     cap_mb = max(... - reserve_mb, 256)
    # passes every check above — the literal is back, the coupling is gone,
    # and the accompanying comment makes the `"_NODE_HEAP_HEADROOM_BYTES" in
    # block` substring check pass too. Resolve the binding instead.
    reserve_name = sub_expr.right.id
    bindings = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == reserve_name
    ]
    assert bindings, f"`{reserve_name}` is used but never assigned in _invoke"
    assert any(
        isinstance(sub, ast.Name)
        and sub.id == "_NODE_HEAP_HEADROOM_BYTES"
        for b in bindings for sub in ast.walk(b.value)
    ), (
        f"`{reserve_name}` is not derived from _NODE_HEAP_HEADROOM_BYTES; "
        f"got {[ast.unparse(b.value) for b in bindings]} — the reserve is a "
        f"literal wearing a name"
    )

    floordiv_expr = sub_expr.left
    assert isinstance(floordiv_expr, ast.BinOp) and isinstance(floordiv_expr.op, ast.FloorDiv)
    divisor = floordiv_expr.right
    # 1024 * 1024
    assert isinstance(divisor, ast.BinOp) and isinstance(divisor.op, ast.Mult)
    assert isinstance(divisor.left, ast.Constant) and divisor.left.value == 1024
    assert isinstance(divisor.right, ast.Constant) and divisor.right.value == 1024
