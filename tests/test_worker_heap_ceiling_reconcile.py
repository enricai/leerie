"""N14-16 preflight: the per-worker memory ceiling reconciles with a
repo's own BLT-declared `--max-old-space-size` (`_declared_node_heap_bytes`
feeding `resolve_worker_memory_max`, orchestrator/leerie.py:5035-5106).

Node's declared heap overrides whatever NODE_OPTIONS leerie injects for a
worker subprocess, so a resolved cgroup ceiling smaller than
heap + `_NODE_HEAP_HEADROOM_BYTES` guarantees an in-cgroup OOM regardless
of how the ceiling was sized. This file exercises the reconciliation via a
synthetic `.leerie/config.toml` (so no real Node install is needed):

- An auto-derived ceiling is silently raised to heap + headroom when the
  repo declares a heap the current auto-derivation would undershoot.
- An explicit (CLI/env/file) ceiling that undershoots the declared heap is
  refused with an actionable die() naming --worker-memory-max, rather than
  silently overridden.
- A repo with no declared heap, or a repo whose control command declares a
  small heap comfortably under the resolved ceiling, is unaffected.

On current main (pre-fix) `resolve_worker_memory_max` never reads BLT
commands at all, so every "raised" assertion here fails and every die()
assertion never fires.
"""
from __future__ import annotations

import pytest


def _write_config(repo_root, *, test_cmd=None, typecheck_cmd=None):
    """Write a `.leerie/config.toml` declaring BLT axes. `typecheck_cmd`
    is folded into `build`, since resolve_blt's axes are build/lint/test
    and the repo's own typecheck step commonly rides the build axis."""
    leerie_dir = repo_root / ".leerie"
    leerie_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    if test_cmd is not None:
        lines.append(f'test = "{test_cmd}"')
    if typecheck_cmd is not None:
        lines.append(f'build = "{typecheck_cmd}"')
    (leerie_dir / "config.toml").write_text("\n".join(lines) + "\n")


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    return tmp_path


# ---- _declared_node_heap_bytes ---------------------------------------------

def test_declared_node_heap_bytes_extracts_max_across_axes(leerie, repo_root):
    """funeralworks' own measured shape: NODE_OPTIONS=--max-old-space-size=8192
    prefixed onto the test command."""
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_declared_node_heap_bytes_takes_max_of_multiple_axes(leerie, repo_root):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=4096 pnpm test",
        typecheck_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm tsc",
    )
    assert leerie._declared_node_heap_bytes(repo_root) == 8192 * 1024 * 1024


def test_declared_node_heap_bytes_none_when_undeclared(leerie, repo_root):
    _write_config(repo_root, test_cmd="pnpm test")
    assert leerie._declared_node_heap_bytes(repo_root) is None


def test_declared_node_heap_bytes_none_with_no_config(leerie, repo_root):
    assert leerie._declared_node_heap_bytes(repo_root) is None


# ---- resolve_worker_memory_max: auto-derived ceiling gets raised ----------

def test_auto_derived_ceiling_raised_above_declared_heap(leerie, repo_root, monkeypatch):
    """A repo declaring an 8192 MiB heap must resolve to a ceiling of at
    least 8192 MiB + headroom, even though nothing (CLI/env/file) pins the
    ceiling explicitly. Auto-derivation itself is pinned to a known-low
    6 GiB value (below the 8192 MiB + 2048 MiB headroom this repo needs)
    so the assertion is independent of the test host's real memory —
    only the reconciliation logic under test is exercised."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    needed = 8192 * 1024 * 1024 + leerie._NODE_HEAP_HEADROOM_BYTES
    assert result >= needed, (
        f"resolved ceiling {result} did not reconcile with the declared "
        f"8192 MiB heap (needed >= {needed})"
    )
    assert result == needed


def test_auto_derived_ceiling_unaffected_without_declared_heap(leerie, repo_root, monkeypatch):
    """Control: no declared heap -> auto-derivation is untouched by the
    reconciliation logic."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    baseline = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)

    _write_config(repo_root, test_cmd="pnpm test")
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result == baseline


def test_auto_derived_ceiling_unaffected_by_small_declared_heap(leerie, repo_root, monkeypatch):
    """Control: a repo whose command declares a heap comfortably under the
    existing auto-derived ceiling is unaffected — reconciliation only ever
    raises, never lowers, and only raises when undershooting."""
    monkeypatch.setattr(leerie, "_auto_worker_memory_max", lambda max_parallel: 6 * 1024**3)
    baseline = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)

    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=1024 pnpm test",
    )
    result = leerie.resolve_worker_memory_max(repo_root, max_parallel=4)
    assert result == baseline


# ---- resolve_worker_memory_max: explicit ceilings die() instead ----------

def test_explicit_cli_value_dies_when_undershooting_declared_heap(leerie, repo_root):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4, cli_value="4G")


def test_die_message_names_worker_memory_max_flag(leerie, repo_root, capsys):
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4, cli_value="4G")
    captured = capsys.readouterr()
    assert "--worker-memory-max" in (captured.out + captured.err)


def test_explicit_env_value_dies_when_undershooting_declared_heap(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "4G")
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_explicit_file_value_dies_when_undershooting_declared_heap(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("worker_memory_max = 4G\n")
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=8192 pnpm test",
    )
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(repo_root, max_parallel=4)


def test_explicit_cli_value_unaffected_without_declared_heap(leerie, repo_root):
    """Control: an explicit value with no declared heap resolves exactly
    as before — no reconciliation logic engaged at all."""
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3


def test_explicit_cli_value_passes_when_it_covers_declared_heap(leerie, repo_root):
    """Control: an explicit ceiling that already comfortably covers the
    declared heap + headroom is left exactly as given."""
    _write_config(
        repo_root,
        test_cmd="NODE_OPTIONS=--max-old-space-size=1024 pnpm test",
    )
    assert leerie.resolve_worker_memory_max(
        repo_root, max_parallel=4, cli_value="4G") == 4 * 1024**3
