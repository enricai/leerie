"""Tests for N14-16 (M9): reconciling the per-worker memory ceiling with a
repo's declared Node heap (--max-old-space-size).

Root cause: Node 20+ ignores host/cgroup memory once --max-old-space-size is
explicitly set -- the heap adjusts to that literal limit and Node throws OOM
if crossed, regardless of container size. A repo's own build/lint/test
command declaring a heap bigger than the per-worker cgroup ceiling
guarantees an in-cgroup OOM that leerie's own NODE_OPTIONS injection (P9)
cannot prevent, since the repo's inline env assignment overrides it for
that one subprocess.

Covers:
- _declared_node_heap_bytes: scans resolve_blt()'s effective build/lint/test
  commands for --max-old-space-size=N, taking the max across axes; None
  when no command declares it.
- resolve_worker_memory_max: raises an auto-derived ceiling above
  heap + headroom; refuses (die()) an explicit --worker-memory-max/env/file
  value that is too small, naming --worker-memory-max; refuses (die()) when
  even the full shared slice budget cannot fit the declared heap; leaves
  today's behavior byte-for-byte unchanged when no heap is declared
  (regression fixture).
"""
from __future__ import annotations

import pytest


def _write_config(tmp_path, **kv):
    leerie_dir = tmp_path / ".leerie"
    leerie_dir.mkdir(exist_ok=True)
    lines = [f'{k} = "{v}"\n' for k, v in kv.items()]
    (leerie_dir / "config.toml").write_text("".join(lines))


# ---- _declared_node_heap_bytes ---------------------------------------------

def test_no_config_no_declared_heap(leerie, tmp_path):
    assert leerie._declared_node_heap_bytes(tmp_path) is None


def test_declared_heap_in_test_command(leerie, tmp_path):
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 next test")
    assert leerie._declared_node_heap_bytes(tmp_path) == 8192 * 1024 * 1024


def test_declared_heap_takes_max_across_axes(leerie, tmp_path):
    _write_config(
        tmp_path,
        test="NODE_OPTIONS=--max-old-space-size=8192 next test",
        lint="NODE_OPTIONS=--max-old-space-size=4096 eslint .",
        build="next build",
    )
    assert leerie._declared_node_heap_bytes(tmp_path) == 8192 * 1024 * 1024


def test_no_flag_in_declared_commands_returns_none(leerie, tmp_path):
    _write_config(tmp_path, test="pytest", build="make build")
    assert leerie._declared_node_heap_bytes(tmp_path) is None


def test_empty_declared_axis_tolerated(leerie, tmp_path):
    _write_config(tmp_path, test="")
    assert leerie._declared_node_heap_bytes(tmp_path) is None


def test_declared_heap_of_zero_is_a_real_value_not_none(leerie, tmp_path):
    """--max-old-space-size=0 is a syntactically valid (if unusual) Node
    flag. It must parse to 0, distinct from "no flag declared" (None) --
    0 is falsy in Python, so a caller checking truthiness rather than
    identity-with-None would silently treat this the same as "nothing
    declared"."""
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=0 next test")
    assert leerie._declared_node_heap_bytes(tmp_path) == 0


# ---- resolve_worker_memory_max: regression (no declared heap) -------------

def test_no_declared_heap_behavior_unchanged(leerie, tmp_path, monkeypatch):
    """A repo with no declared heap must resolve exactly like before this
    fix -- the plain CLI/env/file/auto chain, untouched."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    assert leerie.resolve_worker_memory_max(
        tmp_path, max_parallel=4, cli_value="4G") == 4 * 1024**3


def test_no_declared_heap_auto_path_unchanged(leerie, tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: 9 * 1024**3)
    assert leerie.resolve_worker_memory_max(
        tmp_path, max_parallel=4) == 9 * 1024**3


# ---- resolve_worker_memory_max: declared heap raises the auto-derived cage

def test_declared_heap_raises_auto_derived_ceiling(leerie, tmp_path, monkeypatch):
    """Fixture BLT config declaring --max-old-space-size=8192 must push the
    computed worker memory ceiling above 8192 MiB + headroom, when the
    slice has room for it."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 next test")
    # Auto-derivation would otherwise produce a ceiling well under the
    # declared heap (mirrors the real funeralworks failure: a 9.45 GiB
    # auto-derived cage against an 8 GiB heap leaves ~1 GiB for
    # everything else).
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: int(6.3 * 1024**3))
    # Plenty of slice room so the raise can actually happen.
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (64 * 1024**3, 2, 4 * 1024**3))
    result = leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)
    heap_bytes = 8192 * 1024 * 1024
    assert result > heap_bytes
    assert result >= heap_bytes + leerie._NODE_HEAP_HEADROOM_BYTES


def test_declared_heap_already_covered_by_auto_ceiling_is_a_noop(
        leerie, tmp_path, monkeypatch):
    """When the auto-derived ceiling already exceeds heap + headroom,
    resolve_worker_memory_max must not perturb it."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=1024 jest")
    auto_val = 9 * 1024**3
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: auto_val)
    assert leerie.resolve_worker_memory_max(
        tmp_path, max_parallel=4) == auto_val


def test_declared_heap_raise_bounded_when_slice_unknown(
        leerie, tmp_path, monkeypatch):
    """No broker / containment off: still raises to heap + headroom on a
    best-effort basis (nothing to gate against, matching the fail-open
    convention used elsewhere in this module)."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: int(6.3 * 1024**3))
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    result = leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)
    heap_bytes = 8192 * 1024 * 1024
    assert result >= heap_bytes + leerie._NODE_HEAP_HEADROOM_BYTES


def test_declared_heap_of_zero_still_reconciled_not_skipped(
        leerie, tmp_path, monkeypatch):
    """--max-old-space-size=0 must not be mistaken for "no declared heap".
    0 is Python-falsy, so a truthiness check (`if not declared_heap_bytes`)
    rather than an identity check (`is None`) would silently bypass the
    reconciliation for exactly this value. Force the auto-derived ceiling
    below headroom alone so the raise is only observable if reconciliation
    actually ran."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=0 jest")
    auto_val = leerie._NODE_HEAP_HEADROOM_BYTES // 2
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: auto_val)
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    result = leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)
    assert result >= leerie._NODE_HEAP_HEADROOM_BYTES
    assert result != auto_val


# ---- resolve_worker_memory_max: die() paths --------------------------------

def test_explicit_cli_value_too_small_dies_naming_the_flag(
        leerie, tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(
            tmp_path, max_parallel=4, cli_value="1G")


def test_explicit_env_value_too_small_dies(leerie, tmp_path, monkeypatch):
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    monkeypatch.setenv("LEERIE_WORKER_MEMORY_MAX", "1G")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)


def test_explicit_file_value_too_small_dies(leerie, tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    (tmp_path / "leerie.toml").write_text("worker_memory_max = 1G\n")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)


def test_die_message_names_worker_memory_max_flag(leerie, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(
            tmp_path, max_parallel=4, cli_value="1G")
    captured = capsys.readouterr()
    assert "--worker-memory-max" in (captured.err + captured.out)


def test_cannot_fit_within_slice_dies(leerie, tmp_path, monkeypatch):
    """When even the whole shared slice budget cannot cover the declared
    heap + headroom, auto-derivation must refuse rather than silently
    hand out an insufficient ceiling."""
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    _write_config(tmp_path, test="NODE_OPTIONS=--max-old-space-size=8192 jest")
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda max_parallel: int(6.3 * 1024**3))
    # A tiny slice — well under the declared 8 GiB heap + 2 GiB headroom.
    monkeypatch.setattr(leerie, "_cgroup_slice_info",
                        lambda: (4 * 1024**3, 1, 1 * 1024**3))
    with pytest.raises(SystemExit):
        leerie.resolve_worker_memory_max(tmp_path, max_parallel=4)
